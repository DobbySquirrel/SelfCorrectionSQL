from utils.agent_helpers import AgentHelpers
from core.database_connector import DatabaseConnector
from utils.prompts import Prompts
from workflows.sql_validator import SQLValidator
from workflows.sql_generator import SQLGenerator
from workflows.python_generator_workflow import PythonGeneratorWorkflow
import autogen
import re
import types
import json
import os

def load_cached_code(question):
    filename = f"/home/shenshuyu/SQL_tool/generated_code_cache/code_gen_result_{question}.json"
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

class StraightforwardWorkflow:
    """直接工作流，简化的问题解决流程"""
    
    def __init__(self, user_proxy, generator, judger, 
                 aligner, code_executor, answer_format, semantics_evaluator):
        """初始化工作流
        Args:
            user_proxy: 用户代理
            generator: 生成器Agent
            judger: 判断器Agent
            aligner: 对齐器Agent
            code_executor: 代码执行器
            answer_format: 答案格式Agent
            semantics_evaluator: 语义评估Agent
        """
        self.user_proxy = user_proxy
        self.generator = generator
        self.judger = judger
        self.aligner = aligner
        self.code_executor = code_executor
        self.answer_format = answer_format
        self.helpers = AgentHelpers()
        self.sql_validator = SQLValidator(user_proxy, aligner, answer_format)
        self.sql_generator = SQLGenerator(user_proxy, generator, judger)
        # 初始化Python生成器工作流
        self.python_generator = PythonGeneratorWorkflow(
            user_proxy=user_proxy,
            code_generator=generator,
            code_executor=code_executor,
            semantics_evaluator=semantics_evaluator
        )
    
    def solve(self, node, tree, db_name, schema_info=None, additional_context="",
              tables_schema_first_three=None, example_data=None,
              sql_pandas=None, sql_salchemy=None, related_python_code=None,
              analysis_based_on_few_shot_logic=None, df_list=None,id=None):
        """直接工作流解决问题
        Args:
            node: 当前处理的节点
            tree: 推理树
            db_name: 数据库名称
            schema_info: 可选，表结构信息字典
            additional_context: 可选，额外的上下文信息
            tables_schema_first_three: 可选，表结构前三行
            example_data: 可选，示例数据
            sql_pandas: 可选，pandas执行的SQL
            sql_salchemy: 可选，sqlalchemy执行的SQL
            related_python_code: 可选，相关的Python代码
            analysis_based_on_few_shot_logic: 可选，基于少量样本的分析逻辑
            df_list: 可选，可用的DataFrame列表
        """
        # 确保SQL参数不为None，并处理分隔符
        sql_pandas = sql_pandas.split("\t")[0] if sql_pandas else ""
        sql_salchemy = sql_salchemy.split("\t")[0] if sql_salchemy else ""

        # 存储用于check_sql的代码和原因
        code_list_for_check_sql = []  
        reason_list_for_check_sql = [] 

        self.db_name = db_name

        # 重置节点状态
        node.actions = None
        node.cell_value = None
        node.is_solved = False

        # 首先尝试从缓存中读取代码
        cached_data = load_cached_code(id)
        if cached_data and cached_data['is_solved']:
            print(f"从缓存中读取到成功的代码生成结果")
            is_solved = cached_data['is_solved']
            top3_code_records = cached_data['top3_code_records']
            # 更新节点状态
            if len(top3_code_records) > 0:
                best_record = top3_code_records[0]  # 使用得分最高的记录
                node.actions = best_record['code']
                node.cell_value = best_record['result']
                node.is_solved = best_record['is_success']
        else:
            # 如果没有缓存或缓存中的结果不成功，使用Python生成器工作流生成代码
            print(f"没有找到缓存或缓存结果不成功，开始生成新代码")
            is_solved, top3_code_records = self.python_generator.generate_python_code(
                node=node,
                schema_info=schema_info,
                additional_context=additional_context,
                df_list=df_list,
                related_python_code=related_python_code
            )
            
            # 保存生成的代码到缓存
            save_data = {
                "is_solved": is_solved,
                "top3_code_records": [
                    {
                        "code": record["code"],
                        "score": record["score"],
                        "result": record["result"],
                        "feedback": record["feedback"],
                        "is_success": record["is_success"]
                    } for record in top3_code_records
                ]
            }
            
            # 确保目录存在
            os.makedirs("generated_code_cache", exist_ok=True)
            
            # 使用node的问题作为文件名的一部分
            filename = f"/home/shenshuyu/SQL_tool/generated_code_cache/code_gen_result_{id}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

        # 对每个代码记录进行2SQL检查
        code_list_for_check_sql = []
        reason_list_for_check_sql = []
        
        for code_record in top3_code_records:
            # 对每个代码记录单独进行SQL生成
            sql_suggestion, reasoning_for_sql = self.sql_generator.conduct_2sql(
                node=node, 
                python_code=code_record['code'],  # 使用记录中的代码
                python_result=code_record['result'],  # 使用记录中的执行结果
                schema_info=schema_info, 
                additional_context=additional_context,
                db_name=db_name, 
                tables_schema_first_three=tables_schema_first_three, 
                example_data=example_data,
                df_list=df_list
            )
            
            if sql_suggestion:  # 只添加非空的SQL建议
                code_list_for_check_sql.append(sql_suggestion)
                reason_list_for_check_sql.append(reasoning_for_sql)
        # 进行最终的SQL验证
        if code_list_for_check_sql:
            self.sql_validator.check_sql(
                node, db_name, schema_info, Prompts.TOOL_DESCRIPTION,
                sql_pandas, sql_salchemy,
                code_list_for_check_sql, reason_list_for_check_sql,
                example_data, additional_context, tables_schema_first_three,
                analysis_based_on_few_shot_logic,
                top3_code_records
            )

        return node.is_solved