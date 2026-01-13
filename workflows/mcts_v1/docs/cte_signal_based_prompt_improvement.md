# 基于CTE执行信号的Prompt改进策略

## 背景

在MCTS工作流中，每个CTE的执行都会产生三种可能的信号：
1. **空结果**：CTE执行成功但返回0行数据
2. **报错**：CTE执行失败（语法错误、列名错误、表不存在等）
3. **输出内容**：CTE执行成功并返回数据

当前系统已经记录了这些信号，但还没有充分利用它们来指导下一个CTE的生成。本文档讨论如何将这些执行信号转化为prompt改进策略，帮助LLM更智能地探索数据库。

---

## 一、CTE执行信号分析

### 1.1 空结果信号

**特征**：
- CTE语法正确，执行成功
- 返回结果集为空（0行）
- 可能的原因：
  - WHERE条件过于严格，没有匹配的数据
  - 使用了错误的列值（拼写错误、大小写不匹配等）
  - 需要JOIN其他表才能找到数据
  - 数据确实不存在（需要验证）

**当前处理**：
- 允许空结果节点继续扩展
- 在下一层可能触发模糊匹配（LIKE/Levenshtein）

**改进机会**：
- 分析WHERE条件，提供列值建议
- 检查是否需要JOIN其他表
- 提供模糊匹配提示

### 1.2 报错信号

**特征**：
- CTE执行失败
- 错误类型包括：
  - 列名错误：`no such column: xxx`
  - 表名错误：`no such table: xxx`
  - 语法错误：SQL语法不正确
  - 类型错误：数据类型不匹配

**当前处理**：
- 记录错误信息
- 尝试自动修复列名错误（相似度匹配）
- 创建失败节点，允许继续探索

**改进机会**：
- 更智能的错误分析和修复建议
- 基于schema的错误预防
- 提供更具体的修复指导

### 1.3 输出内容信号

**特征**：
- CTE执行成功
- 返回非空结果集
- 包含实际数据

**当前处理**：
- 记录结果到节点
- 用于后续CTE的构建

**改进机会**：
- 分析返回数据的特征（列名、值分布、行数）
- 判断数据是否符合预期
- 提供下一步探索方向（是否需要进一步过滤、聚合、JOIN）

---

## 二、Prompt改进策略设计

### 2.1 空结果反馈策略

#### 策略A：列值建议
当CTE返回空结果时，分析WHERE条件中的列值，提供相似值建议：

```
**前序CTE执行结果**：
- CTE执行成功，但返回0行数据
- WHERE条件：`column_name = 'user_input_value'`

**建议**：
1. 检查列值拼写：'user_input_value' 在数据库中不存在
2. 相似值建议（基于Levenshtein距离）：
   - 'actual_value_1' (相似度: 0.85)
   - 'actual_value_2' (相似度: 0.78)
3. 考虑使用模糊匹配：`column_name LIKE '%user_input_value%'`
```

#### 策略B：表关系建议
当空结果可能因为缺少JOIN时：

```
**前序CTE执行结果**：
- CTE执行成功，但返回0行数据
- 当前查询的表：`table1`
- 问题涉及的概念：`concept_x`

**建议**：
1. 检查是否需要JOIN其他表：
   - `table2` 包含与 `concept_x` 相关的列
   - 通过外键 `table1.id = table2.table1_id` 关联
2. 考虑使用子查询或CTE链式查询
```

#### 策略C：数据验证提示
当空结果可能是数据确实不存在时：

```
**前序CTE执行结果**：
- CTE执行成功，但返回0行数据
- 查询条件看起来合理

**建议**：
1. 先验证数据是否存在：`SELECT COUNT(*) FROM table WHERE condition`
2. 如果数据存在但当前查询为空，考虑：
   - 检查数据类型匹配（字符串 vs 数字）
   - 检查NULL值处理
   - 检查日期格式
```

### 2.2 报错反馈策略

#### 策略A：列名修复建议
当遇到列名错误时，提供详细的修复建议：

```
**前序CTE执行错误**：
- 错误类型：列名不存在
- 错误列名：`wrong_column_name`
- 错误位置：表 `table_name`

**修复建议**：
1. 表 `table_name` 中不包含列 `wrong_column_name`
2. 相似列名建议：
   - `correct_column_1` (相似度: 0.92, 表: table_name)
   - `correct_column_2` (相似度: 0.85, 表: table_name)
3. 如果列在其他表中，考虑：
   - JOIN包含该列的表
   - 使用表别名：`other_table.correct_column`
```

#### 策略B：Schema上下文增强
当遇到表/列错误时，提供更详细的schema信息：

```
**前序CTE执行错误**：
- 错误类型：表/列不存在
- 涉及的表：`table_name`

**相关Schema信息**：
表 `table_name` 的完整结构：
- 列：`col1` (类型: TEXT, 示例值: 'value1', 'value2')
- 列：`col2` (类型: INTEGER, 范围: 1-100)
- 外键：`table_name.id -> other_table.table_name_id`

**建议**：
1. 使用上述列名重新构建CTE
2. 如果需要其他表的列，使用JOIN
```

#### 策略C：语法错误修复
当遇到语法错误时，提供修复指导：

```
**前序CTE执行错误**：
- 错误类型：SQL语法错误
- 错误信息：`syntax error near ...`

**修复建议**：
1. 检查SQL语法：确保所有括号匹配
2. 检查关键字拼写：SELECT, FROM, WHERE, JOIN等
3. 检查引号使用：字符串值需要用单引号包裹
4. 参考正确的SQL模板：
   ```sql
   WITH cte_name AS (
     SELECT column1, column2
     FROM table_name
     WHERE condition
   )
   SELECT * FROM cte_name
   ```
```

### 2.3 输出内容反馈策略

#### 策略A：数据特征分析
当CTE返回数据时，分析数据特征并指导下一步：

```
**前序CTE执行结果**：
- 返回行数：150行
- 列：`col1` (类型: TEXT), `col2` (类型: INTEGER)
- 数据特征：
  - `col1` 的唯一值数量：50
  - `col2` 的范围：1-1000
  - 示例数据：{'col1': 'value1', 'col2': 42}

**下一步建议**：
1. 如果问题需要进一步过滤，考虑：
   - 基于 `col1` 的特定值过滤
   - 基于 `col2` 的范围过滤
2. 如果问题需要聚合，考虑：
   - GROUP BY `col1` 进行分组
   - 对 `col2` 进行SUM/AVG/COUNT等聚合
3. 如果问题需要JOIN其他表，考虑：
   - 基于 `col2` 作为外键JOIN相关表
```

#### 策略B：数据验证提示
当返回的数据可能不符合预期时：

```
**前序CTE执行结果**：
- 返回行数：0行（但查询应该返回数据）

**可能的问题**：
1. WHERE条件可能过于严格
2. 可能需要使用OR而不是AND
3. 可能需要处理NULL值

**建议**：
1. 尝试放宽WHERE条件
2. 检查是否需要使用 `IS NULL` 或 `IS NOT NULL`
3. 考虑使用 `LIKE` 进行模糊匹配
```

#### 策略C：探索方向建议
基于返回的数据，建议下一步探索方向：

```
**前序CTE执行结果**：
- 返回了相关数据，但可能还需要进一步处理

**探索建议**：
1. 如果需要更精确的结果，添加额外的WHERE条件
2. 如果需要关联其他信息，JOIN相关表
3. 如果需要聚合统计，使用GROUP BY和聚合函数
4. 如果需要排序，添加ORDER BY子句
```

---

## 三、实现方案

### 3.1 信号收集与分类

在 `cte_processor.py` 中，已经收集了执行结果。需要增强信号分类：

```python
def classify_cte_signal(execution_result: Dict) -> str:
    """
    分类CTE执行信号
    
    Returns:
        'empty_result': 空结果
        'column_error': 列名错误
        'table_error': 表名错误
        'syntax_error': 语法错误
        'success_with_data': 成功且有数据
        'success_empty': 成功但空结果（已处理）
    """
    if not execution_result.get('valid', False):
        error = execution_result.get('error', '')
        if 'no such column' in error.lower():
            return 'column_error'
        elif 'no such table' in error.lower():
            return 'table_error'
        else:
            return 'syntax_error'
    else:
        result = execution_result.get('query_result', [])
        if not result or len(result) == 0:
            return 'empty_result'
        else:
            return 'success_with_data'
```

### 3.2 Prompt增强函数

创建 `prompt_enhancer.py`，根据CTE信号生成增强的prompt：

```python
def enhance_prompt_with_cte_signals(
    node: MCTSNode,
    cte_signals: List[Dict],  # 路径上所有CTE的执行信号
    schema_info: str
) -> str:
    """
    基于CTE执行信号增强prompt
    
    Args:
        node: 当前节点
        cte_signals: 路径上所有CTE的执行信号列表
        schema_info: schema信息
        
    Returns:
        增强后的prompt文本
    """
    enhancement_blocks = []
    
    # 分析最近的CTE信号
    recent_signals = cte_signals[-3:]  # 只看最近3个CTE
    
    for signal in recent_signals:
        signal_type = signal.get('type')
        cte = signal.get('cte', '')
        error = signal.get('error', '')
        result = signal.get('result', [])
        
        if signal_type == 'empty_result':
            enhancement = generate_empty_result_enhancement(cte, result, schema_info)
        elif signal_type == 'column_error':
            enhancement = generate_column_error_enhancement(cte, error, schema_info)
        elif signal_type == 'table_error':
            enhancement = generate_table_error_enhancement(cte, error, schema_info)
        elif signal_type == 'success_with_data':
            enhancement = generate_success_enhancement(cte, result, schema_info)
        else:
            continue
            
        if enhancement:
            enhancement_blocks.append(enhancement)
    
    if enhancement_blocks:
        return "\n\n".join(enhancement_blocks)
    return ""
```

### 3.3 集成到CTE生成流程

在 `cte_generator.py` 的 `generate_multiple_cte_variants` 方法中：

```python
def generate_multiple_cte_variants(self, node, ...):
    # 收集路径上的CTE执行信号
    cte_signals = self._collect_cte_signals(node)
    
    # 基于信号增强prompt
    enhanced_prompt = enhance_prompt_with_cte_signals(
        node, cte_signals, node.schema_info
    )
    
    # 将增强的prompt添加到additional_context
    original_context = node.additional_context
    if enhanced_prompt:
        node.additional_context = f"{original_context}\n\n{enhanced_prompt}"
    
    # 生成CTE
    cte_variants = self._generate_cte(...)
    
    # 恢复原始context
    node.additional_context = original_context
    
    return cte_variants
```

---

## 四、具体实现细节

### 4.1 空结果分析

```python
def analyze_empty_result(cte: str, schema_info: str) -> Dict:
    """
    分析空结果CTE，提取WHERE条件和列值
    
    Returns:
        {
            'where_conditions': [...],
            'column_values': {...},
            'suggestions': [...]
        }
    """
    # 提取WHERE条件
    where_match = re.search(r'WHERE\s+(.+?)(?:\s+GROUP\s+BY|\s+ORDER\s+BY|\s+LIMIT|$)', cte, re.IGNORECASE | re.DOTALL)
    if where_match:
        where_clause = where_match.group(1)
        # 解析列值对
        # ...
    
    # 从schema中查找相似值
    # ...
    
    return analysis_result
```

### 4.2 列名错误分析

```python
def analyze_column_error(error: str, cte: str, schema_info: str) -> Dict:
    """
    分析列名错误，提供修复建议
    
    Returns:
        {
            'wrong_column': 'xxx',
            'table': 'yyy',
            'suggestions': [
                {'column': 'zzz', 'similarity': 0.9, 'table': 'yyy'}
            ]
        }
    """
    # 提取错误列名
    # 从schema中查找相似列
    # 返回建议
```

### 4.3 数据特征提取

```python
def extract_data_features(result: List[Dict]) -> Dict:
    """
    提取返回数据的特征
    
    Returns:
        {
            'row_count': 100,
            'columns': [...],
            'value_distributions': {...},
            'sample_data': [...]
        }
    """
    # 分析数据特征
    # 返回统计信息
```

---

## 五、预期效果

### 5.1 改进空结果处理
- **当前**：空结果节点继续扩展，可能生成更多空结果
- **改进后**：基于空结果分析，提供列值建议和JOIN提示，提高下一层CTE的成功率

### 5.2 改进错误恢复
- **当前**：错误节点创建失败节点，继续探索
- **改进后**：基于错误分析，提供具体的修复建议，减少重复错误

### 5.3 改进数据探索
- **当前**：基于返回数据继续构建CTE，但缺乏指导
- **改进后**：基于数据特征，提供下一步探索方向，更高效地找到答案

---

## 六、实施优先级

### 阶段1：基础信号分析（高优先级）
1. ✅ 空结果检测和分析
2. ✅ 列名错误提取和相似度匹配
3. ✅ 数据特征提取（行数、列名、示例值）

### 阶段2：Prompt增强（中优先级）
1. 空结果反馈prompt生成
2. 列名错误修复prompt生成
3. 数据探索指导prompt生成

### 阶段3：智能建议（低优先级）
1. 基于schema的JOIN建议
2. 基于数据分布的过滤建议
3. 基于外键关系的表关联建议

---

## 七、潜在挑战

### 7.1 Prompt长度
- 添加太多反馈信息可能导致prompt过长
- **解决方案**：只包含最近的3-5个CTE信号，优先显示最重要的建议

### 7.2 信号噪声
- 不是所有信号都有用，需要过滤
- **解决方案**：只传递有意义的信号（错误、空结果、关键数据）

### 7.3 LLM理解能力
- LLM可能无法很好地理解复杂的反馈信息
- **解决方案**：使用清晰的结构化格式，提供具体的示例

---

## 八、下一步行动

1. **讨论确认**：确认这个方向是否符合预期
2. **原型实现**：先实现空结果分析的基础版本
3. **测试验证**：在小规模数据上测试效果
4. **迭代优化**：根据测试结果调整策略
5. **全面部署**：集成到完整的工作流中

---

## 九、参考示例

### 示例1：空结果 -> 列值建议
```
问题：查找名为"John Smith"的员工

CTE1执行结果：空结果
WHERE条件：name = 'John Smith'

增强Prompt：
**前序CTE执行反馈**：
- CTE执行成功但返回0行
- 查询条件：`name = 'John Smith'`
- 数据库中相似的名字：
  - 'John A. Smith' (相似度: 0.89)
  - 'Johnny Smith' (相似度: 0.82)
- 建议：尝试使用 `name LIKE '%John%Smith%'` 进行模糊匹配
```

### 示例2：列名错误 -> 修复建议
```
问题：查找员工的部门信息

CTE1执行错误：no such column: dept_name

增强Prompt：
**前序CTE执行错误**：
- 错误：列 'dept_name' 不存在于表 'employees'
- 修复建议：
  - 表 'employees' 中的相关列：'department_id' (外键)
  - 需要JOIN表 'departments' 获取部门名称
  - 正确的查询：`SELECT e.*, d.name as dept_name FROM employees e JOIN departments d ON e.department_id = d.id`
```

### 示例3：数据特征 -> 探索方向
```
问题：统计每个部门的员工数量

CTE1执行结果：返回150行员工数据

增强Prompt：
**前序CTE执行结果**：
- 已获取150行员工数据
- 数据包含列：employee_id, name, department_id
- department_id的唯一值：10个不同的部门
- 下一步建议：使用 `GROUP BY department_id` 进行分组统计
```

---

## 十、总结

基于CTE执行信号的prompt改进是一个有前景的方向，可以：
1. **提高探索效率**：减少无效的CTE生成
2. **加速错误恢复**：提供具体的修复建议
3. **改善数据理解**：基于实际数据特征指导下一步

建议先从**空结果分析**和**列名错误修复**开始，这两个是最常见且最容易产生效果的场景。
