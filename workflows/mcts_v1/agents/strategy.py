# ==========================================================
# Strategy Injection (for SimpleRolloutWorkflow, no MASTER prompt)
# ==========================================================
from dataclasses import dataclass
from typing import Optional, Literal, Tuple

StrategyMode = Literal[
    "FORCE_S1", "FORCE_S2", "FORCE_S3",
    "NONE",
    "LLM_PICK_ONCE",
]

@dataclass
class StrategyConfig:
    """
    mode:
      - FORCE_Sx: always inject Sx policy into prompts
      - NONE: inject nothing (baseline)
      - LLM_PICK_ONCE: at depth=0 ask model to pick strategy once, then lock for later steps
    """
    mode: StrategyMode = "FORCE_S2"
    # 如果你想在一次run中固定策略，不允许后续prompt再出现"可切换"表述，就保持True
    lock_after_picked: bool = True


GLOBAL_STRATEGY_CONFIG = StrategyConfig(mode="FORCE_S4", lock_after_picked=True)
CTE_ACTION_level="""

Expression Types:

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

Example:

Question: Among the schools with the average score in Math over 560 in the SAT test, how many schools are directly charter-funded?
Schema: satscores(cds, AvgScrMath), frpm(CDSCode, School Code, Charter Funding Type), schools(CDSCode, Charter)

First CTE (Column Selection)
WITH c_cols AS (
    SELECT ss.cds, ss.`AvgScrMath`
    FROM satscores AS ss
)

Obtained `AvgScrMath` values are [500,501,569,640...]
Second CTE (Row Filtering)
WITH c_rows AS (
    SELECT cds, `AvgScrMath`
    FROM c_cols
    WHERE `AvgScrMath` IS NOT NULL AND `AvgScrMath` > 560
)

Third CTE (Table Join)
WITH t_join AS (
    SELECT f.`School Code`, f.`Charter Funding Type`
    FROM c_rows r
    INNER JOIN frpm f ON r.cds = f.`CDSCode`
)

Obtained `Charter Funding Type` values are [Directly funded,Somehow funded, w/o funded,...]
Fourth CTE (Row Filtering)
WITH t_rows AS (
    SELECT `School Code`
    FROM t_join
    WHERE `Charter Funding Type` = 'Directly funded'
)

Fifth CTE (Aggregation)
WITH final_count AS (
    SELECT COUNT(*) AS answer
    FROM t_rows
)

Stop
<END>

"""

# ---- 策略手册（给 CTE/SQL 生成器看的文本）----
_STRATEGY_DESCRIPTIONS = {
    "S1": """S1 Entity-First:
- If you introduce a new value filter (keyword/category/date/currency/segment) not confirmed, first sanity-check via safe assumptions:
  * prefer simple filters on one table before joins
  * prefer explicit WHERE on known columns, avoid guessing enums
- Avoid wide joins early.""",
    
    "S2": """S2 Relation-First:
- First ensure join path correctness (use FK hints from schema/foreign_key text).
- Build join skeleton first, then add filters.""",
    
    "S3": """S3 Proactive:
- Upfront disambiguation: if schema is large/ambiguous, create a robust intermediate CTE with only necessary columns.
- Avoid fancy expressions until you have the correct grain.""",
    
    "S4": """S4 Custom:
- You can choose S4 if you want to create your own custom strategy plan based on the specific question and schema.
- When selecting S4, you MUST provide a detailed "thought" field explaining your custom strategy plan.
- The "thought" will be used as the strategy guidance in subsequent CTE generation steps.""",
    
    "S5": """S5 Evidence-Based:
Always validate your CTE against the evidence fields in the dataset.
Use the evidence to verify column names, filter values, join conditions, and constraints.
Apply specific values, column names, or relationships from the evidence to your CTE.
Cross-check your CTE logic with the evidence and adjust accordingly.
If your CTE contradicts the evidence, you MUST revise it before proceeding
- If "Additional context" does not contain evidence information, proceed with standard CTE generation while following evidence-based validation principles when evidence becomes available in later steps 
"""
}

# 完整的策略手册（用于 LLM_PICK_ONCE 模式的选择阶段）
_FULL_STRATEGY_HANDBOOK = f"""
STRATEGYs:
{_STRATEGY_DESCRIPTIONS['S1']}

{_STRATEGY_DESCRIPTIONS['S2']}

{_STRATEGY_DESCRIPTIONS['S3']}

{_STRATEGY_DESCRIPTIONS['S4']}

{_STRATEGY_DESCRIPTIONS['S5']}

"""

def build_strategy_injection_text(
    mode: StrategyMode,
    fixed_strategy: Optional[str] = None,
    picked_strategy: Optional[str] = None,
    picked_strategy_thought: Optional[str] = None,
    depth: int = 0,
) -> str:
    """
    Return text to append into node.additional_context.
    - mode NONE => ""
    - FORCE_Sx => fixed policy
    - LLM_PICK_ONCE:
        depth=0 => ask model to output a STRATEGY line
        depth>0 => inject locked strategy policy (picked_strategy required)
    """
    if mode == "NONE":
        return ""

    # FORCE
    if mode.startswith("FORCE_"):
        s = mode.replace("FORCE_", "")
        # 只返回当前策略的说明，不包含其他策略
        strategy_desc = _STRATEGY_DESCRIPTIONS.get(s, "")
        return f"""
{strategy_desc}

{CTE_ACTION_level}
"""

    # LLM pick once
    if mode == "LLM_PICK_ONCE":
        # 注意：策略选择阶段使用 build_strategy_selection_prompt 单独调用
        # 这里只处理 depth>0 的情况，即策略已选择后，注入已选择的策略说明
        if depth == 0 and not picked_strategy:
            # depth=0 时策略还未选择，不应该调用此函数
            # 策略选择应该使用 build_strategy_selection_prompt 单独调用
            return ""
        
        # depth>0 时只需要已选择策略的说明
        s = picked_strategy or fixed_strategy or "S2"
        if s == "S4" and picked_strategy_thought:
            # S4使用LLM自己规划的thought作为策略描述
            strategy_desc = f"S4 Custom Strategy:\n{picked_strategy_thought}"
        else:
            strategy_desc = _STRATEGY_DESCRIPTIONS.get(s, "")

        return f"""
{strategy_desc}

{CTE_ACTION_level}
"""

    # fallback
    return ""


def build_strategy_selection_prompt(question: str, schema_info: str, additional_context: str = "") -> str:
    """
    构建策略选择的prompt（单独调用，JSON格式输出）
    
    Args:
        question: 自然语言问题
        schema_info: 数据库schema信息
        additional_context: 额外上下文
        
    Returns:
        策略选择的prompt文本
    """
    return f"""# STRATEGY SELECTION TASK

You need to select ONE strategy from S1, S2, S3, S4, or S5 for solving the following SQL generation task.

**Question**: {question}

**Database Schema**:
{schema_info}

{f"**Additional Context**: {additional_context}" if additional_context else ""}

**Available Strategies**:

{_FULL_STRATEGY_HANDBOOK}

**CRITICAL**: You MUST output your response in JSON format with a "strategy" field containing your chosen strategy (S1, S2, or S3).

Example JSON format:
```json
{{
  "thought": "I choose S1 because the question requires filtering by specific values that need verification first.",
  "strategy": "S1"
}}
```

**Output Requirements**:
- Output MUST be valid JSON wrapped in ```json code block
- The "strategy" field MUST be exactly one of: "S1", "S2", or "S3"
- Include a "thought" field explaining your choice
"""


def extract_strategy_from_json(response: str) -> Tuple[Optional[str], Optional[str]]:
    """
    从JSON响应中提取策略和thought
    
    Args:
        response: LLM的JSON响应
        
    Returns:
        (策略字符串 (S1/S2/S3), thought字符串) 或 (None, None)
    """
    import json
    import re
    
    try:
        # 1. 优先提取 ```json 代码块中的内容
        json_str = None
        json_block_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_block_match:
            json_str = json_block_match.group(1).strip()
        else:
            # 尝试提取普通代码块
            code_block_match = re.search(r'```\s*(.*?)\s*```', response, re.DOTALL)
            if code_block_match:
                potential_json = code_block_match.group(1).strip()
                if potential_json.strip().startswith('{'):
                    json_str = potential_json
        
        # 2. 如果代码块提取失败，尝试从文本中提取JSON对象
        if not json_str:
            brace_count = 0
            start_idx = -1
            for i in range(len(response) - 1, -1, -1):
                if response[i] == '}':
                    if brace_count == 0:
                        start_idx = i
                    brace_count += 1
                elif response[i] == '{':
                    brace_count -= 1
                    if brace_count == 0 and start_idx != -1:
                        json_str = response[i:start_idx + 1]
                        break
        
        if not json_str:
            return None
        
        # 3. 解析JSON
        json_str = json_str.strip()
        data = json.loads(json_str)
        
        # 4. 提取策略字段和thought
        strategy_str = data.get("strategy", "").upper().strip()
        thought_str = data.get("thought", "").strip()
        
        # 5. 验证策略
        valid_strategies = ["S1", "S2", "S3"]
        if strategy_str in valid_strategies:
            return strategy_str, thought_str if thought_str else None
        
        return None, None
        
    except json.JSONDecodeError as e:
        print(f"[策略提取] JSON解析失败: {e}")
        return None, None
    except Exception as e:
        print(f"[策略提取] 提取策略时出错: {e}")
        return None, None

