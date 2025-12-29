import re
from typing import List, Dict
from mcts.node import MCTSNode, ActionType
from .llm_client import LLMClient
from .prompts import (
    CTE_SYSTEM_PROMPT_TEMPLATE, 
    CTE_USER_PROMPT_TEMPLATE, 
    STRATEGY_DEFINITIONS
)

class CTEGenerator:
    def __init__(self, llm_config: Dict, max_depth: int = 8):
        self.llm = LLMClient(llm_config)
        self.max_depth = max_depth

    def generate_ctes(self, node: MCTSNode, k: int = 5) -> List[str]:
        """
        生成 K 个 CTE 候选。
        对应 CoCTEWorkflow 中的 _expand_and_evaluate(ActionType.BUILD/REFINE)
        """
        # 1. 准备 System Prompt
        strategy_desc = STRATEGY_DEFINITIONS.get(node.strategy, STRATEGY_DEFINITIONS["S4"])
        system_msg = CTE_SYSTEM_PROMPT_TEMPLATE.format(strategy_desc=strategy_desc)

        # 2. 准备 User Input (Context)
        user_msg = self._construct_user_prompt(node)

        # 3. 调用 LLM (并行采样 k 次)
        # 使用 temperature=0.7 以获得多样化的 CTE (特别是针对模糊匹配)
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg}
        ]
        
        # 调用 LLMClient
        raw_responses = self.llm.chat(messages, temperature=0.7, n=k)
        
        # 4. 提取和清洗
        valid_ctes = []
        for response in raw_responses:
            cte = self._extract_cte_from_response(response)
            if cte:
                valid_ctes.append(cte)
        
        # 如果生成失败，返回空列表，MCTS 会处理
        return valid_ctes

    def _construct_user_prompt(self, node: MCTSNode) -> str:
            """
            构建详细的用户输入 Prompt。
            (已移除模糊匹配 Hint，保持职责单一：只根据已知 Fact 写 SQL)
            """
            obs = node.observation
            
            # 1. 格式化 Observation (上一步的执行反馈)
            obs_section = f"- Status: {obs.get('status', 'init')}\n"
            
            if obs.get('status') == 'error':
                obs_section += f"- Error: {obs.get('error_message', 'Unknown Error')}\n"
            else:
                rows = obs.get('row_count', -1)
                obs_section += f"- Row Count: {rows}\n"

            # 2. 渲染模板
            return CTE_USER_PROMPT_TEMPLATE.format(
                question=getattr(node, 'question', ''),
                schema_info=getattr(node, 'schema_info', ''),
                
                # 核心：注入知识库
                knowledge_text=node.knowledge.to_prompt_text(), 
                
                # 题目上下文
                context=getattr(node, 'additional_context', ''),
                
                # 代码骨架
                accumulated_sql=node.accumulated_sql if node.accumulated_sql else "-- Start of query",
                
                # 动态反馈区
                observation_section=obs_section,
                
                # 强制传入空字符串，彻底禁用 Hint
                fuzzy_match_hint="",
                
                # 深度控制
                depth=node.depth,
                max_depth=self.max_depth,
                remaining_steps=max(0, self.max_depth - node.depth)
            )
    def _extract_cte_from_response(self, response: str) -> str:
        """
        移植你原有的 CTE 提取逻辑。
        负责处理 Markdown、WITH 提取和 SELECT 补全。
        """
        response = response.strip()
        
        # 1. 处理 <END>
        if "<END>" in response:
            return "<END>"

        # 2. 提取代码块
        code_block_match = re.search(r'```sql\s*(.*?)\s*```', response, re.DOTALL)
        if code_block_match:
            cte_text = code_block_match.group(1).strip()
        else:
            # 尝试直接提取 WITH ... )
            # 简单正则提取 WITH 到最后一个 )
            # 这里简化处理，假设 LLM 听话。如果很复杂，可以复用你原来的逐行解析逻辑
            cte_text = response

        # 3. 清理不需要的 SELECT * FROM ...
        # (因为 CoCTE 框架会在 Execution 阶段自动拼接 SELECT，或者由下一节点拼接)
        # 但为了保持你原有的逻辑："LLM只生成 WITH，系统自动添加 SELECT"
        # 我们这里只返回 WITH 部分。ExecutionProfiler 会负责拼接测试用的 SELECT。
        
        # 移除末尾分号
        cte_text = cte_text.rstrip(';')
        
        # 移除末尾可能的 SELECT
        # 简单 heuristic: 如果最后有 SELECT 且不在括号内，去掉它
        if "SELECT" in cte_text.upper().split(')')[-1]:
             last_paren = cte_text.rfind(')')
             if last_paren != -1:
                 cte_text = cte_text[:last_paren+1]

        if "WITH" not in cte_text.upper():
            return "" # 无效

        return cte_text