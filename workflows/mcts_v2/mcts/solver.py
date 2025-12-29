import time
import math
import re
import json
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .node import MCTSNode, ActionType, KnowledgeState
from agents.llm_client import LLMClient
from agents.probe_generator import ProbeGenerator # 只用到它的 render_tool_library 方法，或者你可以把那个方法搬过来
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
        if not new_cte or new_cte == "<END>": return history_sql
        new_cte = new_cte.strip()
        if not history_sql: return new_cte
        cleaned_new_cte = re.sub(r'^\s*WITH\s+', '', new_cte, flags=re.IGNORECASE).strip()
        return f"{history_sql},\n{cleaned_new_cte}"

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
            'sc_score': reward
        }

        # 根据动作类型更新状态
        if major_action == ActionType.PROBE:
            # Probe 成功的话，更新知识库
            self._update_knowledge_from_probe(child, sc_data['best_result'])
            child.accumulated_sql = node.accumulated_sql # 保持 SQL 不变
            
        elif major_action in [ActionType.BUILD, ActionType.REFINE]:
            if best_content != "<END>":
                child.accumulated_sql = self._merge_ctes(node.accumulated_sql, best_content)
            else:
                child.accumulated_sql = node.accumulated_sql
                # 如果生成了 <END>，虽然 Action 是 BUILD，但实际上意味着结束
                # 我们在下一轮或者这里直接标记 finish
                # 为了简单，如果这里解析出 <END>，可以直接标记 terminal
                child.action_type = ActionType.FINISH
                child.is_terminal = True
                
        elif major_action == ActionType.FINISH:
            child.accumulated_sql = node.accumulated_sql
            child.is_terminal = True
            
        node.add_child(child)
        return reward

    def _generate_unified_candidates(self, node: MCTSNode, k: int) -> List[Dict]:
        """调用 LLM 并解析"""
        prompt = MASTER_AGENT_PROMPT.format(
            question=node.question,
            schema_info=node.schema_info,
            knowledge_text=node.knowledge.to_prompt_text(),
            accumulated_sql=node.accumulated_sql if node.accumulated_sql else "-- Start",
            observation_section=f"- Status: {node.observation.get('status', 'init')}\n- Error: {node.observation.get('error_message')}\n- Rows: {node.observation.get('row_count', 0)}",
            tool_library_desc=self.tool_library_text
        )
        
        raw_responses = self.llm_client.chat([{"role": "user", "content": prompt}], n=k)
        
        parsed = []
        for text in raw_responses:
            res = self._parse_unified_response(text)
            if res: parsed.append(res)
        return parsed

    def _parse_unified_response(self, text: str) -> Optional[Dict]:
        """解析 ACTION: ... \n CONTENT: ..."""
        try:
            lines = text.strip().split('\n')
            action = None
            content_lines = []
            is_collecting = False
            
            for line in lines:
                clean_line = line.strip()
                if clean_line.upper().startswith("ACTION:"):
                    act_str = clean_line.split(":", 1)[1].strip().upper()
                    if "PROBE" in act_str: action = ActionType.PROBE
                    elif "BUILD" in act_str: action = ActionType.BUILD
                    elif "REFINE" in act_str: action = ActionType.REFINE
                    elif "FINISH" in act_str: action = ActionType.FINISH
                elif clean_line.upper().startswith("SQL:") or clean_line.upper().startswith("TOOLS:"):
                    is_collecting = True
                elif is_collecting:
                    content_lines.append(line)
            
            content = "\n".join(content_lines).strip()
            # 清理 Markdown
            content = re.sub(r'^```\w*\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
            
            if action:
                return {"action": action, "content": content}
        except Exception as e:
            print(f"Parse Error: {e}")
        return None

    def _execute_unified(self, node: MCTSNode, candidates: List[str], action: ActionType) -> List[Tuple[List, str]]:
        """根据动作类型执行"""
        tasks = []
        for cand in candidates:
            if action == ActionType.PROBE:
                # 解析 JSON -> SQL
                probe_sql = self._json_tools_to_sql(cand)
                tasks.append(probe_sql)
                
            elif action in [ActionType.BUILD, ActionType.REFINE]:
                if cand == "<END>":
                    # 测试当前的 SQL
                    cte_name = self._get_last_cte_name(node.accumulated_sql)
                    tasks.append(f"{node.accumulated_sql}\nSELECT * FROM {cte_name} LIMIT 5;")
                else:
                    full_sql = self._merge_ctes(node.accumulated_sql, cand)
                    cte_name = self._extract_cte_name(cand)
                    tasks.append(f"{full_sql}\nSELECT * FROM {cte_name} LIMIT 5;")
            
            elif action == ActionType.FINISH:
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