from utils.agent_helpers import AgentHelpers
from core.database_connector import DatabaseConnector
from utils.prompts import Prompts
import autogen
import re
import types

class StraightforwardWorkflow:
    """直接工作流，简化的问题解决流程"""
    
    def __init__(self, user_proxy, generator, judger, 
                 debater_pro, debater_con, debate_judge,
                 sql_executor, aligner, code_executor, answer_format):
        """初始化工作流
        
        Args:
            user_proxy: 用户代理
            generator: 生成器Agent
            judger: 判断器Agent
            debater_pro: 辩论正方Agent
            debater_con: 辩论反方Agent
            debate_judge: 辩论裁判Agent
            sql_executor: SQL执行器Agent
            aligner: 对齐器Agent
            code_executor: 代码执行器
            answer_format: 答案格式Agent
        """
        self.user_proxy = user_proxy
        self.generator = generator
        self.judger = judger
        self.debater_pro = debater_pro
        self.debater_con = debater_con
        self.debate_judge = debate_judge
        self.sql_executor = sql_executor
        self.aligner = aligner
        self.code_executor = code_executor
        self.answer_format = answer_format
        self.helpers = AgentHelpers()
        
        # 保存原始方法到debater_pro对象上
        self.debater_pro._original_generate_reply = self.debater_pro.generate_reply
        
        # 创建一个闭包函数来保持对self的引用
        # 注意：当作为方法调用时，agent_self会自动作为第一个参数传入
        def wrapped_custom_generate_reply(messages=None, sender=None, config=None):
            # 这里不需要agent_self参数，因为这个函数会被作为方法调用
            # 此时self.debater_pro会自动作为第一个参数传入custom_debater_pro_generate_reply
            return self.custom_debater_pro_generate_reply(self.debater_pro, messages, sender, config)
            
        # 绑定闭包函数到debater_pro
        self.debater_pro.generate_reply = wrapped_custom_generate_reply
    # 定义SQL执行函数
    def execute_sql(self,sql):
        """执行SQL并返回结果"""
        try:
            db_connector = DatabaseConnector(self.db_name)
            if db_connector.connect():
                sql_result_df, error_message = db_connector.execute_query_cell(sql)
                if sql_result_df is not None:
                    sql_result_dict = {'result': sql_result_df, 'stdout': '', 'stderr': '', 'error': None}
                    result = AgentHelpers.format_execution_result(sql_result_dict)
                else:
                    result = f"SQL执行错误: {error_message}"
                db_connector.disconnect()
                return result
            else:
                return "无法连接到数据库"
        except Exception as e:
            return f"执行SQL时发生错误: {str(e)}.请注意是不是返回格式错误。需要<sql></sql>标签"
    def custom_debater_pro_generate_reply(self, agent_self, messages=None, sender=None, config=None):
        # 检查是否在递归调用中
        if getattr(agent_self, '_executing_sql', False):
            # 如果是递归调用，直接使用原始方法并且不执行SQL
            return agent_self._original_generate_reply(messages=messages, sender=sender, config=config)
        
        try:
            # 设置标志表示正在执行SQL过程中
            agent_self._executing_sql = True
            
            # 调用原始方法获取回复
            reply = agent_self._original_generate_reply(messages=messages, sender=sender, config=config)
            
            # 检查回复中是否包含SQL，并且之前没有执行过SQL
            sql_xml_pattern = r"<sql>(.*?)</sql>"
            if "```" in reply:
                reply = reply.replace("```", "")
            sql_xml_matches = re.findall(sql_xml_pattern, reply, re.DOTALL)
            
            # 检查回复中是否已经包含SQL执行结果
            if "自动执行SQL结果:" not in reply and sql_xml_matches and hasattr(self, 'db_name'):
                sql = sql_xml_matches[0].strip()
                result = self.execute_sql(sql)
                reply += f"\n\n自动执行SQL结果:\n\n{result}"
            
            return reply
        finally:
            # 无论如何都要重置标志
            agent_self._executing_sql = False
     
    
    def solve(self, node, tree, db_name, schema_info=None, additional_context="",
            tables_schema_first_three=None, example_data=None,
            sql_pandas=None, sql_salchemy=None):
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

        # Store code and results for two runs
        code_list_for_check_sql = []  # To store the main SQLs from each run
        reason_list_for_check_sql = [] # To store the reasoning for each main SQL

        self.db_name = db_name

        # Run two complete iteration loops
        for run in [1,0]:
            # Initialize variables for the current run
            current_code = ""  # Accumulates all code parts for the current iteration's feedback
            current_result_str = ""  # Accumulates all result parts for the current iteration's feedback
            main_code_for_check = ""  # Will hold the identified 'main_code' for run=1's final check
            max_iterations = 5
            iterations = 0

            # If this is the second run, save the first run's result before resetting node state
            if run == 1:
                # These were already captured at the end of run 0, so no need to re-snapshot here
                # just need to ensure the node state is clean for run 1's iterations
                node.actions = None
                node.cell_value = None
                node.is_solved = False # Reset solved state

            while iterations < max_iterations:
                iterations += 1

                if run == 0:
                    if iterations == 1:
                        # Initial code generation for run 0
                        generator_task = Prompts.SOLVE_DIRECTLY.format(
                            question=node.question,
                            tables_schema_first_three=tables_schema_first_three,
                            tables_schema=schema_info,
                            additional_context=additional_context,
                            tool_description=Prompts.TOOL_DESCRIPTION,
                        )
                        self.user_proxy.initiate_chat(self.generator, message=generator_task)
                        current_code = AgentHelpers.extract_code_from_message(self.user_proxy.last_message(self.generator))
                    else:
                        # Regeneration for run 0
                        regenerate_task = Prompts.REGENERATE_TASK.format(
                            question=node.question,
                            tables_schema=schema_info,
                            tables_schema_first_three=tables_schema_first_three,
                            additional_context=additional_context,
                            previous_code=current_code,
                            previous_result=current_result_str,
                            tool_description=Prompts.TOOL_DESCRIPTION,
                            judger_feedback=reason # 'reason' comes from the previous judgment
                        )
                        self.user_proxy.initiate_chat(self.generator, message=regenerate_task)
                        current_code = AgentHelpers.extract_code_from_message(self.user_proxy.last_message(self.generator))

                    # Execute and update node for run 0
                    node.update_actions(current_code)
                    execution_result = self.code_executor.execute(current_code)
                    current_result_str = AgentHelpers.format_execution_result(execution_result)
                    node.update_cell_value(current_result_str)

                elif run == 1:
                    if iterations == 1:
                        # Initial code generation for run 1 (sub-tasks and main code)
                        generator_task = Prompts.GENERATOR_SUBTASK.format(
                            question=node.question,
                            tables_schema_first_three=tables_schema_first_three,
                            tables_schema=schema_info,
                            additional_context=additional_context,
                            tool_description=Prompts.TOOL_DESCRIPTION,
                        )
                        self.user_proxy.initiate_chat(self.generator, message=generator_task)
                        generated_codes = AgentHelpers.extract_multi_code_from_message(self.user_proxy.last_message(self.generator))
                        main_code_for_check = generated_codes["code"]

                        # Accumulate all code and results for feedback to the judger
                        current_code = ""
                        current_result_str = ""
                        if main_code_for_check:
                            # If main_code is separate, execute it too and append
                            main_code_exec_result = self.code_executor.execute(main_code_for_check)
                            main_code_result_str = AgentHelpers.format_execution_result(main_code_exec_result)
                            current_code += f"<code>\n{main_code_for_check}\n</code>\n\n" # Store in full for judger feedback
                            current_result_str += f"\n{main_code_result_str}\n\n"


                    else:
                        # Regeneration for run 1 (sub-tasks and main code)
                        regenerate_task = Prompts.REGENERATE_SUB_TASK.format(
                            question=node.question,
                            tables_schema=schema_info,
                            tables_schema_first_three=tables_schema_first_three,
                            additional_context=additional_context,
                            previous_code=current_code, # Send accumulated code from previous iteration for context
                            previous_result=current_result_str, # Send accumulated result from previous iteration
                            tool_description=Prompts.TOOL_DESCRIPTION,
                            judger_feedback=reason
                        )
                        self.user_proxy.initiate_chat(self.generator, message=regenerate_task)
                        generated_codes = AgentHelpers.extract_multi_code_from_message(self.user_proxy.last_message(self.generator))
                        main_code_for_check = generated_codes["code"]

                        current_code = ""
                        current_result_str = ""
                        if main_code_for_check:
                            main_code_exec_result = self.code_executor.execute(main_code_for_check)
                            main_code_result_str = AgentHelpers.format_execution_result(main_code_exec_result)
                            current_code += f"<code>\n{main_code_for_check}\n</code>\n\n"
                            current_result_str += f"\n{main_code_result_str}\n\n"

                    # Update node for run 1 with accumulated code and results
                    node.update_actions(current_code)
                    if "f-string" in current_result_str:
                        current_result_str = current_result_str+"请分步骤方法解决反斜杠问题"
                    node.update_cell_value(current_result_str)

                # Let the Judger evaluate the result
                judger_task = Prompts.Strightforward_TASK.format(
                    question=node.question,
                    tables_schema=schema_info,
                    additional_context=additional_context,
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
                    node.mark_as_solved()
                    break # Break inner loop, solution found
                # If not solved, loop will continue for regeneration if max_iterations not reached

            # After the inner while loop finishes (either solved or max_iterations reached)

            # Conduct 2SQL for the current run
            # For run 0, use current_code (which is the single code block)
            # For run 1, use main_code_for_check
            code_for_2sql = main_code_for_check if run == 1 else current_code
            sql_suggestion, reasoning = self.conduct_2sql(
                node, code_for_2sql, current_result_str, schema_info, additional_context, db_name, tables_schema_first_three, example_data
            )

            # Save SQL suggestion and its reasoning to the lists
            code_list_for_check_sql.append(sql_suggestion)
            reason_list_for_check_sql.append(reasoning)
        # After both runs are complete, call check_sql with the collected lists
        self.check_sql(node, db_name, schema_info, Prompts.TOOL_DESCRIPTION,
                        sql_pandas, sql_salchemy,
                        code_list_for_check_sql, reason_list_for_check_sql,
                        example_data, additional_context, tables_schema_first_three)

        return False
    def check_sql(self, node, db_name, schema_info, tool_description, sql_pandas, sql_salchemy,
                sql_list, reason_list, example_data, additional_context, tables_schema_first_three):
        """验证SQL结果与代码执行结果是否一致

        Args:
            node: 当前处理的节点
            db_name: 数据库名称
            schema_info: 表结构信息字典
            tool_description: 工具描述
            sql_pandas: pandas执行的SQL (备用)
            sql_salchemy: sqlalchemy执行的SQL (备用)
            sql_list: 建议的SQL语句列表
            reason_list: 对应的推理过程列表
            example_data: 示例数据
            additional_context: 额外上下文
            tables_schema_first_three: 表结构前三行
        """

        # 初始化SQL队列
        sql_queue = []
        reasoning_queue = []

        # 添加辩论生成的SQL和推理过程
        for sql, reason in zip(sql_list, reason_list):
            if sql and sql not in sql_queue:
                sql_queue.append(sql)
                reasoning_queue.append(reason)

        # 如果队列中的SQL不足两个，使用外部SQL补充
        if len(sql_queue) < 2:
            if sql_pandas and sql_pandas not in sql_queue:
                sql_queue.append(sql_pandas)
                reasoning_queue.append("外部生成的SQL (pandas)")

            if len(sql_queue) < 2 and sql_salchemy and sql_salchemy not in sql_queue:
                sql_queue.append(sql_salchemy)
                reasoning_queue.append("外部生成的SQL (sqlalchemy)")

        if not sql_queue:
            return  # 如果没有有效的SQL，则直接返回

        def run_answer_format():
            answer_format_task = Prompts.ANSWER_FORMAT_TASK.format(
                question=node.question,
            )
            self.user_proxy.initiate_chat(self.answer_format, message=answer_format_task)
            return self.user_proxy.last_message(self.answer_format)

        answer_format = run_answer_format()
        answer_format = AgentHelpers.extract_xml_tag(answer_format, "answer")

        # 执行所有SQL并获取结果
        sql_results = []
        for sql in sql_queue:
            db_connector = DatabaseConnector(db_name)
            if db_connector.connect():
                sql_result_df, error_message = db_connector.execute_query_cell(sql)
                if sql_result_df is not None:
                    # 查询成功
                    sql_result_dict = {'result': sql_result_df, 'stdout': '', 'stderr': '', 'error': None}
                else:
                    # 查询失败
                    sql_result_dict = {
                        'result': None,
                        'stdout': '',
                        'stderr': '',
                        'error': {'type': 'SQLError', 'message': error_message, 'traceback': ''}
                    }
                sql_results.append(AgentHelpers.format_execution_result(sql_result_dict))
                db_connector.disconnect()
            else:
                sql_results.append("无法连接到数据库")

        # 尝试对每个SQL进行重新格式化
        # 创建一个副本，以便在迭代时可以修改原始队列
        original_sql_queue_len = len(sql_queue)
        for i in range(original_sql_queue_len):
            try:
                reformat_task = Prompts.ANSWER_REFORMAT_TASK.format(
                    question=node.question,
                    answer_format=answer_format,
                    sql_output_format=sql_queue[i]
                )
                self.user_proxy.initiate_chat(self.aligner, message=reformat_task)
                reformat_result = self.user_proxy.last_message(self.aligner)
                reformatted_sql = AgentHelpers.extract_xml_tag(reformat_result, "SQL")

                if reformatted_sql and reformatted_sql.strip() != sql_queue[i].strip():
                    # 执行重新格式化的SQL
                    db_connector = DatabaseConnector(db_name)
                    if db_connector.connect():
                        sql_result_df, error_message = db_connector.execute_query_cell(reformatted_sql)
                        if sql_result_df is not None:
                            # 查询成功
                            sql_result_dict = {'result': sql_result_df, 'stdout': '', 'stderr': '', 'error': None}
                            reformatted_result_formatted = AgentHelpers.format_execution_result(sql_result_dict)

                            # 比较执行结果，只有当结果不同时才添加到队列
                            if reformatted_result_formatted != sql_results[i]:
                                # 添加重新格式化的SQL到队列
                                sql_queue.append(reformatted_sql)
                                reasoning_queue.append(f"基于问题需求重新格式化的SQL (基于SQL {i+1})")
                                sql_results.append(reformatted_result_formatted)
                        db_connector.disconnect()
            except Exception as e:
                print(f"重新格式化SQL {i+1}时出错: {str(e)}")

        # 使用Aligner选择最佳SQL
        def run_aligner():
            aligner_task = Prompts.FINAL_ALIGNER_TASK.format(
                example_data=example_data,
                question=node.question,
                tables_schema=schema_info,
                tables_schema_first_three=tables_schema_first_three,
                additional_context=additional_context,
                sql_candidates="\n\n".join([f"SQL {i+1}:\n{sql}\n推理：{reasoning}\n执行结果：{result}"
                                            for i, (sql, reasoning, result) in enumerate(zip(sql_queue, reasoning_queue, sql_results))]),
            
            )

            self.user_proxy.initiate_chat(self.aligner, message=aligner_task)
            return self.user_proxy.last_message(self.aligner)

        # 最大尝试次数
        max_attempts = 5
        current_attempt = 0
        best_sql = None
        existence = None
        reason = None

        # 循环尝试生成和评估SQL
        # 循环尝试生成和评估SQL
        while current_attempt < max_attempts:
            current_attempt += 1

            # 运行Aligner
            alignment_result = run_aligner()

            # 解析最佳SQL和存在性判断
            best_sql_candidate = AgentHelpers.extract_xml_tag(alignment_result, "SQL")
            if not best_sql_candidate:
                best_sql_candidate = AgentHelpers.extract_xml_tag(alignment_result, "sql")

            existence_candidate = AgentHelpers.extract_xml_tag(alignment_result, "existence")
            reason_candidate = AgentHelpers.extract_xml_tag(alignment_result, "reason")

            # If there's a SQL candidate, attempt to execute it
            if best_sql_candidate:
                db_connector = DatabaseConnector(db_name)
                sql_executed_successfully = False
                if db_connector.connect():
                    sql_result_df, error_message = db_connector.execute_query_cell(best_sql_candidate)
                    if sql_result_df is not None:
                        # Query successful
                        sql_result_dict = {'result': sql_result_df, 'stdout': '', 'stderr': '', 'error': None}
                        sql_executed_successfully = True
                    else:
                        # Query failed
                        sql_result_dict = {
                            'result': None,
                            'stdout': '',
                            'stderr': '',
                            'error': {'type': 'SQLError', 'message': error_message, 'traceback': ''}
                        }
                    new_result = AgentHelpers.format_execution_result(sql_result_dict)
                    db_connector.disconnect()
                else:
                    new_result = "无法连接到数据库"

                # Check for both "true" existence and successful execution
                if existence_candidate and existence_candidate.strip().lower() == "true" and sql_executed_successfully:
                    best_sql = best_sql_candidate
                    existence = existence_candidate
                    reason = reason_candidate
                    break  # Found correct and executable SQL, exit loop
                elif current_attempt == max_attempts:  # Reached max attempts without finding a definitively correct and executable SQL
                    best_sql = best_sql_candidate
                    existence = existence_candidate
                    reason = reason_candidate
                    break
                else:
                    # If not definitively correct and executable, and there's a new SQL suggestion,
                    # add it to the queue for further exploration in the next iteration.
                    sql_queue.append(best_sql_candidate)
                    reasoning_queue.append(f"Aligner生成的探索性SQL (尝试 {current_attempt})")
                    sql_results.append(new_result)
            elif current_attempt == max_attempts: # No SQL candidate and reached max attempts
                best_sql = None
                existence = existence_candidate
                reason = reason_candidate
                break
        # 更新节点的证据为最佳SQL
        if best_sql:
            node.update_evidences([best_sql])

        # 保存SQL、执行结果和推理过程作为参考
        existence_str = f"存在正确SQL: {existence}" if existence else ""
        reason_str = f"\n判断理由: {reason}" if reason else ""

        # 构建结果字符串，动态处理任意数量的SQL
        result_str = ""
        actions_str = ""

        for i, (sql, reasoning, result) in enumerate(zip(sql_queue, reasoning_queue, sql_results)):
            # 确定SQL来源标签
            source_label = ""
            if "Aligner生成" in reasoning:
                source_label = " (Aligner生成)"
            elif "外部生成" in reasoning:
                source_label = " (外部生成)"
            elif "重新格式化" in reasoning:
                source_label = " (重新格式化)"

            sql_label = f"SQL {i+1}{source_label}"

            # 添加到结果字符串
            result_str += f"{sql_label}: {sql}\n执行结果 {i+1}: {result}\n推理过程 {i+1}: {reasoning}\n\n"
            actions_str += f"#SQL{i+1}\n{sql}\n\n"

        # 添加存在性判断和理由
        result_str += existence_str + reason_str

        node.update_cell_value(result_str)
        node.update_actions(actions_str.strip())
    
    def conduct_2sql(self, node, current_code, result_str, schema_info, additional_context, db_name, tables_schema_first_three, example_data):
        """进行SQL生成和评估过程
        
        Args:
            node: 当前处理的节点
            current_code: 当前Python代码
            result_str: 执行结果
            schema_info: 表结构信息
            additional_context: 额外上下文
            db_name: 数据库名称
            tables_schema_first_three: 表结构前三行
            example_data: 示例数据
        
        Returns:
            tuple: (SQL建议, 推理过程)
        """
        # 重置代理的消息历史
        for agent in [self.generator, self.judger]:
            if hasattr(agent, '_messages'):
                agent._messages = {}
        
        # 设置最大重试次数
        max_retries = 6
        current_retry = 0
        best_sql = None
        best_thinking = None
        
        while current_retry < max_retries:
            current_retry += 1
            
            # 准备SQL生成提示
            if current_retry == 1:
                # 第一次尝试，使用原始提示
                sql_generation_prompt = Prompts.SQL_GENERATION_TASK.format(
                    example_data=example_data,
                    question=node.question,
                    code=current_code,
                    result=result_str,
                    tables_schema=schema_info,
                    tables_schema_first_three=tables_schema_first_three,
                    additional_context=additional_context
                )
            else:
                # 后续尝试，使用带有前一次尝试信息的提示
                sql_generation_prompt = Prompts.SQL_REGENERATE_TASK.format(
                    question=node.question,
                    code=current_code,
                    result=result_str,
                    tables_schema=schema_info,
                    tables_schema_first_three=tables_schema_first_three,
                    additional_context=additional_context,
                    previous_sql=sql if sql else "未生成有效SQL",
                    previous_result=sql_result if 'sql_result' in locals() else "未执行SQL",
                    judger_feedback=reason if 'reason' in locals() else "需要生成有效的SQL查询"
                )
            
            # 使用Generator生成SQL
            self.user_proxy.initiate_chat(self.generator, message=sql_generation_prompt)
            generation_response = self.user_proxy.last_message(self.generator)
            
            # 提取SQL
            sql = AgentHelpers.extract_xml_tag(generation_response, "sql")
            thinking = AgentHelpers.extract_xml_tag(generation_response, "thinking")
            
            # 如果没有提取到SQL，尝试从代码块中提取
            if not sql:
                sql_pattern = r"```sql\s*(.*?)\s*```"
                sql_matches = re.findall(sql_pattern, generation_response, re.DOTALL)
                if sql_matches:
                    sql = sql_matches[0].strip()
            
            # 如果仍然没有SQL，继续下一次尝试
            if not sql:
                continue
            
            # 执行SQL
            sql_result = ""
            try:
                db_connector = DatabaseConnector(db_name)
                if db_connector.connect():
                    sql_result_df, error_message = db_connector.execute_query_cell(sql)
                    if sql_result_df is not None:
                        sql_result_dict = {'result': sql_result_df, 'stdout': '', 'stderr': '', 'error': None}
                        sql_result = AgentHelpers.format_execution_result(sql_result_dict)
                        # 如果SQL执行成功，保存为最佳SQL
                        best_sql = sql
                        best_thinking = thinking
                    else:
                        sql_result = f"SQL执行错误: {error_message}"
                db_connector.disconnect()
            except Exception as e:
                sql_result = f"执行SQL时发生错误: {str(e)}"
            
            # 让Judger评估SQL结果
            judger_prompt = Prompts.SQL_JUDGER_TASK.format(
                question=node.question,
                code=current_code,
                result=result_str,
                tables_schema=schema_info,
                additional_context=additional_context,
                sql=sql,
                sql_result=sql_result
            )
            
            self.user_proxy.initiate_chat(self.judger, message=judger_prompt)
            judgment = self.user_proxy.last_message(self.judger)
            
            # 解析判断结果
            qa_result = AgentHelpers.extract_xml_tag(judgment, "QA")
            reason = AgentHelpers.extract_xml_tag(judgment, "reason")
            
            # 如果判断结果为true，表示SQL正确，可以提前结束循环
            if qa_result and "true" in qa_result.lower():
                best_sql = sql
                best_thinking = thinking
                break
            
            # 如果判断结果为false但SQL执行成功，仍然保存为候选SQL
            if sql_result and "SQL执行错误" not in sql_result and not best_sql:
                best_sql = sql
                best_thinking = thinking
        
        # 如果所有尝试都失败，但有执行成功的SQL，返回最后一个成功的SQL
        if not best_sql and sql and "SQL执行错误" not in sql_result:
            best_sql = sql
            best_thinking = thinking
        
        # 返回最终SQL和推理过程
        return best_sql, best_thinking