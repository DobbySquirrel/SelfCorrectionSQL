import pandas as pd
import json
import re
import types
import subprocess
import tempfile
import os

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

        """
        self.user_proxy = user_proxy
        self.python_code_generator = python_code_generator
        self.semantics_evaluator = semantics_evaluator
        self.problem_diagnoser = problem_diagnoser
        self.code_executor = code_executor
        self.helpers = AgentHelpers()
        self.code_history = []
        self.max_history_size = 3
        self.top3_insights = []

    def create_evaluation_template(self, db_id, sql_query, generated_function):
        """创建评估模板，将生成的函数嵌入到测试环境中"""
        # 读取模板文件
        template_path = "/home/shenshuyu/SQL_tool/utils/evaluation_template.py"
        with open(template_path, 'r', encoding='utf-8') as f:
            template = f.read()
        safe_sql_query = repr(sql_query)[1:-1]
        
        # 替换模板中的占位符
        template = template.format(
            sys_path="/home/shenshuyu/SQL_tool",
            db_id=db_id,
            sql_query=safe_sql_query,
            generated_function=generated_function
        )
        
        return template

    def execute_evaluation(self, template_code, db_id):
        """执行评估模板并返回结果"""

        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(template_code)
            temp_file = f.name
        
        # 执行代码
        result = subprocess.run(
            ['python', temp_file], 
            capture_output=True, 
            text=True, 
            cwd='/home/shenshuyu/SQL_tool/tongji/'
        )
        
        # 清理临时文件
        os.unlink(temp_file)
        
        return {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode
        }


    def extract_function_from_code(self, code):
        """从生成的代码中提取execute_chain函数"""
        # 查找execute_chain函数定义
        pattern = r'@snoop\s*\ndef execute_chain\(\)[^:]*:(.*?)(?=\n\S|$)'
        match = re.search(pattern, code, re.DOTALL)
        if match:
            return f"@snoop\ndef execute_chain() -> tuple[tuple, ...]:\n{match.group(1)}"
        return None

    def solve(self, node, tree, db_name, additional_context="",Gold_sql=None,related_python_code=None,related_sql=None,operations=None):
        """
        直接工作流解决问题。
        此方法协调多个 Agent 来尝试生成、执行、评估 Python 代码，以回答用户问题。
        """
        # 设置当前问题，用于生成insights
        self.current_question = node.question
        
        # 确保 SQL 参数不为 None，并处理分隔符
        self.db_name = db_name
        
        # 执行Gold_sql获取结果
        Gold_sql_result = None
        if Gold_sql:
            print(f"执行Gold SQL:\n{Gold_sql}")
            db_connector = DatabaseConnector(db_name)
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

        # 定义整体最大迭代次数
        overall_max_iterations = 3
        # 重置节点状态
        node.actions = None
        node.cell_value = None
        node.is_solved = False
        current_python_code = ""
        current_execution_result_str = ""
        feedback_from_evaluator = ""
        
        print(f"开始解决问题: {node.question} (数据库: {db_name})")
        
        for current_iteration in range(1, overall_max_iterations + 1):
            print(f"\n--- 迭代 {current_iteration}/{overall_max_iterations} ---")

            # 生成或重新生成代码
            if current_iteration == 1:
                generator_task = Prompts.PYTHON_GENERATE_WITH_SNOOP.format(
                    SQL_QUERY=Gold_sql,
                    tool_description=operations
                )
                print("PythonCodeGenerator 开始生成代码...")
                self.user_proxy.initiate_chat(self.python_code_generator, message=generator_task)
                current_python_code = AgentHelpers.extract_code_from_message(
                    self.user_proxy.last_message(self.python_code_generator)
                )
            else:
                # 重新生成代码，包含之前的执行结果作为反馈
                regenerate_task = Prompts.PYTHON_REGENERATE_WITH_SNOOP.format(
                    SQL_QUERY=Gold_sql,
                    tool_description=operations,
                    previous_code=current_python_code,
                    previous_result=current_execution_result_str
                )
                print("PythonCodeGenerator 根据snoop结果重新生成代码...")
                self.user_proxy.initiate_chat(self.python_code_generator, message=regenerate_task)
                current_python_code = AgentHelpers.extract_code_from_message(
                    self.user_proxy.last_message(self.python_code_generator)
                )

            print(f"生成的 Python 代码:\n```python\n{current_python_code}\n```")

            # 提取execute_chain函数
            extracted_function = self.extract_function_from_code(current_python_code)
            if not extracted_function:
                print("无法提取execute_chain函数，继续下一次迭代")
                current_execution_result_str = "错误：无法提取execute_chain函数"
                continue

            # 创建评估模板
            evaluation_template = self.create_evaluation_template(db_name, Gold_sql, extracted_function)
            
            # 执行评估
            evaluation_result = self.execute_evaluation(evaluation_template, db_name)
            current_execution_result_str = (
                f"STDOUT:\n{evaluation_result['stdout']}\n"
                f"STDERR:\n{evaluation_result['stderr']}"
            )
            
            # 检查是否成功执行
            if not evaluation_result['success']:
                print(f"评估执行失败，继续下一次迭代")
                continue

            # 检查结果是否一致
            if "结果是否一致: True" in evaluation_result['stdout']:
                print("✅ 结果一致！问题解决成功")
                node.is_solved = True
                return node.is_solved, current_python_code, current_execution_result_str, {"status": "success", "message": "结果一致"}
            else:
                print("❌ 结果不一致，需要重新生成")
                if current_iteration == overall_max_iterations:
                    print(f"达到最大迭代次数 ({overall_max_iterations})，停止尝试")
                    node.is_solved = False
                    node.unfixable_reason = "达到最大迭代次数，结果仍不一致"
                    return node.is_solved, current_python_code, current_execution_result_str, {"status": "failed", "message": "结果不一致"}

        # 如果达到最大迭代次数仍未解决
        return False, current_python_code, current_execution_result_str, {"status": "failed", "message": "达到最大迭代次数"}