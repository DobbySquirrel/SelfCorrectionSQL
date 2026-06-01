# Refine 阶段对比：Alpha-SQL-2 vs mcts_v4

## 一、Alpha-SQL-2 refine（SQL Revision）传入内容

### 1.1 入口与模板

- **类**：`alphasql/algorithm/mcts/mcts_action.py` 的 `SQLRevisionAction`
- **模板**：`alphasql/templates/sql_revision.txt`
- **占位符**：`{SCHEMA_CONTEXT}`, `{QUESTION}`, `{HINT}`

### 1.2 实际传入（create_children_nodes 里拼的 prompt）

| 参数 | 内容 |
|------|------|
| **QUESTION** | 原问题（或 rephrased question） |
| **HINT** | 原始 hint + **"Here are my previous thoughts:\n" + previous_thoughts** |
| **SCHEMA_CONTEXT** | 表结构 DDL（selected_schema_context 或 schema_context） |

### 1.3 previous_thoughts 的构成（关键）

对**路径上已有节点**按类型拼接：

- **IdentifyColumnValues**：`"Identify column values: {identified_column_values}\n"`
- **IdentifyColumnFunctions**：`"Identify column functions: {identified_column_functions}\n"`
- **SQLGeneration**：  
  `"SQL generation: {path_node.sql_query}\nSQL execution result:\n{sql_execution_result_str}\n"`  
  其中 `sql_execution_result_str = format_execution_result(sql_execution_result)`：
  - **执行成功**：表格形式（列名 + 前几行数据，row_limit=3）
  - **执行失败**：`result.error_message`（错误信息）

因此 Alpha-SQL 的 refine 显式包含：

- **上一次执行的 SQL**（path 上 SQL generation 节点的 sql_query）
- **执行结果**：成功=结果表格，失败=错误信息

### 1.4 模板里的步骤（sql_revision.txt）

1. Review Database Schema  
2. Analyze Query Requirements：**Original Question**、**Hint**、**Executed SQL Query**、**Execution Result**  
3. Correct the Query：根据上述信息修正

模板本身没有 `{EXECUTED_SQL}` 等占位符，这些内容都通过 **HINT 里的 previous_thoughts** 传入。

---

## 二、mcts_v4 的 refine 现状

### 2.1 两处“refine”

| 场景 | 位置 | 传入内容 |
|------|------|----------|
| **列名修复**（CTE 执行报错，如 no such column） | `sql_executor._build_column_fix_prompt` → `_regenerate_cte_with_fix` | original_cte、error_msg、**suggestions**（错误列→推荐列名+表+相似度）、question、schema_info。**没有**执行结果表格或行数摘要。 |
| **节点内迭代**（M_verify 不通过） | `_expand_leaf_v4` → `failed_attempts_for_gen` → CTE 生成器 `failed_attempts` | 每条：`{"cte": cte_text, "error": reason}`，其中 **reason 来自 CTESufficientChecker.verify** 的文本理由（“不足以解决当前子问题”等）。**没有**执行结果摘要（行数、前几行等）。 |

### 2.2 CTE 生成器里 failed_attempts 的展示

- `cte_generator.py` 里 `failed_attempts_section`：  
  `**Failed Attempt #n:**` + 该条 CTE（若有）+ `**Error:** {error}`  
- 支持 column_hint、duplicate_cte_name、requires_full_cte_chain 等扩展，但 **error 始终是“错误/理由”文本**，没有“执行出了什么结果”的摘要。

---

## 三、差异小结

| 项目 | Alpha-SQL refine | mcts_v4 refine |
|------|------------------|-----------------|
| **执行失败的 case** | 传 error_message | 列名修复：传 error_msg + 推荐列名（更好）；节点内：只传 M_verify 的 reason |
| **执行成功但结果不对的 case** | 传 **format_execution_result**（表格/前几行） | 只传 M_verify 的 **reason**，不传执行结果摘要（行数、样例行） |
| **结构化步骤** | 模板里明确 Procedure：Review schema → Analyze（含 Executed SQL + Result）→ Correct | 自由文本 + Failed Attempt #n，未强调“根据执行结果修正” |

---

## 四、可优化方向（mcts_v4）

1. **在执行成功但 M_verify 不通过时，把执行结果摘要也放进 refine**
   - 在 `_expand_leaf_v4` 里，往 `failed_attempts_for_gen` 的项里增加字段，例如：
     - `result_summary`：已有 "Execution OK" / "行数 N" 等，可一并给 CTE 生成器；
     - 或 `execution_preview`：前 1–3 行字符串（类似 Alpha-SQL 的 row_limit=3）。
   - 在 `cte_generator` 的 `failed_attempts_section` 里：若存在 `result_summary` / `execution_preview`，则显式写出「执行结果（或摘要）：xxx」，让模型知道“当前 CTE 跑出了什么”，便于针对性修正。

2. **Refine 提示结构化（对齐 Alpha-SQL 的 Procedure）**
   - 在 `failed_attempts_section` 或单独一块中，写清步骤：  
     1）根据 Schema 与 Question/Hint；2）结合 **已执行的 CTE** 与 **执行结果/错误** 分析问题；3）给出修正后的 CTE。  
   - 这样和 Alpha-SQL 的 “Executed SQL Query + Execution Result → Correct” 一致，便于模型遵循。

3. **列名修复侧**
   - 已有 error_msg + suggestions，优于 Alpha-SQL。  
   - 若希望进一步对齐 Alpha-SQL，可考虑在列名修复时也传入「当前 CTE 执行错误时的结果摘要」（若有），一般列名错误时可能没有结果，保持现状也可。

4. **可选：执行结果长度控制**
   - Alpha-SQL 用 `format_execution_result(..., row_limit=3, val_length_limit=100)` 限制长度；  
   - mcts_v4 若增加执行结果摘要，建议同样做行数/长度截断，避免 prompt 过长。

---

## 五、实现时可改的文件（参考）

- **mcts_v4**  
  - `mcts_workflow.py`：`_expand_leaf_v4` 里构造 `failed_attempts_for_gen` 时，为每条增加 `result_summary` 或 `execution_preview`（从当前已有的 `exec_res` / `result_summary` 取或再算一次）。  
  - `agents/cte_generator.py`：在 `failed_attempts_section` 中若存在执行结果摘要，则输出「**Execution result (or summary):** ...」，并可选地加一句“请根据上述执行结果/错误修正 CTE”。

这样 mcts_v4 的 refine 在“传入内容”和“步骤提示”上就与 Alpha-SQL 的 refine 对齐，同时保留你现有的列名建议和 M_verify 理由。
