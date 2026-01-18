"""
CTE生成器智能体

负责生成Common Table Expression (CTE)，
这是MCTS算法中的关键组件，用于生成SQL查询的中间步骤。 
"""

import autogen
from typing import Dict, List, Any, Optional, Tuple
import Levenshtein
import random
import re
import concurrent.futures
import threading
from openai import OpenAI
from ..utils.mcts_helpers import MCTSUtils


class CTEGenerator:
    """CTE生成器智能体"""
    
    def __init__(self, llm_config: Dict, max_depth: int = 5, multi_model_configs: List[Dict] = None, relationships_map: Dict[str, Dict[str, Any]] = None, relationships_data: Dict[str, Any] = None, cte_probe_limit: int = 15):
        """
        初始化CTE生成器
        
        Args:
            llm_config: LLM配置
            max_depth: 最大允许深度（步骤数）
            multi_model_configs: 多个模型配置列表（用于多模型并行加速）
            relationships_map: 关系映射字典，格式: {f"{table1}<->{table2}": {'type': '1:1', ...}, ...}
            relationships_data: 关系数据字典，格式: {db_name: {relationships: [...], metadata: {...}}}
            cte_probe_limit: CTE探针查询的LIMIT值（默认15）
        """
        self.llm_config = llm_config
        self.max_depth = max_depth
        self.multi_model_configs = multi_model_configs or []
        self.relationships_map = relationships_map or {}
        self.relationships_data = relationships_data or {}
        self.cte_probe_limit = cte_probe_limit
        # 线程锁：保护 agent 创建过程（避免并行环境下的 Pydantic 冲突）
        self._agent_lock = threading.Lock()
        # 用于轮询选择模型的计数器（线程安全）
        self._model_counter_lock = threading.Lock()
        self._model_counter = 0
        self.setup_agent()
    
    def setup_agent(self, temperature: float = 0.2):
        """
        设置CTE生成智能体（线程安全）
        
        Args:
            temperature: 温度参数
        """
        # 使用锁保护 agent 创建，避免并行环境下的冲突
        with self._agent_lock:
            # 创建带有指定temperature的llm_config
            llm_config_with_temp = self.llm_config.copy()
            if 'config_list' in llm_config_with_temp:
                for config in llm_config_with_temp['config_list']:
                    # 常见两种配置方式都同步设置
                    try:
                        config['temperature'] = temperature
                    except Exception:
                        pass
                    try:
                        if isinstance(config.get('openai'), dict):
                            config['openai']['temperature'] = temperature
                    except Exception:
                        pass
            
            self.cte_agent = autogen.AssistantAgent(
                name="CTEGenerator",
                llm_config=llm_config_with_temp,
                system_message=self._get_cte_system_message()
            )
            
            self.user_proxy = autogen.UserProxyAgent(
                name="CTEUserProxy",
                human_input_mode="NEVER",
                max_consecutive_auto_reply=0,
                code_execution_config=False
            )
    
    def _get_cte_system_message(self) -> str:
        """获取CTE生成器的系统消息"""
        max_depth_str = str(self.max_depth) if hasattr(self, 'max_depth') else "5"
        return f"""You are a professional data analyst. You need to decide what to explore next based on the existing content.

**Task: Based on the provided natural language question, database schema and Evidence/Additional Context, do the generation.

**CRITICAL: Always check the execution results of the last CTE before deciding to output <END>.**
- If the last CTE execution FAILED or returned EMPTY results, you MUST generate a new CTE to fix the issue
- Execution results are FACTS - if they show errors or empty results, the question is NOT answered yet
- Only when the last CTE executed successfully AND contains all required information should you consider outputting <END>

**Decision Logic - When to Output <END> vs Generate New CTE**:

**ONLY output <END> when ALL of the following conditions are met:**
1. The last CTE executed SUCCESSFULLY (no errors, no empty results)
2. The last CTE contains ALL information needed to answer the question COMPLETELY
3. For compound questions requiring multiple pieces of information, the last CTE must be a MERGING CTE that JOINs all separate CTEs together in a single result set
4. All required information is present and correctly formatted in the final result

**MUST generate a new CTE (DO NOT output <END>) when ANY of the following is true:**
1. The last CTE execution FAILED (SQL error) → 
   - **If the error is a CTE column reference error** (column doesn't exist in preceding CTEs): You MUST regenerate the COMPLETE CTE chain from Step 0 to the current step, ensuring all columns are properly propagated through the chain
   - **If the error is other types** (e.g., table/column not found in schema, syntax error): Generate a repair/exploratory CTE
2. The last CTE returned EMPTY results unexpectedly → Generate an exploratory CTE to find the correct format/column
3. You have separate CTEs for different parts of a compound question but NO merging CTE yet → Generate a merging CTE that JOINs all separate CTEs
4. The last CTE only contains PARTIAL information → Continue generating CTEs until ALL information is gathered and merged
5. Execution results indicate string literal mismatch or wrong column selection → Generate an exploratory CTE

**Example - When NOT to output <END>:**
- Question: "Which bond type is majority AND is it carcinogenic?"
- You have: `majority_bond_type` CTE (successful) and `carcinogenic_status` CTE (returned empty result)
- **WRONG**: Outputting <END> because you have majority_bond_type
- **CORRECT**: Generate a new exploratory CTE to fix the carcinogenic_status query (check correct column name, format, etc.), then generate a merging CTE, then output <END>

**Output Format**:

1. **If ALL conditions for <END> are met** → Output:
```sql
<END>
```

2. **If ANY condition requires continuation** → Generate a new CTE:
   - **Normal case**: Generate a single new CTE:
   ```sql
   WITH new_cte_name AS (
       SELECT ...
       FROM ...
       ...
   )
   ```
   - **Special case - CTE column reference error**: If the error indicates a column was referenced that doesn't exist in preceding CTEs, you MUST regenerate the COMPLETE CTE chain from Step 0:
   ```sql
   WITH cte1 AS (
       SELECT ...
       FROM ...
   ),
   cte2 AS (
       SELECT ...
       FROM cte1
       ...
   ),
   ...
   ```

**Generation Rules**:
- Priority: Execution Results > Evidence. Execution Results are FACTS - use exact values, formats, column names. Evidence/Additional Context are hints - verify against execution results first
- Do not add any SELECT statements
- You can reference preceding CTE names. I will add them in the executor, you don't need to repeat them.
- You MUST use a NEW, UNUSED CTE name for each CTE you generate.
- Must use the complete column names provided in the schema. Column names containing spaces, parentheses, or other special characters must be wrapped in backticks
- **CRITICAL: Do NOT use table aliases for CTEs.** When referencing a CTE in FROM clause, use the CTE name directly without aliasing (e.g., `FROM cte1` NOT `FROM cte1 c`). Only use column names directly from the CTE (e.g., `SELECT column_name FROM cte1` NOT `SELECT c.column_name FROM cte1 c`).
- **CRITICAL: NEVER output <END> if the last CTE failed or returned empty results.** You MUST generate a new exploratory/repair CTE to fix the issue.
- **CRITICAL: For CTE column reference errors** (when a column doesn't exist in preceding CTEs), you MUST regenerate the COMPLETE CTE chain from Step 0, ensuring all columns are properly propagated. This is different from generating a single new CTE.
- **CRITICAL: For compound questions requiring multiple pieces of information, you MUST generate a final merging CTE that combines all required information before outputting <END>.** If the question asks for multiple things (e.g., "What is X AND what is Y?"), and you have separate CTEs for each part, you MUST create a final CTE that JOINs all the separate CTEs together to produce a single result set containing all required information.
- **CRITICAL: If execution results show "String Literal Mismatch" or "Wrong Column Selection" or "returned empty result set", you MUST generate an exploratory CTE to investigate and fix the issue. DO NOT output <END> in this case.**

**Database Admin Instructions (Must Strictly Adhere):**
1.  **SELECT Clause:** Only select columns explicitly mentioned in the question. Avoid unnecessary columns or values. When performing JOINs, you MUST retain all columns that will be needed in subsequent CTE steps. Do NOT drop columns that are required for later operations.
2.  **FROM Table Selection:** If filtering/ordering columns come from a specific table, use that table as the FROM driving table. This ensures the filtering/ordering conditions can be applied correctly.
3.  **Aggregation (MAX/MIN):** Always perform JOINs before using `MAX()` or `MIN()`.
4.  **ORDER BY with Distinct Values:** Use `GROUP BY <column>` before `ORDER BY <column> ASC|DESC` to ensure distinct values.
5.  **Handling NULLs:** If a column may contain NULL values (indicated by "None" in value examples or explicitly stated), use `JOIN` or `WHERE <column> IS NOT NULL`.
6.  **FROM/JOIN Clauses:** Only include tables essential to answer the question.
7.  **Strictly Follow Hints:** Adhere to all provided hints.
8.  **Thorough Question Analysis:** Address all conditions mentioned in the question.
9.  **DISTINCT Keyword:** Use `SELECT DISTINCT` when the question requires unique values or when selecting columns that may have duplicates (e.g., IDs, URLs, names, nationalities, categories). If the question asks for a list of unique items or the result may contain duplicate values, always use `SELECT DISTINCT`. Refer to column statistics ("Value Statics") to determine if `DISTINCT` is necessary. When in doubt, use `DISTINCT` to ensure unique results.
10. **COUNT with DISTINCT:** When using `COUNT()` after JOINs, especially with N:1 or M:N relationships, you MUST use `COUNT(DISTINCT column)` instead of `COUNT(*)` to avoid counting duplicate rows. If the relationship is N:1 (Many-to-One), the child table may have multiple rows per parent, so always use `COUNT(DISTINCT column)` when counting entities from the parent table. **Check the table relationships provided in the prompt - if you see N:1 relationships and you're counting entities from the parent table, you MUST use `COUNT(DISTINCT column)` where the column is the unique identifier from the parent table.**
11. **Column Selection:** When similar columns exist across tables, carefully analyze column descriptions and hints to choose the correct column.
12. **String Concatenation:** Never use `|| ' ' ||` or any other method to concatenate strings in the `SELECT` clause.
13. **JOIN Preference:** Prioritize `INNER JOIN` over nested `SELECT` statements.
14. **SQLite Functions Only:** Use only functions available in SQLite (unless fuzzy matching extensions are available).
15. **Date Processing:** Utilize `STRFTIME()` for date manipulation (e.g., `STRFTIME('%Y', SOMETIME)` to extract the year).

/no_think
"""

    def _extract_cte_from_response(self, response) -> str:
        """
        从响应中提取CTE代码，并自动添加SELECT语句
        
        LLM只生成: WITH xxx AS (...)
        系统自动添加: SELECT * FROM xxx;
        
        改进：使用平衡括号算法提取完整的CTE，避免提取不完整的CTE
        """
        # 处理autogen返回的不同类型
        if isinstance(response, dict):
            response = response.get('content', '') or str(response)
        elif not isinstance(response, str):
            response = str(response)
        
        # 首先检查是否包含<END>标记
        if "<END>" in response:
            print(f"[CTE提取] 检测到<END>标记")
            return "<END>"
        
        # 尝试从代码块中提取
        code_block_match = re.search(r'```sql\s*(.*?)\s*```', response, re.DOTALL)
        if code_block_match:
            raw_text = code_block_match.group(1).strip()
        
        else:
            # 如果没有代码块，使用整个响应文本
            raw_text = response.strip()
          
        
        if not raw_text or 'WITH' not in raw_text.upper():

            return ""
        
        # 清理：移除末尾的分号
        raw_text = raw_text.rstrip(';').strip()
        
        # 使用平衡括号算法提取完整的CTE
        # 找到 WITH name AS ( 的位置
        with_match = re.search(r'WITH\s+(\w+)\s+AS\s*\(', raw_text, re.IGNORECASE)
        if not with_match:
            print(f"[CTE提取] ⚠️ 未找到WITH name AS (模式")
            return ""
        
        cte_name = with_match.group(1)
        print(f"[CTE提取] 找到CTE名称: {cte_name}")
        paren_start = with_match.end() - 1  # AS 后面的 ( 的位置
        
        # 使用平衡括号算法找到匹配的右括号（考虑字符串中的括号）
        # SQL中字符串转义通常使用 ''（两个单引号）而不是 \'
        paren_count = 0
        in_string = False
        string_char = None  # 记录字符串的引号类型（' 或 "）
        
        i = paren_start
        while i < len(raw_text):
            char = raw_text[i]
            
            # 处理字符串边界（SQL中单引号字符串用 '' 转义，双引号用 "" 转义）
            if char in ("'", '"'):
                if not in_string:
                    in_string = True
                    string_char = char
                elif char == string_char:
                    # 检查是否是转义的引号（两个连续的引号）
                    if i + 1 < len(raw_text) and raw_text[i + 1] == string_char:
                        i += 1  # 跳过转义的引号
                    else:
                        in_string = False
                        string_char = None
            
            # 只在非字符串状态下计算括号
            if not in_string:
                if char == '(':
                    paren_count += 1
                elif char == ')':
                    paren_count -= 1
                    if paren_count == 0:
                        # 找到了匹配的右括号，提取完整的CTE
                        cte_text = raw_text[:i + 1].strip()
                        break
            i += 1
        else:
            # 如果循环结束还没找到匹配的右括号，说明CTE不完整
            print(f"⚠️ _extract_cte_from_response: CTE括号不匹配，拒绝不完整的CTE")
            print(f"   不完整的CTE内容:\n{raw_text[:500]}...")
            print(f"   括号计数: {paren_count} (未闭合)")
            return ""  # 拒绝不完整的CTE
        
        # 如果LLM生成了SELECT，移除它（在右括号之后的部分）
        # 检查右括号后是否有SELECT
        after_paren = raw_text[i + 1:].strip()
        if after_paren.upper().startswith('SELECT'):
            # SELECT部分已经被排除在cte_text之外，无需处理
            pass
        
        # 系统自动添加SELECT语句
        full_cte = f"{cte_text}\nSELECT * FROM {cte_name};"

        return full_cte
    
    def _extract_complete_cte_chain(self, response) -> str:
        """
        从响应中提取完整的CTE链，保留为一个整体（不拆分）
        
        当LLM返回完整的CTE链时（如 WITH cte1 AS (...), cte2 AS (...), cte3 AS (...)），
        保留整个链作为一个整体，供sql_executor直接使用（不和历史CTE拼接）
        
        Args:
            response: LLM响应内容
            
        Returns:
            完整的CTE链字符串，如果提取失败则返回空字符串
        """
        # 处理autogen返回的不同类型
        if isinstance(response, dict):
            response = response.get('content', '') or str(response)
        elif not isinstance(response, str):
            response = str(response)
        
        # 首先检查是否包含<END>标记
        if "<END>" in response:
            return "<END>"
        
        # 尝试从代码块中提取
        code_block_match = re.search(r'```sql\s*(.*?)\s*```', response, re.DOTALL)
        if code_block_match:
            raw_text = code_block_match.group(1).strip()
        else:
            # 如果没有代码块，使用整个响应文本
            raw_text = response.strip()
        
        if not raw_text or 'WITH' not in raw_text.upper():
            return ""
        
        # 查找第一个WITH关键字的位置
        first_with_match = re.search(r'WITH\s+', raw_text, re.IGNORECASE)
        if not first_with_match:
            return ""
        
        # 从第一个WITH开始提取
        cte_chain_text = raw_text[first_with_match.start():]
        
        # 检查是否已经有SELECT语句
        if re.search(r'\bSELECT\s+.*?\s+FROM\s+\w+', cte_chain_text, re.IGNORECASE | re.DOTALL):
            # 已经有SELECT语句，直接返回
            return cte_chain_text.strip()
        
        # 没有SELECT语句，需要找到最后一个CTE的名称并添加SELECT
        # 找到所有CTE名称
        cte_names = re.findall(r'\b(\w+)\s+AS\s*\(', cte_chain_text, re.IGNORECASE)
        if cte_names:
            last_cte_name = cte_names[-1]
            return f"{cte_chain_text.rstrip(';').strip()}\nSELECT * FROM {last_cte_name};"
        
        return cte_chain_text.strip()
    
    def _extract_and_split_cte_chain(self, response) -> List[str]:
        """
        从响应中提取完整的CTE链，并拆分为多个单独的CTE
        
        当LLM返回完整的CTE链时（如 WITH cte1 AS (...), cte2 AS (...), cte3 AS (...)），
        需要拆分成多个单独的CTE，每个CTE单独执行，保持格式统一。
        
        Args:
            response: LLM响应内容
            
        Returns:
            CTE列表，每个CTE都是独立的（包含WITH ... AS (...) SELECT * FROM ...）
        """
        # 处理autogen返回的不同类型
        if isinstance(response, dict):
            response = response.get('content', '') or str(response)
        elif not isinstance(response, str):
            response = str(response)
        
        # 首先检查是否包含<END>标记
        if "<END>" in response:
            print(f"[CTE链提取] 检测到<END>标记")
            return ["<END>"]
        
        # 尝试从代码块中提取
        code_block_match = re.search(r'```sql\s*(.*?)\s*```', response, re.DOTALL)
        if code_block_match:
            raw_text = code_block_match.group(1).strip()
            print(f"[CTE链提取] 从SQL代码块中提取，代码块长度: {len(raw_text)} 字符")
        else:
            # 如果没有代码块，使用整个响应文本
            raw_text = response.strip()
            print(f"[CTE链提取] 未找到SQL代码块，使用整个响应文本，长度: {len(raw_text)} 字符")
        
        if not raw_text or 'WITH' not in raw_text.upper():
            print(f"[CTE链提取] ⚠️ 未找到WITH关键字或文本为空")
            return []
        
        # 清理：移除末尾的分号
        raw_text = raw_text.rstrip(';').strip()
        
        # 查找第一个WITH关键字的位置
        first_with_match = re.search(r'WITH\s+', raw_text, re.IGNORECASE)
        if not first_with_match:
            return []
        
        # 从第一个WITH开始提取
        cte_chain_text = raw_text[first_with_match.start():]
        
        # 查找所有CTE定义：name AS (...)
        # 第一个CTE有WITH关键字，后续CTE用逗号分隔
        cte_pattern = r'(?:WITH\s+)?(\w+)\s+AS\s*\('
        cte_matches = list(re.finditer(cte_pattern, cte_chain_text, re.IGNORECASE))
        
        if not cte_matches:
            print(f"[CTE链提取] ⚠️ 未找到任何CTE定义")
            return []
        
        print(f"[CTE链提取] 找到 {len(cte_matches)} 个CTE定义")
        cte_list = []
        
        # 提取每个CTE
        for i, match in enumerate(cte_matches):
            cte_name = match.group(1)
            paren_start = match.end() - 1  # AS 后面的 ( 的位置
            
            # 使用平衡括号算法找到匹配的右括号
            paren_count = 0
            in_string = False
            string_char = None
            
            paren_end = None
            # 从当前CTE的括号开始，找到匹配的右括号
            for j in range(paren_start, len(cte_chain_text)):
                char = cte_chain_text[j]
                
                # 处理字符串边界
                if char in ("'", '"'):
                    if not in_string:
                        in_string = True
                        string_char = char
                    elif char == string_char:
                        # 检查是否是转义的引号（SQL中单引号用 '' 转义，双引号用 "" 转义）
                        if j + 1 < len(cte_chain_text) and cte_chain_text[j + 1] == string_char:
                            j += 1  # 跳过转义的引号
                        else:
                            in_string = False
                            string_char = None
                
                # 只在非字符串状态下计算括号
                if not in_string:
                    if char == '(':
                        paren_count += 1
                    elif char == ')':
                        paren_count -= 1
                        if paren_count == 0:
                            paren_end = j
                            break
            
            if paren_end is None:
                # 括号不匹配，跳过这个CTE
                print(f"⚠️ _extract_and_split_cte_chain: CTE {cte_name} 括号不匹配，跳过")
                continue
            
            # 提取完整的CTE定义
            # 第一个CTE包含WITH关键字，后续CTE不包含
            if i == 0:
                # 第一个CTE：包含WITH关键字
                cte_text = cte_chain_text[match.start():paren_end + 1].strip()
            else:
                # 后续CTE：不包含WITH关键字，需要添加
                cte_def = cte_chain_text[match.start():paren_end + 1].strip()
                # 移除可能的逗号前缀
                cte_def = re.sub(r'^,\s*', '', cte_def)
                cte_text = f"WITH {cte_def}"
            
            # 系统自动添加SELECT语句
            full_cte = f"{cte_text}\nSELECT * FROM {cte_name};"
            print(f"[CTE链提取] CTE #{i+1} ({cte_name}): CTE文本长度 {len(cte_text)} 字符，完整CTE长度 {len(full_cte)} 字符")
            cte_list.append(full_cte)
        
        print(f"[CTE链提取] 成功提取 {len(cte_list)} 个CTE")
        return cte_list
    
    def _remove_foreign_key(self, schema_info: str) -> str:
        """
        移除schema_info中的foreign_key部分
        
        Args:
            schema_info: 包含foreign_key的schema信息
            
        Returns:
            移除foreign_key后的schema信息
        """
        try:
            if 'foreign_key:' not in schema_info:
                return schema_info
            
            # 分离表定义和外键定义
            schema_part, _ = schema_info.split('foreign_key:', 1)
            # 移除末尾可能的空行和单独的 #
            lines = schema_part.rstrip().split('\n')
            # 移除末尾的空行和单独的 #
            while lines and (not lines[-1].strip() or lines[-1].strip() == '#'):
                lines.pop()
            return '\n'.join(lines)
        except Exception as e:
            print(f"⚠️  移除foreign_key失败: {e}，使用原始schema")
            return schema_info
    
    def _find_relevant_values(self, values: List[Any], question: str, top_n: int = 3) -> List[Any]:
        """
        找到与自然语言问题最相关的数据值
        
        Args:
            values: 数据值列表
            question: 自然语言问题
            top_n: 返回的top值数量
            
        Returns:
            最相关的top_n个值
        """
        if not values or not question:
            return values[:top_n]
        
        # 去重
        unique_values = list(set(values))
        
        # 限制计算数量，避免太慢
        if len(unique_values) > 100:
            import random
            unique_values = random.sample(unique_values, 100)
        
        # 计算每个值与问题的相似度
        similarities = []
        question_lower = question.lower()
        
        for value in unique_values:
            # 转换为字符串
            value_str = str(value).lower()
            
            # 计算相似度分数（多个维度）
            score = 0.0
            
            # 1. 子串匹配（最重要）
            if value_str in question_lower or question_lower in value_str:
                score += 100.0
            
            # 2. 单词级别匹配
            value_words = set(value_str.split())
            question_words = set(question_lower.split())
            common_words = value_words & question_words
            if common_words:
                score += len(common_words) * 50.0
            
            # 3. Levenshtein距离（相似度）
            # 对较短的字符串计算编辑距离
            if len(value_str) < 50 and len(question_lower) < 100:
                # 计算最小编辑距离（与问题中的每个单词）
                min_distance = min([Levenshtein.distance(value_str, word) for word in question_words] + [999])
                # 距离越小，分数越高
                if min_distance < 10:
                    score += (10 - min_distance) * 2.0
            
            similarities.append((value, score))
        
        # 按分数降序排序
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        # 返回top_n个值
        return [value for value, score in similarities[:top_n]]
    
    def _get_used_cte_names(self, node) -> List[str]:
        """
        获取已使用的CTE名称列表
        
        Args:
            node: 当前MCTS节点
            
        Returns:
            已使用的CTE名称列表
        """
        used_names = []
        current = node
        
        while current is not None:
            if current.cte and current.cte != "" and current.cte != "<END>":
                # 提取CTE名称
                match = re.search(r'WITH\s+(\w+)\s+AS', current.cte, re.IGNORECASE)
                if match:
                    cte_name = match.group(1)
                    if cte_name not in used_names:
                        used_names.append(cte_name)
            current = current.parent
        
        return used_names
    
    def _has_where_clause(self, cte: str) -> bool:
        """
        检查CTE中是否包含WHERE子句
        
        Args:
            cte: CTE文本
            
        Returns:
            如果包含WHERE子句返回True，否则返回False
        """
        if not cte or cte == "<END>":
            return False
        
        # 提取CTE定义部分（去除WITH和CTE名称）
        match = re.search(r'WITH\s+\w+\s+AS\s*\((.*?)\)', cte, re.DOTALL | re.IGNORECASE)
        if match:
            select_part = match.group(1).strip()
        else:
            # 如果没有WITH，尝试直接提取SELECT
            select_part = cte
        
        # 检查是否包含WHERE关键字（需要排除字符串中的WHERE）
        where_pattern = r'\bWHERE\b'
        # 检查是否有WHERE关键字（忽略大小写）
        if re.search(where_pattern, select_part, re.IGNORECASE):
            # 进一步验证：确保WHERE后面有内容（不是空WHERE）
            where_match = re.search(r'\bWHERE\s+', select_part, re.IGNORECASE)
            if where_match:
                # 找到WHERE后的内容，检查是否有实际条件
                after_where = select_part[where_match.end():].strip()
                # 移除可能的注释和空白
                after_where = re.sub(r'--.*$', '', after_where, flags=re.MULTILINE)  # 移除单行注释
                after_where = re.sub(r'/\*.*?\*/', '', after_where, flags=re.DOTALL)  # 移除多行注释
                after_where = after_where.strip()
                # 如果WHERE后有内容（不是空），返回True
                if after_where and not after_where.startswith(';') and not after_where.startswith(')'):
                    return True
        return False
    
    def _get_fuzzy_match_hint(self, is_second_empty: bool = False) -> str:
        """
        生成模糊匹配提示信息（简洁版）
        
        Args:
            is_second_empty: 是否是第二次空结果
            
        Returns:
            模糊匹配提示字符串
        """
        # 如果是第二次空结果，添加检查其他列的提醒
        other_column_hint = ""
        if is_second_empty:
            other_column_hint = """
### [CHECK OTHER COLUMNS]
**Empty results after fuzzy matching twice** suggests you may be querying the **wrong column**.

1. **Review the question/evidence**: Check for explicit or implied column names.
2. **Check the schema**: Look for matching columns like IDs, names, or codes.
3. **Try a DIFFERENT column** with relevant semantics (e.g., name/ID, code/ID).
"""
        
        hint_text = f"""
This likely indicates a **String Literal Mismatch** OR **Wrong Column Selection**. The format might differ, or the wrong column is being queried.

{other_column_hint}

### [YOUR TASK]

Create a **Exploratory CTE with new name** to find the correct format or column.

1. **Identify Targets**: Check string literals used in the failed query.
2. **Generate Variations**: Use distinct `LIKE` patterns for each string.
3. **Cross-Column Check**: Include fuzzy checks for all relevant columns.
"""
        return hint_text
    
    def _get_preceding_cte_info(self, node) -> str:
        """
        通过node向上追溯，获取所有前序CTE及其执行结果（包括当前节点）
        
        Args:
            node: 当前MCTS节点（要扩展的节点）
            
        Returns:
            格式化的前序CTE信息字符串
        """
        # 收集从根节点到当前节点的所有CTE（包括当前节点自己的CTE）
        # 按顺序收集，不处理同名替换（不允许重用CTE名称）
        cte_path = []
        seen_cte_names = set()  # 用于检测重复的CTE名称
        current = node  # 从node本身开始
        
        # 从根节点到当前节点，按顺序收集CTE
        root_to_current = []
        temp = node
        while temp is not None:
            root_to_current.insert(0, temp)
            temp = temp.parent
        
        # 按顺序收集CTE（只收集第一次出现的CTE名称，忽略重复的）
        for n in root_to_current:
            if n.cte and n.cte != "" and n.cte != "<END>":
                # 提取CTE名称
                match = re.search(r'WITH\s+(\w+)\s+AS', n.cte, re.IGNORECASE)
                if match:
                    cte_name = match.group(1)
                    # 只添加第一次出现的CTE（如果遇到重复名称，跳过）
                    if cte_name not in seen_cte_names:
                        cte_path.append({
                            'cte': n.cte,
                            'execution_result': n.execution_results.get('cte_result', {})
                        })
                        seen_cte_names.add(cte_name)
        
        # 如果没有前序CTE，返回提示信息
        if not cte_path:
            return "No preceding CTE"
        
        # 获取问题文本（用于相似度计算）
        question = node.question
        
        # 格式化前序CTE信息
        formatted_info = []
        # 检查最后一个（最近的）前序CTE是否返回空结果，用于决定是否提示模糊匹配
        # 只有当CTE中有WHERE子句且结果为空时，才提示模糊匹配
        last_cte_is_empty = False
        last_cte_has_where = False
        is_second_empty = False  # 是否是第二次空结果
        if cte_path:
            last_cte_info = cte_path[-1]
            last_cte_text = last_cte_info['cte']
            last_exec_result = last_cte_info['execution_result']
            if last_exec_result.get('valid', False):
                last_query_result = last_exec_result.get('query_result', [])
                if not last_query_result or len(last_query_result) == 0:
                    # 检查是否有WHERE子句
                    last_cte_has_where = self._has_where_clause(last_cte_text)
                    # 只有当有WHERE子句且结果为空时，才标记为空结果（用于提示模糊匹配）
                    last_cte_is_empty = last_cte_has_where
                    
                    # 检查是否是第二次空结果：通过检查node的consecutive_empty_count或父节点是否也是空结果
                    if last_cte_is_empty:
                        # 检查当前节点是否是第二次空结果（consecutive_empty_count == 2）
                        if hasattr(node, 'consecutive_empty_count') and node.consecutive_empty_count >= 2:
                            is_second_empty = True
                        elif hasattr(node, 'parent') and node.parent:
                            # 检查父节点是否也是空结果节点
                            parent_exec_results = getattr(node.parent, 'execution_results', {})
                            if parent_exec_results.get('is_empty_result', False):
                                is_second_empty = True
        
        for idx, cte_info in enumerate(cte_path, 1):
            formatted_info.append(f"### Step {idx}:")
            formatted_info.append(f"```sql\n{cte_info['cte']}\n```")
            
            # 添加执行结果信息
            exec_result = cte_info['execution_result']
            
            # 添加关系检查结果（如果存在）
            relationship_check = exec_result.get('relationship_check')
            if relationship_check:
                is_valid = relationship_check.get('is_valid', True)
                feedback = relationship_check.get('feedback', '')
                error_type = relationship_check.get('error_type', None)
                
                if is_valid:
                    # 通过时：保持简洁，作为正向确认
                    formatted_info.append(f"**[Verification Passed]**: Relationship logic consistency check passed.")
                else:
                    # 失败时：使用大写和方括号强调错误严重性
                    formatted_info.append(f"**[CRITICAL LOGIC ERROR] Type: {error_type or 'Unknown'}**")
                    formatted_info.append(f"--------------------------------------------------")
                    formatted_info.append(f"**Diagnosis**: {feedback}")
                # 如果有警告但通过检查
                if is_valid and feedback and feedback != "Pass" and "warnings" in feedback.lower():
                    formatted_info.append(f"**Note**: {feedback}")
            
            if exec_result.get('valid', False):
                query_result = exec_result.get('query_result', [])
                # 确保query_result是列表格式（可能需要转换）
                try:
                    query_result = MCTSUtils.safe_to_dict(query_result)
                except Exception:
                    pass
                if not isinstance(query_result, list):
                    try:
                        query_result = list(query_result)
                    except Exception:
                        query_result = []
                if query_result:
                    # 限制只展示前20行数据
                    total_rows = len(query_result)
                    query_result_limited = query_result[:20]
                    formatted_info.append(f"**Execution Result**: Successfully returned {total_rows} rows")
                    # 只保留与问题相关的示例数据值，不再打印"结果列"和"实际数据行"
                    if len(query_result_limited) > 0:
                        columns = list(query_result_limited[0].keys())
                        # 显示与问题相关的示例数据值（每列的唯一值样本）
                        relevant_sample_values = []
                        for col in columns:
                            # 收集该列的所有值（仅从前20行）
                            col_values = [row[col] for row in query_result_limited if row.get(col) is not None]
                            if col_values:
                                # 获取唯一值（最多10个）
                                unique_values = []
                                seen = set()
                                for v in col_values:
                                    v_str = str(v)
                                    if v_str not in seen:
                                        seen.add(v_str)
                                        unique_values.append(v)
                                        if len(unique_values) >= 10:
                                            break
                                
                                if unique_values:
                                    # 判断列的数据类型：如果是数字类型，直接排序；如果是字符串，使用相似度
                                    is_numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in unique_values)
                                    
                                    if is_numeric:
                                        # 数字类型：按顺序排序，取前10个
                                        sorted_values = sorted(unique_values)[:10]
                                        selected_values = sorted_values
                                    else:
                                        # 字符串类型：使用相似度计算选择最相关的值
                                        selected_values = self._find_relevant_values(unique_values, question, top_n=10)
                                    
                                    # 格式化值（字符串加引号，数字不加）
                                    formatted_values = []
                                    for v in selected_values:
                                        if isinstance(v, str):
                                            formatted_values.append(f"'{v}'")
                                        else:
                                            formatted_values.append(str(v))
                                    # 列名也使用引号包裹，避免包含空格时产生歧义
                                    relevant_sample_values.append(f"      '{col}': {', '.join(formatted_values)}")
                        
                        if relevant_sample_values:
                            formatted_info.append("    **Relevant Sample Values**:")
                            formatted_info.extend(relevant_sample_values)
                else:
                    formatted_info.append("**Execution Result**: Successfully executed, returned empty result set")
                    # 只在最后一个（最近的）前序CTE有WHERE子句且返回空结果时，提示使用模糊匹配
                    if idx == len(cte_path) and last_cte_is_empty and last_cte_has_where:
                        # 使用之前已经检查好的 is_second_empty 变量
                        if is_second_empty:
                            # 第二次空结果：重复提示使用模糊匹配（避免检查其他列导致SQL超时）
                            print("Preceding CTE has WHERE clause but returned empty result for the second time. Repeating hint to use fuzzy matching to create new CTE (with new table name).")
                            formatted_info.append(self._get_fuzzy_match_hint(is_second_empty=True))
                        else:
                            # 第一次空结果：提示使用模糊匹配
                            formatted_info.append(self._get_fuzzy_match_hint(is_second_empty=False))
            else:
                # 执行失败
                error_msg = exec_result.get('error', 'Execution failed')
                formatted_info.append(f"**Execution Result**: Execution failed")
                formatted_info.append(f"**Error**: {error_msg}")
        
        return "\n".join(formatted_info)
    
    def _extract_db_name_from_schema(self, schema_info: str) -> Optional[str]:
        """从schema_info中提取数据库名称"""
        if not schema_info:
            return None
        # schema_info格式通常是: "db_name:california_schools\n# ..."
        match = re.search(r'db_name:\s*(\w+)', schema_info, re.IGNORECASE)
        if match:
            return match.group(1)
        return None
    
    def _get_relationships_info(self, schema_info: str) -> str:
        """获取并格式化关系信息"""
        db_name = self._extract_db_name_from_schema(schema_info)
        if not db_name or db_name not in self.relationships_data:
            return ""
        
        relationships = self.relationships_data[db_name].get('relationships', [])
        if not relationships:
            return ""
        
        # 格式化关系信息
        lines = ["* **Table Relationships**: "]
        for rel in relationships:
            table1 = rel.get('table1', '')
            col1 = rel.get('col1', '')
            table2 = rel.get('table2', '')
            col2 = rel.get('col2', '')
            rel_type = rel.get('relationship_type', '')
            description = rel.get('description', '')
            
            lines.append(f"  - {table1}.{col1} <-> {table2}.{col2}: {rel_type} - {description}")
        
        return "\n".join(lines)
    
    def _enhance_foreign_key_with_relationship_types(self, schema_info: str) -> str:
        """
        增强 foreign_key 信息，在每行后面添加关系类型（1:1, N:1, M:N等）
        
        Args:
            schema_info: 包含 foreign_key 的 schema 信息
            
        Returns:
            增强后的 schema 信息
        """
        if 'foreign_key:' not in schema_info:
            return schema_info
        
        db_name = self._extract_db_name_from_schema(schema_info)
        if not db_name or db_name not in self.relationships_data:
            # 如果没有关系数据，返回原始信息
            return schema_info
        
        relationships = self.relationships_data[db_name].get('relationships', [])
        if not relationships:
            return schema_info
        
        # 创建关系查找字典：{(table1, col1, table2, col2): relationship_type}
        rel_dict = {}
        for rel in relationships:
            table1 = rel.get('table1', '').lower()
            col1 = rel.get('col1', '').lower()
            table2 = rel.get('table2', '').lower()
            col2 = rel.get('col2', '').lower()
            rel_type = rel.get('relationship_type', '')
            
            # 存储两个方向的关系
            rel_dict[(table1, col1, table2, col2)] = rel_type
            rel_dict[(table2, col2, table1, col1)] = rel_type
        
        # 分离 foreign_key 部分
        parts = schema_info.split('foreign_key:', 1)
        if len(parts) != 2:
            return schema_info
        
        schema_part = parts[0]
        fk_part = parts[1]
        
        # 解析并增强每一行 foreign_key
        import re
        enhanced_lines = []
        for line in fk_part.split('\n'):
            original_line = line
            line = line.strip()
            
            # 匹配格式: # table1(col1) references table2(col2)
            match = re.match(r'#\s*(\w+)\(([^)]+)\)\s+references\s+(\w+)\(([^)]+)\)', line, re.IGNORECASE)
            if match:
                table1, col1, table2, col2 = match.groups()
                # 查找关系类型
                key = (table1.lower(), col1.lower(), table2.lower(), col2.lower())
                rel_type = rel_dict.get(key, '')
                
                if rel_type:
                    # 添加关系类型信息
                    enhanced_lines.append(f"{line} ({rel_type})")
                else:
                    enhanced_lines.append(original_line)
            else:
                # 保留原始行（包括空行和注释行）
                enhanced_lines.append(original_line)
        
        # 重新组合
        enhanced_fk = '\n'.join(enhanced_lines)
        return f"{schema_part}foreign_key:{enhanced_fk}"
    
    def _extract_strategy_from_context(self, additional_context: str) -> Tuple[str, str]:
        """
        从additional_context中分离strategy_text和剩余的additional_context
        
        Args:
            additional_context: 包含strategy_text和原始additional_context的字符串
            
        Returns:
            (strategy_text, remaining_additional_context) 元组
        """
        if not additional_context:
            return "", ""
        
        # 识别strategy_text的标记：
        # 1. "[GLOBAL STRATEGY MODE:" - FORCE模式
        # 2. "Expression Types:" - CTE_ACTION_level的开始
        # 3. "S1 Entity-First:" / "S2 Relation-First:" 等 - 策略描述的开始
        
        strategy_markers = [
            "[GLOBAL STRATEGY MODE:",
            "Expression Types:",
            "S1 Entity-First:",
            "S2 Relation-First:",
            "S3 Evidence-Based:"
        ]
        
        # 查找第一个strategy标记的位置
        first_marker_pos = len(additional_context)
        first_marker = None
        for marker in strategy_markers:
            pos = additional_context.find(marker)
            if pos != -1 and pos < first_marker_pos:
                first_marker_pos = pos
                first_marker = marker
        
        # 如果没有找到strategy标记，说明没有strategy_text
        if first_marker_pos == len(additional_context):
            return "", additional_context
        
        # 分离strategy_text和remaining_additional_context
        # strategy_text从第一个标记开始，到additional_context的末尾
        # 但需要处理可能的多个部分（strategy_text可能在中间，前后都有其他内容）
        
        # 尝试找到strategy_text的结束位置
        # strategy_text通常以CTE_ACTION_level的示例结束，或者以空行+其他内容结束
        # 简单处理：从第一个标记开始，到下一个明显的分隔（双换行+非strategy内容）或文件末尾
        
        # 更简单的方法：从第一个标记开始，提取到additional_context末尾
        # 因为strategy_text通常是在additional_context的后面添加的
        strategy_text = additional_context[first_marker_pos:].strip()
        remaining_additional_context = additional_context[:first_marker_pos].strip()
        
        # 如果remaining_additional_context为空，返回空字符串
        if not remaining_additional_context:
            remaining_additional_context = ""
        
        return strategy_text, remaining_additional_context
    
    def _should_monitor_cte(self, question: str) -> bool:
        """
        判断是否需要监控此问题的CTE生成过程
        
        Args:
            question: 自然语言问题
            
        Returns:
            是否需要监控（打印详细信息）
        """
        # 为了调试目的，暂时对所有问题启用监控
        return True
    
    def generate_multiple_cte_variants(self, node, num_variants: int = 3, failed_attempts: List[str] = None) -> List[str]:
        """
        生成多个CTE变体（并行生成，使用分组temperature策略）
        
        将变体分成多个temperature组 [0.0, 0.3, 0.6, 0.9]，每组内并行调用
        
        Args:
            node: MCTS节点
            num_variants: 变体数量
            failed_attempts: 之前失败的CTE尝试列表（用于重试时提示）
            
        Returns:
            CTE变体列表
        """
        # 判断是否需要监控此问题
        should_monitor = self._should_monitor_cte(node.question)
        
        # 通过node.parent向上追溯获取前序CTE信息
        preceding_cte_info = self._get_preceding_cte_info(node)
        
        # 检查前序CTE是否执行失败，如果失败则直接剪枝
        # 但是，如果当前节点是失败节点（is_failed=True），允许继续生成（因为失败节点的目的就是保存错误信息并继续探索）
        is_failed_node = node.execution_results.get('is_failed', False)
        if preceding_cte_info and "Execution failed" in preceding_cte_info and not is_failed_node:
            if should_monitor:
                print("⚠️ 前序CTE执行失败，跳过此路径生成")
            return []
        
        # 获取已使用的CTE名称
        used_cte_names = self._get_used_cte_names(node)
        
        # 格式化已使用的CTE名称提示
        if used_cte_names:
            used_names_str = f"* **Used CTE Names**: {', '.join(used_cte_names)}\n"
        else:
            used_names_str = "* **Used CTE Names**: None"
        
        # 使用多个temperature值增加多样性
        # 将变体分成多个temperature组：[0.3, 0.6, 0.9]
        temperature_groups = [0.3, 0.6, 0.9]
        num_groups = len(temperature_groups)
        
        # 计算每组应该生成多少个变体
        variants_per_group = num_variants // num_groups
        remainder = num_variants % num_groups
        
        # 获取当前深度和剩余深度
        # current_depth从0开始，current_step = current_depth + 1
        # remaining_steps = max_depth - current_step = max_depth - (current_depth + 1)
        current_depth = node.depth
        remaining_steps = max(0, self.max_depth - current_depth - 1)
        
        # 构建失败尝试提示（如果有）
        failed_attempts_section = ""
        column_hints = []  # 收集所有列名到表的映射提示
        requires_full_cte_chain = False  # 是否需要重新生成完整CTE链
        
        if failed_attempts and len(failed_attempts) > 0:
            # 处理失败信息：可能是字符串（旧格式）或字典（新格式，包含错误信息）
            failed_items = []
            attempt_count = 0
            duplicate_cte_names = []  # 收集重复的CTE名称
            for item in failed_attempts[:5]:  # 最多显示5个
                if isinstance(item, dict):
                    # 新格式：包含CTE和错误信息
                    cte = item.get('cte', '').strip()
                    error = item.get('error', 'Execution failed or timeout')
                    
                    # 收集重复的CTE名称
                    if item.get('duplicate_cte_name'):
                        duplicate_cte_names.append(item['duplicate_cte_name'])
                    
                    # 检查是否需要重新生成完整CTE链
                    if item.get('requires_full_cte_chain', False):
                        requires_full_cte_chain = True
                        print(f"[CTE生成] ✅ 检测到 requires_full_cte_chain=True，将重新生成完整CTE链")
                    
                    # 检查是否有列名到表的映射提示
                    column_hint = item.get('column_hint')
                    if column_hint:
                        column_hints.append(column_hint)
                    
                    attempt_count += 1
                    # 统一格式：编号 + CTE（如果有）+ 错误信息
                    if cte:
                        failed_items.append(f"**Failed Attempt #{attempt_count}:**\n```sql\n{cte}\n```\n**Error:** {error}")
                    else:
                        # CTE为空，只显示错误信息（可能是失败节点的汇总错误）
                        failed_items.append(f"**Failed Attempt #{attempt_count}:**\n**Error:** {error}")
                else:
                    # 旧格式：只有CTE文本
                    cte_text = str(item).strip()
                    attempt_count += 1
                    if cte_text:
                        failed_items.append(f"**Failed Attempt #{attempt_count}:**\n```sql\n{cte_text}\n```\n**Error:** Execution failed or timeout")
                    else:
                        failed_items.append(f"**Failed Attempt #{attempt_count}:**\n**Error:** Execution failed or timeout")
            
            if failed_items or duplicate_cte_names:
                failed_list = "\n\n".join(failed_items) if failed_items else ""
                
                # 如果有列名映射提示，单独放在一个部分
                column_hints_section = ""
                if column_hints:
                    # 去重列名提示
                    unique_hints = list(set(column_hints))
                    column_hints_section = f"\n\n**⚠️ Column Location Hints (CRITICAL):**\n" + "\n".join(f"- {hint}" for hint in unique_hints)
                
                # 如果有重复的CTE名称，添加警告
                duplicate_cte_warning = ""
                if duplicate_cte_names:
                    unique_dup_names = list(set(duplicate_cte_names))
                    duplicate_cte_warning = f"\n\n**⚠️ DUPLICATE CTE NAME ERROR (CRITICAL):**\n" \
                        f"The following CTE names already exist in the preceding CTEs and CANNOT be reused: **{', '.join(unique_dup_names)}**\n" \
                        f"You MUST use a DIFFERENT name for your new CTE (e.g., add suffix like `_v2`, `_new`, `_final`, or use a completely different descriptive name).\n"
                
                # 如果需要重新生成完整CTE链，添加特殊提示
                full_chain_instruction = ""
                if requires_full_cte_chain:
                    print(f"[CTE生成] ✅ 添加完整CTE链重新生成指令到prompt")
                    full_chain_instruction = """
**⚠️ CRITICAL INSTRUCTION - OVERRIDES GENERAL RULES ⚠️**

The error indicates that a column was referenced that doesn't exist in the preceding CTEs. This is a **CTE column reference error**.

**You MUST regenerate the COMPLETE CTE chain from Step 0 to the current step** (NOT just a single new CTE). This instruction OVERRIDES the general rule of "generating a new CTE" - in this case, you must regenerate the ENTIRE chain.

**Requirements:**
1. Generate ALL CTEs from the first step to the current step in a single response
2. Each CTE must select all columns that will be needed in subsequent CTEs
3. Do NOT use table aliases for CTEs (e.g., use `FROM cte1` NOT `FROM cte1 c`)
4. When referencing columns from CTEs, use the column name directly (e.g., `SELECT column_name FROM cte1` NOT `SELECT c.column_name FROM cte1 c`)
5. Ensure column names are correctly propagated through the entire CTE chain

**Output Format**: Generate the COMPLETE CTE chain starting from the first CTE:
```sql
WITH cte1 AS (
    SELECT ...
    FROM ...
),
cte2 AS (
    SELECT ...
    FROM cte1
    ...
),
cte3 AS (
    SELECT ...
    FROM cte2
    ...
),
...
```

**DO NOT output just a single new CTE. You MUST output the complete chain.**

"""
                
                failed_attempts_section = f"""
* **Previous Failed Attempts (Please avoid generating similar CTEs)**:
The following CTEs failed during generation or execution in previous attempts. Please avoid generating similar CTEs:

{failed_list}{column_hints_section}{duplicate_cte_warning}{full_chain_instruction}

"""
                
        # 增强 foreign_key 信息，添加关系类型
        enhanced_schema = self._enhance_foreign_key_with_relationship_types(node.schema_info)
        
        # 如果 foreign_key 已经增强（包含关系类型），则不再显示 Table Relationships（避免重复）
        # 检查 enhanced_schema 是
        # 否包含关系类型标记（如 "(N:1)"）
        has_enhanced_fk = 'foreign_key:' in enhanced_schema and any(
            f'({rel_type})' in enhanced_schema 
            for rel_type in ['1:1', '1:N', 'N:1', 'M:N']
        )
        
        # 只在 foreign_key 未增强时显示 Table Relationships
        relationships_info = ""
        if not has_enhanced_fk:
            relationships_info = self._get_relationships_info(node.schema_info)
            if relationships_info:
                relationships_info = "\n" + relationships_info
        
        # 从additional_context中分离strategy_text和剩余的additional_context
        strategy_text, remaining_additional_context = self._extract_strategy_from_context(node.additional_context)
        
        # 构建用户输入
        strategy_section = f"{strategy_text}\n\n" if strategy_text else ""
        user_input = f"""
{strategy_section}**Input**:
* **Natural language question**: {node.question}
* **Database schema**: {enhanced_schema}
{relationships_info}
* **Additional context**: {remaining_additional_context}

* **Preceding CTE and Results (Quick verification with LIMIT {self.cte_probe_limit})**: {preceding_cte_info}
* {used_names_str}
{failed_attempts_section}* **Depth Information**: 
  - Maximum allowed steps: {self.max_depth}
  - Current step: {current_depth + 1}
  - Remaining steps: {remaining_steps}. 

"""
        
        # 从llm_config中提取OpenAI配置
        config = self.llm_config.get('config_list', [{}])[0]
        model = config.get('model')
        base_url = config.get('base_url')
        api_key = config.get('api_key')
        
        # 构建系统消息
        system_message = self._get_cte_system_message()
        
        # 打印CTE生成的prompt（用于调试）- 不打印策略部分
        if should_monitor:
            print(f"[CTE生成] User Input (parallel):")
            print(f"  {'='*80}")
            # 打印时去掉策略部分，只打印 **Input**: 开始的内容
            user_input_for_print = user_input
            if "**Input**:" in user_input:
                user_input_for_print = "**Input**:" + user_input.split("**Input**:", 1)[1]
            print(f"\n[User Input]:")
            print(user_input_for_print)
            print(f"  {'='*80}")
        
        # 并行为每个temperature组生成变体
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time
        
        total_start_time = time.time()
        if should_monitor:
            if self.multi_model_configs:
                print(f"[CTE生成] 开始并行生成 {num_variants} 个CTE变体（4个temperature组，{len(self.multi_model_configs)} 个模型端点）...")
            else:
                print(f"[CTE生成] 开始并行生成 {num_variants} 个CTE变体（4个temperature组）...")
        variants = []
        
        def get_model_config_for_group(group_idx):
            """为当前组选择模型配置（轮询方式）"""
            if self.multi_model_configs:
                # 使用轮询方式选择模型
                with self._model_counter_lock:
                    selected_idx = self._model_counter % len(self.multi_model_configs)
                    self._model_counter += 1
                    return self.multi_model_configs[selected_idx]
            else:
                # 使用默认配置
                return {'model': model, 'base_url': base_url, 'api_key': api_key}
        
        def generate_group(group_idx, temperature, group_size, requires_full_chain_flag):
            """生成单个temperature组的CTE变体（每个线程使用独立的client）"""
            if group_size == 0:
                return []
            try:
                # 为当前组选择模型配置（轮询方式）
                model_config = get_model_config_for_group(group_idx)
                selected_base_url = model_config['base_url']
                selected_api_key = model_config.get('api_key', api_key)
                selected_model = model_config.get('model', model)
                
                # 每个线程创建独立的client（OpenAI client不是线程安全的）
                # 设置超时为120秒，避免LLM调用卡住
                client = OpenAI(base_url=selected_base_url, api_key=selected_api_key, timeout=120.0)
                start_time = time.time()
                response = client.chat.completions.create(
                    model=selected_model,
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_input}
                    ],
                    temperature=temperature,
                    n=group_size  # 当前组生成group_size个响应
                )
                elapsed = time.time() - start_time
                if should_monitor:
                    endpoint_info = f" (端点: {selected_base_url.split('/')[-2]})" if self.multi_model_configs else ""
                    print(f"[CTE生成] temperature={temperature}, group_size={group_size}, 耗时={elapsed:.2f}s{endpoint_info}")
                
                # 提取当前组的CTE
                group_ctes = []
                for idx, choice in enumerate(response.choices):
        
                    # 获取完整的消息内容
                    content = choice.message.content
                    
                    # 如果需要重新生成完整CTE链，保留整个链作为一个整体（不拆分）
                    # sql_executor会检测到这是完整链并直接使用，不再和历史CTE拼接
                    if requires_full_chain_flag:
                        # 提取完整的CTE链作为一个整体
                        cte_chain = self._extract_complete_cte_chain(content)
                        if cte_chain:
                            group_ctes.append(cte_chain)
                    else:
                        cte = self._extract_cte_from_response(content)
                        if cte:
                            group_ctes.append(cte)
                return group_ctes
            except Exception:
                return []
        
        # 并行执行所有temperature组
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {}
            for group_idx, temperature in enumerate(temperature_groups):
                group_size = variants_per_group + (1 if group_idx < remainder else 0)
                if group_size > 0:
                    future = executor.submit(generate_group, group_idx, temperature, group_size, requires_full_cte_chain)
                    futures[future] = (group_idx, temperature)
            
            # 收集结果
            for future in as_completed(futures):
                group_idx, temperature = futures[future]
                try:
                    group_ctes = future.result()
                    variants.extend(group_ctes)
                except Exception as e:
                    if should_monitor:
                        print(f"[CTE生成] temperature={temperature} 执行异常: {e}")
        
        total_elapsed = time.time() - total_start_time
        if should_monitor:
            print(f"[CTE生成] 完成！共生成 {len(variants)} 个CTE变体，总耗时={total_elapsed:.2f}s")
        return variants
