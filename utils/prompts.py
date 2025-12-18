class Prompts:
    """存储系统中使用的各种提示词"""


    ALIGNER_SYSTEM = """你是一个专门负责基于原始问题，针对QA问题的text to SQL专家。
你的主要职责是：
1. 分析每个SQL候选的优缺点，主要是和原始问题的需求顺序和返回顺序是否一致，我之后会有SQL验证程序来横向SQL是否正确，所以不要自己加字段提示...
2.注意你是一个"不好心"的 AI 助手，请你努力克制住"好心",不要做任何画蛇添足的事情。只需要展示用户明确指示（只需要用户明确要求的数据），并且只返回这些数据，不再包含任何辅助信息支持这些数据。
3. null的返回是有可能的，数据库可能没有足够的信息来解答原始问题。
4. 请注意我之后有正确的Result进行比较验证结果，所以不要生成创造的Result，由数据本身传达就可以了。
5. 分析SQL语句和Python代码，理解它们的执行逻辑
6. 找出正确的SQL语句·你认为正确的SQL一定是执行过的SQL。如果想探索，请返回False。
7. SQL输出符合你分析的答案格式，不要画蛇添足。
"""

    ALIGNER_TASK = """

这是一些标准答案的例子，你可以看到里面他们对于只会回答问题需要的内容。
example_data:{example_data}


原始问题: {question}
数据库信息: {tables_schema}
表结构前3行:
{tables_schema_first_three}
线索: {additional_context}
Python_1代码:
{code_1}

Python_1执行结果:
{code_result_1}

Python_2代码:
{code_2}

Python_2执行结果:
{code_result_2}


SQL1语句:
{sql_1}

SQL1执行结果:
{sql_result_1}

SQL2语句:
{sql_2}

SQL2执行结果:
{sql_result_2}


请执行以下任务：
1. 上面基于原始问题回答数据的代码和执行结果。
3. 两者的数据源是一致的。
4. 判断是否存在正确的SQL。
5. 如果存在，请返回True，返回正确的SQL。
6. 如果不存在，请返回False，返回你认为可以探索的SQL(参照你认为正确的Python逻辑)。

请按照以下格式返回你的分析：

```xml
<existence>
    True/False  <!-- 是否存在正确的SQL -->
</existence>
<reason>
    <!-- 解释你的判断理由。 -->
</reason>
<SQL>
    <!-- 提供你认为正确的SQL。如果你认为不存在正确的SQL则返回探索尝试的SQL -->
</SQL>
```
"""
    TOOL_DESCRIPTION="""
{
    "db_loader": {
        "name": "db_loader",
        "description": "从指定数据库加载目标表格数据。列名都会转换成大小写是不一样的",
        "parameters": [
            {"name": "db_name", "type": "str", "description": "数据库名称"},
            {"name": "target_table", "type": "str", "description": "表名"}
        ],
        "returns": "DataFrame对象",
        "examples": {
            "correct_usage": [
                "patients_data = db_loader('hospital', 'patients')",
                "employees_df = db_loader('company_db', 'employees')"
            ],
            "incorrect_usage": [
                "❌ db_loader(patients)",
                "❌ db_loader('hospital', 'non_existent_table')"
            ]
        }
    },
    "controlled_print": {
        "name": "controlled_print",
        "description": "以受控方式打印任何数据类型，如果内容过长会自动截断。",
        "parameters": [
            {"name": "data", "type": "any", "description": "任何要打印的数据"}
        ],
        "returns": "None (直接打印到控制台)",
        "examples": {
            "correct_usage": [
                "controlled_print('Hello, world!')",
                "controlled_print(patients_data)",
                "controlled_print({'key': 'value', 'long_text': 'a' * 2000})"
            ],
            "incorrect_usage": [
                "❌ controlled_print()"
            ]
        }
    },
,
    "data_filter": {
        "name": "data_filter",
        "description": "根据条件筛选 DataFrame 数据。支持标准 SQL WHERE 筛选表达式（不含子查询），例如 >, <, >=, <=, !=, <>, LIKE, IN, IS NULL, IS NOT NULL, BETWEEN。多个条件可用 AND 连接。列名里有特殊字符请用反引号包裹。",
        "parameters": [
            {"name": "data", "type": "DataFrame", "description": "要筛选的 DataFrame 对象"},
            {"name": "argument", "type": "str", "description": "筛选条件字符串"}
        ],
        "returns": "筛选后的 DataFrame 对象",
        "examples": {
            "correct_usage": [
                "filtered_data = data_filter(patients_data, 'age>=65 AND gender IN ('female')')",
                "filtered_data = data_filter(molecule_data, 'bond_type IN ('=')')",
                "filtered_data = data_filter(sales_data, 'region IN ('North', 'South') AND amount>1000')",
                "filtered_data = data_filter(customer_data, 'name LIKE '%Smith%' AND status IS NOT NULL')",
                "filtered_data = data_filter(frpm_high_math_schools, '`Charter Funding Type` IN ('Directly funded')')"
            ],
            "incorrect_usage": [
                "❌ data_filter(high_math_schools, '`cds` IN (SELECT `CDSCode` FROM directly_funded_charter_schools)')",
                "❌ data_filter(sales_data, 'age > 50 AND city = Boston')",
                "❌ data_filter('df', 'age > 10')",
                "❌antic_wid = get_value(antic_word_record, 'wid')\n 不能放list!!!得是字符串形式w2nd = '5'(类SQL表述),filtered_biwords = data_filter(biwords_df, f\"w2nd = {antic_wid}\")"
            ]
        }
    },
    "get_value": {
        "name": "get_value",
        "description": "从数据中获取指定列的值或执行聚合操作,。argument 中列名只能放一个列名，如果需要多个列，请多次调用 get_value 或自行处理。返回值类型取决于操作：单个值（int/float/str）、列表或字典（多列无操作）。",
        "parameters": [
            {"name": "data", "type": "DataFrame | list", "description": "输入数据（DataFrame 或 Python 列表）"},
            {"name": "argument", "type": "str", "description": "指定列名或聚合操作（例如 'column_name', 'column_name, mean', 'count(*)'）。"}
        ],
        "returns": "any (单个值, 列表, 或字典)",
        "examples": {
            "correct_usage": [
                "patient_name = get_value(patient_data, 'name')",
                "ages = get_value(patients_data, 'age, list')",
                "avg_age = get_value(patients_data, 'age, mean')",
                "total_records = get_value(patients_data, 'count(*)')",
                "active_counts = get_value(df, 'status, count')",
                "filtered = data_filter(patients_data, 'age > 65')",
                "result = get_value(filtered, 'name, list')"
            ],
            "incorrect_usage": [
                "❌ get_value(patients_data, 'age > 65, list')",
                "❌ get_value(patients_data, 'name, age')",
                "❌ get_value('not_a_df', 'column_name')",
                "❌ 不支持时间类的处理,get_value(hypertension_data, 'START, max')",
            ]
        }
    },
    "group_and_aggregate_df": {
        "name": "group_and_aggregate_df",
        "description": "对 DataFrame 进行分组和聚合操作。",
        "parameters": [
            {"name": "data", "type": "pd.DataFrame", "description": "输入的 DataFrame。"},
            {"name": "group_by_columns", "type": "str | list", "description": "用于分组的列名，可以是单个字符串或列名列表。"},
            {"name": "agg_operations", "type": "dict", "description": "聚合操作的字典。**键为新列名**，**值为一个元组 `('原始列名', '聚合函数名')`**（例如 `('amount', 'sum')`）。聚合函数名必须是 Pandas 支持的字符串（如 'sum', 'mean', 'count' 等）。"}
        ],
        "returns": "pd.DataFrame: 包含分组和聚合结果的新 DataFrame。",
        "examples": {
            "correct_usage": [
                "customer_sales = group_and_aggregate_df(sales_df, group_by_columns='customer_id', agg_operations={'total_amount': ('amount', 'sum'), 'num_items': ('item_count', 'mean')})",
                "daily_transactions = group_and_aggregate_df(transactions_df, group_by_columns=['date', 'type'], agg_operations={'sum_value': ('value', 'sum')})"
            ],
            "incorrect_usage": [
                "❌ group_and_aggregate_df(data_list, 'category', {'value': 'sum'})",
                "❌ group_and_aggregate_df(df, 'non_existent_column', {'value': 'sum'})",
                "❌ group_and_aggregate_df(df, 'category', {'value': 'unsupported_function'})"
            ]
        }
    },
    "__general_python_and_pandas_usage__": {
        "description": "注意：除了上述工具函数，你还可以使用标准的 Python 语法和 Pandas DataFrame 操作来处理数据，例如：\n- **条件判断**: `if`, `elif`, `else`\n- **直接计算**: 算术运算 (`+`, `-`, `*`, `/`), 逻辑运算 (`and`, `or`, `not`)\n- **数据结构操作**: 列表 (`[]`), 字典 (`{}`), 元组 (`()`) 的创建和操作\n- **DataFrame操作**: \n  - 列选择 (`df['col']` 或 `df[['col1', 'col2']]`)\n  - 链式方法 (`df.groupby(...).sum()`)\n  - 元素访问 (`df.iloc[0]`, `df.loc[row_label]`)\n  - 转换为列表 (`.values.tolist()`)\n  - **联接/合并**: `DataFrame.merge()` 函数用于模拟 SQL 的 JOIN 操作（如 `df1.merge(df2, on='id', how='inner')`）。\n",
        "examples": {
            "correct_usage": [
                "if x > 0: result = x * 2 else: result = 0",
                "proportion = count_a / total_count if total_count > 0 else 0",
                "first_row_data = df.iloc[0]",
                "selected_columns_list = df[['colA', 'colB']].values.tolist()",
                "sum_of_column = df['numeric_col'].sum()",
                "merged_df = df1.merge(df2, left_on='key1', right_on='key2', how='inner')",
                "sorted_df = df.sort_values(by=['GWG', 'playerID'], ascending=[False, True]).iloc[0]#sort的时候考虑和SQL的排序方式一致"
            ]
        }
    },
    "time_module_usage": {
        "description": "Python 的 `time` 模块提供了各种时间相关的函数。",

    },
    "math_module_usage": {
        "description": "Python 的 `math` 模块提供了对标准数学函数和常数的访问。",
}
"""    
# {
#     "db_loader": {
#         "name": "db_loader",
#         "description": "从指定数据库加载目标表格数据。",
#         "parameters": [
#             {"name": "db_name", "type": "str", "description": "数据库名称"},
#             {"name": "target_table", "type": "str", "description": "表名"}
#         ],
#         "returns": "DataFrame对象",
#         "examples": {
#             "correct_usage": [
#                 "patients_data = db_loader('hospital', 'patients')",
#                 "employees_df = db_loader('company_db', 'employees')"
#             ],
#             "incorrect_usage": [
#                 "❌ db_loader(patients)",
#                 "❌ db_loader('hospital', 'non_existent_table')"
#             ]
#         }
#     },
#     "data_filter": {
#         "name": "data_filter",
#         "description": "根据条件筛选 DataFrame 数据。支持标准 SQL WHERE 筛选表达式（不含子查询），例如 >, <, >=, <=, !=, <>, LIKE, IN, IS NULL, IS NOT NULL, BETWEEN。多个条件可用 AND 连接。列名里有特殊字符请用反引号包裹。",
#         "parameters": [
#             {"name": "data", "type": "DataFrame", "description": "要筛选的 DataFrame 对象"},
#             {"name": "argument", "type": "str", "description": "筛选条件字符串"}
#         ],
#         "returns": "筛选后的 DataFrame 对象",
#         "examples": {
#             "correct_usage": [
#                 "filtered_data = data_filter(patients_data, 'age>=65 AND gender IN ('female')')",
#                 "filtered_data = data_filter(molecule_data, 'bond_type IN ('=')')",
#                 "filtered_data = data_filter(sales_data, 'region IN ('North', 'South') AND amount>1000')",
#                 "filtered_data = data_filter(customer_data, 'name LIKE '%Smith%' AND status IS NOT NULL')",
#                 "filtered_data = data_filter(frpm_high_math_schools, '`Charter Funding Type` IN ('Directly funded')')"
#             ],
#             "incorrect_usage": [
#                 "❌ data_filter(high_math_schools, '`cds` IN (SELECT `CDSCode` FROM directly_funded_charter_schools)')",
#                 "❌ data_filter(sales_data, 'age > 50 AND city = Boston')",
#                 "❌ data_filter('df', 'age > 10')",
#                 "❌antic_wid = get_value(antic_word_record, 'wid')\n 不能放list!!!得是字符串形式w2nd = '5'(类SQL表述),filtered_biwords = data_filter(biwords_df, f\"w2nd = {antic_wid}\")"
#             ]
#         }
#     },
#     "get_value": {
#         "name": "get_value",
#         "description": "从数据中获取指定列的值或执行聚合操作。支持多种参数格式，包括直接列名、'列名, 操作' 和 '操作(列名)'。对于 count 操作，支持 'count(*)' 和 'count(distinct 列名)'。返回值类型取决于操作：单个值（int/float/str）、列表或字典（多列无操作）。",
#         "parameters": [
#             {"name": "data", "type": "DataFrame | list", "description": "输入数据（Pandas DataFrame 或 Python 列表）。如果列表包含字典，会自动尝试转换为 DataFrame。"},
#             {"name": "argument", "type": "str", "description": "指定列名或聚合操作。支持以下格式：\n    - '列名'：返回该列的所有非空值列表（DataFrame多行）或单个值（DataFrame单行）。\n    - '列名1, 列名2, ...'：返回一个字典，键为列名，值为对应列的非空值列表。\n    - '列名, 操作'：对指定列执行聚合操作（例如 'age, mean', 'status, count'）。\n    - '操作(列名)'：对指定列执行聚合操作（例如 'sum(cost)', 'max(price)'）。\n    - 'count(*)'：返回数据中的总行数。\n    - 'count(distinct 列名)'：返回指定列的唯一非空值的数量。"}
#         ],
#         "returns": "any (单个值, 列表, 或字典)",
#         "examples": {
#             "correct_usage": [
#                 "patient_name = get_value(patient_data, 'name')",
#                 "ages_list = get_value(patients_data, 'age, list')",
#                 "avg_age = get_value(patients_data, 'mean(age)')",
#                 "total_cost = get_value(expenses_data, 'sum(cost)')",
#                 "distinct_products = get_value(orders_df, 'count(distinct product_id)')",
#                 "total_records = get_value(my_data, 'count(*)')",
#                 "filtered_patients = data_filter(patients_data, 'age > 65')",
#                 "names_of_filtered_patients = get_value(filtered_patients, 'name, list')",
#                 "multiple_columns = get_value(df, 'column_A, column_B')"
#             ],
#             "incorrect_usage": [
#                 "❌ get_value(patients_data, 'age > 65, list') # 过滤条件应使用 data_filter",
#                 "❌ get_value('not_a_df', 'column_name') # data 参数必须是 DataFrame 或 list",
#                 "❌ 不支持时间类的直接聚合，例如 get_value(hypertension_data, 'START, max') # 时间类型处理可能需要额外逻辑"
#             ]
#         }
#     },
#     "controlled_print": {
#         "name": "controlled_print",
#         "description": "以受控方式打印任何数据类型，如果内容过长会自动截断。",
#         "parameters": [
#             {"name": "data", "type": "any", "description": "任何要打印的数据"}
#         ],
#         "returns": "None (直接打印到控制台)",
#         "examples": {
#             "correct_usage": [
#                 "controlled_print('Hello, world!')",
#                 "controlled_print(patients_data)",
#                 "controlled_print({'key': 'value', 'long_text': 'a' * 2000})"
#             ],
#             "incorrect_usage": [
#                 "❌ controlled_print()"
#             ]
#         }
#     },
#     "group_and_aggregate_df": {
#         "name": "group_and_aggregate_df",
#         "description": "对 DataFrame 进行分组和聚合操作。",
#         "parameters": [
#             {"name": "data", "type": "pd.DataFrame", "description": "输入的 DataFrame。"},
#             {"name": "group_by_columns", "type": "str | list", "description": "用于分组的列名，可以是单个字符串或列名列表。"},
#             {"name": "agg_operations", "type": "dict", "description": "聚合操作的字典。**键为新列名**，**值为一个元组 `('原始列名', '聚合函数名')`**（例如 `('amount', 'sum')`）。聚合函数名必须是 Pandas 支持的字符串（如 'sum', 'mean', 'count' 等）。"}
#         ],
#         "returns": "pd.DataFrame: 包含分组和聚合结果的新 DataFrame。",
#         "examples": {
#             "correct_usage": [
#                 "customer_sales = group_and_aggregate_df(sales_df, group_by_columns='customer_id', agg_operations={'total_amount': ('amount', 'sum'), 'num_items': ('item_count', 'mean')})",
#                 "daily_transactions = group_and_aggregate_df(transactions_df, group_by_columns=['date', 'type'], agg_operations={'sum_value': ('value', 'sum')})"
#             ],
#             "incorrect_usage": [
#                 "❌ group_and_aggregate_df(data_list, 'category', {'value': 'sum'})",
#                 "❌ group_and_aggregate_df(df, 'non_existent_column', {'value': 'sum'})",
#                 "❌ group_and_aggregate_df(df, 'category', {'value': 'unsupported_function'})"
#             ]
#         }
#     },
#     "__general_python_and_pandas_usage__": {
#         "description": "注意：除了上述工具函数，你还可以使用标准的 Python 语法和 Pandas DataFrame time, math package,操作来处理数据，例如：\n- **条件判断**: `if`, `elif`, `else`\n- **直接计算**: 算术运算 (`+`, `-`, `*`, `/`), 逻辑运算 (`and`, `or`, `not`)\n- **数据结构操作**: 列表 (`[]`), 字典 (`{}`), 元组 (`()`) 的创建和操作\n- **DataFrame操作**: \n  - 列选择 (`df['col']` 或 `df[['col1', 'col2']]`)\n  - 链式方法 (`df.groupby(...).sum()`)\n  - 元素访问 (`df.iloc[0]`, `df.loc[row_label]`)\n  - 转换为列表 (`.values.tolist()`)\n  - **联接/合并**: `DataFrame.merge()` 函数用于模拟 SQL 的 JOIN 操作（如 `df1.merge(df2, on='id', how='inner')`）。\n",
#         "examples": {
#             "correct_usage": [
#                 "if x > 0: result = x * 2 else: result = 0",
#                 "proportion = count_a / total_count if total_count > 0 else 0",
#                 "first_row_data = df.iloc[0]",
#                 "selected_columns_list = df[['colA', 'colB']].values.tolist()",
#                 "sum_of_column = df['numeric_col'].sum()",
#                 "merged_df = df1.merge(df2, left_on='key1', right_on='key2', how='inner')",
#                 "sorted_df = df.sort_values(by=['GWG', 'playerID'], ascending=[False, True]).iloc[0]#sort的时候考虑和SQL的排序方式一致"
#             ]
#         }
#     }
# }
    # Generator Agent的提示词
    GENERATOR_SYSTEM = """你是一个Python和SQL专家。
"""

    GENERATOR_SUBTASK = """请分析以下问题，并生成解决方案：

问题: {question}
数据库信息:{tables_schema}
表结构前3行:{tables_schema_first_three}
线索：{additional_context}
{tool_description}
基于原始问题，生成2-3个子问题，这些子问题的答案将有助于解决原始问题。
请生成这些子问题的Python代码。写在一个<code>标签中，用#分割就行。
每一段code代码都应该包括输出(controlled_print)。
最终生成一个可以解决原始问题的完整代码。
你可以使用我提供给你的代码API接口。同时，可以写其他代码。用controlled_print输出你想查看到的内容。
```xml
<thinking>
... 请基于线索的角度思考，生成子问题。
</thinking>
<code>
#subproblem...

#subproblem...
...

</code>
```
"""

    # 重新生成代码的提示词
    REGENERATE_SUB_TASK = """请根据之前的执行结果和评估反馈，重新生成更好的代码来解决问题：

问题: {question}
数据库信息:{tables_schema}

表结构前3行:
{tables_schema_first_three}

线索：{additional_context}
之前的代码:
{previous_code}

执行结果:
{previous_result}

评估反馈:
{judger_feedback}

{tool_description}
请分析上述反馈，生成新的、更准确的Python代码。
基于原始问题，生成2-3个子问题，这些子问题的答案将有助于解决原始问题。
请生成这些子问题的Python代码。写在一个<code>标签中，用#分割就行。
每一段code代码都应该包括输出(controlled_print)。
最终生成一个可以解决原始问题的完整代码。
你可以使用我提供给你的代码API接口。同时，可以写其他代码。
请按照下面的格式生成代码，确保返回完整的修复后代码,用户不需要修改任何代码。用controlled_print输出你想查看到的内容。
```xml

<code>
#subproblem...

#subproblem...
...

</code>
```
"""

    # 重新生成代码的提示词
    REGENERATE_TASK = """请根据之前的执行结果和评估反馈，重新生成更好的代码来解决问题：
{tool_description}
相关问题的解决方法:
{related_python_code}
现在解决下面的问题:

数据库信息:{tables_schema}

df_list:
{df_list}

问题: {question}
线索：{additional_context}
之前的代码:
{previous_code}

执行结果:
{previous_result}

反思反馈:
{reflection_feedback}

历史分析：{top3_insights}

请分析上述反馈，生成新的、更准确的Python代码。确保新代码能够正确解决原始问题。
请按照下面的格式生成代码，确保返回完整的修复后代码,用户不需要修改任何代码。用controlled_print输出中间的内容进行调试是允许的，你的最终结果也要用controlled_print输出。
```xml
<thinking>
...
</thinking>
<code>
...
</code>
```
"""
    # 直接生成Python代码
    SOLVE_DIRECTLY = """

你可以使用我提供给你的代码API接口。同时，可以写其他代码。
{tool_description}
相关问题解决方法:
{related_python_code}

现在开始新的任务：
数据库信息:{tables_schema}
df_list:
{df_list}

问题: {question}
线索：{additional_context}
历史分析：{top3_insights}

请按照下面的格式生成代码来综合这些信息并解决问题。请确保返回的代码是完整的,用户不需要修改任何代码。用controlled_print输出你想查看到的内容（最终结果）。

```xml
<code>
...
</code>
```
"""

    # Fixer Agent的提示词
    FIXER_SYSTEM = """你是一个专门修复Python代码错误的助手。
你的任务是：
根据错误分析，提供修复后的代码
"""

    FIXER_TASK = """以下Python代码执行时出现了错误：

{code}
错误信息：
{error_message}
错误分析：
{reason}
你可以使用我提供给你的代码API接口。同时，可以写其他代码。
{tool_description}
请按照下面的格式生成代码，确保返回完整的修复后代码,用户不需要修改任何代码。
```xml
<code>
...
</code>
```
"""
    SQL_SELECTOR_SYSTEM = """你是一个专门的SQL选择器，负责从多个候选SQL中选择最佳的一个。
你的主要职责是：
1. 分析每个SQL候选的执行结果和相似度分数
2. 评估每个SQL与Python代码的语义一致性
3. 选择最符合原始问题要求的SQL
4. 确保选择的SQL能够准确反映Python代码的执行逻辑
"""

    SQL_SELECTOR_TASK = """请从以下SQL候选中选择最佳的一个：

问题: {question}

Python代码:
{code}

Python执行结果:
{result}

SQL候选及其执行结果:
{sql_candidates}


请仔细分析每个SQL候选：
1. 评估SQL执行结果与Python结果的相似度
2. 检查SQL是否准确反映了Python代码的数据处理逻辑
3. 验证SQL返回的结果格式是否符合要求
4. 确保SQL没有多余的操作或返回不必要的列

请按照以下格式提供你的选择：

```xml
<selection>
选择的SQL编号及原因...
</selection>
<sql>
完整的选中的SQL
</sql>
```
"""

    # Judger Agent的提示词
    JUDGER_SYSTEM = """你是一个QA问题输出评估专家"""

    Strightforward_TASK = """请评估以下查询结果输出是否合理：
代码API接口:{tool_description}
数据库信息:{tables_schema}
df_list:{df_list}
原始问题: {question}
线索：{additional_context}

代码:
{code}

执行结果:
{result}

请进行以下三个判断：
1. 如果你觉得现在的信息对于问题的解答是合理的，输出True。如果你觉得结果输出不合理，请返回False，允许有多余的输出，只要最终的结果合理.
2. 解释你的判断理由。如果你觉得不合理，给出更多我如何继续解决这个问题。
3. 线索的思考逻辑一定要体现在代码中。
4. null/None的返回是有可能的，数据库可能没有足够的信息来解答原始问题。这种情况请返回True。
5.注意大小写问题，可能导致merge不匹配。
6. pandas表中如果有两列相同名字的，需要提示重命名。
7. 可以提示输出中间结果。
请按照以下格式返回你的判断：
```xml
<QA>
    True/False  <!-- 如果你觉得结果对于原始问题不合理，返回False，否则返回True -->
</QA>
<reason>
    <!-- 如果你觉得结果对于原始问题不合理,Analyze the outcome of the executed query to identify why it failed (e.g., syntax
errors, incorrect column references, logical mistakes)，给出你的指导 -->
</reason>

```
"""


    # 辩论Agent的提示词
    DEBATER_PRO_SYSTEM = """你是辩论正方，支持将Python代码直接转换为SQL。
你的任务是分析Python代码，找出其中的数据处理逻辑，并说明如何将其转换为等效的SQL查询。

在群组辩论中，你应该：
1. 首先提出初始SQL转换方案
2. 回应反方的质疑，必要时修改你的SQL，只能一次生成一个SQL
3. 坚持讨论直到达成共识
4. 你提出的SQL都会经过SQLExecutor执行，不需要你执行。

请解释你的推理过程，并提供具体的SQL。按照下面的格式输出：
```xml
<thinking>
...
</thinking>
<sql>
...
</sql>
```
"""

    DEBATER_CON_SYSTEM = """你是辩论反方，质疑将Python代码直接转换为SQL的合理性。
你的任务是找出Python代码中难以用SQL表达的部分，以及可能的转换陷阱，原始问题是否解答。


在群组辩论中，你应该：
1. 对正方提出的SQL方案提出具体质疑
2. 评估正方的回应和修改
3. 当问题解决后，明确表示你的顾虑已解决

请提出具体的质疑，并解释你的推理过程。按照下面的格式输出：
```xml
<thinking>
...
</thinking>
<key_point>
...
</key_point>
```
"""

    DEBATE_JUDGE_SYSTEM = """你是辩论裁判，负责总结辩论并提出最终的SQL建议。
你的任务是：
1. 客观评估正反双方的论点
2. 找出最合理的SQL转换方案

在群组辩论中，你应该：
1. 在辩论初期保持沉默，仔细观察
2. 当讨论陷入循环或已充分展开时介入
3. 在辩论结束时提供最终判决
4. 你选择的SQL一定是经过SQLExecutor执行过的SQL

请使用以下XML标签格式提供最终判决：按照下面的格式输出：
```xml
<summary>...</summary>
<sql>最终SQL</sql>
```
"""
    question_format_system = """你是一个专门描述答案格式的专家。
"""
    ANSWER_FORMAT_TASK = """
注意你是一个"不好心"的 AI 助手，请你努力克制住"好心",不要做任何画蛇添足的事情。只需要展示用户明确指示（只需要用户明确要求的数据），并且只返回这些数据，不再包含任何辅助信息支持这些数据,返回顺序和问题中要求的顺序一致。    
请描述你认为的答案格式，不需要真实数据，只需要给我个大概的样子就行，你不需要添加index。
如果问题里是was/were,有可能返回多个内容，
如果问题里有list，则返回list明确的要求即可，其他都是多余的。
CONCAT或者case when可以是数字计算，但是不能是字符赋值。
其他问题的回答规模:
{example_data}
问题: {question}
```xml
<answer>
描述一下你认为的答案规格.表明只是你的猜测，不需要回复行数...
</answer>
```
"""
    ANSWER_REFORMAT_TASK = """
其他问题的分析：{analysis_based_on_few_shot_logic}
In which city is there a greater number of schools that have received donations of less than 10 dollars?
SELECT T2.school_city FROM donations AS T1 INNER JOIN projects AS T2 ON T1.projectid = T2.projectid WHERE T1.dollar_amount = 'under_10' GROUP BY T2.school_city ORDER BY COUNT(T2.schoolid) DESC LIMIT 1
User's Comment: 主需要主语city (Only the subject 'city' is needed)

List the poverty level of all the schools that received donations with the zip code \"7079\".
SELECT DISTINCT T2.poverty_level FROM donations AS T1 INNER JOIN projects AS T2 ON T1.projectid = T2.projectid WHERE T1.donor_zip = 7079
User's Comment: 主语只需要poverty_level (Only the subject 'poverty_level' is needed)

Among the students with less than four intelligence, list the full name and phone number of students with a greater than 3 GPA.
SELECT f_name, l_name, phone_number FROM student WHERE gpa > 3 AND intelligence < 4
User's Comment: 不需要f_name, l_name拼接在一起。 (No need to concatenate f_name, l_name.)

What is the power play percentage of the team with the least number of penalty kill chances and to which team were they playing against? Indicate whether the team lost or victorious.
SELECT SUM(T1.A), T2.firstName, T2.lastName FROM Scoring AS T1 INNER JOIN Master AS T2 ON T1.playerID = T2.playerID WHERE T1.lgID = 'NHL' GROUP BY T2.firstName, T2.lastName ORDER BY SUM(T1.A) DESC LIMIT 1
User's Comment:  不需要返回Ture or False回应whether.
Analysis based on Few-shot Logic:

现在调整下面的问题：
问题: {question}
evidence: {evidence}
可能需要调整的SQL：{sql_output_format}
SQL的返回结果：{sql_result}
请你重新排序输出的列名，让SQL的输出更贴近原始问题所需要展示的内容，请注意展示和计算逻辑的区分，我只需要展示的列名，不需要计算的列名。
如果问题里是was/were,有可能返回多个内容，
如果问题里有list，则返回list明确的要求即可，其他都是多余的。
CONCAT或者case when可以是数字计算，但是不能是字符赋值。
```xml
<reason>
...
</reason>
<SQL>
...
</SQL>
```
    
"""
    # 最终Aligner的提示词
    FINAL_ALIGNER_TASK = """
其他问题的分析：{analysis_based_on_few_shot_logic}
In which city is there a greater number of schools that have received donations of less than 10 dollars?
SELECT T2.school_city FROM donations AS T1 INNER JOIN projects AS T2 ON T1.projectid = T2.projectid WHERE T1.dollar_amount = 'under_10' GROUP BY T2.school_city ORDER BY COUNT(T2.schoolid) DESC LIMIT 1
User's Comment: 主需要主语city (Only the subject 'city' is needed)

List the poverty level of all the schools that received donations with the zip code \"7079\".
SELECT DISTINCT T2.poverty_level FROM donations AS T1 INNER JOIN projects AS T2 ON T1.projectid = T2.projectid WHERE T1.donor_zip = 7079
User's Comment: 主语只需要poverty_level (Only the subject 'poverty_level' is needed)

Among the students with less than four intelligence, list the full name and phone number of students with a greater than 3 GPA.
SELECT f_name, l_name, phone_number FROM student WHERE gpa > 3 AND intelligence < 4
User's Comment: 不需要f_name, l_name拼接在一起。 (No need to concatenate f_name, l_name.)

What is the power play percentage of the team with the least number of penalty kill chances and to which team were they playing against? Indicate whether the team lost or victorious.
SELECT SUM(T1.A), T2.firstName, T2.lastName FROM Scoring AS T1 INNER JOIN Master AS T2 ON T1.playerID = T2.playerID WHERE T1.lgID = 'NHL' GROUP BY T2.firstName, T2.lastName ORDER BY SUM(T1.A) DESC LIMIT 1
User's Comment:  不需要返回Ture or False回应whether.
Analysis based on Few-shot Logic:

    请分析以下SQL候选，选择获修改处最佳SQL：
问题: {question}
evidence: {evidence}
表结构:
{tables_schema}

请严格按照线索的需要作答：
{additional_context}


{verified_code_records}

请评估以下SQL查询并选择出最佳的一个:
{sql_candidates}

请分析SQL候选
1. SQL输出符合你分析的答案格式，不要画蛇添足，不要添加辅助逻辑（你只要关注主语，不要关注谓语）。你不需要更改计算的逻辑，但是你需要控制输出的格式。
2. 如果问题里有list，则返回list明确的要求即可，对list外的描述需求（例如who ,where 介词后如何判断该list是需要的）这些都是多余的。
3. CONCAT或者case when可以是数字计算，但是不能是字符赋值,例如THEN 'Yes' ELSE 'No'这种字符直接赋值是不允许的，因为我之后要和gold sql对比，这样没有办法进行字符匹配，因此请帮我去掉。
4. 同样string的拼接也是不需要的，按顺序返回就行。
5. 返回的顺序和问题中要求的顺序一致。
existence 是True/False，表示是否存在正确SQL。
请提供以下格式的回答:
```xml
<reason>每条数据都按照我上面的分析格式来分析，选择或者细微修改原因的详细解释</reason>
<SQL>你选择的完整SQL</SQL>
```
"""

    # 群组辩论的提示词
    DEBATE_GROUP_START = """我们将讨论如何将以下Python代码转换为等效的SQL查询。
例子:
{example_data}


表结构:
{tables_schema}

表结构前三行:
{tables_schema_first_three}

问题: {question}

有用的线索:
{additional_context}

Python代码:
```
{code}
```

执行结果:
{result}

辩论规则:
1. 正方(DebaterPro)首先分析Python代码并提出SQL转换方案,我之后会有SQL验证程序来横向SQL是否正确，所以不要自己加字段提示。
2. 反方(DebaterCon)对正方的方案提出质疑,主要是执行结果和原始Python是否保持同样语义(不需要完全一致，但是需要保持同样语义)，如果执行结果和原始Python语义相同，那就没什么问题。
3. 正方可以回应反方的质疑并修改SQL，只能一次生成一个SQL
4. 反方可以继续提出新的质疑或确认问题已解决，请注意我之后有正确的Result进行比较验证结果，所以不要生成创造的Result，由数据本身传达就可以了。
5. 辩论裁判(DebateJudge)在充分讨论后提出最终被验证过和Python语义相同的SQL
注意：每个参与者必须使用以下格式提供回复：

辩论正方(DebaterPro)格式：
```xml
<thinking>
分析Python代码并提出SQL转换方案的思考过程...
</thinking>
<sql>提出的SQL</sql>
```

辩论反方(DebaterCon)格式：
```xml
<thinking>
分析正方提出的SQL方案并提出质疑的思考过程，主要是和原始问题的需求顺序和返回顺序是否一致，（不要问题中没有要求的返回列），执行结果和原始Python不需要完全一致，但是需要保持同样语义。
null的返回是有可能的，数据库可能没有足够的信息来解答原始问题。
</thinking>
<key_point>
关键质疑点...
</key_point>
```

辩论裁判(DebateJudge)格式：
```xml
<summary>...</summary>
<sql>最终SQL</sql>
```
请开始辩论。正方先发言。
"""

 

    # SQL生成任务的提示词
    SQL_GENERATION_DIRECT_TASK = """

例子:
{example_data}

表结构:
{tables_schema}

表结构前三行:
{tables_schema_first_three}

问题: {question}

有用的线索:
{additional_context}

{join_paths_guidance}


Think step-by-step and provide your reasoning in <thinking> tags. Then, generate the SQL query in <sql> tags.
请按照以下格式提供你的回答：
```xml
<thinking>

</thinking>
<sql>提出的SQL</sql>
```
"""

    # SQL重新生成任务的提示词
    SQL_REGENERATE_DIRECT_TASK = """请根据反馈修改你的SQL查询：

问题: {question}

非常有用的线索:
{additional_context}

表结构:
{tables_schema}

表结构前三行:
{tables_schema_first_three}

{join_paths_guidance}

之前的SQL:
```sql
{previous_sql}
```

执行结果:
{previous_result}

评估反馈:
{judger_feedback}

请根据上述反馈修改SQL查询，确保新的SQL能够正确回答原始问题。
注意：
1. 确保SQL查询能够准确反映Python代码的数据处理逻辑
2. 不要添加问题中没有要求的返回列
3. 保持与原始问题的需求顺序和返回顺序一致
4. 不要自己创造结果，由数据本身传达就可以了

请按照以下格式提供你的回答：

```xml
<thinking>
分析反馈并修改SQL的思考过程...
</thinking>
<sql>修改后的SQL</sql>
```
"""

    # SQL评估任务的提示词
    SQL_JUDGER_TASK = """请评估以下SQL查询是否正确回答了原始问题：
正确的例子:
{example_data}

问题: {question}

非常有用的线索:
{additional_context}

表结构:
{tables_schema}

Python代码:
{code}
```
Python执行结果:
{result}

SQL查询:
{sql}

SQL执行结果:
{sql_result}


请评估SQL查询是否正确回答了原始问题，并提供详细的分析。
注意：
1. 判断SQL查询是否准确反映Python代码的数据处理逻辑
2. 验证SQL是否保持了与原始问题的需求顺序和返回顺序一致
3. SQLnull的返回是有可能的，数据库可能没有足够的信息来解答原始问题
4.SQL的隐式规则导致的不一致是允许的，比如默认排序，默认返回等，请贴近正确例子的SQL书写风格。
5. SQL的返回结果和Python的返回结果可能不一致，请根据Python的返回结果来判断SQL的正确性，不一致就是错误的。（除非Python的返回结果是报错，这种情况请返回True）


请按照以下格式提供你的评估：

```xml
<reason>
详细解释你的判断理由...如果SQL不正确，请具体指出问题所在和改进建议
</reason>
<QA>
True/False  <!-- 如果SQL正确回答了问题，返回True；否则返回False -->
</QA>
<score>
0.0-1.0之间的分数  <!-- 请给出一个精确到小数点后一位的分数 你觉得转换的分数-->
</score>
```
"""
    SQL_JUDGER_FINAL_TASK = """请评估以下SQL查询是否正确回答了原始问题：

问题: {question}

非常有用的线索:
{additional_context}

表结构:
{tables_schema}

Python代码:
{code}
Python执行结果:
{result}

SQL候选:
{sql}


请你选择上面候选中最能代表Python和其结果的SQL，你只能从上面的SQL候选中选择，不要自己生成SQL。

```xml
<SQL>
完整的SQL语句
</SQL>
```
"""


    # SQL评估任务的提示词
    SQL_JUDGER_WO_TASK = """请评估以下查询结果输出是否合理：
原始问题: {question}
数据库信息:{tables_schema}
线索：{additional_context}

代码:
{sql}

执行结果:
{sql_result}

请进行以下三个判断：
1. 如果你觉得现在的信息对于解答原始问题合理，True。如果你觉得结果输出不合理，请返回False，允许有多余的输出，只要最终的结果合理.
2. 解释你的判断理由。如果你觉得不合理，给出更多我如何继续解决这个问题。
3. 线索的思考逻辑一定要体现在代码中。
4. null的返回是有可能的，数据库可能没有足够的信息来解答原始问题。这种情况请返回True。
请按照以下格式返回你的判断：
```xml
<QA>
    True/False  <!-- 如果你觉得结果对于原始问题不合理，返回False，否则返回True -->
</QA>
<reason>
    <!-- 如果你觉得结果对于原始问题不合理,Analyze the outcome of the executed query to identify why it failed (e.g., syntax
errors, incorrect column references, logical mistakes) -->
</reason>

"""


    SCHEMA_AGENT_SYSTEM = """你是一个专门用于理解数据库模式和设计最优 Join 策略的智能助手。
    你的主要职责是：
    1. 分析用户查询，识别关键表和列
    2. 评估不同的 Join 路径，考虑：
       - 路径长度和复杂性
       - 列覆盖率
       - 语义相关性
       - 数据质量和完整性
    3. 根据查询语义选择最优的 Join 策略
    """
    
    SCHEMA_NAVIGATOR_IDENTIFY_ELEMENTS = """请分析以下查询并识别关键的表和列：
    
    查询: {question}
    数据库模式:
    {tables_schema}
    
    额外上下文:
    {additional_context}
    
    请用 XML 标签返回结果：
    <tables>表1, 表2, ...</tables>
    <columns>列1, 列2, ...</columns>
    <reasoning>你的推理过程...</reasoning>
    """
    
    SCHEMA_NAVIGATOR_FILTER_PATHS = """请评估以下 Join 路径，选择最适合查询的路径：
    
    查询: {question}
    可能的 Join 路径:
    {potential_join_paths}
    
    请用 XML 标签返回结果：
    <selected_paths>1,2,...</selected_paths>
    <reasoning>选择理由...</reasoning>
    """





    PYTHON_SOLVE_DIRECTLY = """
    你可以使用我提供给你的代码API接口。同时，也可以编写其他必要的 Python 代码。
    {tool_description}
    例子:
    {related_python_code}
现在开始解决下面的问题: 
    问题: {question}
    线索：{additional_context}
    db_name: {db_name}    

    请参考以下 Gold SQL 逻辑作为你生成 Python 代码的指导原则，你的 Python 代码应实现与此 SQL 相同的逻辑：
    {gold_sql_logic}
    {gold_sql_result}
    历史代码分析的insights：
    {top3_insights}


    请按照下面的格式生成代码来综合这些信息并解决问题。请确保返回的代码是完整的，用户不需要修改任何代码。用 `controlled_print` 输出你想查看到的内容。
    如果你改不明白，可以输出中间变量。进行debug.

    ```xml
    <thinking>
    ...
    </thinking>
    <code>
    # 例如:
    # df = db_loader('your_db', 'your_table')
    ...
    # controlled_print(final_result)
    </code>
    ```
    """
    Python_Insight_Task = """
    This is the Api of the code:
    {tool_description}
Please reflect on ideas for how to improve your current code. Examine the provided code and think very specifically (with precise ideas) on how to improve performance, which methods to use, how to improve generalization on the test set with line-by-line examples below:\n
    {question}
    {Top3_code}
    """
    PYTHON_GENERATE_WITH_SNOOP = """
    你可以例如函数结构实现SQL的数据处理逻辑，类似下面这样。
# @snoop
def execute_chain() -> tuple[tuple, ...]:
    df_patients = load_df(DB_ID, "patients")
    df_conditions = load_df(DB_ID, "conditions")

    # 1. join，获得每个条件和患者的出生日期
    df_joined = build_join(
        join_type='inner',
        df1=df_conditions,
        df2=df_patients,
        left_on="PATIENT",
        right_on="patient"
    )

    # 2. 计算年龄（年）
    start_ts = op_tsordstotimestamp('START', df=df_joined)
    birth_ts = op_tsordstotimestamp('birthdate', df=df_joined)
    age_days = op_sub(start_ts, birth_ts, df=None).dt.days
    age_years = op_div(age_days, 365, df=None)
    age_rounded = op_round(age_years, decimals=0, df=None)
    cond_age = op_gt(age_rounded, 60, df=None)
    df_filtered_age = df_joined[cond_age]

    # 3. 分组：每种DESCRIPTION和patient
    df_grouped = build_group_by('DESCRIPTION', df=df_filtered_age)
    df_count = op_count('patient', df=df_grouped, alias='count_patient')

    # 4. 找到数量最多的DESCRIPTION
    df_ordered = build_order_by(('count_patient', 'DESC'), df=df_count)
    df_max_description = build_limit(1, df=df_ordered)
    top_description = df_max_description.iloc[0]['DESCRIPTION']
    top_count = df_max_description.iloc[0]['count_patient']
    

    # 5. 满足条件的总患者数
    total_patients = df_filtered_age['patient'].nunique()

    # 6. 计算比例，使用SUM和COUNT逻辑
    count_matching_description = op_sum(op_case(
        (op_eq('DESCRIPTION', top_description), 1),
        else_value=0,
        df=df_filtered_age
    ), df=None)
    
    ratio = op_div(count_matching_description, total_patients, df=None) * 100 if total_patients > 0 else 0.0
    
    return ((top_description, ratio),)

    
    SQL:{SQL_QUERY}
    函数操作:{tool_description}，最后返回的格式是tuple((column1, column2, ...) for row in result)
    
    返回的代码格式如下：
    ```xml
    <thinking>
    ...
    </thinking>
    <code>
@snoop
def execute_chain() -> tuple[tuple, ...]:
    </code>
    ```
    """

    PYTHON_REGENERATE_WITH_SNOOP = """
    SQL:{SQL_QUERY}
    尽量利用这些函数进行操作:{tool_description}

    你之前生成的 Python 代码是：
    {previous_code}

    该代码执行后的结果是：
    {previous_result}

    请分析输出结果，找出代码执行过程中的问题，并重新生成正确的代码。请按照下面的格式返回：
    ```xml
    <thinking>
    分析snoop输出，识别问题所在，并思考如何修正代码逻辑..
    </thinking>
    <code>
@snoop
def execute_chain() -> tuple[tuple, ...]:
    </code>
    ```
    """


    PYTHON_REGENERATE_TASK = """
    {tool_description}
    问题: {question}
    线索：{additional_context}
    db_name: {db_name}

    你之前生成的 Python 代码是：
    {previous_code}
    该代码执行后的结果是：
    {previous_result}

    根据之前的评估和诊断，代码存在以下问题或需要改进：
    {judger_feedback}

    代码错误反思：
    {reflection_feedback}

    历史代码分析的insights：
    {top3_insights}

    你可以使用我提供给你的代码API接口。同时，也可以编写其他必要的 Python 代码。用 `controlled_print` 输出你想查看到的内容。
    如果你改不明白，可以输出中间变量。进行debug.

    请再次参考以下 Gold SQL 逻辑作为你生成 Python 代码的指导原则，你的 Python 代码应实现与此 SQL 相同的逻辑：
    {gold_sql_logic}
    {gold_sql_result}

    ```xml
    <thinking>
    仔细分析提供的反馈和历史insights，识别并修正之前代码中的错误或遗漏，确保新的代码能够准确实现 Gold SQL 逻辑并解决问题。
    </thinking>
    <code>
    # 例如，基于反馈修改的代码:
    # df = db_loader('your_db', 'your_table')
    ...
    # controlled_print(final_result)
    </code>
    ```
    """


    SEMANTICS_EVALUATOR_TASK = """
    原始问题: {question}
    线索：{additional_context}
    db_name: {db_name}

    以下是生成的 Python 代码及其执行结果：
    {code}
    执行结果:
    {result}

    请参考以下 Gold SQL 逻辑。你的核心任务是评估上述 Python 代码的语义是否与此 SQL 逻辑以及原始问题一致。
    Gold SQL 逻辑:
    {gold_sql_logic}

    执行结果:
    {gold_sql_result}

    结果准确性： Python 代码的执行结果是否在语义上与 Gold SQL 的预期结果相符，并能回答原始问题？从而回答Alignment.
    结果正确性： SQL代码的执行结果是否了回答原始问题？从而回答SQL_correct.
    结果正确性： Python 代码的执行结果是否了回答原始问题？从而回答Python_correct.
    代码质量： 请给Python代码的质量打分(0-100)，考虑Python是否解答了问题。

    请以 XML 格式返回你的判断和理由：
    ```xml 
    <Alignment>true/false</Alignment>
    <SQL_correct>true/false</SQL_correct>
    <Python_correct>true/false</Python_correct>
    <difficulty_score>0-100的整数</difficulty_score>
    <reason>
    详细说明为什么是 true 或 false。
    解释difficulty_score的评分理由。

    如果 <Python_correct> 为 **false**：
    1.  **具体不符之处**：请指出 Python 代码与 Gold SQL 逻辑或预期结果不一致的具体点。例如：数据筛选条件错误、聚合方式不正确、API 使用不当、逻辑未完全匹配 Gold SQL 等。
    2.  **修正建议**：给出具体、可操作的建议，以便 PythonCodeGenerator 进行修正。例如，提示使用链式方法逐步处理复杂计算，或者建议输出中间变量进行调试。
    3.如果答案出错，类似CASE，复杂计算，reason中提示使用链式方法，一步步做。
    4.要求输出中间变量。进行debug.
    5.同时注意columns merge可能导致相同的columns rename_x,_y.此外还有大小写在python中是敏感的。

    如果 <SQL_correct> 为 **false**：
    请解释为什么这种差异无法通过修改 Python 代码来解决。这通常意味着问题源于更深层次的、超出代码生成器控制的因素，例如：
    * **SQL 的隐式规则**：数据库特有的隐式类型转换规则或 NULL 值处理逻辑与 Python 的行为存在根本性差异。
    * **数据库特性**：Gold SQL 使用了数据库特定的函数或高级特性，在标准 Python/Pandas 中无法完全模拟。
    *  SQL逻辑错误，未能解决答案。例如value写错，或者不严谨少筛选了内容。
    </reason>
    ```
    """

    JUDGER_SYSTEM = """你是一个QA问题输出评估专家"""

    PYTHON_EVALUATOR_TASK = """
    This is the Api docs: {tool_description}
        
    请评估以下查询结果输出是否合理：
    数据库信息:{tables_schema}
    df_list:{df_list}
    原始问题: {question}
    线索：{additional_context}

    代码:
    {code}
    执行结果:
    {result}

    请进行correct的判断：
    1. 如果你觉得现在的信息对于问题的解答是合理的，输出True。如果你觉得结果输出不合理，请返回False，允许有多余的输出，只要最终的结果合理.
    2. 解释你的判断理由。如果你觉得不合理，给出更多我如何继续解决这个问题。
    3. 线索的思考逻辑一定要体现在代码中。
    4. null/None的返回是有可能的，数据库可能没有足够的信息来解答原始问题。这种情况请返回True。
    5.注意大小写问题，可能导致merge不匹配。
    6. pandas表中如果有两列相同名字的，需要提示重命名。
    7. 可以提示输出中间结果。

    代码质量： 
    1.请给Python代码的质量打分(0-100)，考虑Python是否解答了问题。
    2.Python返回的格式是否符合要求。
    3.Python代码不应该有多余的不属于数据库的文字提示。

    请按照以下格式返回你的判断：
    ```xml
    <correct>True/False</correct>
    <difficulty_score>0-100的整数</difficulty_score>
    <reason>
        <!-- 如果你觉得结果对于原始问题不合理，给出你的指导 -->
    </reason>

    ```
    """

    REFLECTION_TASK = """
    This is the Api docs: {tool_description}
    This is your code: {current_python_code}\n
    Your code returned the following error {current_execution_result_str}. 
    Please provide a detailed reflection on why this error was returned, 
    which lines in the code caused this error, and exactly (line by line) 
    how you hope to fix this in the next update. 
    This step is mostly meant to reflect in order to help your future self fix the error better. 
    Do not provide entirely new code but provide suggestions on how to fix the bug using LINE EDITS.
    如果答案出错，类似CASE，复杂计算，提示使用链式方法。
    要求输出中间变量。进行debug.
    同时注意columns merge可能导致相同的columns rename_x,_y.此外还有大小写在python中是敏感的。"""

    PROBLEM_DIAGNOSER_TASK = """
        {tool_api_docs}
        原始问题: {question}
        线索：{additional_context}
        db_name: {db_name}

        当前的 Python 代码:
        {generated_code}
        执行结果:
        {code_execution_result}

        SQL:
        {Gold_sql}
        SQL执行结果:
        {Gold_sql_result}


        evaluation_feedback:
        {evaluation_feedback}


        请根据以上所有信息，诊断问题出现的原因，并提出下一步的行动建议。你的任务是提供深入的分析和具体的可行方案，帮助系统取得进展。
        请特别考虑以下几点：

        问题根源： 是 Python 代码逻辑错误、工具使用不当、对 Gold SQL 逻辑理解偏差、还是数据或 schema 信息理解错误？
        API 文档关联： 问题是否与工具 API 文档的理解或其局限性有关？
        建议类型： 是建议 PythonCodeGenerator 带着详细提示重新生成代码，还是需要更新底层 API 文档，亦或是底层工具函数代码本身存在问题？

        请以 XML 格式返回诊断报告：
        ```xml
        <diagnosis_report>
            <summary>对问题原因的简要总结。</summary>
            <details>详细说明问题所在，并提供具体的、可操作的提示或代码修改建议。例如："问题在于 `db_loader` 调用时表名错误，正确应为 'patients' 而非 'patient'。"或"`data_filter` 的条件语句不符合 SQL 表达式语法，应使用 'column_name >= 10' 而非 'column_name => 10'。"</details>
            <recommendation>
                REGENERATE_WITH_HINT: 建议 PythonCodeGenerator 带着 details 中的提示重新生成代码。
                UPDATE_API_DOC: API 文档需要更新或完善（例如：缺失某个工具的说明，或者说明不清晰）。
                FIX_FUNCTION_CODE: 底层工具函数（如 `db_loader`, `data_filter` 等）的实现代码本身存在 bug 或功能缺失。
            </recommendation>
        </diagnosis_report>
        ```
        """

    SQL_REFLECTION_TASK = """请对执行失败的SQL进行深入反思：

    原始问题: {question}
    线索：{additional_context}



    SQL使用的数据库：
    数据库信息: {tables_schema}
    数据库信息的前三行: {tables_schema_first_three}


    执行失败的SQL:
    {failed_sql}

    错误信息:
    {error_message}

    请按照以下格式提供你的反思：
    ```xml
    <reflection>
        <!-- 详细分析错误原因，包括具体的问题代码位置和可能的影响 -->
    </reflection>

    ```
    """
    SQL_INSIGHT_GENERATION = """请基于失败的SQL尝试生成洞察：

    原始问题: {question}
    线索：{additional_context}

    Python使用的df_list:
    {df_list}

    PythonCodeGenerator生成的代码：
    {python_code}
    PythonResult:
    {python_result}

    SQL使用的数据库：
    数据库信息: {tables_schema}



    没能转换成功的SQL尝试：
    {top3_failed_sql_list}


    请分析这些失败尝试，生成有价值的洞察，帮助改进后续的SQL生成。

    请按照以下格式提供你的洞察：

    ```xml
    <insight>
     如何帮助改进后续的SQL生成...
    </insight>
    ```
    """

    # SQL生成任务的提示词
    SQL_GENERATION_TASK = """

    相似的SQL例子:
    {example_data}

    
    原始问题: {question}
    线索：{additional_context}

    Python使用的df_list:
    {df_list}

    PythonCodeGenerator生成的代码：
    {python_code}
    PythonResult:
    {python_result}

    SQL使用的数据库：
    数据库信息: {tables_schema}


    请分析Python代码的执行逻辑，并生成一个能够产生相同结果的SQL查询。
    注意：
    1. 确保SQL查询能够准确反映Python代码的数据处理逻辑。
    2. 保持与原始问题的需求顺序和返回顺序一致
    3.注意Python的列名和SQL的列名可能有一点点细微差异。
    4. 如果Python结果报错了，则SQL结果可以不一致。
    5. 不要把提示语句写入SQL。


    请按照以下格式提供你的回答：

    ```xml
    <thinking>
    分析Python代码并提出SQL转换方案的思考过程...
    </thinking>
    <sql>提出的SQL</sql>

    ```
    """

        # SQL重新生成任务的提示词
    SQL_REGENERATE_TASK = """请根据反馈修改你的SQL查询：

    相似的SQL例子:
    {example_data}

    
    原始问题: {question}
    线索：{additional_context}

    Python使用的df_list:
    {df_list}

    PythonCodeGenerator生成的代码：
    {python_code}
    PythonResult:
    {python_result}

    SQL使用的数据库：
    数据库信息: {tables_schema}


    评估反馈:
    {reflection}
    
    之前生成的Insight:
    {insight}

    请根据上述反馈修改SQL查询，确保新的SQL能够正确回答原始问题。
    注意：
    1. 确保SQL查询能够准确反映Python代码的数据处理逻辑。
    2. 保持与原始问题的需求顺序和返回顺序一致
    3.注意Python的列名和SQL的列名可能有一点点细微差异。
    4. 如果Python结果报错了，则SQL结果可以不一致。
    5. 不要把提示语句写入SQL

    请按照以下格式提供你的回答：

    ```xml
    <thinking>
    分析反馈并修改SQL的思考过程...
    </thinking>
    <sql>修改后的SQL</sql>
    ```
    """