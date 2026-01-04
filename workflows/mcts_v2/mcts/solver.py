import time
import math
import re
import json
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .node import MCTSNode, ActionType, KnowledgeState
from agents.llm_client import LLMClient
from agents.prompts import MASTER_AGENT_PROMPT, PROBE_TEMPLATES_JSON, build_strategy_section, get_task_instruction, get_strategy_rules
from env.db_connector import DatabaseConnector
from env.sc_evaluator import SelfConsistencyEvaluator
from utils.execution_trace import StepRecord, render_execution_trace, create_step_record

class CoCTEMCTSSolver:
    def __init__(self, llm_config: Dict, db_path: str, max_rollouts: int = 10):
        """
        Args:
            max_rollouts: 最大 rollout 次数（真正扩展新节点的次数）
                         遇到 terminal 节点不算 rollout，会继续尝试直到达到 max_rollouts
        """
        self.max_rollouts = max_rollouts
        self.max_depth = 8
        self.k_samples = 1 # 调试时用 1，正式跑建议 3-5
        self.max_children_per_node = 2  # 每个节点最多扩展2个子节点
        self.debug_prompt = False  # 是否打印完整 prompt（调试用）
        
        self.db = DatabaseConnector(db_name=db_path)
        self.llm_client = LLMClient(llm_config)
        self.sc_eval = SelfConsistencyEvaluator()
        
        # 预处理 Probe 工具库文本 (用于 Prompt)
        self.tool_library_text = self._render_tool_library()

    def solve(self, question: str, additional_context: str = "", fixed_strategy: Optional[str] = None) -> Dict[str, Any]:
        """
        Args:
            question: 问题文本
            additional_context: 额外的上下文信息（如 evidence）
            fixed_strategy: 固定策略（S1/S2/S3/S4），如果提供则跳过策略选择步骤
        """
        start_time = time.time()
        
        # 根节点不需要 action_type，因为第一步就是 unify 生成
        root = MCTSNode(parent=None, action_type=ActionType.STRATEGIZE)
        root.question = question
        root.schema_info = self.db.get_formatted_schema()
        root.additional_context = additional_context
        
        # 如果提供了固定策略，在根节点设置策略
        if fixed_strategy:
            root.strategy = fixed_strategy.upper()
            print(f"🚀 Start Unified MCTS Solving (Strategy: {fixed_strategy}): '{question}'")
        else:
            print(f"🚀 Start Unified MCTS Solving: '{question}'")

        rollout_count = 0
        total_iterations = 0
        
        while rollout_count < self.max_rollouts:
            total_iterations += 1
            iter_start = time.time()
            print(f"\n--- Iteration {total_iterations} (Rollout {rollout_count + 1}/{self.max_rollouts}) ---")
            
            leaf = self._select(root)
            
            if leaf.is_terminal:
                print(f"  > Reached Terminal Node (Reward: {leaf.q_value:.2f}), skipping (not counted as rollout)")
                self._backpropagate(leaf, leaf.q_value)
                # 继续循环，不计数为 rollout
                continue
            
            # 核心变化：一步生成动作和代码（这才是真正的 rollout）
            reward = self._unified_expand_and_evaluate(leaf)
            self._backpropagate(leaf, reward)
            rollout_count += 1  # 只有真正扩展了新节点才算一次 rollout
            
            print(f"  > Rollout {rollout_count}/{self.max_rollouts} completed, Time: {time.time() - iter_start:.2f}s")

        best_sql, final_score = self._get_best_result(root)
        return {
            "sql": best_sql,
            "score": final_score,
            "time": time.time() - start_time,
            "rollouts": rollout_count,
            "total_iterations": total_iterations
        }

    # ... _select, _backpropagate, _merge_ctes, _extract_cte_name, _get_last_cte_name, _find_representative_candidate, _get_best_result, _update_knowledge_from_probe 保持不变 ...
    # 为了完整性，我把没变的简单列在这里，请确保你有这些 helper 函数
    
    def _select(self, node: MCTSNode) -> MCTSNode:
        """
        Selection with stop condition: 如果当前节点 children 还没到 max_children_per_node，
        就停在这里扩展它（而不是继续往下走）
        """
        current = node
        while current.children:
            # 如果当前节点还没扩满，就停在这里扩它
            if len(current.children) < self.max_children_per_node and not current.is_terminal:
                return current
            
            # 过滤掉 terminal 节点，只从非 terminal 子节点中选择
            non_terminal_children = [c for c in current.children if not c.is_terminal]
            if not non_terminal_children:
                # 所有子节点都是 terminal，返回当前节点（会在主循环中被处理）
                return current
            
            current = max(non_terminal_children, key=lambda c: c.uct_score())
        return current

    def _backpropagate(self, node: MCTSNode, reward: float):
        current = node
        while current:
            current.visits += 1
            current.value_sum += reward
            current = current.parent

    def _canonicalize_sql(self, s: str) -> str:
        """
        归一化 SQL 字符串，用于去重比较
        - 去掉多余空白/换行
        - 统一大小写（可选，这里先不做）
        - 去掉结尾分号
        - 把连续空白压成单空格
        """
        if not s:
            return ""
        s = s.strip()
        s = re.sub(r"\s+", " ", s)  # 压空白：多个空格/换行/制表符变成单个空格
        s = s.rstrip(";")
        return s
    
    def _merge_ctes(self, history_sql: str, new_cte: str) -> str:
        """拼接CTE，处理WITH和逗号"""
        if not new_cte or new_cte == "<END>": 
            return history_sql
            
        # 1. 预处理：去掉首尾空白
        clean_new = new_cte.strip()
        
        # 2. 去掉开头的 WITH
        clean_new = re.sub(r'^\s*WITH\s+', '', clean_new, flags=re.IGNORECASE).strip()
        
        # 3. 去掉开头的逗号
        if clean_new.startswith(','):
            clean_new = clean_new[1:].strip()

        # 4. 拼接逻辑
        if not history_sql:
            # 第一步：补上 WITH
            return f"WITH {clean_new}"
        else:
            # 后续步骤：补上逗号
            return f"{history_sql},\n{clean_new}"

    def _extract_cte_name(self, cte_sql: str) -> str:
        """提取 CTE 名称，支持多种格式：
        - WITH name AS (...)
        - , name AS (...)
        - name AS (...)  (新增支持)
        """
        match = (
            re.search(r"WITH\s+(\w+)\s+AS", cte_sql, re.IGNORECASE)
            or re.search(r",\s*(\w+)\s+AS", cte_sql, re.IGNORECASE)
            or re.search(r"^\s*(\w+)\s+AS\s*\(", cte_sql, re.IGNORECASE)  # 支持 "name AS (" 格式
        )
        if match: 
            return match.group(1)
        return "unknown_cte"
        
    def _get_last_cte_name(self, accumulated_sql: str) -> str:
        matches = re.findall(r"(\w+)\s+AS\s*\(", accumulated_sql, re.IGNORECASE)
        if matches: return matches[-1]
        return "unknown_table"
    
    def _replace_last_cte(self, accumulated_sql: str, new_cte: str) -> str:
        """
        替换 accumulated_sql 中的最后一个 CTE，而不是追加。
        这是 REFINE 动作的正确语义：修正最后一个 CTE，而不是添加同名 CTE。
        
        Args:
            accumulated_sql: 当前的累积 SQL（可能包含多个 CTE）
            new_cte: 新的 CTE（可能是 "WITH name AS (...)" 或 ", name AS (...)" 或 "name AS (...)" 或 "SELECT ..."）
        
        Returns:
            替换后的完整 SQL
        """
        if not accumulated_sql or not accumulated_sql.strip():
            # 如果没有历史 SQL，直接返回新的（补上 WITH）
            clean_new = new_cte.strip()
            clean_new = re.sub(r'^\s*WITH\s+', '', clean_new, flags=re.IGNORECASE).strip()
            if clean_new.startswith(','):
                clean_new = clean_new[1:].strip()
            return f"WITH {clean_new}"
        
        if not new_cte or new_cte.strip() == "<END>":
            return accumulated_sql
        
        # 1. 清理新的 CTE：去掉 WITH 和开头的逗号
        clean_new = new_cte.strip()
        clean_new = re.sub(r'^\s*WITH\s+', '', clean_new, flags=re.IGNORECASE).strip()
        if clean_new.startswith(','):
            clean_new = clean_new[1:].strip()
        
        # 2. 检测 clean_new 是否包含多个 CTE（通过查找 ", name AS (" 模式）
        # 如果包含多个 CTE，只提取第一个 CTE（因为 REFINE 应该只替换最后一个 CTE）
        cte_matches = list(re.finditer(r'(\w+)\s+AS\s*\(', clean_new, re.IGNORECASE))
        if len(cte_matches) > 1:
            # 包含多个 CTE，只提取第一个 CTE
            first_cte_match = cte_matches[0]
            first_cte_start = first_cte_match.start()
            
            # 找到第一个 CTE 的结束位置（匹配的右括号）
            paren_count = 0
            i = first_cte_match.end() - 1  # AS 后面的 ( 的位置
            found_open = False
            
            while i < len(clean_new):
                if clean_new[i] == '(':
                    paren_count += 1
                    found_open = True
                elif clean_new[i] == ')':
                    paren_count -= 1
                    if found_open and paren_count == 0:
                        # 找到了匹配的右括号
                        first_cte_end = i + 1
                        break
                i += 1
            else:
                # 如果没有找到匹配的右括号，使用整个字符串
                first_cte_end = len(clean_new)
            
            # 提取第一个 CTE
            clean_new = clean_new[first_cte_start:first_cte_end].strip()
        
        # 3. 检测 clean_new 是否是 SELECT 语句（不是 CTE）
        # 如果以 SELECT 开头且不包含 "AS ("，说明是 SELECT 语句，需要转换为 CTE
        is_select_statement = (clean_new.upper().startswith("SELECT") and 
                              not re.search(r'\w+\s+AS\s*\(', clean_new, re.IGNORECASE))
        
        if is_select_statement:
            # 如果是 SELECT 语句，需要获取最后一个 CTE 的名称，然后将 SELECT 包装成 CTE
            last_cte_name = self._get_last_cte_name(accumulated_sql)
            if last_cte_name == "unknown_table":
                # 如果找不到 CTE 名称，使用默认名称
                last_cte_name = "refined_cte"
            # 将 SELECT 语句包装成 CTE 格式
            clean_new = f"{last_cte_name} AS ({clean_new})"
        
        # 4. 找到最后一个 CTE 的位置
        # 使用正则匹配所有 CTE：name AS (...)
        # 需要处理嵌套括号的情况
        cte_pattern = r'(\w+)\s+AS\s*\('
        matches = list(re.finditer(cte_pattern, accumulated_sql, re.IGNORECASE))
        
        if not matches:
            # 如果没有找到 CTE，直接追加
            return f"{accumulated_sql},\n{clean_new}"
        
        # 5. 找到最后一个 CTE 的起始位置
        last_match = matches[-1]
        last_cte_start = last_match.start()
        
        # 6. 找到最后一个 CTE 的结束位置（匹配的右括号）
        # 从 last_cte_start 开始，找到对应的右括号
        paren_count = 0
        i = last_match.end() - 1  # AS 后面的 ( 的位置
        found_open = False
        
        while i < len(accumulated_sql):
            if accumulated_sql[i] == '(':
                paren_count += 1
                found_open = True
            elif accumulated_sql[i] == ')':
                paren_count -= 1
                if found_open and paren_count == 0:
                    # 找到了匹配的右括号
                    last_cte_end = i + 1
                    break
            i += 1
        else:
            # 如果没有找到匹配的右括号，可能是格式错误，直接追加
            return f"{accumulated_sql},\n{clean_new}"
        
        # 7. 检查最后一个 CTE 前面是否有逗号
        # 从 last_cte_start 往前找，跳过空白，看是否有逗号
        before_start = accumulated_sql[:last_cte_start].rstrip()
        has_comma_before = before_start.endswith(',')
        
        # 8. 检查最后一个 CTE 后面是否有逗号或其他内容
        after_end = accumulated_sql[last_cte_end:].strip()
        
        # 9. 构建新的 SQL
        # 保留最后一个 CTE 之前的所有内容
        prefix = accumulated_sql[:last_cte_start].rstrip()
        if has_comma_before:
            # 如果前面有逗号，去掉它（因为我们要替换，不是追加）
            prefix = prefix[:-1].rstrip()
        
        # 10. 拼接：prefix + 逗号（如果需要）+ 新的 CTE + 后面的内容（如果有）
        if prefix:
            # 检查 prefix 是否是 "WITH"（第一个 CTE 的情况）
            if prefix.upper().strip() == "WITH":
                # 如果 prefix 是 WITH，说明这是第一个 CTE，不需要逗号
                if after_end:
                    return f"{prefix} {clean_new}{after_end}"
                else:
                    return f"{prefix} {clean_new}"
            else:
                # 如果 prefix 不为空且不是 WITH，需要添加逗号
                if after_end:
                    return f"{prefix},\n{clean_new}{after_end}"
                else:
                    return f"{prefix},\n{clean_new}"
        else:
            # 如果 prefix 为空，说明这是第一个 CTE，需要 WITH
            if after_end:
                return f"WITH {clean_new}{after_end}"
            else:
                return f"WITH {clean_new}"

    def _update_knowledge_from_probe(self, node: MCTSNode, result_logs: List[str], probe_json_str: str):
            """
            [知识生成 V2] 也就是配置驱动版：从 JSON 模板读取翻译逻辑
            """
            if not result_logs or not probe_json_str: return
            
            import ast
            import json

            try:
                # 1. 解析请求意图
                clean_json = probe_json_str.strip()
                if clean_json.startswith("```"):
                    import re
                    match = re.search(r"```(?:json)?\s*(.*?)```", clean_json, re.DOTALL)
                    if match: clean_json = match.group(1).strip()
                
                tools = json.loads(clean_json)
                if isinstance(tools, dict): tools = [tools]
                
                # 2. 遍历结果
                for i, tool in enumerate(tools):
                    if i >= len(result_logs): break
                    
                    raw_result = result_logs[i]
                    if "Error" in raw_result: continue # 跳过报错的

                    # 3. 解析数据库返回的数据
                    actual_rows = []
                    try:
                        if raw_result.startswith("[") and raw_result.endswith("]"):
                            actual_rows = ast.literal_eval(raw_result)
                    except: continue

                    if not actual_rows: continue

                    # 4. 格式化数据结果 (result_value)
                    # 把 [('A',), ('B',)] 变成 "['A', 'B']" 这种易读格式
                    extracted_values = []
                    for row in actual_rows:
                        if isinstance(row, (list, tuple)) and len(row) > 0:
                            val = str(row[0])
                            if val and val.lower() != 'none':
                                extracted_values.append(val)
                    
                    if not extracted_values: continue
                    
                    # 截断长列表
                    result_value_str = str(extracted_values[:5])

                    # 5. [核心逻辑] 从 JSON 配置中查找翻译模板
                    tid = tool.get('tool_id')
                    params = tool.get('params', {})
                    
                    template_config = next((t for t in PROBE_TEMPLATES_JSON if t['id'] == tid), None)
                    
                    if template_config and "fact_template" in template_config:
                        fact_tmpl = template_config["fact_template"]
                        
                        # 准备填空数据：包括原来的参数 (table, column) + 查到的结果 (result_value)
                        format_data = params.copy()
                        format_data['result_value'] = result_value_str
                        
                        # 生成自然语言 Fact
                        try:
                            fact = fact_tmpl.format(**format_data)
                            
                            # 存入 Knowledge
                            key = f"Fact_{node.depth}_{i}"
                            node.knowledge.verified_values[key] = fact
                            print(f"  > 🧠 Learned: {fact}")
                            
                        except KeyError as e:
                            print(f"  > ⚠️ Template format error: missing key {e}")
                    else:
                        # 兜底逻辑
                        print(f"  > ⚠️ No fact_template for {tid}")

            except Exception as e:
                print(f"  > ⚠️ Knowledge Gen Failed: {e}")        
    def _find_representative_candidate(self, candidates, results, target_fingerprint):
        """找到与目标 fingerprint 匹配的候选"""
        for i, (res, err) in enumerate(results):
            fp = self.sc_eval.compute_result_fingerprint(res, err)
            if fp == target_fingerprint:
                return candidates[i] if i < len(candidates) else candidates[0]
        return candidates[0] if candidates else ""

    def _get_best_result(self, root: MCTSNode) -> Tuple[str, float]:
        best_node = None
        best_score = -1.0
        queue = [root]
        while queue:
            node = queue.pop(0)
            if node.is_terminal:
                if node.q_value > best_score:
                    best_score = node.q_value
                    best_node = node
            queue.extend(node.children)
        if best_node:
            # 优先使用 FINISH 时保存的 final_select
            final_select = getattr(best_node, "final_select", None)
            if final_select and final_select.strip().upper().startswith("SELECT"):
                final_sql = f"{best_node.accumulated_sql}\n{final_select}"
            else:
                # Fallback: 使用默认的 SELECT * FROM last_cte
                last_cte = self._get_last_cte_name(best_node.accumulated_sql)
                final_sql = f"{best_node.accumulated_sql}\nSELECT * FROM {last_cte};"
            return final_sql, best_score
        return "", 0.0

    # ============================================================
    # 新的核心逻辑：Unified Generation
    # ============================================================

    def _unified_expand_and_evaluate(self, node: MCTSNode) -> float:
        print(f"  > [Unified] Asking Master Agent (Depth {node.depth})...")
        
        # 1. 生成混合候选项
        candidates_data = self._generate_unified_candidates(node, k=self.k_samples)
        
        if not candidates_data:
            print("  > ⚠️ Generation failed.")
            return 0.0

        # 2. 多数派投票决定 Action Type
        actions = [c['action'] for c in candidates_data]
        if not actions: return 0.0
        
        major_action = Counter(actions).most_common(1)[0][0]
        print(f"  > Majority Action: {major_action.value} (from {len(actions)} samples)")
        
        # 3. 过滤出符合主流动作的候选
        # 注意：candidates_data 里的 'content' 可能是 SQL 字符串，也可能是 Probe JSON
        valid_candidates = [c['content'] for c in candidates_data if c['action'] == major_action]
        
        # 4. 执行
        execution_results = self._execute_unified(node, valid_candidates, major_action)
        
        # 5. 评分（需要转换为 sc_eval 期望的格式）
        sc_input = [(rows, err) for rows, cols, err in execution_results]
        sc_data = self.sc_eval.evaluate_consensus(sc_input)
        reward = sc_data['score']
        
        # 找到最佳结果的索引，以便获取 columns
        best_idx = 0
        for i, (rows, cols, err) in enumerate(execution_results):
            fp = self.sc_eval.compute_result_fingerprint(rows, err)
            if fp == sc_data['fingerprint']:
                best_idx = i
                break
        
        best_content = self._find_representative_candidate(valid_candidates, sc_input, sc_data['fingerprint'])
        best_columns = execution_results[best_idx][1] if best_idx < len(execution_results) else None
        
        print(f"  > Eval: V={reward:.2f} | Status={sc_data['status']}")
        if sc_data['best_error']: 
            print(f"    Err: {sc_data['best_error']}")
        sample_rows = sc_data['best_result'][:3] if sc_data['best_result'] else []
        
        # 6. 去重检查：避免同一父节点创建重复的子节点（使用规范化后的 SQL）
        existing = set()
        for ch in node.children:
            edge_content = getattr(ch, "edge_content", "")
            canon_content = self._canonicalize_sql(edge_content)
            edge = (ch.action_type, hash(canon_content[:300]))
            existing.add(edge)
        
        canon_best = self._canonicalize_sql(best_content)
        new_edge = (major_action, hash(canon_best[:300]))
        if new_edge in existing:
            print(f"  > ⚠️ Duplicate child edge, skip. (action={major_action.value}, canon_hash={hash(canon_best[:300])})")
            return 0.0
        
        # 7. 创建子节点
        child = MCTSNode(parent=node, action_type=major_action)
        child.question = node.question
        child.schema_info = node.schema_info
        child.additional_context = node.additional_context
        child.knowledge = node.knowledge.clone()  # ⚠️ 修复：深拷贝，避免分支污染
        
        # 策略处理：
        # - 如果是根节点（depth=0），使用模型选择的策略（如果模型输出了策略）
        # - 否则继承父节点策略
        # 找到与 best_content 匹配的候选，提取策略
        best_parsed = None
        for cand in candidates_data:
            if cand['action'] == major_action:
                # 规范化比较，找到匹配的候选
                cand_content = str(cand.get('content', '')).strip()
                best_content_str = str(best_content).strip()
                if self._canonicalize_sql(cand_content) == self._canonicalize_sql(best_content_str):
                    best_parsed = cand
                    break
        
        # 如果没找到精确匹配，使用第一个符合 major_action 的候选
        if not best_parsed:
            for cand in candidates_data:
                if cand['action'] == major_action:
                    best_parsed = cand
                    break
        
        if node.depth == 0:
            # 根节点：如果父节点已有策略（fixed_strategy），则继承；否则从模型响应中提取
            if node.strategy:
                # 固定策略模式：直接继承
                child.strategy = node.strategy
                print(f"  > 🎯 Using fixed strategy: {child.strategy}")
            elif best_parsed and "strategy" in best_parsed:
                # 策略选择模式：从模型响应中提取
                child.strategy = best_parsed["strategy"]
                print(f"  > 🎯 Strategy selected: {child.strategy}")
            else:
                # 兜底：继承父节点策略（如果有）
                child.strategy = node.strategy
        else:
            # 非根节点：继承父节点策略
            child.strategy = node.strategy

        if major_action == ActionType.PROBE:
            # PROBE 动作：observation 应该基于 accumulated_sql 的执行结果，而不是 PROBE SQL 的结果
            # ⚠️ 修复：按 tool 对齐结果，而不是只传一个合并的结果
            # 解析 probe JSON 获取 tool 列表
            try:
                import json
                probe_tools = json.loads(best_content)
                if isinstance(probe_tools, dict):
                    probe_tools = [probe_tools]
                
                # 解析每个 tool 对应的 SQL 并执行，获取独立结果
                probe_sqls = self._parse_probe_candidate(best_content)
                result_logs = []
                
                for i, sql in enumerate(probe_sqls):
                    if i < len(probe_tools):
                        rows, cols, err = self.db.execute_sql(sql)
                        # 转换为字符串格式（_update_knowledge_from_probe 期望字符串列表）
                        if rows:
                            result_logs.append(str(rows))
                        elif err:
                            result_logs.append(f"Error: {err}")
                        else:
                            result_logs.append("[]")  # 空结果
                    else:
                        break
                
                # 如果解析失败，fallback 到原来的逻辑
                if not result_logs:
                    result_logs = [str(sc_data['best_result'])] if sc_data['best_result'] else []
                
            except Exception as e:
                print(f"  > ⚠️ Probe result alignment failed: {e}, using fallback")
                result_logs = [str(sc_data['best_result'])] if sc_data['best_result'] else []
            
            self._update_knowledge_from_probe(child, result_logs, best_content)
            child.accumulated_sql = node.accumulated_sql
            
            # 如果 accumulated_sql 不为空，执行它来获取正确的 observation
            if node.accumulated_sql:
                cte_name = self._get_last_cte_name(node.accumulated_sql)
                if cte_name and cte_name != "unknown_table":
                    test_sql = f"{node.accumulated_sql}\nSELECT * FROM {cte_name} LIMIT 5;"
                    rows, cols, err = self.db.execute_sql(test_sql)
                    sample_rows = rows[:3] if rows else []
                    child.observation = {
                        'status': 'success' if rows and not err else ('error' if err else 'empty'),
                        'row_count': len(rows) if rows else 0,
                        'error_message': err,
                        'sc_score': reward,
                        'sample': sample_rows,  # 保持为列表格式
                        'columns': cols
                    }
                else:
                    # 如果没有有效的 CTE，保持父节点的 observation
                    child.observation = node.observation.copy()
            else:
                # 如果 accumulated_sql 为空，保持父节点的 observation
                child.observation = node.observation.copy()
            
            # 记录 PROBE 步骤（sql_delta 为空或摘要）
            sql_delta = ""  # PROBE 没有 SQL delta
            step_obs = {
                'status': child.observation.get('status', 'unknown'),
                'error': child.observation.get('error_message'),
                'rows': child.observation.get('row_count', 0),
                'columns': child.observation.get('columns'),
                'sample': child.observation.get('sample', [])
            }
            step_record = create_step_record(
                step_id=len(node.execution_trace) + 1,
                strategy_id=child.strategy or "UNKNOWN",
                action="PROBE",
                sql_delta=sql_delta,
                observation=step_obs
            )
            child.execution_trace = node.execution_trace.copy()
            child.execution_trace.append(step_record)
        elif major_action in [ActionType.BUILD, ActionType.REFINE]:
            # BUILD/REFINE 动作：使用当前执行结果作为 observation
            child.observation = {
                'status': sc_data['status'],
                'row_count': len(sc_data['best_result']) if sc_data['best_result'] else 0,
                'error_message': sc_data['best_error'],
                'sc_score': reward,
                'sample': sample_rows,  # 保持为列表格式
                'columns': best_columns
            }
            if best_content == "<END>":
                child.accumulated_sql = node.accumulated_sql
                child.action_type = ActionType.FINISH
                child.is_terminal = True
            else:
                # ====================================================
                # 🔥 核心修复逻辑：REFINE 替换最后一个 CTE，BUILD 追加新 CTE
                # ====================================================
                
                if major_action == ActionType.REFINE:
                    # REFINE: 替换 accumulated_sql 中的最后一个 CTE
                    # 这是正确的语义：修正最后一个 CTE，而不是添加同名 CTE
                    child.accumulated_sql = self._replace_last_cte(node.accumulated_sql, best_content)
                else:
                    # BUILD: 追加新的 CTE
                    child.accumulated_sql = self._merge_ctes(node.accumulated_sql, best_content)
            
            # 记录 BUILD/REFINE 步骤
            sql_delta = best_content  # BUILD/REFINE 的 SQL delta 就是 best_content
            step_obs = {
                'status': child.observation.get('status', 'unknown'),
                'error': child.observation.get('error_message'),
                'rows': child.observation.get('row_count', 0),
                'columns': child.observation.get('columns'),
                'sample': child.observation.get('sample', [])
            }
            step_record = create_step_record(
                step_id=len(node.execution_trace) + 1,
                strategy_id=child.strategy or "UNKNOWN",
                action=major_action.value.upper(),
                sql_delta=sql_delta,
                observation=step_obs
            )
            child.execution_trace = node.execution_trace.copy()
            child.execution_trace.append(step_record)
                
        elif major_action == ActionType.FINISH:
            # FINISH 动作：使用当前执行结果作为 observation
            child.observation = {
                'status': sc_data['status'],
                'row_count': len(sc_data['best_result']) if sc_data['best_result'] else 0,
                'error_message': sc_data['best_error'],
                'sc_score': reward,
                'sample': sample_rows,  # 保持为列表格式
                'columns': best_columns
            }
            child.accumulated_sql = node.accumulated_sql
            child.is_terminal = True
            # 保存 final_select，用于最终输出
            child.final_select = best_content
            
            # 记录 FINISH 步骤
            sql_delta = best_content  # FINISH 的 SQL delta 是 final SELECT
            step_obs = {
                'status': child.observation.get('status', 'unknown'),
                'error': child.observation.get('error_message'),
                'rows': child.observation.get('row_count', 0),
                'columns': child.observation.get('columns'),
                'sample': child.observation.get('sample', [])
            }
            step_record = create_step_record(
                step_id=len(node.execution_trace) + 1,
                strategy_id=child.strategy or "UNKNOWN",
                action="FINISH",
                sql_delta=sql_delta,
                observation=step_obs
            )
            child.execution_trace = node.execution_trace.copy()
            child.execution_trace.append(step_record)
        
        # 保存 edge_content 用于去重
        child.edge_content = best_content
        
        node.add_child(child)
        print(f"  > Added child: parent_depth={node.depth}, now_children={len(node.children)}")
        return reward
    
    def _debug_execution_state(self, node: MCTSNode, prompt: str):
        """输出执行状态摘要（用于调试）"""
        if self.debug_prompt:
            # 完整 prompt 模式
            print(f"\n{'='*20} [PROMPT DEBUG] {'='*20}")
            print(prompt)
            print(f"{'='*60}\n")
        else:
            # 执行状态摘要模式
            strategy_display = node.strategy if node.strategy else "SELECT"
            print(f"\n  > [State] Depth={node.depth} | Strategy={strategy_display} | Children={len(node.children)}")
            
            # Accumulated SQL 摘要
            if node.accumulated_sql and node.accumulated_sql != "-- Start":
                sql_lines = node.accumulated_sql.split('\n')
                sql_preview = '\n'.join(sql_lines[:3])  # 前3行
                if len(sql_lines) > 3:
                    sql_preview += f"\n  ... ({len(sql_lines)-3} more lines)"
                print(f"  > [SQL] {sql_preview}")
            else:
                print(f"  > [SQL] (empty)")
            
            # Execution Trace 摘要
            trace_steps = len(node.execution_trace)
            if trace_steps > 0:
                recent_steps = node.execution_trace[-2:]  # 最近2步
                print(f"  > [Trace] {trace_steps} steps total, recent:")
                for step in recent_steps:
                    obs = step.observation
                    status = obs.get('status', 'unknown')
                    rows = obs.get('rows', 0)
                    error = obs.get('error', '')
                    print(f"      Step {step.step_id}: {step.action} | {status} | rows={rows}" + 
                          (f" | error={error[:50]}" if error else ""))
            else:
                print(f"  > [Trace] (no steps yet)")
            
            # Knowledge 摘要
            knowledge = node.knowledge
            if knowledge.verified_values or knowledge.confirmed_joins:
                facts_count = len(knowledge.verified_values)
                joins_count = len(knowledge.confirmed_joins)
                print(f"  > [Knowledge] {facts_count} facts, {joins_count} joins")
            else:
                print(f"  > [Knowledge] (empty)")
            
            # Observation 摘要
            obs = node.observation
            if obs.get('status') != 'init':
                print(f"  > [Last Obs] status={obs.get('status')}, rows={obs.get('row_count', 0)}, " +
                      f"score={obs.get('sc_score', 0):.2f}")
            
            # Prompt 统计
            print(f"  > [Prompt] length={len(prompt)}, prompt_tokens≈{len(prompt)//4}")
    
    def _generate_unified_candidates(self, node: MCTSNode, k: int) -> List[Dict]:
        """调用 LLM 并解析 (带 Execution Trace)"""   
        
        # 渲染 execution trace
        trace_text = render_execution_trace(node.execution_trace, k=3, max_sql_lines=40, max_sample_rows=3)
        
        # 获取最后一个 CTE 名称（如果 accumulated_sql 不为空）
        last_cte_name = "N/A"
        if node.accumulated_sql and node.accumulated_sql.strip() and node.accumulated_sql != "-- Start":
            last_cte_name = self._get_last_cte_name(node.accumulated_sql)
            if last_cte_name == "unknown_table":
                last_cte_name = "N/A"
        
        # 判断是否是根节点
        is_root = (node.depth == 0) or (node.strategy is None)
        
        # 组装 Prompt（使用 prompts.py 中的函数生成策略相关部分）
        prompt = MASTER_AGENT_PROMPT.format(
            question=node.question,
            schema_info=node.schema_info,
            knowledge_text=node.knowledge.to_prompt_text(),
            accumulated_sql=node.accumulated_sql if node.accumulated_sql else "-- Start",
            execution_trace=trace_text,
            last_cte_name=last_cte_name,
            is_root="true" if is_root else "false",
            strategy_section=build_strategy_section(node.strategy),
            task_instruction=get_task_instruction(node.strategy),
            strategy_rules=get_strategy_rules(node.strategy),
            tool_library_desc=self.tool_library_text
        )

        # --- DEBUG: 打印执行状态（而不是完整 prompt）---
        self._debug_execution_state(node, prompt)

        # 2. 调用 LLM
        raw_responses = self.llm_client.chat([{"role": "user", "content": prompt}], n=k)
        
        # 3. 解析
        parsed = []
        for text in raw_responses:
            res = self._parse_unified_response(text)
            if res: parsed.append(res)
        return parsed

    def _parse_unified_response(self, text: str) -> Optional[Dict]:
        """
        专用 JSON 解析器：只提取第一个合法的 JSON 对象
        """
        import json
        import re

        try:
            # 1. 优先提取 ```json 代码块中的内容（最可靠）
            json_str = None
            
            # 方法1: 提取 ```json 代码块
            json_block_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_block_match:
                json_str = json_block_match.group(1).strip()
            else:
                # 尝试提取普通代码块
                code_block_match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
                if code_block_match:
                    potential_json = code_block_match.group(1).strip()
                    if potential_json.strip().startswith('{'):
                        json_str = potential_json
            
            # 如果代码块提取失败，从后往前找最后一个 { ... } 块
            if not json_str:
                brace_count = 0
                start_idx = -1
                for i in range(len(text) - 1, -1, -1):
                    if text[i] == '}':
                        if brace_count == 0:
                            start_idx = i
                        brace_count += 1
                    elif text[i] == '{':
                        brace_count -= 1
                        if brace_count == 0 and start_idx != -1:
                            json_str = text[i:start_idx + 1]
                            break
            
            if not json_str:
                return None
            
            # 2. 清理并解析 JSON
            json_str = json_str.strip()
            data = json.loads(json_str)
            
            # 4. 提取字段
            action_str = data.get("action", "").upper()
            content = data.get("content", "")
            thought = data.get("thought", "")
            strategy_str = data.get("strategy", "").upper()  # 提取策略字段
            
            # 5. 处理content：如果是字符串且看起来像JSON，尝试解析
            if isinstance(content, str):
                content_str = content.strip()
                if content_str.startswith('[') or content_str.startswith('{'):
                    try:
                        content = json.loads(content_str)
                    except:
                        pass

            # 6. 映射 Action
            action_map = {
                "PROBE": ActionType.PROBE,
                "BUILD": ActionType.BUILD,
                "REFINE": ActionType.REFINE,
                "FINISH": ActionType.FINISH
            }
            action = action_map.get(action_str)
            
            # 7. 验证和规范化策略
            valid_strategies = ["S1", "S2", "S3", "S4"]
            strategy = None
            if strategy_str in valid_strategies:
                strategy = strategy_str

            if action:
                if thought:
                    print(f"  > 💭 Thought: {thought}")
                
                # 如果是 Probe，content 可能是 list/dict，转成 JSON 字符串
                if isinstance(content, (list, dict)):
                    content = json.dumps(content)
                
                content_str = str(content).strip()
                print(f"  > 📝 Content: {content_str[:200]}{'...' if len(content_str) > 200 else ''}")
                
                result = {"action": action, "content": content_str}
                if strategy:
                    result["strategy"] = strategy
                return result
            else:
                print(f"  > ❌ Parser: Unknown action '{action_str}'")

        except json.JSONDecodeError as e:
            print(f"  > ❌ Parser: Invalid JSON format: {e}")
        except Exception as e:
            print(f"  > ❌ Parser: Error: {e}")
        return None
    def _parse_probe_candidate(self, json_or_str: Any) -> List[str]:
            """
            [翻译层] 将 Probe 的 JSON 内容翻译成可执行的 SQL 列表
            """
            sqls = []
            try:
                # 1. 归一化：如果是字符串，先转成 Python 对象
                if isinstance(json_or_str, str):
                    # 清理可能的 markdown 代码块标记
                    clean_str = json_or_str.strip()
                    if clean_str.startswith("```"):
                        import re
                        match = re.search(r"```(?:json)?\s*(.*?)```", clean_str, re.DOTALL)
                        if match: clean_str = match.group(1).strip()
                    
                    if not clean_str: 
                        print(f"  > ⚠️ Probe Parse: Empty string after cleaning")
                        return []
                    
                    try:
                        tools = json.loads(clean_str)
                    except json.JSONDecodeError:
                        return []
                else:
                    tools = json_or_str

                # 2. 归一化：确保是列表
                if isinstance(tools, dict):
                    tools = [tools]
                
                if not isinstance(tools, list):
                    return []

                # 3. 遍历并生成 SQL
                for tool in tools:
                    tid = tool.get('tool_id')
                    params = tool.get('params', {})
                    
                    # 查找模板
                    template = next((t for t in PROBE_TEMPLATES_JSON if t['id'] == tid), None)
                    if not template:
                        continue
                    
                    # 合并默认值
                    defaults = template.get('default_values', {})
                    final_params = defaults.copy()
                    final_params.update(params)
                    
                    # 替换参数
                    sql = template['sql_template']
                    try:
                        for k, v in final_params.items():
                            val_str = str(v)
                            
                            # 🔥 修复：如果表名或列名包含空格，且没有被引号包裹，自动加上双引号
                            if k in ['table', 'column'] and ' ' in val_str:
                                if not (val_str.startswith('"') or val_str.startswith("'") or val_str.startswith("[")):
                                    val_str = f'"{val_str}"'
                            
                            # 防注入处理
                            safe_v = val_str.replace("'", "''")
                            sql = sql.replace(f"{{{k}}}", safe_v)
                        sqls.append(sql)
                    except Exception as e:
                        print(f"  > ⚠️ Probe Param Error: {e}")

            except Exception as e:
                print(f"  > ⚠️ Probe Parse Error: {e}")
                
            return sqls
    def _execute_unified(self, node: MCTSNode, candidates: List[str], action: ActionType) -> List[Tuple[List, Optional[List[str]], Optional[str]]]:
            """
            执行逻辑 (严谨版：防止逻辑泄露)
            
            Returns:
                List[Tuple[rows, columns, error]]: 每个候选的执行结果
            """

            # 结果列表: (rows, columns, error)
            results = [([], None, None)] * len(candidates)
            
            # 任务映射 {future: index}
            task_map = {} 

            with ThreadPoolExecutor(max_workers=len(candidates) or 1) as executor:
                for i, cand in enumerate(candidates):
                    
                    # =================================================
                    # 分支 1: PROBE (必须翻译，且支持多条语句)
                    # =================================================
                    if action == ActionType.PROBE:
                        probe_sqls = self._parse_probe_candidate(cand)
                        
                        if not probe_sqls:
                            results[i] = ([], None, "Probe parsed but yielded no SQL (Check JSON format or tool_id)")
                            continue
                        
                        # 串行执行 Probe 的多条 SQL
                        def run_probe_sequence(sqls):
                            all_rows = []
                            all_columns = []
                            errors = []
                            for sql in sqls:
                                rows, cols, err = self.db.execute_sql(sql)
                                if rows:
                                    all_rows.extend(rows)  # 合并所有结果
                                if cols:
                                    # 合并列名（去重）
                                    for col in cols:
                                        if col not in all_columns:
                                            all_columns.append(col)
                                if err:
                                    errors.append(err)
                            if not all_rows and not errors: 
                                return ([], None, None)
                            # 返回原始行数据、列名和错误
                            return (all_rows, all_columns if all_columns else None, "; ".join(errors) if errors else None)

                        future = executor.submit(run_probe_sequence, probe_sqls)
                        task_map[future] = i

                    # =================================================
                    # 分支 2: BUILD / REFINE / FINISH (单条 SQL)
                    # =================================================
                    elif action in [ActionType.BUILD, ActionType.REFINE, ActionType.FINISH]:
                        task_sql = ""
                        
                        if action == ActionType.BUILD:
                            if cand == "<END>":
                                cte_name = self._get_last_cte_name(node.accumulated_sql)
                                task_sql = f"{node.accumulated_sql}\nSELECT * FROM {cte_name} LIMIT 5;"
                            else:
                                full_sql = self._merge_ctes(node.accumulated_sql, cand)
                                cte_name = self._extract_cte_name(cand)
                                task_sql = f"{full_sql}\nSELECT * FROM {cte_name} LIMIT 5;"
                        
                        elif action == ActionType.REFINE:
                            # REFINE: 替换 accumulated_sql 中的最后一个 CTE
                            # 这是正确的语义：修正最后一个 CTE，而不是添加同名 CTE
                            full_sql = self._replace_last_cte(node.accumulated_sql, cand)
                            # 从替换后的 SQL 中提取最后一个 CTE 名称（因为 cand 可能是 SELECT 语句）
                            cte_name = self._get_last_cte_name(full_sql)
                            task_sql = f"{full_sql}\nSELECT * FROM {cte_name} LIMIT 5;"
                        
                        elif action == ActionType.FINISH:
                            clean_cand = cand.strip()
                            if clean_cand.upper().startswith("SELECT"):
                                task_sql = f"{node.accumulated_sql}\n{clean_cand}"
                            else:
                                cte_name = self._get_last_cte_name(node.accumulated_sql)
                                task_sql = f"{node.accumulated_sql}\nSELECT * FROM {cte_name};"

                        # 提交任务
                        if task_sql:
                            future = executor.submit(self.db.execute_sql, task_sql)
                            task_map[future] = i
                    
                    # =================================================
                    # 分支 3: 未知动作 (防守逻辑)
                    # =================================================
                    else:
                        print(f"  > ❌ [Exec Error] Unknown ActionType: {action}")
                        results[i] = ([], None, f"Unknown ActionType: {action}")

                # 收集结果
                for future in as_completed(task_map):
                    idx = task_map[future]
                    try:
                        result = future.result()
                        # 确保返回格式是 (rows, columns, error)
                        if len(result) == 2:
                            # 兼容旧格式 (rows, error)
                            rows, err = result
                            results[idx] = (rows, None, err)
                        else:
                            results[idx] = result
                    except Exception as e:
                        results[idx] = ([], None, str(e))
            
            return results

    def _render_tool_library(self) -> str:
        """渲染工具库文本"""
        txt = ""
        for t in PROBE_TEMPLATES_JSON:
            txt += f"- ID: {t['id']}\n  Desc: {t['description']}\n  Params: {t['parameters']}\n"
        return txt


# python /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts_v1/test/run_all_strategies.py \
#   --ppl_file /home/shenshuyu/SQL_tool_multiAgent/data/subset_ppl_dev_python.json \
#   --sql_out /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts_v1/test/out/test_single_rollout.txt \
#   --json_out /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts_v1/test/out/test_single_rollout.json \
#   --gold_file /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5 \
#   --max_cte_nodes 5 \
#   --max_depth 8 \
#   --num_sql_variants 5 \
#   --multi_base_urls "http://localhost:8010/v1,http://localhost:8012/v1,http://localhost:8009/v1"