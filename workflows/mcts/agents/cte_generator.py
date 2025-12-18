"""
CTE生成器智能体

负责生成Common Table Expression (CTE)，
这是MCTS算法中的关键组件，用于生成SQL查询的中间步骤。 
"""

import autogen
from typing import Dict, List, Any
from utils.prompts import Prompts
import Levenshtein
import random
import re
import concurrent.futures
import threading
from functools import partial
from openai import OpenAI


class CTEGenerator:
    """CTE生成器智能体"""
    
    def __init__(self, llm_config: Dict, max_depth: int = 5, multi_model_configs: List[Dict] = None, relationships_map: Dict[str, Dict[str, Any]] = None):
        """
        初始化CTE生成器
        
        Args:
            llm_config: LLM配置
            max_depth: 最大允许深度（步骤数）
            multi_model_configs: 多个模型配置列表（用于多模型并行加速）
            relationships_map: 关系映射字典，格式: {f"{table1}<->{table2}": {'type': '1:1', ...}, ...}
        """
        self.llm_config = llm_config
        self.max_depth = max_depth
        self.multi_model_configs = multi_model_configs or []
        self.relationships_map = relationships_map or {}
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
        return f"""你是一个专业的数据分析师，你需要根据现有内容决定下一步探索什么。

**重要：直接输出SQL代码，不要输出推理过程或解释！只返回SQL代码块！**

**任务**: 基于提供的自然语言问题和数据库模式，**只生成一个CTE定义**（不包含SELECT语句），系统会自动添加SELECT。

**重要：在生成新CTE之前，先判断前序CTE是否已经恰好回答了问题，没有多余的信息！**

**两种可能的输出**:

1. **如果最后一个CTE返回的内容回答了问题** → 输出:
```sql
<END>
```

2. **如果需要继续** → 生成一个新的CTE，从以下类型中选择一个:

**表达式类型** (按"列先、行后"分层原则):

**列选择操作** (Column-only - 只选列，不改行数，不筛选):
1. **<列选择>**: 只选择后续会用到的列，不添加WHERE条件
   - 格式: `SELECT col1, col2 FROM table`
   - 用途: 先与数据库交互，看到需要的列和数据类型

**行筛选操作** (Row-only - 只加WHERE条件，不新增/删除列):
2. **<行筛选>**: 只添加WHERE条件进行行筛选
   - 格式: `SELECT col1, col2 FROM previous_cte WHERE condition`
   - 用途: 基于前序CTE的列进行行筛选

**表连接操作** (Table-only - 只做JOIN，不筛选，不改列表达式):
3. **<表连接>**: 只进行表连接，不添加WHERE条件
   - 格式: `SELECT t1.col1, t2.col2 FROM cte1 t1 JOIN table2 t2 ON condition`
   - 用途: 连接不同表获取更多列

**聚合操作** (Agg-only - 只做聚合，不筛选):
4. **<聚合>**: 只进行聚合计算
   - 格式: `SELECT COUNT(*), SUM(col) FROM previous_cte GROUP BY col`
   - 用途: 对前序CTE结果进行聚合

**高级操作** (需要前序CTE结果):
5. **<集合>**: 集合运算 (UNION, INTERSECT, EXCEPT, DISTINCT)
6. **<字符串>**: 字符串处理函数 (CONCAT, SUBSTR, UPPER, LOWER, TRIM, REPLACE)
7. **<日期>**: 日期时间处理 (STRFTIME, DATE, DATETIME, julianday)
8. **<窗口>**: 窗口函数 (ROW_NUMBER, RANK, DENSE_RANK, PARTITION BY, ORDER BY)


**重要规则**:
- **第一步必须是列选择**: 无前序CTE时，必须先选择需要的列与数据库交互
- **严格分层**: 每个CTE只做一种变化（列选择→行筛选→表连接→行筛选→聚合）
- **避免复合变化**: 不要在一个CTE中同时做列选择和行筛选
- **深度限制**: 系统允许生成最多{max_depth_str}个CTE步骤。当前步骤信息会在输入中提供，请根据剩余步骤数合理安排CTE生成策略

生成格式:
```sql
WITH new_cte_name AS (
    SELECT ...
    FROM previous_cte_name
    ...
)
```

**生成规则**:
- 只生成CTE定义（到 `)` 为止），每次只能生成一个类型
- 列级别变化和行级变化的操作不要合并在一起
- 不能使用逗号连接多个CTE.
- 不要添加任何SELECT语句
- 可以引用前序CTE的名称，我会添加在执行程序中，不需要你重复写。
- With不能命名已存在的CTE名称！必须使用新的、未被使用过的名称`
- 列名规则：必须使用schema中提供的完整列名，包含空格、括号等特殊字符的列名必须用反引号包裹



**Database Admin Instructions (Must Strictly Adhere):**
1.  **SELECT Clause:** Only select columns explicitly mentioned in the question. Avoid unnecessary columns or values.
2.  **Aggregation (MAX/MIN):** Always perform JOINs before using `MAX()` or `MIN()`.
3.  **ORDER BY with Distinct Values:** Use `GROUP BY <column>` before `ORDER BY <column> ASC|DESC` to ensure distinct values.
4.  **Handling NULLs:** If a column may contain NULL values (indicated by "None" in value examples or explicitly stated), use `JOIN` or `WHERE <column> IS NOT NULL`.
5.  **FROM/JOIN Clauses:** Only include tables essential to answer the question.
6.  **Strictly Follow Hints:** Adhere to all provided hints.
7.  **Thorough Question Analysis:** Address all conditions mentioned in the question.
8.  **DISTINCT Keyword:** Use `SELECT DISTINCT` when the question requires unique values (e.g., IDs, URLs). Refer to column statistics ("Value Statics") to determine if `DISTINCT` is necessary.
9.  **Column Selection:** When similar columns exist across tables, carefully analyze column descriptions and hints to choose the correct column.
10. **String Concatenation:** Never use `|| ' ' ||` or any other method to concatenate strings in the `SELECT` clause.
11. **JOIN Preference:** Prioritize `INNER JOIN` over nested `SELECT` statements.
12. **SQLite Functions Only:** Use only functions available in SQLite (unless fuzzy matching extensions are available).
13. **Date Processing:** Utilize `STRFTIME()` for date manipulation (e.g., `STRFTIME('%Y', SOMETIME)` to extract the year).
14. **Fuzzy Matching (When Previous CTE Returns Empty Results):** If the previous CTE execution returned an empty result set, it may indicate that exact string matching failed. In such cases, use fuzzy matching methods:
    - **CRITICAL**: When generating fuzzy matching CTE, you MUST generate **DIVERSE** LIKE patterns. **DO NOT** repeat the same pattern multiple times.
    - **Required Pattern Types** (for each target string):
      1. **Broad Match**: `LIKE '%Target%'` (Contains)
      2. **Spacing Variants**: `LIKE '% Target %'` (with spaces) or `LIKE 'Target'` (exact)
      3. **Boundary Match**: `LIKE 'Target%'` (starts with) OR `LIKE '%Target'` (ends with)
      4. **Structural Match**: If contains symbols (e.g., "=", "-"), try removing them or adding spaces (e.g., " = " → "=", "%=%")
    - **BAD Example** (DO NOT DO THIS):
      ```sql
      WHERE col LIKE '%Value%' OR col LIKE '%Value%' OR col LIKE '%Value%'  -- All duplicates!
      ```
    - **GOOD Example** (DO THIS):
      ```sql
      WHERE col LIKE '%Value%'      -- Contains
      OR col LIKE 'Value%'          -- Starts with
      OR col LIKE '%Value'          -- Ends with
      OR col = 'Value'              -- Exact match
      OR col LIKE '% Value %'       -- With spaces
      LIMIT 10
      ```
    - **Important**: The system generates multiple CTE variants in parallel. Each variant should explore **DIFFERENT** LIKE patterns. Do NOT generate the same pattern in multiple variants.
    - **Method 2** (if Method 1 fails): Use SQLite functions (LENGTH, INSTR, SUBSTR, CASE WHEN) to calculate similarity scores and ORDER BY to find the most similar rows.
    - When you see "CRITICAL ALERT: EMPTY RESULT RECOVERY" in the preceding CTE results, prioritize using these fuzzy matching techniques with diverse patterns.

**CRITICAL: Column Name Rules (Must Follow Exactly):**
Schema provides column names with backticks when they contain spaces/special characters.
**You MUST copy these column names EXACTLY as shown in the schema - DO NOT convert spaces to underscores!**

问题：Among the schools with the average score in Math over 560 in the SAT test, how many schools are directly charter-funded?
Schema：
satscores(cds, AvgScrMath), frpm(CDSCode, School Code, Charter Funding Type), schools(CDSCode, Charter)

示例 1 — 第一个 CTE（列选择）
WITH c_cols AS (
    SELECT ss.cds, ss.`AvgScrMath`
    FROM satscores AS ss
)

获得`AvgScrMath`的数值是[500,501,569,640...]
示例 2 — 第二个 CTE（行筛选）
WITH c_rows AS (
    SELECT cds, `AvgScrMath`
    FROM c_cols
    WHERE `AvgScrMath` IS NOT NULL AND `AvgScrMath` > 560
)

示例 3 — 第三个 CTE（表连接）
WITH t_join AS (
    SELECT f.`School Code`, f.`Charter Funding Type`
    FROM c_rows r
    INNER JOIN frpm f ON r.cds = f.`CDSCode`
)

获得`Charter Funding Type`的数值是[Directly funded,Somehow funded, w/o funded,...]
示例 4 — 第四个 CTE（行筛选）
WITH t_rows AS (
    SELECT `School Code`
    FROM t_join
    WHERE `Charter Funding Type` = 'Directly funded'
)

示例 5 — 第五个 CTE（聚合）
WITH final_count AS (
    SELECT COUNT(*) AS answer
    FROM t_rows
)

示例 6 — 停止
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
        
        # 获取1:1邻居表信息（作为背景信息）
        one_to_one_neighbors = self._get_one_to_one_neighbors(node, self.relationships_map)
        
        # 获取当前深度和剩余深度
        current_depth = node.depth
        remaining_steps = max(0, self.max_depth - current_depth)
        
        # 构建用户输入
        user_input = f"""
**Input**:

* **Natural language question**: {node.question}
* **Database schema**: {node.schema_info}
* **Additional context**: {node.additional_context} (syntactical adjustments are acceptable regarding spacing and formatting, based on the actual CTE results)
* **Preceding CTE and Results (Limit执行快速验证)**: 
{preceding_cte_info}
{one_to_one_neighbors}
* **深度信息**: 
  - 允许生成的最大步骤数: {self.max_depth}
  - 当前是第 {current_depth + 1} 步（深度: {current_depth}）
  - 剩余可生成步骤数: {remaining_steps}
  - 注意：如果剩余步骤数较少，建议优先考虑生成能直接回答问题的CTE，或输出<END>结束
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
                # 使用平衡括号算法检查括号是否匹配
                paren_count = 0
                for i in range(paren_start, len(cte_text)):
                    if cte_text[i] == '(':
                        paren_count += 1
                    elif cte_text[i] == ')':
                        paren_count -= 1
                        if paren_count == 0:
                            # 找到了匹配的右括号，CTE完整
                            break
                else:
                    # 没有找到匹配的右括号，CTE不完整
                    print(f"⚠️ _extract_cte_from_response: CTE括号不匹配，拒绝不完整的CTE")
                    return ""  # 拒绝不完整的CTE
        
        # 系统自动添加SELECT语句
        full_cte = f"{cte_text}\nSELECT * FROM {cte_name};"
        
        return full_cte
    
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
        生成模糊匹配提示信息（完整字符串格式）
        
        Args:
            is_second_empty: 是否是第二次空结果
            
        Returns:
            模糊匹配提示字符串
        """
        # 如果是第二次空结果，添加检查其他列的提醒
        other_column_hint = ""
        if is_second_empty:
            other_column_hint = """
### [CRITICAL: CHECK OTHER COLUMNS]

⚠️ **You have tried fuzzy matching on the same column twice and still got empty results.**

**This strongly suggests you may be querying the WRONG COLUMN.**

**ACTION REQUIRED**: 
1. **Review the question/evidence carefully** - Identify what column names are explicitly mentioned or implied:
   - If the question mentions a specific column name, use that exact column
   - If the question mentions an ID, check ID columns (e.g., `*_id`, `id`)
   - If the question mentions a name/label, check name/label columns (e.g., `name`, `label`, `title`)
   
2. **Check the schema** - Look for columns that semantically match what the question asks for:
   - Primary key columns (often `id`, `*_id`)
   - Name/label columns (often `name`, `label`, `title`)
   - Code columns (often `code`, `*_code`)
   
3. **Try querying a DIFFERENT column** that matches the semantic meaning in the question:
   - If you've been querying a name/label column, try the corresponding ID column
   - If you've been querying an ID column, try the corresponding name/label column
   - Consider querying multiple relevant columns with OR conditions

4. **Match column semantics to question intent** - Use columns that align with what the question is asking for.

**DO NOT** keep trying the same column with more LIKE patterns - it's likely the wrong column!
"""
        
        hint_text = f"""### [CRITICAL ALERT: EMPTY RESULT RECOVERY]

The previous CTE executed successfully but returned **0 rows** (Empty Result).

This strongly indicates a **String Literal Mismatch** OR **Wrong Column Selection**. The string values provided in the question/evidence likely differ from the actual formatting in the database (e.g., " Coldsnap" vs "Coldsnap", " = " vs "="), OR you may be querying the wrong column entirely.

{other_column_hint}

### [YOUR TASK]

Create a new, **Exploratory CTE** to find the correct string format OR the correct column.

**DO NOT** simply repeat the previous logic.

**DO NOT** repeat the exact same `LIKE` pattern.

### [STRATEGY: DIVERSE PATTERN MATCHING]

1. **Identify Targets**: Look at ALL string literals used in the previous failed query (in WHERE, JOIN, or CASE WHEN clauses).

2. **Generate Variations**: For EACH string literal, generate 3-5 **distinct** `LIKE` patterns using `OR`.

3. **Cross-Column Check**: If the failed query filtered on multiple columns, include fuzzy checks for ALL of them in this single probe.

### [REQUIRED PATTERN TYPES]

For a target string value (e.g., "Target"), you MUST include:

1. **Broad Match**: `LIKE '%Target%'` (Contains)

2. **Spacing Variants**: `LIKE '% Target %'` (Spaces inside/around) or `LIKE 'Target'` (Exact, if previous was fuzzy)

3. **Boundary Match**: `LIKE 'Target%'` (Starts with) OR `LIKE '%Target'` (Ends with)

4. **Structural Match**: If the string contains symbols (e.g., "=", "-", ":"), try removing them or adding spaces around them (e.g., if target is " = ", try "=" and "%=%").

### [BAD EXAMPLE - DO NOT DO THIS]

❌ Redundant and useless:

```sql
WHERE col1 LIKE '%Value%'
   OR col1 LIKE '%Value%' -- Duplicate!
   OR col1 LIKE '%Value%' -- Duplicate!
```

### [GOOD EXAMPLE - DO THIS]

✅ Diverse and exploratory:

```sql
WHERE 
   -- Checking Column A (Target: "Value")
   col1 LIKE '%Value%'       -- Standard contains
   OR col1 LIKE 'Value %'    -- Trailing space
   OR col1 LIKE '% Value'    -- Leading space
   OR col1 = 'Value'         -- Exact match
   OR col1 LIKE 'Value%'     -- Starts with
   OR col1 LIKE '%Value'      -- Ends with
   
   -- Checking Column B (Target: " = ")
   OR col2 LIKE '%=%'        -- No spaces
   OR col2 = '='             -- Exact symbol
   OR col2 LIKE '% = %'      -- With spaces
   OR col2 LIKE '%=%'        -- No spaces, contains
   
LIMIT 5;
```

### [IMPORTANT NOTES]

- **Each CTE variant should explore DIFFERENT patterns** - the system generates multiple variants in parallel, so each should try different LIKE patterns.
- **If the target string is simple (e.g., "TR047")**, try: `'%TR047%'`, `'TR047%'`, `'%TR047'`, `'TR047'`, `'%TR%047%'` (split), etc.
- **If the target contains special characters**, try variations with/without spaces, with/without the special characters.
- **Always include a LIMIT clause** to prevent excessive results.

**Method 2: Use SQLite functions for similarity calculation (if Method 1 fails)**:
```sql
WITH fuzzy_match AS (
    SELECT column,
           ABS(LENGTH(column) - LENGTH('search_value')) AS len_diff,
           CASE WHEN INSTR(column, 'search_value') > 0 THEN 0 ELSE 1 END AS contains_score,
           CASE WHEN column LIKE '%search%' AND column LIKE '%value%' THEN 0 ELSE 1 END AS pattern_score
    FROM previous_cte
    WHERE column LIKE '%search%' OR column LIKE '%value%'
    ORDER BY len_diff, contains_score, pattern_score
    LIMIT 5
)
```
"""
        return hint_text
    
    def _get_one_to_one_neighbors(self, node, relationships_map: Dict[str, Dict[str, Any]]) -> str:
        """
        获取1:1邻居表信息（作为背景信息）
        
        Args:
            node: MCTS节点
            relationships_map: 关系映射字典，格式: {f"{table1}<->{table2}": {'type': '1:1', ...}, ...}
            
        Returns:
            格式化的1:1邻居表信息字符串
        """
        if not relationships_map:
            return ""
        
        # 从schema_info中提取所有表名
        schema_info = node.schema_info if hasattr(node, 'schema_info') else ""
        tables_in_schema = set()
        
        # 从schema_info中提取表名（格式通常是 "table_name(col1, col2, ...)"）
        import re
        for line in schema_info.split('\n'):
            line = line.strip()
            if not line:
                continue
            # 匹配表名（在括号前）
            match = re.match(r'^`?([^`(]+)`?\s*\(', line)
            if match:
                table_name = match.group(1).strip().strip('`')
                tables_in_schema.add(table_name)
        
        # 从前序CTE中提取表名
        current = node
        while current is not None:
            if current.cte and current.cte != "" and current.cte != "<END>":
                # 从CTE中提取FROM和JOIN后的表名
                cte_text = current.cte
                # 匹配 FROM table_name 或 JOIN table_name
                from_matches = re.findall(r'\bFROM\s+`?([^\s`(,]+)`?', cte_text, re.IGNORECASE)
                join_matches = re.findall(r'\bJOIN\s+`?([^\s`(,]+)`?', cte_text, re.IGNORECASE)
                for table in from_matches + join_matches:
                    table = table.strip().strip('`')
                    if table:
                        tables_in_schema.add(table)
            current = current.parent
        
        if not tables_in_schema:
            return ""
        
        # 找到与这些表有1:1关系的邻居表
        one_to_one_neighbors = []
        for table in tables_in_schema:
            for rel_key, rel_info in relationships_map.items():
                rel_type = rel_info.get('type', '')
                if rel_type != '1:1':
                    continue
                
                table1 = rel_info.get('table1', '').strip().strip('`')
                table2 = rel_info.get('table2', '').strip().strip('`')
                col1 = rel_info.get('col1', '')
                col2 = rel_info.get('col2', '')
                description = rel_info.get('description', '')
                
                # 如果当前表是table1，则table2是邻居
                if table == table1:
                    neighbor_table = table2
                    neighbor_col = col2
                    join_col = col1
                elif table == table2:
                    neighbor_table = table1
                    neighbor_col = col1
                    join_col = col2
                else:
                    continue
                
                # 如果邻居表不在当前涉及的表列表中，则添加
                if neighbor_table not in tables_in_schema:
                    one_to_one_neighbors.append({
                        'current_table': table,
                        'current_col': join_col,
                        'neighbor_table': neighbor_table,
                        'neighbor_col': neighbor_col,
                        'description': description
                    })
        
        if not one_to_one_neighbors:
            return ""
        
        # 格式化输出
        formatted_info = []
        formatted_info.append("**1:1 Relationship Neighbors (Background Information)**:")
        formatted_info.append("The following tables have 1:1 relationships with tables you've already used.")
        formatted_info.append("**Important**: This is for reference only. Only join these tables if the question explicitly requires data from them. Do NOT join them just because they have a 1:1 relationship.")
        formatted_info.append("")
        
        for neighbor_info in one_to_one_neighbors:
            current_table = neighbor_info['current_table']
            current_col = neighbor_info['current_col']
            neighbor_table = neighbor_info['neighbor_table']
            neighbor_col = neighbor_info['neighbor_col']
            description = neighbor_info['description']
            
            formatted_info.append(f"- `{current_table}`.`{current_col}` <-> `{neighbor_table}`.`{neighbor_col}` (1:1)")
            # 只显示描述中关于关系性质的部分，过滤掉建议join的内容
            if description:
                # 移除包含"Join them"、"access"等建议性内容的句子
                desc_sentences = description.split('.')
                filtered_sentences = []
                for sentence in desc_sentences:
                    sentence_lower = sentence.strip().lower()
                    # 过滤掉建议join的句子
                    if 'join them' in sentence_lower or 'access full details' in sentence_lower:
                        continue
                    # 保留描述关系性质的句子
                    if sentence.strip():
                        filtered_sentences.append(sentence.strip())
                
                if filtered_sentences:
                    filtered_desc = '. '.join(filtered_sentences)
                    if filtered_desc:
                        formatted_info.append(f"  {filtered_desc}")
        
        formatted_info.append("")
        return "\n".join(formatted_info)
    
    def _get_preceding_cte_info(self, node) -> str:
        """
        通过node向上追溯，获取所有前序CTE及其执行结果（包括当前节点）
        
        Args:
            node: 当前MCTS节点（要扩展的节点）
            
        Returns:
            格式化的前序CTE信息字符串
        """
        # 收集从根节点到当前节点的所有CTE（包括当前节点自己的CTE）
        cte_path = []
        current = node  # 🔧 修复：从node本身开始，而不是node.parent
        
        while current is not None:
            if current.cte and current.cte != "" and current.cte != "<END>":
                cte_info = {
                    'cte': current.cte,
                    'execution_result': current.execution_results.get('cte_result', {})
                }
                cte_path.insert(0, cte_info)  # 插入到开头，保持从根到叶的顺序
            current = current.parent
        
        # 如果没有前序CTE，返回提示信息
        if not cte_path:
            return "无前序CTE"
        
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
            formatted_info.append(f"### 步骤 {idx}:")
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
                if query_result:
                    # 限制只展示前20行数据
                    total_rows = len(query_result)
                    query_result_limited = query_result[:20]
                    formatted_info.append(f"**执行结果**: 成功返回 {total_rows} 行数据")
                    # 只保留与问题相关的示例数据值，不再打印“结果列”和“实际数据行”
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
                    formatted_info.append("**执行结果**: 成功执行，返回空结果集")
                    # 只在最后一个（最近的）前序CTE有WHERE子句且返回空结果时，提示使用模糊匹配
                    if idx == len(cte_path) and last_cte_is_empty and last_cte_has_where:
                        # 使用之前已经检查好的 is_second_empty 变量
                        if is_second_empty:
                            # 第二次空结果：重复提示使用模糊匹配（避免检查其他列导致SQL超时）
                            print("前序CTE第二次有WHERE子句但返回空结果，重复提示使用模糊匹配创建新CTE(取新表名)。")
                            formatted_info.append(self._get_fuzzy_match_hint(is_second_empty=True))
                        else:
                            # 第一次空结果：使用当前的相似度方法
                            print("前序CTE第一次有WHERE子句但返回空结果，提示使用模糊匹配创建新CTE(取新表名)。")
                            formatted_info.append(self._get_fuzzy_match_hint(is_second_empty=False))
            else:
                error = exec_result.get('error', '未知错误')
                formatted_info.append(f"**执行结果**: 执行失败 - {error}")
            formatted_info.append("")  # 空行分隔
        
        return "\n".join(formatted_info)
    
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
        
        # 获取1:1邻居表信息（作为背景信息）
        one_to_one_neighbors = self._get_one_to_one_neighbors(node, self.relationships_map)
        
        # 检查前序CTE是否执行失败，如果失败则直接剪枝
        # 但是，如果当前节点是失败节点（is_failed=True），允许继续生成（因为失败节点的目的就是保存错误信息并继续探索）
        is_failed_node = node.execution_results.get('is_failed', False)
        if preceding_cte_info and "执行失败" in preceding_cte_info and not is_failed_node:
            if should_monitor:
                print("⚠️ 前序CTE执行失败，跳过此路径生成")
            return []
        
        # 获取已使用的CTE名称
        used_cte_names = self._get_used_cte_names(node)
        
        # 格式化已使用的CTE名称提示
        if used_cte_names:
            used_names_str = f"**已使用的CTE名称（不能再使用）**: {', '.join(used_cte_names)}"
        else:
            used_names_str = "**已使用的CTE名称**: 无"
        
        # 使用多个temperature值增加多样性
        # 将变体分成多个temperature组：[0.0, 0.3, 0.6, 0.9]
        temperature_groups = [0.0, 0.3, 0.6, 0.9]
        num_groups = len(temperature_groups)
        
        # 计算每组应该生成多少个变体
        variants_per_group = num_variants // num_groups
        remainder = num_variants % num_groups
        
        # 获取当前深度和剩余深度
        current_depth = node.depth
        remaining_steps = max(0, self.max_depth - current_depth)
        
        # 构建失败尝试提示（如果有）
        failed_attempts_section = ""
        if failed_attempts and len(failed_attempts) > 0:
            # 处理失败信息：可能是字符串（旧格式）或字典（新格式，包含错误信息）
            failed_items = []
            for item in failed_attempts[:5]:  # 最多显示5个（增加数量）
                if isinstance(item, dict):
                    # 新格式：包含CTE和错误信息
                    cte = item.get('cte', '').strip()
                    error = item.get('error', '执行失败或超时')
                    # 过滤掉无效的重复命名错误
                    if error and error.lower().find('duplicate with table name') != -1:
                        continue
                    # 如果CTE不为空，显示CTE和错误；如果CTE为空，只显示错误
                    if cte:
                        failed_items.append(f"```sql\n{cte}\n```\n错误信息: {error}")
                    else:
                        # CTE为空，只显示错误信息（可能是失败节点的汇总错误）
                        failed_items.append(f"错误信息: {error}")
                else:
                    # 旧格式：只有CTE文本
                    cte_text = str(item).strip()
                    if cte_text:
                        failed_items.append(f"```sql\n{cte_text}\n```\n错误信息: 执行失败或超时")
                    else:
                        failed_items.append(f"错误信息: 执行失败或超时")
            
            failed_list = "\n\n".join(failed_items)
            failed_attempts_section = f"""
* **之前的失败尝试（请避免生成的CTE）**:
以下CTE在之前的尝试中生成失败或执行失败，请避免生成类似的CTE：
{failed_list}

"""
        
        # 构建用户输入
        # 确保 one_to_one_neighbors 已定义（防御性编程）
        if 'one_to_one_neighbors' not in locals():
            one_to_one_neighbors = self._get_one_to_one_neighbors(node, self.relationships_map)
        
        # 优先级指导prompt（放在最显眼的位置）
        priority_guidance = """
### [CRITICAL: INFORMATION PRIORITY HIERARCHY]

**HIGHEST PRIORITY: Execution Results (Facts)**
   - The **Preceding CTE and Results** section contains **actual execution results** from the database
   - These are **FACTS** - what the database actually contains
   - **ALWAYS trust execution results over Evidence/Additional context**
   - Use the exact values, formats, and column names shown in execution results
   - Pay special attention to the **Relevant Sample Values** section which shows actual data samples

**LOWER PRIORITY: Evidence/Additional Context (Hints)**
   - The **Additional context** section contains hints from the question/evidence
   - These may contain **typos, formatting errors, or incorrect assumptions**
   - **DO NOT blindly follow Evidence** - verify against execution results first
   - If Evidence conflicts with execution results, use the values from execution results

### [ACTION REQUIRED]

1. **Scan the Relevant Sample Values** in the Preceding CTE Results section
2. **Use the exact values** shown in execution results (column names, string formats, spacing, case)
3. **Override Evidence** when it conflicts with execution results
4. **Common errors to watch for**:
   - Spacing variations: values with/without leading/trailing spaces
   - Case sensitivity: uppercase vs lowercase vs mixed case
   - Column name mismatches: different columns that might seem similar
   - Format differences: how values are actually stored vs how they appear in the question

**Remember**: Execution Results = Facts (trust these). Evidence = Hints (verify these).

================================================================================

"""
        
        user_input = f"""
**Input**:
* **Natural language question**: {node.question}
* **Database schema**: {node.schema_info}
* **Additional context**: {node.additional_context} (syntactical adjustments are acceptable regarding spacing and formatting, based on the actual CTE results)

{priority_guidance}

* **Preceding CTE and Results (Limit执行快速验证)**: {preceding_cte_info}
{one_to_one_neighbors}
* {used_names_str}
{failed_attempts_section}* **深度信息**: 
  - 允许生成的最大步骤数: {self.max_depth}
  - 当前是第 {current_depth + 1} 步
  - 剩余可生成步骤数: {remaining_steps} 每一步都在处理数据，要确保后续步骤需要的信息都被传递下去.
  - 注意：如果剩余步骤数较少，建议优先考虑生成能直接回答问题的CTE，或输出<END>结束

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
                        else:
                            # 调试：记录提取失败的原因
                            if should_monitor:
                                print(f"[CTE生成] ⚠️ temperature={temperature}, choice#{idx} 提取失败")
                                print(f"  响应长度: {len(content)} 字符")
                                print(f"  完整原始响应（逐行打印，确保不被截断）:")
                                print(f"  {'='*80}")
                                # 逐行打印，确保完整输出
                                for line_num, line in enumerate(content.split('\n'), 1):
                                    print(f"  [{line_num:4d}] {line}")
                                print(f"  {'='*80}")
                                # 检查是否包含WITH
                                has_with = 'WITH' in content.upper()
                                has_end = '<END>' in content
                                print(f"  包含WITH: {has_with}, 包含<END>: {has_end}")
                                # 尝试查找可能的SQL代码块
                                if '```' in content:
                                    print(f"  包含代码块标记: 是")
                                    # 尝试提取所有代码块
                                    code_blocks = re.findall(r'```(?:sql)?\s*(.*?)\s*```', content, re.DOTALL)
                                    if code_blocks:
                                        print(f"  找到 {len(code_blocks)} 个代码块:")
                                        for i, block in enumerate(code_blocks, 1):
                                            print(f"    代码块 {i}: {block[:100]}...")
                                else:
                                    print(f"  包含代码块标记: 否")
                    return group_ctes
                except Exception as e:
                    if should_monitor:
                        print(f"[CTE生成] temperature={temperature} 失败: {e}")
                    return []
            
            # 并行执行所有temperature组
            with ThreadPoolExecutor(max_workers=4) as executor:
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
