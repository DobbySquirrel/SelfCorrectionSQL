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


GLOBAL_STRATEGY_CONFIG = StrategyConfig(mode="FORCE_S2", lock_after_picked=True)
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

Examples:

**S1 (Entity-First) Example:**
Question: List all patients who were born in 1937 whose total cholesterol was beyond the normal range.
Schema: Patient(ID, Birthday), Laboratory(ID, T_CHO)
Evidence: The patients' birth year must be 1937. The patients' total cholesterol (T-CHO) must be greater than or equal to 250.

Step 1 (Column Selection): Select the columns from Patient and Laboratory tables to identify relevant fields.
WITH cte_born_1937 AS (
    SELECT p.ID, p.Birthday
    FROM Patient p
    WHERE STRFTIME('%Y', p.Birthday) = '1937'
)

Step 2 (Row Filtering): Filter the patients based on the cholesterol level being greater than or equal to 250.
WITH cte_tcho_above_normal AS (
    SELECT p.ID, l.T_CHO
    FROM cte_born_1937 p
    LEFT JOIN Laboratory l ON p.ID = l.ID
    WHERE l.T_CHO >= 250
)

Step 3 (Aggregation): Count the number of patients whose cholesterol exceeds the normal range.
WITH final_results AS (
    SELECT p.ID, l.T_CHO
    FROM cte_tcho_above_normal p
)
SELECT COUNT(*) AS total_patients FROM final_results;

<END>

**S2 (Relation-First) Example:**
Question: Among the cards with converted mana cost higher than 5 in the set Coldsnap, how many of them have unknown power?
Schema: cards(id, convertedManaCost, power, setCode), sets(code, name)
Evidence: The card set must be 'Coldsnap'. The converted mana cost must be greater than 5. The power of the card must be unknown (either '*' or NULL).

Step 1 (Table Join): Join the cards table with the sets table to get the set names.
WITH cte1 AS (
    SELECT c.id, c.convertedManaCost, c.power, s.name AS setName
    FROM cards c
    INNER JOIN sets s ON c.setCode = s.code
)

Step 2 (Row Filtering): Filter cards by set name and converted mana cost greater than 5.
WITH cte2 AS (
    SELECT id, convertedManaCost, power, setName
    FROM cte1
    WHERE setName = 'Coldsnap' AND convertedManaCost > 5
)

Step 3 (Row Filtering): Filter cards with unknown power (either '*' or NULL).
WITH cte3 AS (
    SELECT id, power
    FROM cte2
    WHERE power = '*' OR power IS NULL
)

Step 4 (Aggregation): Count the number of cards with unknown power.
WITH final_count AS (
    SELECT COUNT(*) AS answer
    FROM cte3
)
SELECT answer FROM final_count;

<END>

**S3 (Evidence-Based) Example:**
Question: What was Francesco Parravicini's potential on 2010/8/30?
Schema: Player(player_api_id, player_name), Player_Attributes(player_api_id, potential, date)
Evidence: The player's name must be 'Francesco Parravicini'. The date must be '2010-08-30 00:00:00'.

Step 1 (Column Selection): Select the relevant player columns from the Player table.
WITH cte_player AS (
    SELECT player_api_id, player_name
    FROM Player
    WHERE player_name = 'Francesco Parravicini'
)

Step 2 (Row Filtering): Filter Player_Attributes by date (using the evidence: date = '2010-08-30 00:00:00').
WITH cte_potential AS (
    SELECT player_api_id, potential, date
    FROM Player_Attributes
    WHERE date = '2010-08-30 00:00:00'
)

Step 3 (Table Join): Join the cte_player and cte_potential CTEs based on player_api_id.
WITH cte_final AS (
    SELECT p.player_name, pa.potential
    FROM cte_player p
    INNER JOIN cte_potential pa ON p.player_api_id = pa.player_api_id
)

Step 4 (Aggregation): Extract Francesco Parravicini's potential value.
WITH final_result AS (
    SELECT potential
    FROM cte_final
)
SELECT potential FROM final_result;

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
    
    "S3": """S3 Evidence-Based:
- When creating a CTE, ALWAYS validate it against the evidence fields provided in the dataset.
- Use the evidence information to verify column names, filter values, join conditions, and data constraints.
- If the evidence contains specific values, column names, or relationships, incorporate them into your CTE.
- Cross-reference your CTE logic with the evidence to ensure correctness before proceeding.
- If evidence suggests a different approach, adjust your CTE accordingly.
- If your CTE contradicts the evidence, you MUST revise it before proceeding.
- If "Additional context" does not contain evidence information, proceed with standard CTE generation while following evidence-based validation principles when evidence becomes available in later steps."""
}

# 完整的策略手册（用于 LLM_PICK_ONCE 模式的选择阶段）
_FULL_STRATEGY_HANDBOOK = f"""
STRATEGYs:
{_STRATEGY_DESCRIPTIONS['S1']}

{_STRATEGY_DESCRIPTIONS['S2']}

{_STRATEGY_DESCRIPTIONS['S3']}
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
        strategy_desc = _STRATEGY_DESCRIPTIONS.get(s, "")
        
        # 对于 S3，添加额外的 evidence 验证说明
        if s == "S3":
            return f"""
{strategy_desc}

**⚠️ CRITICAL: Evidence Validation (S3 Strategy)**
- If the "Additional context" field contains evidence information from the dataset (look for text starting with "Evidence from other related questions" or similar patterns), you MUST use it to validate your CTE:
  * Check if column names in your CTE match those mentioned in the evidence
  * Verify filter values against evidence-provided values
  * Ensure join conditions align with evidence-suggested relationships
  * Cross-reference data constraints and constraints mentioned in evidence
  * If your CTE contradicts the evidence, you MUST revise it before proceeding
  * The evidence is a critical validation source - do not ignore it

{CTE_ACTION_level}
"""
        
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

You need to select ONE strategy from S1, S2, or S3 for solving the following SQL generation task.

Note: S3 (Evidence-Based) is recommended when evidence fields are available in the dataset, as it uses evidence to validate CTEs during generation.

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

