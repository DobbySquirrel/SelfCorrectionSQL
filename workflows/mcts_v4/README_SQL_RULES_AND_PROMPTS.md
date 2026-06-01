# MCTS v4：SQL 规则与 Prompt 说明（Alpha-SQL / mcts_v1 对照）

本 README 对照 **Alpha-SQL-2.2.4** 与 **mcts_v1** 中与「数据集 SQL」相关的规则与 prompt，并说明在 **mcts_v4** 中的使用方式。

---

## 一、Alpha-SQL 中的 SQL 规则与 Prompt

### 1.1 模板位置

- 根目录：`SelfCorrectionSQL/Alpha-SQL-2.2.4/`
- 模板目录：`alphasql/templates/`
- 入口：`alphasql/llm_call/prompt_factory.py` 按模板名加载 `.txt`，用 `get_prompt(template_name, template_args)` 填充占位符。

### 1.2 与「最终 SQL」直接相关的模板

| 模板文件 | 用途 | 关键内容 |
|----------|------|----------|
| `sql_generation.txt` | 根据 schema + question + hint 生成 SQL | **Database admin instructions**（13 条）+ Recursive Divide-and-Conquer 流程 |
| `sql_revision.txt` | 根据执行错误/空结果修订 SQL | 分析 schema、原问题、hint、已执行 SQL、执行结果后修正，无额外规则列表 |

### 1.3 Alpha-SQL「Database admin instructions」（sql_generation.txt）

以下为 Alpha-SQL 中**明确写进 prompt 的 SQL 规则**（面向 Text-to-SQL 数据集，保证语法与结果一致）：

1. **SELECT Clause**：只选问题中提到的列，避免多余列。
2. **Aggregation (MAX/MIN)**：先做 JOIN，再使用 MAX()/MIN()。
3. **ORDER BY with Distinct Values**：在 `ORDER BY <column> ASC|DESC` 前用 `GROUP BY <column>` 保证 distinct。
4. **Handling NULLs**：可能为 NULL 的列用 JOIN 或 `WHERE <column> IS NOT NULL`。
5. **FROM/JOIN Clauses**：只包含回答问题所必需的表。
6. **Strictly Follow Hints**：严格遵循题目给出的 hint。
7. **Thorough Question Analysis**：覆盖问题中所有条件。
8. **DISTINCT Keyword**：问题要求唯一值（如 ID、URL）时使用 `SELECT DISTINCT`。
9. **Column Selection**：相似列在不同表时，结合列描述与 hint 选对列。
10. **String Concatenation**：禁止在 SELECT 中用 `\|\| ' ' \|\|` 等拼接字符串。
11. **SQLite Functions Only**：仅使用 SQLite 支持的函数。
12. **Date Processing**：用 `STRFTIME()` 做日期处理（如提取年份）。
13. **Schema Syntax**：表名/列名含空格时用 \`table_name\`.\`column_name\`。
14. **JOIN Preference**：优先 INNER JOIN，不用 CROSS JOIN 或 LEFT/RIGHT JOIN；优先 INNER JOIN 而非嵌套 SELECT。

生成流程：Recursive Divide-and-Conquer（分解子问题 → 子问题伪 SQL → 组合为最终 SQL），并尽量用 INNER JOIN 简化。

---

## 二、mcts_v1 中的 SQL / CTE 规则与 Prompt

### 2.1 完整 SQL 生成（CompleteSQLGenerator）

- 文件：`workflows/mcts_v1/agents/complete_sql_generator.py`
- 方法：`_get_sql_system_message()` 返回 system message，内含 **Database Admin Instructions (Must Strictly Adhere)**，共 **15 条**。

与 Alpha-SQL 的对应与扩展：

- Alpha-SQL 的 1–7、9–12、14 在 mcts_v1 中都有对应（SELECT 只选问题要求的列、MAX/MIN 前 JOIN、ORDER BY 前 GROUP BY、NULL、FROM 只必要表、hint、全面分析、DISTINCT、列选择、禁止字符串拼接、SQLite、STRFTIME、JOIN 优先）。
- mcts_v1 额外/更细的规则包括：
  - **NO Over-Selection / NO Under-Selection**：只选问题明确要求的列，且若问题隐含多意图则必须选全。
  - **Column Order**：SELECT 列顺序与问题中出现的顺序一致。
  - **COUNT with DISTINCT**：JOIN 后尤其是 N:1 / M:N 时用 `COUNT(DISTINCT column)`，避免重复计数。
  - **Multiple Columns from Different Tables**：多表多列要在同一行用 JOIN 组合，禁止用 UNION ALL 竖着堆叠。
  - 明确「仅当问题明确问最高/最低/最大/最小时才用 MAX/MIN」。

### 2.2 CTE 生成（CTEGenerator）

- 文件：`workflows/mcts_v1/agents/cte_generator.py`
- 方法：`_get_cte_system_message()`。

与「数据集 SQL」相关的部分：

- **何时输出 \<END> vs 继续生成 CTE**：根据上一条 CTE 的执行结果（成功/失败/空结果）决定；失败或空结果必须继续生成或修复，不得直接 \<END>。
- **CTE 列引用错误**：若列不在前面 CTE 中，必须从 Step 0 重写整条 CTE 链，保证列正确传递。
- **复合问题**：需要多条信息时，必须有 **merging CTE** 把各子 CTE JOIN 成一张结果再 \<END>。
- **Database Admin Instructions**：与 complete_sql 同风格的 15 条（SELECT、FROM 驱动表、MAX/MIN、ORDER BY、NULL、hint、DISTINCT、COUNT DISTINCT、列选择、字符串拼接、JOIN、SQLite、日期等），保证 CTE 内 SQL 也符合数据集约定。

---

## 三、mcts_v4 中的使用情况

### 3.1 当前实现

- mcts_v4 由 mcts_v1 复制而来。
- **完整 SQL**：`workflows/mcts_v4/agents/complete_sql_generator.py` 与 mcts_v1 **一致**，使用同一套 15 条 Database Admin Instructions。
- **CTE**：`workflows/mcts_v4/agents/cte_generator.py` 与 mcts_v1 **一致**，使用同一套 CTE 逻辑与 15 条规则。

因此，**mcts_v4 已经在使用与 mcts_v1 相同的、面向数据集的 SQL/CTE 规则与 prompt**，无需额外“接入”Alpha-SQL 的规则即可在 arcwise 等数据集上评估。

### 3.2 能否在 mcts_v4 里使用 Alpha-SQL 的规则？

- **语义上**：可以。Alpha-SQL 的 13 条规则是 mcts_v1/v4 中 15 条规则的**子集/精简版**，mcts_v1/v4 的规则更细、更严格（如 COUNT(DISTINCT)、多表 JOIN、Over/Under-Selection）。
- **使用方式**有两种：
  1. **保持现状（推荐）**：继续用 mcts_v1 的 15 条规则，已覆盖 Alpha-SQL 且更适合复杂题与 CTE。
  2. **可选：统一表述**：若希望 prompt 文案与 Alpha-SQL 论文/实现完全一致，可从 `Alpha-SQL-2.2.4/alphasql/templates/sql_generation.txt` 中摘抄 “Database admin instructions” 的 1–14 条，替换或合并进 `complete_sql_generator._get_sql_system_message()` 的对应条目（注意 Alpha-SQL 没有 COUNT(DISTINCT) 与多表 JOIN 的显式规则，建议保留 mcts_v1 的这两条）。

### 3.3 若要在 mcts_v4 中显式引用 Alpha-SQL 模板

- Alpha-SQL 的占位符格式：`{SCHEMA_CONTEXT}`, `{QUESTION}`, `{HINT}` 等，由 `prompt_factory.get_prompt()` 填充。
- mcts_v4 当前是**在代码里拼 system message**（字符串），没有读 Alpha-SQL 的 `.txt` 文件。
- 若要在 mcts_v4 里“用 Alpha-SQL 的 prompt 文件”：
  - 可把 `Alpha-SQL-2.2.4/alphasql/templates/sql_generation.txt` 拷到 mcts_v4 某目录（如 `workflows/mcts_v4/prompts/`），在 `complete_sql_generator` 或单独工具中读取该文件并替换 `SCHEMA_CONTEXT` / `QUESTION` / `HINT`，作为**用户轮**的 prompt；
  - system message 仍可保留 mcts_v1 的 15 条（或你合并后的版本），以保留 COUNT(DISTINCT) 等规则。

---

## 四、对照小结

| 项目 | Alpha-SQL (sql_generation) | mcts_v1 / mcts_v4 (CompleteSQL + CTE) |
|------|---------------------------|---------------------------------------|
| 规则条数 | 13 条（admin instructions） | 15 条（更细：Over/Under-Selection、COUNT DISTINCT、多表 JOIN） |
| 生成方式 | Recursive Divide-and-Conquer + 模板 | CTE 链 + 完整 SQL 生成，system message 内嵌规则 |
| 修订 | sql_revision.txt（无新规则） | 由 CTE 迭代 / 执行反馈驱动，无单独 revision 模板 |
| 在 mcts_v4 中 | 可选：抄写/替换部分表述 | **已默认使用**（与 mcts_v1 相同） |

结论：**mcts_v4 已具备与 mcts_v1 一致的数据集 SQL 规则与 prompt；Alpha-SQL 的规则可作为子集在 mcts_v4 中直接使用（当前已通过 mcts_v1 的 15 条覆盖），或按需摘抄进 prompt 以统一文案。**

---

## 五、run_v4_arcwise_alpha_sql.sh 实际使用的 prompt 与 Alpha-SQL 14 条对照

### 5.1 实际使用的 prompt 来源

- **入口**：`run_v4_arcwise_alpha_sql.sh` 调用 `workflows/mcts_v4/test/test_mcts.py`，使用 `workflows/mcts_v4/mcts_workflow.py` 的 `MCTSWorkflow`。
- **Prompt 来源**：**仅来自 mcts_v4 的 agents**（`complete_sql_generator._get_sql_system_message()`、`cte_generator._get_cte_system_message()` 等），**不会读取** `Alpha-SQL-2.2.4/alphasql/templates/*.txt`。即脚本不会加载 Alpha-SQL 的 sql_generation.txt，规则完全由 mcts_v4 代码中的 system message 决定。

### 5.2 Alpha-SQL 十四条规则在 mcts_v4 完整 SQL 生成中的有无

| # | Alpha-SQL 规则（sql_generation.txt） | mcts_v4 complete_sql_generator | 说明 |
|---|--------------------------------------|---------------------------------|------|
| 1 | SELECT：只选问题提到的列 | ✅ 有 | 规则 1（NO Over/Under-Selection, Column Order） |
| 2 | Aggregation：先 JOIN 再 MAX/MIN | ✅ 有 | 规则 2 |
| 3 | ORDER BY 前 GROUP BY 保证 distinct | ✅ 有 | 规则 3 |
| 4 | Handling NULLs：JOIN 或 IS NOT NULL | ✅ 有 | 规则 4 |
| 5 | FROM/JOIN：只必要表 | ✅ 有 | 规则 5 |
| 6 | Strictly Follow Hints | ✅ 有 | 规则 6 |
| 7 | Thorough Question Analysis | ✅ 有 | 规则 7 |
| 8 | DISTINCT 用于唯一值 | ✅ 有 | 规则 8 |
| 9 | Column Selection（相似列选对） | ✅ 有 | 规则 10 |
| 10 | String Concatenation：禁止 \|\| 等 | ✅ 有 | 规则 11 |
| 11 | SQLite Functions Only | ✅ 有 | 规则 14 |
| 12 | Date Processing：STRFTIME | ✅ 有 | 规则 15 |
| 13 | **Schema Syntax：表/列名含空格时用 \`table\`.\`column\`** | ✅ 有（已补） | 规则 13 |
| 14 | **JOIN Preference：INNER 优先，且不用 CROSS/LEFT/RIGHT** | ✅ 有（已补） | 规则 12 中已加入 “Do not use CROSS JOIN or LEFT JOIN or RIGHT JOIN” |

结论：**run_v4 使用的完整 SQL 生成 prompt（`complete_sql_generator._get_sql_system_message()`）已包含 Alpha-SQL 全部 14 条规则**：第 13 条（Schema Syntax）与第 14 条（禁止 CROSS/LEFT/RIGHT JOIN）已补入 `workflows/mcts_v4/agents/complete_sql_generator.py`。
