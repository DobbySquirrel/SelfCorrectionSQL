明白了，我们要把所有分散的 Prompt 逻辑（包括 **Master Agent Router** 和原有的 CTE/Probe 模板）整合成一个统一的、包含所有内容的 `agents/prompts.py`。

以下是 **完整融合版 `agents/prompts.py**`。请直接覆盖原文件，不要分段复制，以免遗漏。

```python
# agents/prompts.py
import json

# ==========================================
# 1. Global Strategy Prompts (Layer 1)
# ==========================================
STRATEGY_DEFINITIONS = {
    "S1": "Strategy: Entity-First (Cautious). The query contains specific entities. PRIORITY: Verify values using PROBE before joining.",
    "S2": "Strategy: Relation-First (Structural). The query spans multiple tables. PRIORITY: Ensure JOIN paths are correct.",
    "S3": "Strategy: Evidence-First (Investigative). Ambiguous columns. PRIORITY: Check schema definitions.",
    "S4": "Strategy: Reactive (Efficient). Standard query. PRIORITY: Build logic directly.",
}

# ==========================================
# 2. Master Agent / Router Prompts (Unified Decision)
# ==========================================
MASTER_AGENT_PROMPT = """
# ROLE
You are an autonomous SQL Architect (CoCTE Engine).
Your goal is to solve the user's question by iteratively building, verifying, and refining SQL CTEs.

# CURRENT STATE
* **Question**: {question}
* **Schema**:
{schema_info}
* **Verified Knowledge**: {knowledge_text}
* **Accumulated SQL**:
```sql
{accumulated_sql}

```

* **Last Observation**:
{observation_section}

# TASK

Analyze the state and execute the **ONE BEST NEXT ACTION**.

## Option 1: [PROBE]

**When**: Result is unexpectedly empty, or you are unsure about values/column meanings.
**Output**: A JSON list of tool calls from the library (see below).
**Format**:
ACTION: PROBE
TOOLS:

```json
[
  {{ "tool_id": "value_fuzzy_match", "params": {{ "table": "t", "column": "c", "keyword": "k" }} }}
]

```

## Option 2: [BUILD]

**When**: The previous step was successful, and you are ready to add the next logical CTE.
**Output**: The next CTE definition (starting with `WITH` or `, cte_name AS`).
**Format**:
ACTION: BUILD
SQL:
, new_cte AS (
SELECT ...
)

## Option 3: [REFINE]

**When**: The last step failed (Error) or logic was wrong.
**Output**: A corrected version of the last CTE.
**Format**:
ACTION: REFINE
SQL:
, corrected_cte AS (
SELECT ...
)

## Option 4: [FINISH]

**When**: The Accumulated SQL fully answers the question.
**Output**: The final logic (usually a SELECT * FROM the last CTE).
**Format**:
ACTION: FINISH
SQL:
<END>

# PROBE TOOL LIBRARY (For Option 1)

{tool_library_desc}

# REQUIREMENT

* Output **ONLY** the block format above (ACTION + Content).
* Do not explain your thought process outside the block.
"""

# ==========================================

# 3. CTE Generation Prompts (Legacy / Fallback)

# ==========================================

CTE_SYSTEM_PROMPT_TEMPLATE = """You are an expert SQL engineer.
Current Strategy Mode: {strategy_desc}

**TASK**: Based on the existing SQL and execution feedback, generate the **NEXT STEP (CTE)**.

* If the previous step failed, generate a **REFINED** version.
* If the previous step succeeded, generate the **NEXT** logical CTE.
* If the answer is ready, output `<END>`.

**CORE RULES (CoCTE)**:

1. **Incremental**: Generate ONE CTE at a time.
2. **Format**:
```sql
WITH new_cte_name AS ( SELECT ... FROM previous_cte ... )

```


3. **No SELECT Output**: Do not write the final `SELECT * FROM ...` outside the WITH block.

**Database Admin Instructions (Strict Adherence)**:

1. **Column Names**: Use exact column names. Wrap names with spaces in backticks: `\`Avg Score``.
2. **Trust Facts**: Trust "Verified Knowledge" over your intuition.
3. **Aggregation**: Perform JOINs before `MAX()` or `MIN()`.
4. **Fuzzy Match**: Use `LIKE '%val%'` only if verified or prompted.
"""

CTE_USER_PROMPT_TEMPLATE = """
**Input**:

* **Question**: {question}
* **Schema**:
{schema_info}
* **Verified Knowledge**:
{knowledge_text}
* **Context**:
{context}
* **Accumulated SQL**:

```sql
{accumulated_sql}

```

* **Last Observation**:
{observation_section}

{fuzzy_match_hint}

**Depth**: {depth} / {max_depth}
**Action**: Generate the next CTE code block ONLY.
"""

FUZZY_MATCH_HINT = """

### [CRITICAL ALERT: EMPTY RESULT RECOVERY]

The previous CTE executed successfully but returned **0 rows**.
**ACTION REQUIRED**: Create a new CTE using **DIVERSE PATTERN MATCHING** (Broad match, Spacing variants, Boundary match).
"""

# ==========================================

# 4. Probing Templates & Definitions

# ==========================================

# 独立的 Probe 选择指令 (如果单独调用 Probe Generator)

PROBE_SELECTION_PROMPT = """

# ROLE

You are a Database Investigator.
The current query path is blocked or ambiguous.
Status: {status}
Error: {error}

# TASK

Select appropriate **Probing Tools** from the library below to inspect the database.
DO NOT write SQL from scratch. Select a tool ID and provide parameters.

# TOOL LIBRARY

{tool_library_desc}

# OUTPUT FORMAT (JSON)

Return a list of tool calls.

```json
[
  {{
    "tool_id": "value_fuzzy_match",
    "params": {{ "table": "schools", "column": "name", "keyword": "Columbia" }},
    "reason": "Checking if 'Columbia' refers to 'Columbia Elementary'"
  }}
]

```

"""

# 完整的 Probe 模板库

PROBE_TEMPLATES_JSON = [
{
"id": "value_fuzzy_match",
"category": "value_linking",
"description": "Fuzzy Match: Check if keyword is substring.",
"sql_template": "SELECT DISTINCT {column} FROM {table} WHERE {column} LIKE '%{keyword}%' LIMIT 5",
"parameters": ["table", "column", "keyword"],
"usage_scenario": "When entity name might differ from DB value (abbreviation, typo)."
},
{
"id": "value_existence_check",
"category": "value_linking",
"description": "Existence Check: Verify specific value exists.",
"sql_template": "SELECT 1 FROM {table} WHERE {column} = '{value}' LIMIT 1",
"parameters": ["table", "column", "value"],
"usage_scenario": "Verify a specific guess (e.g. status code)."
},
{
"id": "value_column_ambiguity",
"category": "value_linking",
"description": "Ambiguity Resolve: Count keyword occurrence.",
"sql_template": "SELECT COUNT(*) FROM {table} WHERE {column} LIKE '%{keyword}%'",
"parameters": ["table", "column", "keyword"],
"usage_scenario": "When a term (e.g. 'Apple') could be in multiple columns."
},
{
"id": "semantic_sampling",
"category": "semantic_understanding",
"description": "Semantic Sampling: Get non-null samples.",
"sql_template": "SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT {limit}",
"parameters": ["table", "column", "limit"],
"default_values": {"limit": 5},
"usage_scenario": "Understand column meaning by looking at data."
},
{
"id": "semantic_distinct_enum",
"category": "semantic_understanding",
"description": "Enum Values: Get distinct options.",
"sql_template": "SELECT DISTINCT {column} FROM {table} LIMIT {limit}",
"parameters": ["table", "column", "limit"],
"default_values": {"limit": 50},
"usage_scenario": "Get all options for categorical columns."
},
{
"id": "struct_join_validity",
"category": "structural_join",
"description": "Join Validity: Check if two tables can join.",
"sql_template": "SELECT 1 FROM {table_a} A JOIN {table_b} B ON A.{key_a} = B.{key_b} LIMIT 1",
"parameters": ["table_a", "key_a", "table_b", "key_b"],
"usage_scenario": "Verify join path before building CTE."
},
{
"id": "struct_join_coverage",
"category": "structural_join",
"description": "Join Coverage: Check left join match ratio.",
"sql_template": "SELECT COUNT(A.{key_a}) as total_a, COUNT(B.{key_b}) as matched_b FROM {table_a} A LEFT JOIN {table_b} B ON A.{key_a} = B.{key_b}",
"parameters": ["table_a", "key_a", "table_b", "key_b"],
"usage_scenario": "Decide between INNER vs LEFT join."
},
{
"id": "integrity_relationship_type",
"category": "data_integrity",
"description": "Relationship Type (1:N): Check for fan-out.",
"sql_template": "SELECT COUNT(*) FROM {table_a} A JOIN {table_b} B ON A.{key_a} = B.{key_b}",
"parameters": ["table_a", "table_b", "key_a", "key_b"],
"usage_scenario": "Warning for potential row explosion."
},
{
"id": "dist_min_max",
"category": "distribution_range",
"description": "Range Check: Get Min/Max.",
"sql_template": "SELECT MIN({column}) as min_val, MAX({column}) as max_val FROM {table}",
"parameters": ["table", "column"],
"usage_scenario": "Understand value range or time span."
},
{
"id": "dist_null_density",
"category": "distribution_range",
"description": "Null Density: Check null ratio.",
"sql_template": "SELECT CAST(COUNT({column}) AS FLOAT) / COUNT(*) as not_null_ratio FROM {table}",
"parameters": ["table", "column"],
"usage_scenario": "Assess data quality."
},
{
"id": "temporal_format_check",
"category": "temporal_logic",
"description": "Time Format: Check storage type.",
"sql_template": "SELECT {column}, TYPEOF({column}) FROM {table} WHERE {column} IS NOT NULL LIMIT 1",
"parameters": ["table", "column"],
"usage_scenario": "Check if date is string or timestamp."
},
{
"id": "temporal_range_granularity",
"category": "temporal_logic",
"description": "Time Granularity: Get time span.",
"sql_template": "SELECT MIN({column}), MAX({column}) FROM {table}",
"parameters": ["table", "column"],
"usage_scenario": "Relative time calculation (e.g. 'last month')."
},
{
"id": "integrity_uniqueness",
"category": "data_integrity",
"description": "Uniqueness: Check if column is unique.",
"sql_template": "SELECT COUNT(*) as total, COUNT(DISTINCT {column}) as distinct_cnt FROM {table}",
"parameters": ["table", "column"],
"usage_scenario": "Decide if DISTINCT/GROUP BY is needed."
}
]

```

### 确认清单

1.  **`MASTER_AGENT_PROMPT`**：包含了 4 种动作（PROBE, BUILD, REFINE, FINISH）和统一的输出格式。
2.  **`PROBE_TEMPLATES_JSON`**：包含了全部 13 个探针模板。
3.  **兼容性**：保留了 `CTE_SYSTEM_PROMPT_TEMPLATE` 等旧模板，防止其他代码报错。

现在你的 `prompts.py` 已经是全功能的了。

```