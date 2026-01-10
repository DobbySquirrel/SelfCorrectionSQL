"""
CTE生成器智能体

负责生成Common Table Expression (CTE)，
这是MCTS算法中的关键组件，用于生成SQL查询的中间步骤。 
"""

import autogen
from typing import Dict, List, Any, Optional
import Levenshtein
import random
import re
import concurrent.futures
import threading
from openai import OpenAI


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
    
    def setup_agent(self, temperature: float = 0.7):
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

**IMPORTANT: Output SQL code directly, do NOT output reasoning process or explanations! Only return SQL code blocks!**

**Task**: Based on the provided natural language question and database schema, **generate only ONE CTE definition** (without SELECT statement). The system will automatically add SELECT.

**IMPORTANT: Before generating a new CTE, first determine if the preceding CTE has already answered the question exactly, without extra information!**

**⚠️ CRITICAL: Evidence Verification Required**
   - **Before using any information from "Additional context" or "Evidence":**
     1. **MUST first verify** if the preceding CTE execution results already contain the needed columns/values
     2. If Evidence mentions a condition, you MUST:
        - First check if the preceding CTE includes the relevant column mentioned in the condition
        - If the column is missing, add it to your CTE first
        - Then verify the actual values in execution results before applying the condition
     3. **Never blindly trust Evidence** - always verify against execution results first

**Two Possible Outputs**:

1. **If the last CTE's returned content answers the question** → Output:
```sql
<END>
```

2. **If continuation is needed** → Generate a new CTE, choose one from the following types:

**Expression Types** (following "column-first, row-later" layering principle):

**Column Selection Operation** (Column-only - select columns only, don't change row count, no filtering):
1. **<Column Selection>**: Only select columns that will be used later, do not add WHERE conditions
   - Format: `SELECT col1, col2 FROM table`
   - Purpose: Interact with the database first to see the needed columns and data types

**Row Filtering Operation** (Row-only - only add WHERE conditions, no new/removed columns):
2. **<Row Filtering>**: Only add WHERE conditions for row filtering
   - Format: `SELECT col1, col2 FROM previous_cte WHERE condition`
   - Purpose: Filter rows based on columns from preceding CTE

**Table Join Operation** (Table-only - only perform JOIN, no filtering, no column expression changes):
3. **<Table Join>**: Only perform table joins, do not add WHERE conditions
   - Format: `SELECT t1.col1, t2.col2 FROM cte1 t1 JOIN table2 t2 ON condition`
   - Purpose: Join different tables to get more columns

**Aggregation Operation** (Agg-only - only perform aggregation, no filtering):
4. **<Aggregation>**: Only perform aggregation calculations
   - Format: `SELECT COUNT(*), SUM(col) FROM previous_cte GROUP BY col`
   - Purpose: Aggregate results from preceding CTE

**Advanced Operations** (require preceding CTE results):
5. **<Set>**: Set operations (UNION, INTERSECT, EXCEPT, DISTINCT)
6. **<String>**: String processing functions (CONCAT, SUBSTR, UPPER, LOWER, TRIM, REPLACE)
7. **<Date>**: Date/time processing (STRFTIME, DATE, DATETIME, julianday)
8. **<Window>**: Window functions (ROW_NUMBER, RANK, DENSE_RANK, PARTITION BY, ORDER BY)


**Important Rules**:
- **First step must be column selection**: When there are no preceding CTEs, you must first select the needed columns to interact with the database
- **Strict layering**: Each CTE only performs one type of change (column selection → row filtering → table join → row filtering → aggregation)
- **Avoid composite changes**: Do not perform both column selection and row filtering in one CTE
- **Depth limit**: The system allows generating at most {max_depth_str} CTE steps. Current step information will be provided in the input. Please arrange CTE generation strategy reasonably based on remaining steps

Generation format:
```sql
WITH new_cte_name AS (
    SELECT ...
    FROM previous_cte_name
    ...
)
```

**Generation Rules**:
- Only generate CTE definition (up to `)`), only one type per generation
- Do not combine column-level changes and row-level changes in one operation
- Cannot use commas to connect multiple CTEs
- Do not add any SELECT statements
- You can reference preceding CTE names. I will add them in the executor, you don't need to repeat them.
- **CTE Naming Rules**:
  - **CRITICAL**: You MUST use a NEW, UNUSED CTE name for each CTE you generate
  - **DO NOT reuse existing CTE names**: If a preceding CTE has issues, you should create a new CTE with a different name instead of reusing the same name
  - Each CTE in the sequence must have a unique name to avoid conflicts
- Column name rules: Must use the complete column names provided in the schema. Column names containing spaces, parentheses, or other special characters must be wrapped in backticks



**Database Admin Instructions (Must Strictly Adhere):**
1.  **SELECT Clause:** Only select columns explicitly mentioned in the question. Avoid unnecessary columns or values.
   - **CRITICAL**: When performing JOINs, you MUST retain all columns that will be needed in subsequent CTE steps (e.g., join keys like `CDSCode`, columns needed for final answer like `City`, `School`). Do NOT drop columns that are required for later operations.
2.  **FROM Table Selection:** If filtering/ordering columns come from a specific table, use that table as the FROM driving table. This ensures the filtering/ordering conditions can be applied correctly.
3.  **Aggregation (MAX/MIN):** Always perform JOINs before using `MAX()` or `MIN()`.
4.  **ORDER BY with Distinct Values:** Use `GROUP BY <column>` before `ORDER BY <column> ASC|DESC` to ensure distinct values.
5.  **Handling NULLs:** If a column may contain NULL values (indicated by "None" in value examples or explicitly stated), use `JOIN` or `WHERE <column> IS NOT NULL`.
6.  **FROM/JOIN Clauses:** Only include tables essential to answer the question.
7.  **Strictly Follow Hints:** Adhere to all provided hints.
8.  **Thorough Question Analysis:** Address all conditions mentioned in the question.
9.  **DISTINCT Keyword:** Use `SELECT DISTINCT` when the question requires unique values or when selecting columns that may have duplicates (e.g., IDs, URLs, names, nationalities, categories). If the question asks for a list of unique items or the result may contain duplicate values, always use `SELECT DISTINCT`. Refer to column statistics ("Value Statics") to determine if `DISTINCT` is necessary. When in doubt, use `DISTINCT` to ensure unique results.
10. **COUNT with DISTINCT (CRITICAL):** When using `COUNT()` after JOINs, especially with N:1 or M:N relationships, you MUST use `COUNT(DISTINCT column)` instead of `COUNT(*)` to avoid counting duplicate rows. If the relationship is N:1 (Many-to-One), the child table may have multiple rows per parent, so always use `COUNT(DISTINCT column)` when counting entities from the parent table. **Check the table relationships provided in the prompt - if you see N:1 relationships and you're counting entities from the parent table, you MUST use `COUNT(DISTINCT column)` where the column is the unique identifier from the parent table.**
11. **Column Selection:** When similar columns exist across tables, carefully analyze column descriptions and hints to choose the correct column.
12. **String Concatenation:** Never use `|| ' ' ||` or any other method to concatenate strings in the `SELECT` clause.
13. **JOIN Preference:** Prioritize `INNER JOIN` over nested `SELECT` statements.
14. **SQLite Functions Only:** Use only functions available in SQLite (unless fuzzy matching extensions are available).
15. **Date Processing:** Utilize `STRFTIME()` for date manipulation (e.g., `STRFTIME('%Y', SOMETIME)` to extract the year).
15. **Fuzzy Matching (When Previous CTE Returns Empty Results):** If the previous CTE execution returned an empty result set, it may indicate that exact string matching failed. In such cases, use fuzzy matching methods:
    - **CRITICAL**: When generating fuzzy matching CTE, you MUST generate **DIVERSE** LIKE patterns. **DO NOT** repeat the same pattern multiple times.
    - **Required Pattern Types** (for each target string):
      1. **Broad Match**: `LIKE '%Target%'` (Contains)
      2. **Spacing Variants**: `LIKE '% Target %'` (with spaces) or `LIKE 'Target'` (exact)
      3. **Boundary Match**: `LIKE 'Target%'` (starts with) OR `LIKE '%Target'` (ends with)
      4. **Structural Match**: If contains symbols (e.g., "=", "-"), try removing them or adding spaces (e.g., " = " → "=", "%=%")
    - **Important**: The system generates multiple CTE variants in parallel. Each variant should explore **DIFFERENT** LIKE patterns. Do NOT generate the same pattern in multiple variants.
    - **Method 2** (if Method 1 fails): Use SQLite functions (LENGTH, INSTR, SUBSTR, CASE WHEN) to calculate similarity scores and ORDER BY to find the most similar rows.
    - When you see "CRITICAL ALERT: EMPTY RESULT RECOVERY" in the preceding CTE results, prioritize using these fuzzy matching techniques with diverse patterns.

**CRITICAL: Column Name Rules (Must Follow Exactly):**
Schema provides column names with backticks when they contain spaces/special characters.
**You MUST copy these column names EXACTLY as shown in the schema - DO NOT convert spaces to underscores!**

Question: Among the schools with the average score in Math over 560 in the SAT test, how many schools are directly charter-funded?
Schema:
satscores(cds, AvgScrMath), frpm(CDSCode, School Code, Charter Funding Type), schools(CDSCode, Charter)

Example 1 — First CTE (Column Selection)
WITH c_cols AS (
    SELECT ss.cds, ss.`AvgScrMath`
    FROM satscores AS ss
)

Obtained `AvgScrMath` values are [500,501,569,640...]
Example 2 — Second CTE (Row Filtering)
WITH c_rows AS (
    SELECT cds, `AvgScrMath`
    FROM c_cols
    WHERE `AvgScrMath` IS NOT NULL AND `AvgScrMath` > 560
)

Example 3 — Third CTE (Table Join)
WITH t_join AS (
    SELECT f.`School Code`, f.`Charter Funding Type`
    FROM c_rows r
    INNER JOIN frpm f ON r.cds = f.`CDSCode`
)

Obtained `Charter Funding Type` values are [Directly funded,Somehow funded, w/o funded,...]
Example 4 — Fourth CTE (Row Filtering)
WITH t_rows AS (
    SELECT `School Code`
    FROM t_join
    WHERE `Charter Funding Type` = 'Directly funded'
)

Example 5 — Fifth CTE (Aggregation)
WITH final_count AS (
    SELECT COUNT(*) AS answer
    FROM t_rows
)
Note: If the question involves JOINs with N:1 relationships and asks "how many" entities, use COUNT(DISTINCT entity_id) instead of COUNT(*) to avoid counting duplicates.

Example 6 — Stop
<END>

/no_think
"""

    def generate_cte(self, node, temperature: float = 0.7) -> str:
        """
        为给定节点生成CTE
        
        Args:
            node: MCTS节点
            temperature: 生成温度
            
        Returns:
            生成的CTE字符串或<END>标记
        """
        # 每次生成前重新设置agent以应用新的temperature
        self.setup_agent(temperature)
        
        # 通过node.parent向上追溯获取前序CTE信息
        preceding_cte_info = self._get_preceding_cte_info(node)
        
        # 获取关系信息
        relationships_info = self._get_relationships_info(node.schema_info)
        
        # 获取当前深度和剩余深度
        current_depth = node.depth
        remaining_steps = max(0, self.max_depth - current_depth)
        
        # 构建用户输入
        user_input = f"""
**Input**:

* **Natural language question**: {node.question}
* **Database schema**: {node.schema_info}
{relationships_info}
* **Additional context**: {node.additional_context} (syntactical adjustments are acceptable regarding spacing and formatting, based on the actual CTE results)
* **Preceding CTE and Results (Quick verification with LIMIT {self.cte_probe_limit})**: 
{preceding_cte_info}
* **Depth Information**: 
  - Maximum allowed steps: {self.max_depth}
  - Current step: {current_depth + 1} (depth: {current_depth})
  - Remaining steps: {remaining_steps}
  - Note: If remaining steps are limited, prioritize generating CTEs that directly answer the question, or output <END> to finish
"""
        
        # 打印CTE生成的prompt（用于调试）
        should_monitor = self._should_monitor_cte(node.question)
        if should_monitor:
            print(f"[CTE生成] User Input (generate_cte):")
            print(f"  {'='*80}")
            print(user_input)
            print(f"  {'='*80}")
        
        # 使用智能体生成CTE
        messages = [
            {
                "role": "user",
                "content": user_input
            }
        ]
        
        response = self.cte_agent.generate_reply(messages)
        
        # 检查是否输出了<END>
        if "<END>" in response:
            return "<END>"
        
        # 提取CTE代码
        cte = self._extract_cte_from_response(response)
        
        return cte
    
    def _extract_cte_from_response(self, response) -> str:
        """
        从响应中提取CTE代码，并自动添加SELECT语句
        
        LLM只生成: WITH xxx AS (...)
        系统自动添加: SELECT * FROM xxx;
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
            cte_text = code_block_match.group(1).strip()
        else:
            # 如果没有代码块，尝试直接提取WITH语句
            lines = response.split('\n')
            cte_lines = []
            in_cte = False
            
            for line in lines:
                if 'WITH' in line.upper():
                    in_cte = True
                    cte_lines.append(line)
                elif in_cte:
                    cte_lines.append(line)
                    # 检测CTE结束（遇到右括号）
                    if line.strip().endswith(')') and not line.strip().endswith('('):
                        break
            
            cte_text = '\n'.join(cte_lines).strip()
        
        if not cte_text or 'WITH' not in cte_text.upper():
            return ""
        
        # 清理：移除末尾的分号和多余的SELECT语句
        cte_text = cte_text.rstrip(';').strip()
        
        # 如果LLM还是生成了SELECT，移除它
        # 查找最后一个右括号的位置
        last_paren_pos = cte_text.rfind(')')
        if last_paren_pos > 0:
            # 检查右括号后是否有SELECT
            after_paren = cte_text[last_paren_pos + 1:].strip()
            if after_paren.upper().startswith('SELECT'):
                # 移除SELECT部分
                cte_text = cte_text[:last_paren_pos + 1]
        
        # 提取CTE名称
        cte_name_match = re.search(r'WITH\s+(\w+)\s+AS', cte_text, re.IGNORECASE)
        if not cte_name_match:
            return ""
        
        cte_name = cte_name_match.group(1)
        
        # 验证CTE是否完整：检查括号是否匹配
        # 找到 WITH name AS ( 的位置
        as_pos = cte_text.upper().find('AS')
        if as_pos > 0:
            # 从 AS 后面查找第一个 (
            paren_start = cte_text.find('(', as_pos)
            if paren_start > 0:
                # 使用平衡括号算法检查括号是否匹配（考虑字符串中的括号）
                paren_count = 0
                in_string = False
                string_char = None  # 记录字符串的引号类型（' 或 "）
                i = paren_start
                while i < len(cte_text):
                    char = cte_text[i]
                    
                    # 处理字符串边界
                    if char in ("'", '"') and (i == 0 or cte_text[i-1] != '\\'):
                        if not in_string:
                            in_string = True
                            string_char = char
                        elif char == string_char:
                            in_string = False
                            string_char = None
                    
                    # 只在非字符串状态下计算括号
                    if not in_string:
                        if char == '(':
                            paren_count += 1
                        elif char == ')':
                            paren_count -= 1
                            if paren_count == 0:
                                # 找到了匹配的右括号，CTE完整
                                break
                    i += 1
                
                # 如果括号计数不为0，说明括号不匹配
                if paren_count != 0:
                    print(f"⚠️ _extract_cte_from_response: CTE括号不匹配，拒绝不完整的CTE")
                    print(f"   不完整的CTE内容:\n{cte_text}")
                    print(f"   括号计数: {paren_count} (未闭合)")
                    return ""  # 拒绝不完整的CTE
        
        # 系统自动添加SELECT语句
        full_cte = f"{cte_text}\nSELECT * FROM {cte_name};"
        
        return full_cte
    
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
    
    def _extract_table_order(self, schema_info: str) -> List[str]:
        """
        从schema中提取表的顺序
        
        Args:
            schema_info: schema信息
            
        Returns:
            表名列表
        """
        table_names = []
        for line in schema_info.split('\n'):
            line = line.strip()
            if line.startswith('#') and '`' in line and '(' in line:
                match = re.match(r'#\s*(\w+)\(', line)
                if match:
                    table_names.append(match.group(1))
        return table_names
    
    def _extract_first_table_columns(self, schema_info: str, max_cols: int = 5) -> str:
        """
        提取第一个表的前N列（用于显示列顺序变化）
        
        Args:
            schema_info: schema信息
            max_cols: 最多显示的列数
            
        Returns:
            列名字符串，例如 "CDSCode, Charter, School, ..."
        """
        for line in schema_info.split('\n'):
            line = line.strip()
            if line.startswith('#') and '`' in line and '(' in line:
                match = re.match(r'#\s*\w+\((.*)\)\s*$', line)
                if match:
                    columns_str = match.group(1)
                    # 使用正确的解析方法
                    columns = self._parse_columns(columns_str)
                    # 去掉反引号用于显示
                    columns = [col.strip('`') for col in columns]
                    if len(columns) > max_cols:
                        return ', '.join(columns[:max_cols]) + ', ...'
                    else:
                        return ', '.join(columns)
        return "N/A"
    
    def _parse_columns(self, columns_str: str) -> List[str]:
        """
        正确解析列名，处理反引号包裹的列名（列名内可能包含逗号、括号等特殊字符）
        
        Args:
            columns_str: 列定义字符串，例如: "`col1`, `col2`, `col with (special) chars`, ..."
            
        Returns:
            列名列表（保留反引号）
        """
        columns = []
        current_col = ""
        in_backtick = False
        
        for char in columns_str:
            if char == '`':
                in_backtick = not in_backtick
                current_col += char
            elif char == ',' and not in_backtick:
                # 只有在反引号外的逗号才是分隔符
                if current_col.strip():
                    columns.append(current_col.strip())
                current_col = ""
            else:
                current_col += char
        
        # 添加最后一列
        if current_col.strip():
            columns.append(current_col.strip())
        
        return columns
    
    def _shuffle_schema(self, schema_info: str) -> str:
        """
        随机打乱database schema中表和列的顺序，增加生成多样性
        
        Args:
            schema_info: 原始schema信息
            
        Returns:
            打乱后的schema信息
        """
        try:
            # 分离数据库名称和schema主体
            parts = schema_info.split('\n', 1)
            if len(parts) < 2:
                return schema_info
            
            db_name_line = parts[0]  # 例如: "db_name:california_schools\n#"
            rest = parts[1]
            
            # 分离表定义和外键定义
            if 'foreign_key:' in rest:
                schema_part, fk_part = rest.split('foreign_key:', 1)
            else:
                schema_part = rest
                fk_part = None
            
            # 解析表定义（每行格式: # table_name(`col1`, `col2`, ...)）
            table_lines = []
            for line in schema_part.split('\n'):
                line = line.strip()
                if line.startswith('#') and '`' in line:
                    # 提取表名和列（使用正确的正则表达式匹配到行尾）
                    match = re.match(r'#\s*(\w+)\((.*)\)\s*$', line)
                    if match:
                        table_name = match.group(1)
                        columns_str = match.group(2)
                        
                        # 使用新的解析方法正确处理列名
                        columns = self._parse_columns(columns_str)
                        
                        # 随机打乱列的顺序
                        random.shuffle(columns)
                        
                        # 重建表定义
                        new_line = f"# {table_name}({', '.join(columns)})"
                        table_lines.append(new_line)
                elif line:  # 保留其他非空行
                    table_lines.append(line)
            
            # 随机打乱表的顺序
            random.shuffle(table_lines)
            
            # 重建schema
            new_schema = db_name_line + '\n' + '\n'.join(table_lines)
            
            # 处理外键（如果有）
            if fk_part:
                fk_lines = []
                for line in fk_part.split('\n'):
                    line = line.strip()
                    if line.startswith('#') and 'references' in line:
                        fk_lines.append(line)
                    elif line:
                        fk_lines.append(line)
                
                # 随机打乱外键顺序
                fk_constraints = [l for l in fk_lines if 'references' in l]
                other_lines = [l for l in fk_lines if 'references' not in l]
                random.shuffle(fk_constraints)
                
                new_fk = '\n'.join(other_lines + fk_constraints)
                new_schema += '\nforeign_key:' + new_fk
            
            return new_schema
            
        except Exception as e:
            # 如果解析失败，返回原始schema
            print(f"⚠️  Schema打乱失败: {e}，使用原始schema")
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
                    
                    # 根据错误类型给出具体的"处方" (Actionable Advice)
                    advice = ""
                    if error_type and ("Fan-out" in str(error_type) or "1:N" in str(error_type) or "1:N" in str(feedback)):
                        advice = ("**How to Fix**: You are joining a 'One' side table with a 'Many' side table without aggregation. "
                                  "This causes row duplication.\n"
                                  "   1. Use `GROUP BY` on the primary key of the 'One' side table.\n"
                                  "   2. Or use aggregation functions (SUM, AVG) on the 'Many' side columns.")
                    elif error_type and "Cartesian" in str(error_type):
                        advice = ("**How to Fix**: The result size is explosively large. "
                                  "You likely missed a JOIN condition or joined unrelated tables. Please check your `ON` clause.")
                    
                    if advice:
                        formatted_info.append(advice)
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
            used_names_str = f"* **Used CTE Names**: {', '.join(used_cte_names)}\n  - **Important**: You MUST use a NEW CTE name that is different from all existing CTE names. Do NOT reuse any existing CTE name, as this will cause errors."
        else:
            used_names_str = "* **Used CTE Names**: None"
        
        # 使用多个temperature值增加多样性
        # 将变体分成多个temperature组：[0.3, 0.6, 0.9] (移除0.0，因为贪婪采样时n必须为1)
        temperature_groups = [0.3, 0.6, 0.9]
        num_groups = len(temperature_groups)
        
        # 计算每组应该生成多少个变体
        variants_per_group = num_variants // num_groups
        remainder = num_variants % num_groups
        
        # 获取当前深度和剩余深度
        current_depth = node.depth
        remaining_steps = max(0, self.max_depth - current_depth)
        
        # 构建失败尝试提示（如果有）
        failed_attempts_section = ""
        column_hints = []  # 收集所有列名到表的映射提示
        
        if failed_attempts and len(failed_attempts) > 0:
            # 处理失败信息：可能是字符串（旧格式）或字典（新格式，包含错误信息）
            failed_items = []
            attempt_count = 0
            for item in failed_attempts[:5]:  # 最多显示5个
                if isinstance(item, dict):
                    # 新格式：包含CTE和错误信息
                    cte = item.get('cte', '').strip()
                    error = item.get('error', 'Execution failed or timeout')
                    # 过滤掉无效的重复命名错误
                    if error and error.lower().find('duplicate with table name') != -1:
                        continue
                    
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
            
            if failed_items:
            failed_list = "\n\n".join(failed_items)
                
                # 如果有列名映射提示，单独放在一个部分
                column_hints_section = ""
                if column_hints:
                    # 去重列名提示
                    unique_hints = list(set(column_hints))
                    column_hints_section = f"\n\n**⚠️ Column Location Hints (CRITICAL):**\n" + "\n".join(f"- {hint}" for hint in unique_hints)
                
            failed_attempts_section = f"""
* **Previous Failed Attempts (Please avoid generating similar CTEs)**:
The following CTEs failed during generation or execution in previous attempts. Please avoid generating similar CTEs:

{failed_list}{column_hints_section}

"""
        
        # 构建用户输入
        # 优先级指导prompt（放在最显眼的位置）
        priority_guidance = """
### [CRITICAL: INFORMATION PRIORITY]

**Priority: Execution Results > Evidence**
- **Execution Results** are FACTS - use exact values, formats, column names
- **Evidence/Additional Context** are hints - verify against execution results first
- If Evidence mentions a condition, check if the preceding CTE includes the relevant column. If not, add it first, then verify values before applying the condition.

================================================================================

"""
        
        # 获取关系信息
        relationships_info = self._get_relationships_info(node.schema_info)
        
        # 移除foreign_key信息，简化prompt（relationships_info已提供足够的关系信息）
        schema_without_fk = self._remove_foreign_key(node.schema_info)
        
        user_input = f"""
**Input**:
* **Natural language question**: {node.question}
* **Database schema**: {schema_without_fk}
{relationships_info}
* **Additional context**: {node.additional_context} 

{priority_guidance}

* **Preceding CTE and Results (Quick verification with LIMIT {self.cte_probe_limit})**: {preceding_cte_info}
* {used_names_str}
{failed_attempts_section}* **Depth Information**: 
  - Maximum allowed steps: {self.max_depth}
  - Current step: {current_depth + 1}
  - Remaining steps: {remaining_steps}. Each step processes data, ensure that information needed for subsequent steps is passed down.
  - Note: If remaining steps are limited, prioritize generating CTEs that directly answer the question.

"""
        
        # 打印CTE生成的prompt（用于调试）
        if should_monitor:
            print(f"[CTE生成] User Input (parallel):")
            print(f"  {'='*80}")
            print(user_input)
            print(f"  {'='*80}")
        
        # 从llm_config中提取OpenAI配置
        config = self.llm_config.get('config_list', [{}])[0]
        model = config.get('model')
        base_url = config.get('base_url')
        api_key = config.get('api_key')
        
        # 构建系统消息
        system_message = self._get_cte_system_message()
        
        try:
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
            
            def generate_group(group_idx, temperature, group_size):
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
                    # 不设置 timeout，使用默认值（10分钟）或 None（无限制）
                    client = OpenAI(base_url=selected_base_url, api_key=selected_api_key, timeout=None)
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
                        content = choice.message.content
                        cte = self._extract_cte_from_response(content)
                        if cte:
                            group_ctes.append(cte)
                    return group_ctes
                except Exception as e:
                    if should_monitor:
                        error_type = type(e).__name__
                        error_msg = str(e)
                        print(f"[CTE生成] temperature={temperature} 失败: {error_type}: {error_msg}")
                        # 打印更详细的调试信息
                        print(f"  端点: {selected_base_url}, 模型: {selected_model}")
                    return []
            
            # 并行执行所有temperature组
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {}
                for group_idx, temperature in enumerate(temperature_groups):
                    group_size = variants_per_group + (1 if group_idx < remainder else 0)
                    if group_size > 0:
                        future = executor.submit(generate_group, group_idx, temperature, group_size)
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
            
        except Exception as e:
            if should_monitor:
                print(f"❌ 使用n参数生成CTE变体失败: {e}")
            # 如果失败，回退到单次调用（但只生成一个）
            try:
                self.setup_agent(0.6)  # 使用默认temperature
                messages = [{"role": "user", "content": user_input}]
                response = self.cte_agent.generate_reply(messages)
                cte = self._extract_cte_from_response(response)
                return [cte] if cte else []
            except Exception as e2:
                if should_monitor:
                    print(f"❌ 回退方案也失败: {e2}")
                return []
