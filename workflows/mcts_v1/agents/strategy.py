# ==========================================================
# Strategy Injection (for SimpleRolloutWorkflow, no MASTER prompt)
# ==========================================================
from dataclasses import dataclass
from typing import Optional, Literal, Tuple
import threading

StrategyMode = Literal[
    "FORCE_S1", "FORCE_S2", "FORCE_S3", "FORCE_S5", "FORCE_S6", "FORCE_S7", "FORCE_S8",
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
# CTE操作类型说明（所有策略共用）
CTE_ACTION_TYPES = """
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
"""

# 各策略的示例（根据选定的策略动态注入）
_STRATEGY_EXAMPLES = {
    "S1": """
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
""",
    "S2": """
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
""",
    "S3": """
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
""",
    "S5": """
**S5 (Data Pipeline) Example:**
Question: Calculate the average order value by customer segment for orders placed in 2023, where segment is 'Premium' for customers with total orders > 10, 'Regular' for 5-10 orders, and 'New' for < 5 orders.
Schema: Orders(order_id, customer_id, order_date, amount), Customers(customer_id, customer_name)

Step 1 (raw): Extract raw data with necessary joins only, no complex calculations.
WITH raw_data AS (
    SELECT o.order_id, o.customer_id, o.order_date, o.amount, c.customer_name
    FROM Orders o
    LEFT JOIN Customers c ON o.customer_id = c.customer_id
)

Step 2 (clean): Filter, handle NULLs, and type conversions.
WITH clean_data AS (
    SELECT 
        order_id,
        customer_id,
        CAST(order_date AS DATE) AS order_date,
        COALESCE(amount, 0) AS amount,
        COALESCE(customer_name, 'Unknown') AS customer_name
    FROM raw_data
    WHERE order_date IS NOT NULL 
      AND amount IS NOT NULL
      AND STRFTIME('%Y', order_date) = '2023'
)

Step 3 (enrich): Add derived fields (customer segment based on order count).
WITH enrich_data AS (
    SELECT 
        cd.*,
        CASE 
            WHEN order_count > 10 THEN 'Premium'
            WHEN order_count >= 5 THEN 'Regular'
            ELSE 'New'
        END AS customer_segment
    FROM clean_data cd
    LEFT JOIN (
        SELECT customer_id, COUNT(*) AS order_count
        FROM clean_data
        GROUP BY customer_id
    ) oc ON cd.customer_id = oc.customer_id
)

Step 4 (agg): Perform aggregation statistics.
WITH agg_data AS (
    SELECT 
        customer_segment,
        COUNT(*) AS order_count,
        AVG(amount) AS avg_order_value,
        SUM(amount) AS total_revenue
    FROM enrich_data
    GROUP BY customer_segment
)

Step 5 (final): Final selection, sorting, and formatting.
SELECT 
    customer_segment,
    ROUND(avg_order_value, 2) AS avg_order_value,
    order_count,
    total_revenue
FROM agg_data
ORDER BY avg_order_value DESC;

<END>
""",
    "S6": """
**S6 (Business Rules Module) Example:**
Question: Identify high-risk customers based on multiple rules: (1) customers with overdue payments > 30 days, (2) customers with credit score < 600, (3) customers with transaction amount > 10000 in last month. Return customer_id, risk_level (High/Medium/Low), and which rules triggered.
Schema: Customers(customer_id, credit_score), Payments(customer_id, payment_date, due_date, amount), Transactions(customer_id, transaction_date, amount)

Step 1 (rule_1_overdue): Rule 1 - Customers with overdue payments > 30 days.
WITH rule_1_overdue AS (
    SELECT 
        p.customer_id,
        'overdue_30_days' AS rule_name,
        CASE 
            WHEN julianday('now') - julianday(p.due_date) > 30 THEN 'High'
            ELSE NULL
        END AS risk_level,
        julianday('now') - julianday(p.due_date) AS days_overdue
    FROM Payments p
    WHERE p.due_date < date('now', '-30 days')
      AND p.payment_date IS NULL
)

Step 2 (rule_2_low_credit): Rule 2 - Customers with credit score < 600.
WITH rule_2_low_credit AS (
    SELECT 
        c.customer_id,
        'low_credit_score' AS rule_name,
        CASE 
            WHEN c.credit_score < 600 THEN 'High'
            WHEN c.credit_score < 650 THEN 'Medium'
            ELSE NULL
        END AS risk_level,
        c.credit_score
    FROM Customers c
    WHERE c.credit_score < 650
)

Step 3 (rule_3_large_transaction): Rule 3 - Customers with transaction amount > 10000 in last month.
WITH rule_3_large_transaction AS (
    SELECT 
        t.customer_id,
        'large_transaction' AS rule_name,
        CASE 
            WHEN t.amount > 10000 THEN 'High'
            ELSE NULL
        END AS risk_level,
        t.amount AS transaction_amount
    FROM Transactions t
    WHERE t.transaction_date >= date('now', '-1 month')
      AND t.amount > 10000
)

Step 4 (merged_rules): Merge all rule results using UNION and aggregate by customer.
WITH merged_rules AS (
    SELECT customer_id, rule_name, risk_level, days_overdue AS rule_value
    FROM rule_1_overdue
    WHERE risk_level IS NOT NULL
    
    UNION ALL
    
    SELECT customer_id, rule_name, risk_level, credit_score AS rule_value
    FROM rule_2_low_credit
    WHERE risk_level IS NOT NULL
    
    UNION ALL
    
    SELECT customer_id, rule_name, risk_level, transaction_amount AS rule_value
    FROM rule_3_large_transaction
    WHERE risk_level IS NOT NULL
),
customer_risk_summary AS (
    SELECT 
        customer_id,
        MAX(CASE WHEN risk_level = 'High' THEN 1 ELSE 0 END) AS has_high_risk,
        MAX(CASE WHEN risk_level = 'Medium' THEN 1 ELSE 0 END) AS has_medium_risk,
        GROUP_CONCAT(rule_name, ', ') AS triggered_rules
    FROM merged_rules
    GROUP BY customer_id
)

Step 5 (final): Final selection with risk level determination.
SELECT 
    crs.customer_id,
    CASE 
        WHEN crs.has_high_risk = 1 THEN 'High'
        WHEN crs.has_medium_risk = 1 THEN 'Medium'
        ELSE 'Low'
    END AS risk_level,
    crs.triggered_rules
FROM customer_risk_summary crs
ORDER BY 
    CASE crs.risk_level
        WHEN 'High' THEN 1
        WHEN 'Medium' THEN 2
        ELSE 3
    END,
    crs.customer_id;

<END>
""",
    "S7": """
**S7 (Grain/Key-Based) Example:**
Question: Calculate total revenue and order count per customer per month, including customer name and region. The result should be at (customer_id, year_month) grain.
Schema: Orders(order_id, customer_id, order_date, amount), Customers(customer_id, customer_name, region_id), Regions(region_id, region_name)

Step 1 (base_keys): Determine the final result grain (customer_id, year_month).
WITH base_keys AS (
    SELECT DISTINCT
        o.customer_id,
        STRFTIME('%Y-%m', o.order_date) AS year_month
    FROM Orders o
    WHERE o.order_date IS NOT NULL
)

Step 2 (fact_orders_at_grain): Aggregate Orders fact table to the target grain (customer_id, year_month).
WITH fact_orders_at_grain AS (
    SELECT 
        o.customer_id,
        STRFTIME('%Y-%m', o.order_date) AS year_month,
        COUNT(DISTINCT o.order_id) AS order_count,
        SUM(o.amount) AS total_revenue,
        AVG(o.amount) AS avg_order_amount
    FROM Orders o
    WHERE o.order_date IS NOT NULL
    GROUP BY o.customer_id, STRFTIME('%Y-%m', o.order_date)
)

Step 3 (dim_customers): Customer dimension table (already at customer_id grain, no aggregation needed).
WITH dim_customers AS (
    SELECT 
        customer_id,
        customer_name,
        region_id
    FROM Customers
)

Step 4 (dim_regions): Region dimension table (already at region_id grain, no aggregation needed).
WITH dim_regions AS (
    SELECT 
        region_id,
        region_name
    FROM Regions
)

Step 5 (final): Join all tables at the same grain (customer_id, year_month).
SELECT 
    bk.customer_id,
    bk.year_month,
    dc.customer_name,
    dr.region_name,
    fog.order_count,
    fog.total_revenue,
    fog.avg_order_amount
FROM base_keys bk
LEFT JOIN fact_orders_at_grain fog 
    ON bk.customer_id = fog.customer_id 
    AND bk.year_month = fog.year_month
LEFT JOIN dim_customers dc 
    ON bk.customer_id = dc.customer_id
LEFT JOIN dim_regions dr 
    ON dc.region_id = dr.region_id
ORDER BY bk.customer_id, bk.year_month;

<END>
""",
    "S8": """
**S8 (Audit/Validation) Example:**
Question: Calculate monthly revenue per customer, including customer name. Ensure no data loss or duplication occurs.
Schema: Orders(order_id, customer_id, order_date, amount), Customers(customer_id, customer_name)

Step 1 (raw): Extract raw data.
WITH raw AS (
    SELECT 
        o.order_id,
        o.customer_id,
        o.order_date,
        o.amount,
        c.customer_name
    FROM Orders o
    LEFT JOIN Customers c ON o.customer_id = c.customer_id
)

Step 2 (raw_check): Check raw data quality - count rows, distinct IDs, NULL values.
WITH raw_check AS (
    SELECT 
        'raw' AS step_name,
        COUNT(*) AS total_rows,
        COUNT(DISTINCT order_id) AS distinct_order_ids,
        COUNT(DISTINCT customer_id) AS distinct_customer_ids,
        SUM(CASE WHEN order_id IS NULL THEN 1 ELSE 0 END) AS null_order_ids,
        SUM(CASE WHEN customer_id IS NULL THEN 1 ELSE 0 END) AS null_customer_ids,
        SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) AS null_amounts,
        SUM(CASE WHEN order_date IS NULL THEN 1 ELSE 0 END) AS null_dates
    FROM raw
)

Step 3 (clean): Filter and clean data.
WITH clean AS (
    SELECT 
        order_id,
        customer_id,
        STRFTIME('%Y-%m', order_date) AS year_month,
        amount,
        customer_name
    FROM raw
    WHERE order_date IS NOT NULL
      AND customer_id IS NOT NULL
      AND amount IS NOT NULL
)

Step 4 (clean_check): Check clean data - verify row count changes, distinct counts, NULL handling.
WITH clean_check AS (
    SELECT 
        'clean' AS step_name,
        COUNT(*) AS total_rows,
        COUNT(DISTINCT order_id) AS distinct_order_ids,
        COUNT(DISTINCT customer_id) AS distinct_customer_ids,
        COUNT(DISTINCT year_month) AS distinct_months,
        SUM(CASE WHEN order_id IS NULL THEN 1 ELSE 0 END) AS null_order_ids,
        SUM(CASE WHEN customer_id IS NULL THEN 1 ELSE 0 END) AS null_customer_ids,
        SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) AS null_amounts,
        SUM(amount) AS total_amount
    FROM clean
)

Step 5 (agg): Aggregate to monthly revenue per customer.
WITH agg AS (
    SELECT 
        customer_id,
        year_month,
        customer_name,
        COUNT(DISTINCT order_id) AS order_count,
        SUM(amount) AS monthly_revenue,
        AVG(amount) AS avg_order_amount
    FROM clean
    GROUP BY customer_id, year_month, customer_name
)

Step 6 (agg_check): Check aggregation - verify row count reduction, revenue totals, no duplicates.
WITH agg_check AS (
    SELECT 
        'agg' AS step_name,
        COUNT(*) AS total_rows,
        COUNT(DISTINCT customer_id) AS distinct_customer_ids,
        COUNT(DISTINCT year_month) AS distinct_months,
        SUM(order_count) AS total_order_count,
        SUM(monthly_revenue) AS total_revenue,
        SUM(CASE WHEN customer_id IS NULL THEN 1 ELSE 0 END) AS null_customer_ids,
        SUM(CASE WHEN monthly_revenue IS NULL THEN 1 ELSE 0 END) AS null_revenues
    FROM agg
)

Step 7 (final): Final selection.
SELECT 
    customer_id,
    year_month,
    customer_name,
    order_count,
    monthly_revenue,
    avg_order_amount
FROM agg
ORDER BY customer_id, year_month;

-- Audit check: Uncomment to view all check CTEs
-- SELECT * FROM raw_check;
-- SELECT * FROM clean_check;
-- SELECT * FROM agg_check;

<END>
"""
}

# ---- 策略手册（给 CTE/SQL 生成器看的文本）----
_STRATEGY_DESCRIPTIONS = {
    "S1": """S1 Entity-First:
- When introducing a new filter, first apply simple filters on a single table before performing any joins.
- Use explicit `WHERE` conditions on known columns and avoid assumptions about unknown values.
- Minimize the use of wide or complex joins early in the query construction to keep it simple and focused.""",
    
    "S2": """S2 Relation-First:
- Prioritize ensuring the correctness of join paths (using foreign key hints if available).
- Build the join skeleton first, ensuring relationships are correct, then apply filters after the join structure is in place.""",
    
    "S3": """S3 Evidence-Based:
- Always validate CTE fields, filter values, and join conditions against the provided dataset evidence.
- Use evidence to confirm column names, filter values, and relationships.
- If the evidence suggests a different approach, adjust your CTEs accordingly.
- If your CTE contradicts the evidence, revise it before proceeding, or continue with standard CTE generation if no evidence is available at the time.""",
    
    "S5": """S5 Data Pipeline:
- Follow a strict data flow pipeline: raw → clean → enrich → agg → final
- raw: Only extract data and perform necessary joins, no complex calculations
- clean: Filter rows, remove duplicates, handle type conversions, process NULL values
- enrich: Add dimensions, create derived fields (CASE WHEN, classifications, labels)
- agg: Perform aggregation statistics (GROUP BY, COUNT, SUM, AVG, etc.)
- final: Only do final SELECT, sorting (ORDER BY), and LIMIT
- Each layer should be independently queryable (e.g., SELECT * FROM clean LIMIT 100)
- Best for: ETL/Reporting/Metric calculations, complex field logic, multi-step transformations""",
    
    "S6": """S6 Business Rules Module:
- Split business logic into independent rule modules: each rule as a separate CTE
- Naming convention: rule_1_xxx, rule_2_yyy, rule_3_zzz (descriptive names for each rule)
- merged_rules: Combine rule results using JOIN, UNION, or COALESCE operations
- final: Final aggregation and selection
- Each rule CTE should be independently testable (e.g., SELECT * FROM rule_1_xxx LIMIT 100)
- When an error is found, you can quickly identify which rule produced incorrect output
- Best for: Risk control, recommendation systems, review/audit systems, customer segmentation, complex multi-rule logic""",
    
    "S7": """S7 Grain/Key-Based:
- First determine the final result grain (base_keys): identify the primary key set for the final result (e.g., user_id, order_id, date+user_id)
- Aggregate each fact table to the target grain before joining: fact_a_at_grain, fact_b_at_grain
- Dimension tables (dim_x) can remain at their natural grain or be aggregated to match
- final: Join all tables at the same grain to avoid data explosion
- This approach greatly reduces duplicate rows caused by joins and makes metrics more stable
- Each fact table CTE should be independently testable (e.g., SELECT * FROM fact_orders_at_grain LIMIT 100)
- Best for: Multi-table joins that cause data explosion, complex fact tables with different grains, metric calculations that need stable aggregation""",
    
    "S8": """S8 Audit/Validation:
- Add a *_check CTE after each critical CTE to validate data quality
- Each check CTE should calculate: row count (COUNT(*)), distinct count (COUNT(DISTINCT key)), NULL counts for critical fields
- Naming convention: raw_check, clean_check, join_check, agg_check (matching the corresponding CTE name)
- Check CTEs help identify where data explosion or data loss occurs (e.g., row count suddenly increases after join, or decreases unexpectedly)
- You can temporarily run SELECT * FROM *_check to view differences during development (remove before production)
- Best for: Report metrics, data quality validation, detecting missing data or duplicates, scenarios where errors are hard to spot but critical"""
}


# 完整的策略手册（用于 LLM_PICK_ONCE 模式的选择阶段）
_FULL_STRATEGY_HANDBOOK = f"""
STRATEGYs:
{_STRATEGY_DESCRIPTIONS['S1']}

{_STRATEGY_DESCRIPTIONS['S2']}

{_STRATEGY_DESCRIPTIONS['S3']}

{_STRATEGY_DESCRIPTIONS['S5']}

{_STRATEGY_DESCRIPTIONS['S6']}

{_STRATEGY_DESCRIPTIONS['S7']}

{_STRATEGY_DESCRIPTIONS['S8']}
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
        strategy_example = _STRATEGY_EXAMPLES.get(s, "")
        return f"""

{CTE_ACTION_TYPES}
{strategy_desc}
{strategy_example}
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
        strategy_example = _STRATEGY_EXAMPLES.get(s, "")
        
        
        return f"""
{CTE_ACTION_TYPES}

{strategy_desc}


{strategy_example}
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

You need to select ONE strategy from S1, S2, S3, S5, S6, S7, or S8 for solving the following SQL generation task.

**Question**: {question}

**Database Schema**:
{schema_info}

{f"**Additional Context**: {additional_context}" if additional_context else ""}

{CTE_ACTION_TYPES}

**Available Strategies**:

{_FULL_STRATEGY_HANDBOOK}

**Strategy Examples** (to help you understand how each strategy works):



{_STRATEGY_EXAMPLES['S1']}

{_STRATEGY_EXAMPLES['S2']}

{_STRATEGY_EXAMPLES['S3']}

{_STRATEGY_EXAMPLES['S5']}

{_STRATEGY_EXAMPLES['S6']}

{_STRATEGY_EXAMPLES['S7']}

{_STRATEGY_EXAMPLES['S8']}

**CRITICAL**: You MUST output your response in JSON format with a "strategy" field containing your chosen strategy (S1, S2, S3, S5, S6, S7, or S8).

Example JSON format:
```json
{{
  "thought": "I choose S1 because the question requires filtering by specific values that need verification first.",
  "strategy": "S1"
}}
```

**Output Requirements**:
- Output MUST be valid JSON wrapped in ```json code block
- The "strategy" field MUST be exactly one of: "S1", "S2", "S3", "S5", "S6", "S7", or "S8"
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
        valid_strategies = ["S1", "S2", "S3", "S5", "S6", "S7", "S8"]
        if strategy_str in valid_strategies:
            return strategy_str, thought_str if thought_str else None
        
        return None, None
        
    except json.JSONDecodeError as e:
        print(f"[策略提取] JSON解析失败: {e}")
        return None, None
    except Exception as e:
        print(f"[策略提取] 提取策略时出错: {e}")
        return None, None


def select_strategy_with_llm(
    cte_agent,
    question: str,
    schema_info: str,
    additional_context: str = "",
    timeout_s: float = 120.0,
    default_strategy: str = "S2"
) -> Tuple[Optional[str], Optional[str]]:
    """
    使用LLM选择策略（LLM_PICK_ONCE模式）
    
    Args:
        cte_agent: CTE生成器的agent对象（用于调用LLM）
        question: 自然语言问题
        schema_info: 数据库schema信息
        additional_context: 额外上下文
        timeout_s: LLM调用超时时间（秒），默认120秒
        default_strategy: 默认策略（当选择失败时使用），默认"S2"
        
    Returns:
        (策略字符串 (S1/S2/S3/S5/S6/S7/S8), thought字符串) 或 (default_strategy, None)
    """
    print(f"\n[策略选择] ========== 开始单独选择策略 ==========")
    
    # 构建策略选择prompt
    strategy_prompt = build_strategy_selection_prompt(
        question=question,
        schema_info=schema_info,
        additional_context=additional_context
    )
    
    print(f"[策略选择] Strategy Selection Prompt:")
    print(f"{'='*80}")
    print(strategy_prompt)
    print(f"{'='*80}")
    
    # 调用LLM选择策略
    strategy_messages = [
        {
            "role": "system",
            "content": "You are a strategy selection assistant. Your task is to analyze the SQL generation task and select the most appropriate strategy."
        },
        {
            "role": "user",
            "content": strategy_prompt
        }
    ]
    
    # 使用线程包装，添加超时保护
    strategy_response = None
    strategy_response_error = None
    
    def call_llm():
        nonlocal strategy_response, strategy_response_error
        try:
            strategy_response = cte_agent.generate_reply(strategy_messages)
        except Exception as e:
            strategy_response_error = str(e)
    
    # 使用线程包装，添加超时保护
    llm_thread = threading.Thread(target=call_llm)
    llm_thread.daemon = True
    llm_thread.start()
    llm_thread.join(timeout=timeout_s)
    
    if llm_thread.is_alive():
        # 线程仍在运行，说明超时了
        print(f"\n⚠️ [策略选择] LLM调用超时（>{timeout_s}秒），使用默认策略 {default_strategy}")
        strategy_response = None
    elif strategy_response_error:
        print(f"\n⚠️ [策略选择] LLM调用出错: {strategy_response_error}，使用默认策略 {default_strategy}")
        strategy_response = None
    
    if strategy_response:
        print(f"\n[策略选择] LLM响应:")
        print(f"{'='*80}")
        print(strategy_response)
        print(f"{'='*80}")
    else:
        print(f"\n[策略选择] 未获得LLM响应，使用默认策略")
    
    # 处理autogen返回的不同类型：如果是字典，提取content字段
    if isinstance(strategy_response, dict):
        strategy_response_text = strategy_response.get('content', '') or str(strategy_response)
    elif not isinstance(strategy_response, str):
        strategy_response_text = str(strategy_response)
    else:
        strategy_response_text = strategy_response
    
    # 从JSON响应中提取策略和thought
    picked_strategy, picked_strategy_thought = extract_strategy_from_json(strategy_response_text)
    
    if picked_strategy and picked_strategy in ("S1", "S2", "S3", "S5", "S6", "S7", "S8"):
        print(f"\n✅ [策略选择] 成功选择策略: {picked_strategy}")
        if picked_strategy_thought:
            print(f"[策略选择] 策略规划: {picked_strategy_thought}")
        print(f"[策略选择] ========== 策略选择完成 ==========\n")
        return picked_strategy, picked_strategy_thought
    else:
        print(f"\n⚠️ [策略选择] 未能从JSON中提取到有效策略，使用默认策略 {default_strategy}")
        print(f"[策略选择] ========== 策略选择完成 ==========\n")
        return default_strategy, None

