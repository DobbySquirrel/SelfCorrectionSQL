import pandas as pd
import json
import re
import types

from utils.agent_helpers import AgentHelpers
from core.database_connector import DatabaseConnector
from utils.prompts import Prompts
from workflows.sql_validator import SQLValidator
from workflows.sql_generator import SQLGenerator
import autogen

class GenerateFunctionWorkflow:
    """生成函数工作流，专注于将 SQL 逻辑解析为 Python 函数调用"""
    
    def __init__(self, user_proxy,code_executor,python_code_generator, semantics_evaluator, problem_diagnoser):
        """初始化工作流
        Args:
            user_proxy: 用户代理
            python_code_generator: 负责生成 Python 代码的 Agent
            semantics_evaluator: 负责评估语义一致性的 Agent
            problem_diagnoser: 负责诊断问题的 Agent

        """

        self.user_proxy = user_proxy
        self.python_code_generator = python_code_generator
        self.semantics_evaluator = semantics_evaluator
        self.problem_diagnoser = problem_diagnoser
        self.code_executor = code_executor
        self.helpers = AgentHelpers()
        # 加载工具 API 文档，使用 LLM 更易理解和解析的 JSON 格式
        self.tool_api_docs = self._load_tool_api_docs() 
        
    def _load_tool_api_docs(self) -> dict:
        """
        加载预生成的工具 API 文档，以 LLM 更易理解和解析的 JSON 格式返回。
        为每个工具添加了正确用法和错误用法示例。
        
        返回:
            dict: 包含所有工具 API 描述的字典。
        """
        return {
    "db_loader": {
        "name": "db_loader",
        "description": "从指定数据库加载目标表格数据。",
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
    }
}

    def solve(self, node, tree, db_name, additional_context="",Gold_sql=None):
        """
        直接工作流解决问题。
        此方法协调多个 Agent 来尝试生成、执行、评估并诊断 Python 代码，以回答用户问题。
        """
        # 确保 SQL 参数不为 None，并处理分隔符
        # 这些是原始的 SQL 答案，用于 SemanticsEvaluator 进行对比
        self.db_name = db_name
        
        # 执行Gold_sql获取结果
        Gold_sql_result = None
        if Gold_sql:
            print(f"执行Gold SQL:\n{Gold_sql}")
            db_connector = DatabaseConnector(f"/home/shenshuyu/SQL_dataset/train/train_databases/{db_name}/{db_name}.sqlite")
            if db_connector.connect():
                sql_execution_result, error_message = db_connector.execute_query_cell(Gold_sql)
                if sql_execution_result is not None:
                    sql_result_dict = {'result': sql_execution_result, 'stdout': '', 'stderr': '', 'error': None}
                    Gold_sql_result = AgentHelpers.format_execution_result(sql_result_dict)
                else:
                    Gold_sql_result = f"SQL执行错误: {error_message}"
                db_connector.disconnect()
            else:
                Gold_sql_result = "SQL执行错误: 无法连接到数据库"

        # 定义整体最大迭代次数和语义评估失败容忍度
        overall_max_iterations = 7
        # 单次由 PythonCodeGenerator 尝试生成代码后，SemanticsEvaluator 允许的重试次数
        # 这意味着 Generator 有机会根据 Evaluator 的反馈进行多次修正
        max_evaluator_failures_per_gen = 3 
        consecutive_evaluator_failures = 0 # 跟踪连续语义评估失败次数

        max_successful_solutions = 1 # 默认只找一个成功的解决方案，可以根据需求调整
        successful_solutions_found = 0 # 成功解决方案计数器

        # 重置节点状态以开始处理
        node.actions = None
        node.cell_value = None
        node.is_solved = False # 这将反映在整个流程中是否找到了 *任何* 最终被接受的解决方案

        current_python_code = ""
        current_execution_result_str = ""
        # 'feedback_from_evaluator' 用于从 SemanticsEvaluator 获取反馈，并传递给 PythonCodeGenerator 进行重写
        feedback_from_evaluator = "" 
        diagnosis_report = {
            'summary': "",
            'details': "",
            'recommendation': ""
        }

        print(f"开始解决问题: {node.question} (数据库: {db_name})")

        # 修改 JSON 序列化方式，确保中文正常显示
        tool_api_docs_json = json.dumps(
            self.tool_api_docs, 
            indent=2,
            ensure_ascii=False  # 添加这个参数来保持中文字符的原样显示
        )

        for current_iteration in range(1, overall_max_iterations + 1):
            print(f"\n--- 迭代 {current_iteration}/{overall_max_iterations} ---")

            # 决定是生成新代码还是根据之前的反馈重新生成
            # 如果是第一轮，或者 Generator 之前已经连续多次尝试（达到阈值）且都被 Evaluator 拒绝，
            # 则 ProblemDiagnoser 可能需要介入提供新的策略或重置状态
            if current_iteration == 1 or consecutive_evaluator_failures >= max_evaluator_failures_per_gen:
                if current_iteration > 1: # 只有在非首次生成且达到阈值时才触发诊断
                    print(f"连续语义评估失败达到阈值 ({max_evaluator_failures_per_gen})，启动问题诊断。")
                    # 构建诊断任务
                    diagnosis_task = Prompts.PROBLEM_DIAGNOSER_TASK.format(
                        question=node.question,
                        additional_context=additional_context if additional_context else "None",
                        db_name=db_name,
                        generated_code=current_python_code,
                        code_execution_result=current_execution_result_str,
                        Gold_sql=Gold_sql,
                        Gold_sql_result=Gold_sql_result,
                        evaluation_feedback=feedback_from_evaluator,
                        tool_api_docs=tool_api_docs_json
                    )
                    
                    # 让诊断器分析问题
                    self.user_proxy.initiate_chat(self.problem_diagnoser, message=diagnosis_task)
                    diagnosis_response = self.user_proxy.last_message(self.problem_diagnoser)
                    
                    # 从XML格式的响应中提取信息
                    summary = AgentHelpers.extract_xml_tag(diagnosis_response, "summary")
                    details = AgentHelpers.extract_xml_tag(diagnosis_response, "details")
                    recommendation = AgentHelpers.extract_xml_tag(diagnosis_response, "recommendation")
                    
                    diagnosis_report = {
                        'summary': summary,
                        'details': details,
                        'recommendation': recommendation.strip()
                    }
                    
                    print(f"诊断报告：\n{diagnosis_report['summary']}")
                    if 'REGENERATE_WITH_HINT' in diagnosis_report['recommendation']:
                        # 将诊断器提供的详细提示作为新的反馈，传递给 PythonCodeGenerator
                        feedback_from_evaluator = diagnosis_report['details'] 
                        consecutive_evaluator_failures = 0 # 重置计数，给 Generator 新的机会
                        print(f"诊断建议：重写代码并采纳提示。重置失败计数。")
                    elif 'UPDATE_API_DOC' in diagnosis_report['recommendation']:
                        print(f"诊断建议：**需要更新 API 文档** - {diagnosis_report['details']}。请人工介入。")
                        node.is_solved = False # 标记为未解决，需要外部干预
                        return node.is_solved # 退出工作流，等待文档更新
                    elif 'FIX_FUNCTION_CODE' in diagnosis_report['recommendation']:
                        print(f"诊断建议：**需要修复底层函数代码** - {diagnosis_report['details']}。请人工介入。")
                        node.is_solved = False # 标记为未解决
                        return node.is_solved # 退出工作流，等待人工修复
                    else: # 诊断器没有明确建议，或建议继续尝试，按全新生成处理
                        print("诊断器没有明确的行动建议或建议继续尝试，进行全新生成。")
                        feedback_from_evaluator = "" # 清空反馈，按全新生成处理
                        consecutive_evaluator_failures = 0 # 重置失败计数
                
                # 构建 Generator 任务，明确要求基于 Gold SQL 逻辑解析为 Python
                generator_task = Prompts.PYTHON_SOLVE_DIRECTLY.format(
                    db_name=db_name,
                    question=node.question,
                    additional_context=additional_context if additional_context else "None",
                    gold_sql_logic=Gold_sql,
                    gold_sql_result=Gold_sql_result,
                    tool_description=tool_api_docs_json+diagnosis_report['summary'] if diagnosis_report['summary'] else tool_api_docs_json, # 提供新的序列化结果
                )
                print("PythonCodeGenerator 开始生成代码...")
                self.user_proxy.initiate_chat(self.python_code_generator, message=generator_task)
                current_python_code = AgentHelpers.extract_code_from_message(
                    self.user_proxy.last_message(self.python_code_generator)
                )
            else:
                # 构建 Generator 重写任务，明确要求基于 Gold SQL 逻辑解析为 Python
                regenerate_task = Prompts.PYTHON_REGENERATE_TASK.format(
                    db_name=db_name,
                    question=node.question,
                    additional_context=additional_context if additional_context else "None",
                    gold_sql_logic=Gold_sql,
                    gold_sql_result=Gold_sql_result,
                    previous_code=current_python_code,
                    previous_result=current_execution_result_str,
                    judger_feedback=feedback_from_evaluator,
                    tool_description=tool_api_docs_json+diagnosis_report['summary'] if diagnosis_report['summary'] else tool_api_docs_json, # 再次提供新的序列化结果
                )
                print("PythonCodeGenerator 根据反馈重写代码...")
                self.user_proxy.initiate_chat(self.python_code_generator, message=regenerate_task)
                current_python_code = AgentHelpers.extract_code_from_message(
                    self.user_proxy.last_message(self.python_code_generator)
                )

            print(f"生成的 Python 代码:\n```python\n{current_python_code}\n```")

            # 执行生成/重新生成的代码
            node.update_actions(current_python_code)
            execution_output = self.code_executor.execute_train(current_python_code)
            current_execution_result_str = AgentHelpers.format_execution_result(execution_output)
            node.update_cell_value(current_execution_result_str)

            print(f"代码执行结果:\n{current_execution_result_str}")

            # 让 SemanticsEvaluator 评估结果
            evaluator_task = Prompts.SEMANTICS_EVALUATOR_TASK.format( 
                db_name=db_name,
                question=node.question,
                additional_context=additional_context if additional_context else "None",
                code=current_python_code,
                result=current_execution_result_str,
                gold_sql_logic=Gold_sql,
                gold_sql_result=Gold_sql_result,
                tool_description=tool_api_docs_json
            )
            print("SemanticsEvaluator 正在评估语义一致性...")
            self.user_proxy.initiate_chat(self.semantics_evaluator, message=evaluator_task)
            evaluation_judgment = self.user_proxy.last_message(self.semantics_evaluator)

            # 解析评估结果
            qa_result = AgentHelpers.extract_xml_tag(evaluation_judgment, "QA")
            feedback_from_evaluator = AgentHelpers.extract_xml_tag(evaluation_judgment, "reason")

            # 根据评估结果决定下一步
            if "true" in qa_result.lower():
                successful_solutions_found += 1
                print(f"语义评估成功！找到解决方案！({successful_solutions_found}/{max_successful_solutions} 个成功解决方案)")
                node.is_solved = True 
                consecutive_evaluator_failures = 0 
                
                if successful_solutions_found >= max_successful_solutions:
                    print(f"已达到最大成功解决方案数量 ({max_successful_solutions})。终止主循环。")
                    break 
            else:
                consecutive_evaluator_failures += 1
                print(f"语义评估失败。将 '{feedback_from_evaluator}' 反馈给 PythonCodeGenerator。连续失败次数: {consecutive_evaluator_failures}")

            if current_iteration == overall_max_iterations and successful_solutions_found == 0:
                print(f"警告：达到最大迭代次数 ({overall_max_iterations})，未找到任何成功解决方案。")
                break 
            elif current_iteration == overall_max_iterations and successful_solutions_found < max_successful_solutions:
                print(f"达到最大迭代次数 ({overall_max_iterations})。找到了 {successful_solutions_found} 个（共 {max_successful_solutions} 个）所需解决方案。")
                break 

        return node.is_solved
    