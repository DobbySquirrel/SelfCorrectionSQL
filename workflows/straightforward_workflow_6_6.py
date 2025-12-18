from utils.agent_helpers import AgentHelpers
from core.database_connector import DatabaseConnector
from utils.prompts import Prompts
from workflows.sql_validator import SQLValidator
from workflows.sql_generator import SQLGenerator
import autogen
import re
import types

class StraightforwardWorkflow:
    """直接工作流，简化的问题解决流程"""
    
    def __init__(self, user_proxy, generator, judger, 
                 aligner, code_executor, answer_format):
        """初始化工作流
        Args:
            user_proxy: 用户代理
            generator: 生成器Agent
            judger: 判断器Agent

            aligner: 对齐器Agent
            code_executor: 代码执行器
            answer_format: 答案格式Agent
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
      
    
    def solve(self, node, tree, db_name, schema_info=None, additional_context="",
              tables_schema_first_three=None, example_data=None,
              sql_pandas=None, sql_salchemy=None,related_python_code=None,analysis_based_on_few_shot_logic=None,df_list=None):
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
        """
        # Ensure SQL parameters are not None, and process delimiters
        sql_pandas = sql_pandas.split("\t")[0] if sql_pandas else ""
        sql_salchemy = sql_salchemy.split("\t")[0] if sql_salchemy else ""

        # Store code and results for check_sql. We'll store multiple successful attempts.
        code_list_for_check_sql = []  
        reason_list_for_check_sql = [] 

        self.db_name = db_name

        # Define overall max iterations and judgment failure tolerance
        overall_max_iterations = 15
        max_judger_failures = 4 
        consecutive_judger_failures = 0
        max_successful_solutions = 3 # New: Maximum number of successful solutions to find
        successful_solutions_found = 0 # New: Counter for successful solutions

        # Reset node state for the start of the process
        node.actions = None
        node.cell_value = None
        node.is_solved = False # This will reflect if *any* solution was found
        qa_result=""
        current_code = ""
        current_result_str = ""
        reason = "" # Initialize reason for use in regeneration
        for current_iteration in range(1, overall_max_iterations + 1):

            # Determine whether to generate new code or regenerate based on consecutive failures
            if current_iteration == 1 or consecutive_judger_failures >= max_judger_failures or "true" in qa_result.lower():
                # Fresh generation
                generator_task = Prompts.SOLVE_DIRECTLY.format(
                    question=node.question,
                    tables_schema=schema_info,
                    df_list=df_list,
                    additional_context=additional_context if additional_context else "None",
                    tool_description=Prompts.TOOL_DESCRIPTION,
                    related_python_code=related_python_code
                )
                self.user_proxy.initiate_chat(self.generator, message=generator_task)
                current_code = AgentHelpers.extract_code_from_message(self.user_proxy.last_message(self.generator))
                consecutive_judger_failures = 0 # Reset failure count after a fresh generation
            else:
                # Regeneration based on previous feedback
                # Use current_code and current_result_str from the *previous* iteration for regeneration
                # If this is the first regeneration after a fresh generation, previous_code/result might be from the fresh gen.
                regenerate_task = Prompts.REGENERATE_TASK.format(
                    question=node.question,
                    tables_schema=schema_info,
                    additional_context=additional_context if additional_context else "None",
                    df_list=df_list,
                    previous_code=current_code, # Use the code from the *previous* attempt
                    previous_result=current_result_str, # Use the result from the *previous* attempt
                    tool_description=Prompts.TOOL_DESCRIPTION,
                    judger_feedback=reason, # 'reason' comes from the previous judgment
                    related_python_code=related_python_code
                )
                self.user_proxy.initiate_chat(self.generator, message=regenerate_task)
                current_code = AgentHelpers.extract_code_from_message(self.user_proxy.last_message(self.generator))

            # Execute the generated/regenerated code
            node.update_actions(current_code) # Update node's actions with the latest code
            execution_result = self.code_executor.execute(current_code,"dev")
            current_result_str = AgentHelpers.format_execution_result(execution_result)
            node.update_cell_value(current_result_str) # Update node's cell value with the latest result

            # Let the Judger evaluate the result
            judger_task = Prompts.Strightforward_TASK.format(
                tool_description=Prompts.TOOL_DESCRIPTION,
                question=node.question,
                tables_schema=schema_info,
                df_list=df_list,
                additional_context=additional_context if additional_context else "None",
                code=current_code,
                result=current_result_str,
           
            )    

            self.user_proxy.initiate_chat(self.judger, message=judger_task)
            judgment = self.user_proxy.last_message(self.judger)

            # Parse judgment result
            qa_result = AgentHelpers.extract_xml_tag(judgment, "QA")
            reason = AgentHelpers.extract_xml_tag(judgment, "reason")

            # Decide next step based on judgment
            if "true" in qa_result.lower():
                successful_solutions_found += 1
                print(f"Solution found! ({successful_solutions_found}/{max_successful_solutions} successful solutions)")
                node.is_solved = True # Mark that at least one solution was found
                # Conduct 2-SQL check for this successful solution
                sql_suggestion, reasoning_for_sql = self.sql_generator.conduct_2sql(
                    node, current_code, current_result_str, schema_info, additional_context, 
                    db_name, tables_schema_first_three, example_data
                )
                code_list_for_check_sql.append(sql_suggestion)
                reason_list_for_check_sql.append(reasoning_for_sql)
                
                consecutive_judger_failures = 0 # Reset failure count on success
                
                if successful_solutions_found >= max_successful_solutions:
                    print(f"Reached maximum number of successful solutions ({max_successful_solutions}). Terminating.")
                    break # Break from the main loop if enough solutions are found
            else:
                consecutive_judger_failures += 1 # Increment failure count

            # If not solved and reached max iterations, log
            if current_iteration == overall_max_iterations and successful_solutions_found == 0:
                print(f"Warning: Reached maximum iterations ({overall_max_iterations}) without finding any successful solution.")
                sql_suggestion, reasoning_for_sql = self.sql_generator.conduct_2sql(
                    node, current_code, current_result_str, schema_info, additional_context, 
                    db_name, tables_schema_first_three, example_data
                )
                code_list_for_check_sql.append(sql_suggestion)
                reason_list_for_check_sql.append(reasoning_for_sql)
                

            elif current_iteration == overall_max_iterations and successful_solutions_found < max_successful_solutions:
                print(f"Reached maximum iterations ({overall_max_iterations}). Found {successful_solutions_found} out of {max_successful_solutions} desired solutions.")


        # After the main loop, if any solutions were found, proceed to final validation if necessary
        # The check_sql call will now work with potentially multiple SQL suggestions
        if code_list_for_check_sql: # Only call if at least one SQL was suggested
            self.sql_validator.check_sql(
                node, db_name, schema_info, Prompts.TOOL_DESCRIPTION,
                sql_pandas, sql_salchemy,
                code_list_for_check_sql, reason_list_for_check_sql,
                example_data, additional_context, tables_schema_first_three,
                analysis_based_on_few_shot_logic
            )

        return node.is_solved # Return whether at least one solution was ultimately found