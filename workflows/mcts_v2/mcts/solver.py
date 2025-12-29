import time
import math
import re
import json
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .node import MCTSNode, ActionType, KnowledgeState
from agents.llm_client import LLMClient
from agents.prompts import MASTER_AGENT_PROMPT, PROBE_TEMPLATES_JSON
from env.db_connector import DatabaseConnector
from env.sc_evaluator import SelfConsistencyEvaluator

class CoCTEMCTSSolver:
    def __init__(self, llm_config: Dict, db_path: str, max_iterations: int = 10):
        self.max_iterations = max_iterations
        self.max_depth = 8
        self.k_samples = 1 # 调试时用 1，正式跑建议 3-5
        
        self.db = DatabaseConnector(db_name=db_path)
        self.llm_client = LLMClient(llm_config)
        self.sc_eval = SelfConsistencyEvaluator()
        
        # 预处理 Probe 工具库文本 (用于 Prompt)
        self.tool_library_text = self._render_tool_library()

    def solve(self, question: str, additional_context: str = "") -> Dict[str, Any]:
        start_time = time.time()
        
        # 根节点不需要 action_type，因为第一步就是 unify 生成
        root = MCTSNode(parent=None, action_type=ActionType.STRATEGIZE)
        root.question = question
        root.schema_info = self.db.get_formatted_schema()
        root.additional_context = additional_context
        
        print(f"🚀 Start Unified MCTS Solving: '{question}'")

        for i in range(self.max_iterations):
            iter_start = time.time()
            print(f"\n--- Iteration {i+1}/{self.max_iterations} ---")
            
            leaf = self._select(root)
            
            if leaf.is_terminal:
                print(f"  > Reached Terminal Node (Reward: {leaf.q_value:.2f})")
                self._backpropagate(leaf, leaf.q_value)
                continue
            
            # 核心变化：一步生成动作和代码
            reward = self._unified_expand_and_evaluate(leaf)
            self._backpropagate(leaf, reward)
            
            print(f"  > Iteration Time: {time.time() - iter_start:.2f}s")

        best_sql, final_score = self._get_best_result(root)
        return {
            "sql": best_sql,
            "score": final_score,
            "time": time.time() - start_time,
            "steps": i + 1
        }

    # ... _select, _backpropagate, _merge_ctes, _extract_cte_name, _get_last_cte_name, _find_representative_candidate, _get_best_result, _update_knowledge_from_probe 保持不变 ...
    # 为了完整性，我把没变的简单列在这里，请确保你有这些 helper 函数
    
    def _select(self, node: MCTSNode) -> MCTSNode:
        current = node
        while current.children:
            current = max(current.children, key=lambda c: c.uct_score())
        return current

    def _backpropagate(self, node: MCTSNode, reward: float):
        current = node
        while current:
            current.visits += 1
            current.value_sum += reward
            current = current.parent

    def _merge_ctes(self, history_sql: str, new_cte: str) -> str:
            """
            带 Debug 日志的拼接函数，用于定位语法错误
            """
            # --- DEBUG START ---
            print(f"\n{'='*20} [Merge CTE Debug] {'='*20}")
            # 使用 repr() 可以看到换行符 \n 和空格，这对于排查 SQL 错误至关重要
            print(f"[1] History SQL (len={len(history_sql)}): {repr(history_sql)}")
            print(f"[2] Raw New CTE from LLM: {repr(new_cte)}")
            # -------------------

            if not new_cte or new_cte == "<END>": 
                print("[Result] Keep History (End or Empty)")
                return history_sql
                
            # 1. 预处理：去掉首尾空白
            clean_new = new_cte.strip()
            
            # 2. 核心清洗：去掉开头的 WITH (不管有没有)
            # 这一步是为了防止 LLM 自己加了 WITH，导致变成 "WITH WITH CTE..."
            clean_new = re.sub(r'^\s*WITH\s+', '', clean_new, flags=re.IGNORECASE).strip()
            
            # 3. 核心清洗：去掉开头的逗号 (不管有没有)
            # 这一步是为了防止 LLM 听从了 Prompt 里的 ", new_cte" 格式，导致变成 ", , CTE..."
            if clean_new.startswith(','):
                clean_new = clean_new[1:].strip()

            print(f"[3] Cleaned CTE Body: {repr(clean_new)}")

            # 4. 拼接逻辑
            final_sql = ""
            
            # 情况 A: 这是整个链条的第一步 (History 为空)
            if not history_sql:
                # 强制补上 WITH
                final_sql = f"WITH {clean_new}"
                print("[Logic] First Step -> Adding 'WITH' prefix")
                
            # 情况 B: 这是后续步骤 (History 不为空)
            else:
                # 强制补上 逗号
                final_sql = f"{history_sql},\n{clean_new}"
                print("[Logic] Subsequent Step -> Adding ',' connector")

            # --- DEBUG END ---
            print(f"[4] Final Merged SQL:\n{final_sql}")
            print(f"{'='*60}\n")
            
            return final_sql

    def _extract_cte_name(self, cte_sql: str) -> str:
        match = re.search(r"WITH\s+(\w+)\s+AS", cte_sql, re.IGNORECASE) or re.search(r",\s*(\w+)\s+AS", cte_sql, re.IGNORECASE)
        if match: return match.group(1)
        return "unknown_cte"
        
    def _get_last_cte_name(self, accumulated_sql: str) -> str:
        matches = re.findall(r"(\w+)\s+AS\s*\(", accumulated_sql, re.IGNORECASE)
        if matches: return matches[-1]
        return "unknown_table"

    def _update_knowledge_from_probe(self, node: MCTSNode, result_rows: List):
        if not result_rows: return
        try:
            values = [str(row[0]) for row in result_rows[:3]]
            node.knowledge.verified_values[f"Probe_{node.depth}"] = str(values)
        except: pass
        
    def _find_representative_candidate(self, candidates, results, target_fingerprint):
        for i, (res, err) in enumerate(results):
            fp = self.sc_eval.compute_result_fingerprint(res, err)
            if fp == target_fingerprint:
                return candidates[i]
        return candidates[0]

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
        
        # 5. 评分
        sc_data = self.sc_eval.evaluate_consensus(execution_results)
        reward = sc_data['score']
        
        best_content = self._find_representative_candidate(valid_candidates, execution_results, sc_data['fingerprint'])
        
        print(f"  > Eval: V={reward:.2f} | Status={sc_data['status']}")
        if sc_data['best_error']: print(f"    Err: {sc_data['best_error'][:60]}...")
        sample_rows = sc_data['best_result'][:3] if sc_data['best_result'] else []
        # 6. 创建子节点
        child = MCTSNode(parent=node, action_type=major_action)
        child.question = node.question
        child.schema_info = node.schema_info
        child.additional_context = node.additional_context
        child.knowledge = node.knowledge
        child.observation = {
            'status': sc_data['status'],
            'row_count': len(sc_data['best_result']) if sc_data['best_result'] else 0,
            'error_message': sc_data['best_error'],
            'sc_score': reward,
            'sample': str(sample_rows)  # <--- 新增：存入样本字符串
        }

        if major_action == ActionType.PROBE:
            self._update_knowledge_from_probe(child, sc_data['best_result'])
            child.accumulated_sql = node.accumulated_sql 
            
        elif major_action in [ActionType.BUILD, ActionType.REFINE]:
            if best_content == "<END>":
                child.accumulated_sql = node.accumulated_sql
                child.action_type = ActionType.FINISH
                child.is_terminal = True
            else:
                # ====================================================
                # 🔥 核心修复逻辑：智能判断是 [追加] 还是 [替换]
                # ====================================================
                
                # 清理一下，方便判断
                clean_content = best_content.strip()
                
                # 判断条件 1: 动作是 REFINE (通常意味着重写)
                # 判断条件 2: 内容以 WITH 开头 (说明 LLM 给了完整的 SQL)
                is_full_rewrite = (major_action == ActionType.REFINE) or \
                                  (clean_content.upper().startswith("WITH "))

                if is_full_rewrite:
                    print(f"  > [Logic] REFINE/Full Rewrite detected -> Replacing SQL.")
                    # 替换：直接用新的，但要保证它真的以 WITH 开头 (防止 REFINE 只吐了中间一段)
                    # 简单的归一化：如果它没写 WITH，我们帮它加 (虽然很少见)
                    if not clean_content.upper().startswith("WITH"):
                        child.accumulated_sql = "WITH " + clean_content
                    else:
                        child.accumulated_sql = clean_content
                else:
                    print(f"  > [Logic] BUILD/Incremental -> Merging CTE.")
                    # 追加：使用之前的 _merge_ctes 逻辑
                    child.accumulated_sql = self._merge_ctes(node.accumulated_sql, best_content)
                
        elif major_action == ActionType.FINISH:
            child.accumulated_sql = node.accumulated_sql
            child.is_terminal = True
            
        node.add_child(child)
        return reward
    def _generate_unified_candidates(self, node: MCTSNode, k: int) -> List[Dict]:
        """调用 LLM 并解析 (带 Sample)"""   
        # 提取样本，如果没有则显示 None
        sample_text = node.observation.get('sample', '[]')
        
        # 组装 Prompt
        prompt = MASTER_AGENT_PROMPT.format(
            question=node.question,
            schema_info=node.schema_info,
            knowledge_text=node.knowledge.to_prompt_text(),
            accumulated_sql=node.accumulated_sql if node.accumulated_sql else "-- Start",
            
            # --- 修改这里：加入 Sample ---
            observation_section=f"""- Status: {node.observation.get('status', 'init')}
- Error: {node.observation.get('error_message')}
- Rows: {node.observation.get('row_count', 0)}
- Sample Data: {sample_text}""",
            # ---------------------------
            
            tool_library_desc=self.tool_library_text
        )

        # --- DEBUG: 打印完整的 Prompt ---
        # # 只打印前 2000 个字符防止刷屏太严重，或者打印全部
        # print(f"\n{'='*20} [PROMPT DEBUG] {'='*20}")
        # print(prompt) 
        # print(f"{'='*60}\n")
        # -------------------------------

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

        print(f"[DEBUG] Raw LLM Response: {repr(text[:200])}...") # 打印前200字符

        try:
            # 1. 寻找 JSON 块
            # 匹配最外层的 { ... }，re.DOTALL 让 . 可以匹配换行符
            match = re.search(r'\{.*\}', text, re.DOTALL)
            
            if not match:
                print("  > ❌ Parser: No JSON found.")
                return None

            json_str = match.group(0)
            
            # 2. 解析 JSON
            data = json.loads(json_str)
            
            # 3. 提取字段
            action_str = data.get("action", "").upper()
            content = data.get("content", "")
            thought = data.get("thought", "")

            # 4. 映射 Action
            action_map = {
                "PROBE": ActionType.PROBE,
                "BUILD": ActionType.BUILD,
                "REFINE": ActionType.REFINE,
                "FINISH": ActionType.FINISH
            }
            action = action_map.get(action_str)

            if action:
                # 打印模型的思考过程 (很有用!)
                if thought:
                    print(f"  > 💭 Thought: {thought}")
                
                # 如果是 Probe，content 可能是 list/dict，转成 string
                if isinstance(content, (list, dict)):
                    content = json.dumps(content)
                
                return {"action": action, "content": str(content).strip()}
            else:
                print(f"  > ❌ Parser: Unknown action '{action_str}'")

        except json.JSONDecodeError as e:
            print(f"  > ❌ Parser: Invalid JSON format. Error: {e}")
        except Exception as e:
            print(f"  > ❌ Parser: Unexpected error: {e}")

        return None
    def _json_tools_to_sql_list(self, json_str: str) -> List[str]:
        """解析 JSON 并返回 SQL 列表"""
        try:
            tools = json.loads(json_str)
            sqls = []
            for tool in tools:
                tid = tool.get('tool_id')
                params = tool.get('params', {})
                template = next((t for t in PROBE_TEMPLATES_JSON if t['id'] == tid), None)
                if template:
                    sql = template['sql_template']
                    for k, v in params.items():
                        sql = sql.replace(f"{{{k}}}", str(v))
                    sqls.append(sql)
            return sqls
        except:
            return []
    def _execute_unified(self, node: MCTSNode, candidates: List[str], action: ActionType) -> List[Tuple[List, str]]:
            """根据动作类型执行"""
            tasks = []
            for cand in candidates:
                if action == ActionType.PROBE:
                    # --- 修改这里 ---
                    # 获取多条 SQL 语句的列表，而不是拼成一个字符串
                    probe_sqls = self._json_tools_to_sql_list(cand) 
                    
                    # 我们这里简化处理：只执行第一条，或者把它们合并成一个复杂的 Query
                    # 为了不破坏并行结构，最简单的方法是只取第一条有效的
                    if probe_sqls:
                        tasks.append(probe_sqls[0]) 
                    else:
                        tasks.append("") # 空任务
                    
                elif action == ActionType.BUILD:
                    # BUILD 是增量，必须拼接
                    if cand == "<END>":
                        cte_name = self._get_last_cte_name(node.accumulated_sql)
                        tasks.append(f"{node.accumulated_sql}\nSELECT * FROM {cte_name} LIMIT 5;")
                    else:
                        full_sql = self._merge_ctes(node.accumulated_sql, cand)
                        cte_name = self._extract_cte_name(cand)
                        tasks.append(f"{full_sql}\nSELECT * FROM {cte_name} LIMIT 5;")

                elif action == ActionType.REFINE:
                    # ==========================================
                    # 🔥 修复点：REFINE 是替换，不要和旧 SQL 拼接！
                    # ==========================================
                    clean_cand = cand.strip()
                    
                    # 如果 REFINE 给的是完整 SQL (带 WITH)，直接用
                    if clean_cand.upper().startswith("WITH"):
                        full_sql = clean_cand
                    else:
                        # 如果只给了 CTE 定义，帮它补上 WITH
                        full_sql = f"WITH {clean_cand}"
                    
                    cte_name = self._extract_cte_name(clean_cand)
                    tasks.append(f"{full_sql}\nSELECT * FROM {cte_name} LIMIT 5;")
                
                elif action == ActionType.FINISH:
                    # ... (不变)
                    clean_cand = cand.strip()
                    if clean_cand.upper().startswith("SELECT"):
                        tasks.append(f"{node.accumulated_sql}\n{clean_cand}")
                    else:
                        cte_name = self._get_last_cte_name(node.accumulated_sql)
                        tasks.append(f"{node.accumulated_sql}\nSELECT * FROM {cte_name};")

            # 并行执行
            results = []
            with ThreadPoolExecutor(max_workers=len(tasks) or 1) as executor:
                future_to_idx = {executor.submit(self.db.execute_sql, sql): i for i, sql in enumerate(tasks) if sql}
                results = [([], None)] * len(tasks)
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        results[idx] = future.result()
                    except Exception as e:
                        results[idx] = ([], str(e))
            return results

    def _json_tools_to_sql(self, json_str: str) -> str:
        """将 LLM 生成的 JSON 工具调用转换为 SQL"""
        try:
            tools = json.loads(json_str)
            sqls = []
            for tool in tools:
                tid = tool.get('tool_id')
                params = tool.get('params', {})
                # 查找模板
                template = next((t for t in PROBE_TEMPLATES_JSON if t['id'] == tid), None)
                if template:
                    # 简单的字符串替换
                    sql = template['sql_template']
                    for k, v in params.items():
                        sql = sql.replace(f"{{{k}}}", str(v))
                    sqls.append(sql)
            return ";\n".join(sqls) # 如果有多个，拼在一起 (Execute SQL只执行第一条，但这里示意)
        except:
            return ""

    def _render_tool_library(self) -> str:
        """渲染工具库文本"""
        txt = ""
        for t in PROBE_TEMPLATES_JSON:
            txt += f"- ID: {t['id']}\n  Desc: {t['description']}\n  Params: {t['parameters']}\n"
        return txt