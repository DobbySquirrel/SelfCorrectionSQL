# agents/prompts.py
import json

# ==========================================
# 1. Strategy Definitions (Layer 1 Context)
# ==========================================
STRATEGY_DEFINITIONS = {
    "S1": "Strategy: Entity-First. PRIORITY: Verify values using PROBE before joining.",
    "S2": "Strategy: Relation-First. PRIORITY: Ensure JOIN paths are correct.",
    "S3": "Strategy: Evidence-First. PRIORITY: Check schema definitions.",
    "S4": "Strategy: Reactive. PRIORITY: Build logic directly.",
}

# ==========================================
# 2. Unified Master Agent Prompt (The Brain)
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

Analyze the state and execute the **ONE SINGLE NEXT STEP**.
Do NOT generate the whole query at once. Do NOT generate multiple actions.

# AVAILABLE ACTIONS

## 1. [PROBE]

**When**: Result is unexpectedly empty (0 rows), or you are unsure about values/column meanings.
**Content**: A list of tool calls.

## 2. [BUILD]

**When**: The previous step was successful, and you are ready to add the NEXT logical CTE.
**Content**: The next CTE code block (starting with `WITH` or `, cte_name AS`).
**Constraint**: Do NOT write the final `SELECT` unless this is the very last step.

## 3. [REFINE]

**When**: The last step failed (Error) or logic was wrong.
**Content**: The CORRECTED version of the last CTE.

## 4. [FINISH]

**When**: The Accumulated SQL fully answers the question.
**Content**: The final logical query (e.g., `SELECT * FROM last_cte`).

# OUTPUT FORMAT (STRICT JSON)

You must output a single valid JSON object. No markdown outside JSON.

Example:
{{
"thought": "The previous CTE failed because column 'Score' does not exist. I need to check schema.",
"action": "PROBE",
"content": "[{{"tool_id": "value_fuzzy_match", ...}}]"
}}

Example:
{{
"thought": "I have the filtered schools. Now I need to calculate the average.",
"action": "BUILD",
"content": ", avg_scores AS (SELECT ...)"
}}

# TOOL LIBRARY (For PROBE)

{tool_library_desc}
"""

# ==========================================

# 3. Probe Tool Definitions (The Toolkit)

# ==========================================

PROBE_TEMPLATES_JSON = [
{
"id": "value_fuzzy_match",
"category": "value_linking",
"description": "Fuzzy Match: Check if keyword is substring.",
"sql_template": "SELECT DISTINCT {column} FROM {table} WHERE {column} LIKE '%{keyword}%' LIMIT 5",
"parameters": ["table", "column", "keyword"]
},
{
"id": "value_existence_check",
"category": "value_linking",
"description": "Existence Check: Verify specific value exists.",
"sql_template": "SELECT 1 FROM {table} WHERE {column} = '{value}' LIMIT 1",
"parameters": ["table", "column", "value"]
},
{
"id": "value_column_ambiguity",
"category": "value_linking",
"description": "Ambiguity Resolve: Count keyword occurrence.",
"sql_template": "SELECT COUNT(*) FROM {table} WHERE {column} LIKE '%{keyword}%'",
"parameters": ["table", "column", "keyword"]
},
{
"id": "semantic_sampling",
"category": "semantic_understanding",
"description": "Semantic Sampling: Get non-null samples.",
"sql_template": "SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT {limit}",
"parameters": ["table", "column", "limit"],
"default_values": {"limit": 5}
},
{
"id": "semantic_distinct_enum",
"category": "semantic_understanding",
"description": "Enum Values: Get distinct options.",
"sql_template": "SELECT DISTINCT {column} FROM {table} LIMIT {limit}",
"parameters": ["table", "column", "limit"],
"default_values": {"limit": 50}
},
{
"id": "struct_join_validity",
"category": "structural_join",
"description": "Join Validity: Check if two tables can join.",
"sql_template": "SELECT 1 FROM {table_a} A JOIN {table_b} B ON A.{key_a} = B.{key_b} LIMIT 1",
"parameters": ["table_a", "key_a", "table_b", "key_b"]
},
{
"id": "struct_join_coverage",
"category": "structural_join",
"description": "Join Coverage: Check left join match ratio.",
"sql_template": "SELECT COUNT(A.{key_a}) as total_a, COUNT(B.{key_b}) as matched_b FROM {table_a} A LEFT JOIN {table_b} B ON A.{key_a} = B.{key_b}",
"parameters": ["table_a", "key_a", "table_b", "key_b"]
},
{
"id": "dist_min_max",
"category": "distribution_range",
"description": "Range Check: Get Min/Max.",
"sql_template": "SELECT MIN({column}) as min_val, MAX({column}) as max_val FROM {table}",
"parameters": ["table", "column"]
},
{
"id": "temporal_format_check",
"category": "temporal_logic",
"description": "Time Format: Check storage type.",
"sql_template": "SELECT {column}, TYPEOF({column}) FROM {table} WHERE {column} IS NOT NULL LIMIT 1",
"parameters": ["table", "column"]
}
]


