# CTE执行结果对下游Prompt处理的影响

## 概述

在MCTS工作流中，每个CTE（Common Table Expression）的执行会产生三种可能的结果状态：
1. **非空结果**：执行成功，返回数据行
2. **空结果**：执行成功，但返回0行数据
3. **报错**：执行失败（语法错误、列名错误等）

这些执行结果会被传递给下游的CTE生成器和SQL生成器，用于指导后续的生成过程。本文档详细说明各种情况下prompt的处理方式。

---

## 一、CTE执行结果的三种状态

### 1.1 非空结果（Non-empty Result）

**特征**：
- CTE语法正确，执行成功
- 返回非空结果集（至少1行数据）
- `execution_result.valid = True`
- `execution_result.query_result` 包含实际数据

**在Prompt中的处理**：

```markdown
### Step N:
```sql
WITH cte_name AS (
    SELECT ...
)
```
**Execution Result**: Successfully returned {total_rows} rows
    **Relevant Sample Values**:
      'column1': 'value1', 'value2', 'value3'
      'column2': 100, 200, 300
      ...
```

**处理逻辑**：
- 显示总行数
- 展示前20行数据中每列的唯一值样本
- 对于数字类型，按数值排序展示所有唯一值
- 对于字符串类型，使用智能排序（时间格式按时间排序，其他按字母顺序）
- 字符串值用单引号包裹，列名也用单引号包裹（避免空格歧义）

**影响**：
- 下游CTE生成器可以使用这些实际数据值来指导下一步的查询
- SQL生成器可以使用这些数据来验证最终SQL的正确性

---

### 1.2 空结果（Empty Result）

**特征**：
- CTE语法正确，执行成功
- 返回0行数据
- `execution_result.valid = True`
- `execution_result.query_result = []` 或 `len(query_result) == 0`

**在Prompt中的处理**：

#### 情况A：没有WHERE子句的空结果

```markdown
### Step N:
```sql
WITH cte_name AS (
    SELECT ...
)
```
**Execution Result**: Successfully executed, returned empty result set
```

**处理逻辑**：
- 仅显示空结果提示
- 不触发模糊匹配提示（因为没有WHERE条件，可能是数据确实不存在）

#### 情况B：有WHERE子句的空结果（第一次）

```markdown
### Step N:
```sql
WITH cte_name AS (
    SELECT ... WHERE ...
)
```
**Execution Result**: Successfully executed, returned empty result set

This likely indicates a **String Literal Mismatch** OR **Wrong Column Selection**. The format might differ, or the wrong column is being queried.

### [YOUR TASK]

Create a **Exploratory CTE with new name** to find the correct format or column.

1. **Identify Targets**: Check string literals used in the failed query.
2. **Generate Variations**: Use distinct `LIKE` patterns for each string.
3. **Cross-Column Check**: Include fuzzy checks for all relevant columns.
```

**处理逻辑**：
- 检测最后一个CTE是否有WHERE子句（`_has_where_clause()`）
- 如果最后一个CTE有WHERE子句且返回空结果，触发模糊匹配提示
- 提示使用`LIKE`模式进行模糊匹配
- 建议检查其他相关列

**影响**：
- 引导LLM生成探索性CTE，使用模糊匹配（LIKE、Levenshtein等）来查找正确的数据格式

#### 情况C：有WHERE子句的空结果（第二次连续）

```markdown
### Step N:
```sql
WITH cte_name AS (
    SELECT ... WHERE ...
)
```
**Execution Result**: Successfully executed, returned empty result set

This likely indicates a **String Literal Mismatch** OR **Wrong Column Selection**. The format might differ, or the wrong column is being queried.

### [CHECK OTHER COLUMNS]
**Empty results after fuzzy matching twice** suggests you may be querying the **wrong column**.

1. **Review the question/evidence**: Check for explicit or implied column names.
2. **Check the schema**: Look for matching columns like IDs, names, or codes.
3. **Try a DIFFERENT column** with relevant semantics (e.g., name/ID, code/ID).

### [YOUR TASK]

Create a **Exploratory CTE with new name** to find the correct format or column.

1. **Identify Targets**: Check string literals used in the failed query.
2. **Generate Variations**: Use distinct `LIKE` patterns for each string.
3. **Cross-Column Check**: Include fuzzy checks for all relevant columns.
```

**处理逻辑**：
- 检测是否是第二次连续空结果（通过`consecutive_empty_count`或父节点的`is_empty_result`标志）
- 如果连续两次空结果，添加"检查其他列"的提示
- 建议检查是否查询了错误的列

**影响**：
- 引导LLM考虑查询不同的列，而不是继续在当前列上使用模糊匹配

---

### 1.3 报错（Execution Error）

**特征**：
- CTE执行失败
- `execution_result.valid = False`
- `execution_result.error` 包含错误信息
- 错误类型包括：
  - 列名错误：`no such column: xxx`
  - 表名错误：`no such table: xxx`
  - 语法错误：SQL语法不正确
  - 类型错误：数据类型不匹配
  - 超时错误：执行超时

**在Prompt中的处理**：

```markdown
### Step N:
```sql
WITH cte_name AS (
    SELECT ...
)
```
**Execution Result**: Execution failed
**Error**: {error_message}
```

**处理逻辑**：
- 显示执行失败和错误信息
- 错误信息会被收集到`_failed_cte_attempts`列表中
- 对于列名错误，系统会尝试自动查找列名映射（`find_column_table_mapping()`）
- 如果找到列名映射，会在失败信息中添加`column_hint`、`column_name`、`column_tables`字段

**错误处理增强**：

#### 列名错误处理

如果检测到列名错误（`no such column`、`column not found`等）：
- 系统会从schema中查找相似的列名
- 生成列名映射提示，例如：
  ```
  [错误处理] ✅ 找到列名映射: column_name -> ['table1.column_name', 'table2.column_name']
  ```
- 在`failed_item`中添加：
  - `column_hint`: 列名映射提示文本
  - `column_name`: 错误的列名
  - `column_tables`: 包含该列的表列表

#### 错误去重

- 对于相同的错误信息，只保留一个代表性的CTE（最短的）
- 如果新CTE更短，会替换已存在的错误记录
- 保留列名映射信息（如果存在）

**在CTE生成Prompt中的使用**：

失败信息会被传递给`generate_multiple_cte_variants()`方法，在prompt中显示：

```markdown
### [FAILED ATTEMPTS]
The following CTE attempts failed. Please avoid these errors:

1. **CTE**: 
```sql
WITH failed_cte AS (...)
```
   **Error**: no such column: wrong_column
   **Hint**: The column 'wrong_column' might be in table 'table1' or 'table2'. Please check the schema.

2. **CTE**: 
```sql
WITH another_failed_cte AS (...)
```
   **Error**: syntax error near ...
```

**影响**：
- 引导LLM避免重复相同的错误
- 提供列名建议，帮助LLM找到正确的列
- 限制失败信息数量（最多5个），避免prompt过长

---

## 二、关系检查结果（Relationship Check）

除了基本的执行结果，系统还会进行关系逻辑一致性检查。

### 2.1 关系检查通过

```markdown
**[Verification Passed]**: Relationship logic consistency check passed.
```

**处理逻辑**：
- 简洁显示通过信息
- 作为正向确认，不影响后续生成

### 2.2 关系检查失败

```markdown
**[CRITICAL LOGIC ERROR] Type: {error_type}**
--------------------------------------------------
**Diagnosis**: {feedback}

**How to Fix**: {actionable_advice}
```

**错误类型和处理**：

#### Fan-out / 1:N 关系错误

```markdown
**How to Fix**: You are joining a 'One' side table with a 'Many' side table without aggregation. This causes row duplication.
   1. Use `GROUP BY` on the primary key of the 'One' side table.
   2. Or use aggregation functions (SUM, AVG) on the 'Many' side columns.
```

#### Cartesian积错误

```markdown
**How to Fix**: The result size is explosively large. You likely missed a JOIN condition or joined unrelated tables. Please check your `ON` clause.
```

**影响**：
- 帮助LLM识别和修复JOIN逻辑错误
- 防止生成会导致数据爆炸的SQL

---

## 三、执行结果在Prompt中的传递流程

### 3.1 CTE生成阶段

**位置**：`cte_generator.py` → `_get_preceding_cte_info()`

**流程**：
1. 从当前节点向上追溯到根节点，收集所有前序CTE
2. 对每个CTE，获取其`execution_results.cte_result`
3. 根据执行结果状态格式化信息：
   - 非空：显示行数和示例值
   - 空结果：根据是否有WHERE子句决定是否提示模糊匹配
   - 报错：显示错误信息和可能的列名提示
4. 将格式化后的信息添加到prompt的"Existing CTE and Results"部分

**代码位置**：
- `mcts_workflow.py:407` - 调用`_generate_cte_variants()`
- `cte_generator.py:475-668` - `_get_preceding_cte_info()`方法

### 3.2 SQL生成阶段

**位置**：`complete_sql_generator.py` → `_get_preceding_cte_info()`

**流程**：
1. 类似CTE生成阶段，收集所有前序CTE及其执行结果
2. 格式化信息，但更简洁（主要用于验证，不用于指导生成）
3. 显示执行结果状态和示例数据值

**代码位置**：
- `mcts_workflow.py:1050+` - 调用SQL生成
- `complete_sql_generator.py:226-315` - `_get_preceding_cte_info()`方法

### 3.3 失败信息收集

**位置**：`mcts_workflow.py` → `_generate_cte_variants()`

**流程**：
1. 从当前节点向上追溯，收集所有失败节点的`_failed_cte_attempts`
2. 去重处理：相同错误只保留最短的CTE
3. 限制数量：最多保留5个失败尝试
4. 传递给`cte_generator.generate_multiple_cte_variants()`方法

**代码位置**：
- `mcts_workflow.py:1227-1367` - `_generate_cte_variants()`方法

---

## 四、关键判断条件

### 4.1 空结果判断

```python
# 检查结果是否为空
query_result = exec_result.get('query_result', [])
query_result = MCTSUtils.safe_to_dict(query_result)  # 转换为列表格式
is_empty = (not query_result or len(query_result) == 0)
```

### 4.2 WHERE子句检测

```python
# 检查CTE中是否包含WHERE子句
def _has_where_clause(self, cte: str) -> bool:
    # 提取CTE定义部分
    match = re.search(r'WITH\s+\w+\s+AS\s*\((.*?)\)', cte, re.DOTALL | re.IGNORECASE)
    if match:
        select_part = match.group(1).strip()
    # 检查是否包含WHERE关键字
    where_pattern = r'\bWHERE\b'
    return bool(re.search(where_pattern, select_part, re.IGNORECASE))
```

### 4.3 连续空结果检测

```python
# 检查是否是第二次连续空结果
is_second_empty = False
if hasattr(node, 'consecutive_empty_count') and node.consecutive_empty_count >= 2:
    is_second_empty = True
elif hasattr(node, 'parent') and node.parent:
    parent_exec_results = getattr(node.parent, 'execution_results', {})
    if parent_exec_results.get('is_empty_result', False):
        is_second_empty = True
```

### 4.4 列名错误检测

```python
# 检查是否是列名错误
is_column_error = (
    'no such column' in error.lower() or 
    ('column' in error.lower() and ('not found' in error.lower() or 'unknown' in error.lower()))
)
```

---

## 五、示例场景

### 场景1：非空结果 → 继续生成下一个CTE

```
Step 1: WITH step1 AS (SELECT name FROM users)
Result: Successfully returned 100 rows
        Relevant Sample Values:
          'name': 'Alice', 'Bob', 'Charlie'

→ 下游CTE可以使用这些name值来进一步查询
```

### 场景2：空结果（有WHERE） → 触发模糊匹配提示

```
Step 1: WITH step1 AS (SELECT * FROM users WHERE name = 'Alic')
Result: Successfully executed, returned empty result set

→ 提示：使用LIKE模式进行模糊匹配
Step 2: WITH step2 AS (SELECT * FROM users WHERE name LIKE '%Alic%')
```

### 场景3：连续空结果 → 提示检查其他列

```
Step 1: WITH step1 AS (SELECT * FROM users WHERE name = 'Alic')
Result: Empty

Step 2: WITH step2 AS (SELECT * FROM users WHERE name LIKE '%Alic%')
Result: Empty

→ 提示：可能查询了错误的列，检查其他列（如user_id、username等）
```

### 场景4：列名错误 → 提供列名映射

```
Step 1: WITH step1 AS (SELECT wrong_col FROM users)
Result: Execution failed
Error: no such column: wrong_col

→ 系统查找：wrong_col可能应该是 'users.name' 或 'users.username'
→ 在失败信息中添加列名提示
```

---

## 六、总结

CTE执行结果的处理遵循以下原则：

1. **非空结果**：展示实际数据，帮助下游生成使用真实值
2. **空结果**：根据是否有WHERE子句决定是否提示模糊匹配
3. **报错**：收集错误信息，提供修复建议（特别是列名映射）
4. **关系检查**：检测JOIN逻辑错误，提供修复指导

这些处理机制帮助MCTS工作流能够：
- 从执行反馈中学习
- 避免重复错误
- 智能探索数据库
- 生成更准确的SQL查询
