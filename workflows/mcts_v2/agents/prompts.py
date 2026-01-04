# agents/prompts.py
import json

PROBE_TEMPLATES_JSON = [
    {
        "id": "value_fuzzy_match",
        "category": "value_linking",
        "description": "Fuzzy Match: Check if keyword is substring.",
        "sql_template": "SELECT DISTINCT {column} FROM {table} WHERE {column} LIKE '%{keyword}%' LIMIT 5",
        "fact_template": "Values in `{table}.{column}` matching '{keyword}' include: {result_value}",
        "parameters": ["table", "column", "keyword"]
    },
    {
        "id": "value_existence_check",
        "category": "value_linking",
        "description": "Existence Check: Verify specific value exists.",
        "sql_template": "SELECT 1 FROM {table} WHERE {column} = '{value}' LIMIT 1",
        "fact_template": "Value '{value}' EXISTS in column `{table}.{column}`: {result_value}",
        "parameters": ["table", "column", "value"]
    },
    {
        "id": "value_column_ambiguity",
        "category": "value_linking",
        "description": "Ambiguity Resolve: Count keyword occurrence.",
        "sql_template": "SELECT COUNT(*) FROM {table} WHERE {column} LIKE '%{keyword}%'",
        "fact_template": "Count of records in `{table}.{column}` matching '{keyword}': {result_value}",
        "parameters": ["table", "column", "keyword"]
    },
    {
        "id": "semantic_sampling",
        "category": "semantic_understanding",
        "description": "Semantic Sampling: Get non-null samples.",
        "sql_template": "SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT {limit}",
        "fact_template": "Sample values for column `{table}.{column}` are: {result_value}",
        "parameters": ["table", "column", "limit"],
        "default_values": {"limit": 5}
    },
    {
        "id": "semantic_distinct_enum",
        "category": "semantic_understanding",
        "description": "Enum Values: Get distinct options.",
        "sql_template": "SELECT DISTINCT {column} FROM {table} LIMIT {limit}",
        "fact_template": "Distinct values for `{table}.{column}` include: {result_value}",
        "parameters": ["table", "column", "limit"],
        "default_values": {"limit": 50}
    },
    {
        "id": "struct_join_validity",
        "category": "structural_join",
        "description": "Join Validity: Check if two tables can join.",
        "sql_template": "SELECT 1 FROM {table_a} A JOIN {table_b} B ON A.{key_a} = B.{key_b} LIMIT 1",
        "fact_template": "Join valid between `{table_a}` and `{table_b}`: {result_value}",
        "parameters": ["table_a", "key_a", "table_b", "key_b"]
    },
    {
        "id": "struct_join_coverage",
        "category": "structural_join",
        "description": "Join Coverage: Check left join match ratio.",
        "sql_template": "SELECT COUNT(A.{key_a}) as total_a, COUNT(B.{key_b}) as matched_b FROM {table_a} A LEFT JOIN {table_b} B ON A.{key_a} = B.{key_b}",
        "fact_template": "Join coverage stats for `{table_a}` -> `{table_b}`: {result_value}",
        "parameters": ["table_a", "key_a", "table_b", "key_b"]
    },
    {
        "id": "dist_min_max",
        "category": "distribution_range",
        "description": "Range Check: Get Min/Max.",
        "sql_template": "SELECT MIN({column}) as min_val, MAX({column}) as max_val FROM {table}",
        "fact_template": "Range (Min/Max) for `{table}.{column}` is: {result_value}",
        "parameters": ["table", "column"]
    },
    {
        "id": "temporal_format_check",
        "category": "temporal_logic",
        "description": "Time Format: Check storage type.",
        "sql_template": "SELECT {column}, TYPEOF({column}) FROM {table} WHERE {column} IS NOT NULL LIMIT 1",
        "fact_template": "Data type sample for `{table}.{column}`: {result_value}",
        "parameters": ["table", "column"]
    }
]


# agents/prompts.py

# ==========================================
# ==========================================
# 1. 策略定义 (战术手册)
# ==========================================

def get_strategy_instruction(strategy: str = None) -> str:
    """
    根据策略是否为空生成不同的策略指令
    
    Args:
        strategy: 策略字符串 (S1/S2/S3/S4) 或 None（根节点需要选择策略）
    
    Returns:
        策略指令文本
    """
    if strategy:
        return f"""# STRATEGY-CONDITIONED POLICY (HARD CONSTRAINT)
You are running ONE rollout under a FIXED strategy: {strategy}.
You MUST follow this strategy; do NOT switch unless the switch rule triggers.
You MUST output field "strategy" exactly as "{strategy}"."""
    else:
        return """# STRATEGY-CONDITIONED POLICY (HARD CONSTRAINT)
**STRATEGY SELECTION REQUIRED** (Root Node)

You MUST select ONE strategy from S1, S2, S3, or S4 based on the question and schema.
Analyze the question and choose the most appropriate strategy:
- S1 (Entity-First): Use when you need to verify entity/value constraints first
- S2 (Relation-First): Use when join paths are uncertain
- S3 (Proactive): Use when schema is ambiguous or knowledge is thin
- S4 (Reactive): Use when schema is clear and you can try quickly

**CRITICAL**: You MUST output your response in JSON format with a "strategy" field containing your chosen strategy (S1, S2, S3, or S4).

Example JSON format:
```json
{{
  "thought": "I choose S1 because...",
  "strategy": "S1",
  "action": "PROBE",
  "content": [...]
}}
```"""

def get_task_instruction(strategy: str = None) -> str:
    """
    根据策略是否为空生成不同的任务指令
    
    Args:
        strategy: 策略字符串 (S1/S2/S3/S4) 或 None
    
    Returns:
        任务指令文本
    """
    if strategy:
        return f"Analyze the state **under strategy {strategy}** and execute the **ONE SINGLE NEXT STEP**."
    else:
        return "**SELECT a strategy** and execute the **ONE SINGLE NEXT STEP** based on that strategy."

def get_strategy_rules(strategy: str = None) -> str:
    """
    根据策略是否为空生成不同的策略规则
    
    Args:
        strategy: 策略字符串 (S1/S2/S3/S4) 或 None
    
    Returns:
        策略规则文本
    """
    if strategy:
        return f"""* Do NOT change strategy unless the switch rule triggers.
* Before finalizing, verify that your chosen action satisfies the gating rules of strategy {strategy}.
  If not, revise your action."""
    else:
        return '* You MUST output the "strategy" field with your chosen strategy (S1, S2, S3, or S4).'

# 策略定义模板（共享部分）
STRATEGY_DEFINITIONS = """
## Shared constraints (all strategies)
- Execute ONE SINGLE NEXT STEP only.
- Choose exactly ONE action: PROBE, BUILD, REFINE, or FINISH.
- If "Last Observation" indicates SQL error: action MUST be REFINE (no exceptions).
- If result is empty unexpectedly AND not explained by the question: action MUST be PROBE or REFINE (not FINISH).
- Never invent columns/tables; use schema and verified knowledge only.

## S1: Entity-First (Bottom-Up)
Intent: filter early; avoid expensive JOIN until entity/value constraints are verified.
Action gating:
- If you are about to introduce a new value/entity filter (names, categories, keywords, date ranges) that is not already in Verified Knowledge: action MUST be PROBE (value_fuzzy_match / existence_check / sampling).
- Prefer BUILD of single-table filtering CTE before any multi-table JOIN.
- Do NOT build wide JOIN unless join keys/path are already verified or obvious.

## S2: Relation-First (Top-Down)
Intent: ensure JOIN paths are correct first; build skeleton then fill values.
Action gating:
- If the join path is uncertain (multiple possible foreign keys / multiple bridge tables): action MUST be PROBE (struct_join_validity / struct_join_coverage).
- Prefer BUILD of JOIN skeleton CTE before adding strict value filters.
- You may postpone entity/value verification until after the skeleton exists.

## S3: Proactive (Evidence-First / Batch-Probe)
Intent: gather evidence upfront; then write deterministic SQL.
Action gating:
- If Verified Knowledge is thin or schema is ambiguous: action MUST be PROBE with a BATCH of 3–8 tool calls covering:
  (1) key entity columns (sampling/distinct),
  (2) join validity for the intended path,
  (3) enum/range checks for filters/aggregations.
- After evidence exists: prefer BUILD; avoid repeated PROBE unless new uncertainty appears.

## S4: Reactive (Try-Execute-Repair)
Intent: try a plausible CTE quickly; fix based on observation.
Action gating:
- If there is no prior SQL or the previous step succeeded: prefer BUILD a plausible next CTE (do not PROBE unless clearly necessary).
- If error: MUST REFINE.
- If empty unexpectedly: MUST PROBE before further BUILD.

## Switch rule (rare)
Do NOT switch strategy in this rollout unless:
- 2 consecutive errors OR 2 consecutive unexpected empty results.
If switching, explicitly state "switch: Sx -> Sy" in thought, and output the NEW strategy in "strategy".
"""

def build_strategy_section(strategy: str = None) -> str:
    """
    构建完整的策略部分
    
    Args:
        strategy: 策略字符串 (S1/S2/S3/S4) 或 None
    
    Returns:
        完整的策略部分文本
    """
    return get_strategy_instruction(strategy) + "\n" + STRATEGY_DEFINITIONS

MASTER_AGENT_PROMPT = """
# ROLE
You are an autonomous SQL Architect (CoCTE Engine).
Your goal is to solve the user's question by iteratively building, verifying, and refining SQL CTEs.

You do NOT own the full SQL program.
The system maintains the accumulated SQL.
You are responsible for producing ONLY the NEXT SQL DELTA.

# CURRENT STATE
* **Question**: {question}

* **Schema**:
{schema_info}

* **Verified Knowledge**:
{knowledge_text}

* **Accumulated SQL (Read-Only Program State)**:
```sql
{accumulated_sql}
```

* **Context Information**:
  - Last CTE Name: {last_cte_name}
  - Is Root Node: {is_root}

{execution_trace}

If a required output column contains many NULLs, do NOT fix by filtering them out.
You must merge alternative sources (e.g., COALESCE, extra JOIN) or explicitly justify why NULLs are acceptable.

# HARD OUTPUT CONTRACT (STRICT — READ CAREFULLY)

You MUST follow the SQL DELTA PROTOCOL below.
Violating this protocol makes your output INVALID.

## Allowed SQL shapes by action

### [BUILD]

You are ADDING exactly ONE new CTE.

Output MUST be:
<cte_name> AS ( ... )

**CRITICAL**: The <cte_name> you output MUST be NEW and MUST NOT duplicate any existing CTE name.

You MUST NOT output:

* the keyword WITH
* any leading or trailing commas
* multiple CTEs
* any SELECT outside the CTE

---

### [REFINE]

You are FIXING exactly ONE existing CTE — the MOST RECENT one.

Output MUST be:
<cte_name> AS ( ... )

**CRITICAL**: The <cte_name> you output MUST equal the most recent CTE name: "{last_cte_name}".

If the last CTE name is "N/A" (meaning there is no accumulated SQL yet), or if you are not sure what the most recent CTE name is, you MUST choose action PROBE (not REFINE).

You MUST NOT output:

* WITH
* commas
* multiple CTEs
* final SELECT
* unchanged earlier CTEs

If the correct fix would require changing multiple CTEs or rewriting the query,
DO NOT do it in one step. Fix ONLY the most recent CTE.

---

### [FINISH]

You are FINALIZING the query.

Output MUST be:
SELECT ...

You MUST NOT output:

* WITH
* CTE definitions
* multiple SELECT statements

---

Think in terms of **incremental diffs**, not full SQL programs.

{strategy_section}

# TASK

{task_instruction}

Rules:

* Do NOT generate the whole query at once.
* Do NOT generate multiple actions.
{strategy_rules}

# AVAILABLE ACTIONS

## 1. [PROBE]

Use PROBE when you are unsure about:

* column meanings or value distributions
* entity/value existence
* join keys or join paths
* enums, ranges, or temporal formats
* an unexpected empty result in the most recent step

Content: A list of tool calls.

---

## 2. [BUILD]

Use BUILD when the previous step succeeded and you are ready to ADD the next logical CTE.

Content MUST be exactly:
<cte_name> AS ( ... )

---

## 3. [REFINE]

Use REFINE ONLY when the most recent SQL delta caused:

* a SQL error
* logically incorrect results

Content MUST be exactly:
<cte_name> AS ( ... )

---

## 4. [FINISH]

Use FINISH ONLY when the accumulated SQL fully answers the question.

Content MUST be exactly:
SELECT ...

---

# OUTPUT FORMAT (STRICT JSON)

You MUST wrap your response in a ```json code block.

```json
{{
  "thought": "...",
  "strategy": "S1|S2|S3|S4",
  "action": "PROBE|BUILD|REFINE|FINISH",
  "content": "..."
}}
```

# EXAMPLES

### Example — S1 (Entity-First, PROBE)

```json
{{
  "thought": "Strategy S1: I am about to apply a value filter that is not yet verified. I must probe entity values first.",
  "strategy": "S1",
  "action": "PROBE",
  "content": [
    {{
      "tool_id": "semantic_distinct_enum",
      "params": {{"table": "schools", "column": "FundingType", "limit": 50}}
    }}
  ]
}}
```

---

### Example — S2 (Relation-First, PROBE)

```json
{{
  "thought": "Strategy S2: The join path is uncertain. I will verify join validity before building the skeleton.",
  "strategy": "S2",
  "action": "PROBE",
  "content": [
    {{
      "tool_id": "struct_join_validity",
      "params": {{"table_a": "schools", "key_a": "CDSCode", "table_b": "satscores", "key_b": "cds"}}
    }}
  ]
}}
```

---

### Example — S3 (Proactive, BUILD)

```json
{{
  "thought": "Strategy S3: Evidence is sufficient. I can now build the next deterministic CTE.",
  "strategy": "S3",
  "action": "BUILD",
  "content": "schools_with_scores AS (SELECT s.CDSCode, s.School, sc.AvgScrMath FROM schools s JOIN satscores sc ON s.CDSCode = sc.cds)"
}}
```

---

### Example — S4 (Reactive, BUILD)

```json
{{
  "thought": "Strategy S4: The previous step succeeded. I will add the next plausible CTE and rely on execution feedback.",
  "strategy": "S4",
  "action": "BUILD",
  "content": "filtered_schools AS (SELECT School, FundingType FROM schools_with_scores WHERE AvgScrMath > 400)"
}}
```

---

# TOOL LIBRARY (For PROBE)

{tool_library_desc}
"""
