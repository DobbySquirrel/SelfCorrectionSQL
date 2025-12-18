from utils.agent_helpers import AgentHelpers
from core.database_connector import DatabaseConnector
from utils.prompts import Prompts

class SQLValidator:
    """SQL验证器，用于验证和比较SQL结果"""
    
    def __init__(self, user_proxy, aligner, answer_format):
        """初始化SQL验证器
        
        Args:
            user_proxy: 用户代理
            aligner: 对齐器Agent
            answer_format: 答案格式Agent
        """
        self.user_proxy = user_proxy
        self.aligner = aligner
        self.answer_format = answer_format
        self.helpers = AgentHelpers()
        self.db_name = None  # 添加数据库名称属性
        
    def set_db_name(self, db_name):
        """设置数据库名称
        
        Args:
            db_name: 数据库名称
        """
        self.db_name = db_name
    def are_results_equivalent(result1, result2):
        """比较两个SQL结果是否相同
        
        Args:
            result1: 第一个SQL结果（字符串）
            result2: 第二个SQL结果（字符串）
            
        Returns:
            bool: 两个结果是否相同
        """
        if "执行错误" in result1:
            return False
        return str(result1) == str(result2)
        
    def _execute_sql_query(self, db_name, sql):
        """Helper to execute SQL and format results."""
        db_connector = DatabaseConnector(db_name)
        if db_connector.connect():
            try:
                sql_result_df, error_message = db_connector.execute_query_cell(sql)
                if sql_result_df is not None:
                    sql_result_dict = {'result': sql_result_df, 'stdout': '', 'stderr': '', 'error': None}
                else:
                    sql_result_dict = {
                        'result': None,
                        'stdout': '',
                        'stderr': '',
                        'error': {'type': 'SQLError', 'message': error_message, 'traceback': ''}
                    }
                return AgentHelpers.format_execution_result(sql_result_dict)
            finally:
                db_connector.disconnect()
        else:
            return "无法连接到数据库"

    def check_sql(self, node, db_name, schema_info, tool_description, sql_pandas, sql_salchemy,
                  sql_list, reason_list, example_data, additional_context, tables_schema_first_three,
                  analysis_based_on_few_shot_logic,top3_code_records):
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
            analysis_based_on_few_shot_logic: 基于少量样本的推理
        """

        # Initialize SQL candidates, using a set to track unique SQLs
        # and a list to maintain order for presentation
        sql_candidates = [] # List of (sql, reasoning, result) tuples
        seen_sqls = set()

        def add_sql_candidate(sql, reasoning):
            if sql and sql.strip() not in seen_sqls:
                formatted_result = self._execute_sql_query(db_name, sql)
                sql_candidates.append((sql, reasoning, formatted_result))
                seen_sqls.add(sql.strip())
                return True
            return False

        # Add initially provided SQLs
        for sql, reason in zip(sql_list, reason_list):
            add_sql_candidate(sql, reason)

        # Supplement with external SQLs if needed (only if they are unique)
        if True:
            # add_sql_candidate(sql_pandas, "外部生成的SQL (pandas)")
            add_sql_candidate(sql_salchemy, "外部生成的SQL (sqlalchemy)")
        
        if not sql_candidates:
            return  # If no valid SQL, return immediately

        # Get answer format from answer_format agent
        # answer_format_task = Prompts.ANSWER_FORMAT_TASK.format(
        #     question=node.question,
        #     example_data=example_data,
        # )
        # self.user_proxy.initiate_chat(self.answer_format, message=answer_format_task)
        # answer_format = AgentHelpers.extract_xml_tag(self.user_proxy.last_message(self.answer_format), "answer")

        # Iteratively reformat and add new SQLs
        # This loop now correctly processes new SQLs added within it
        current_processing_index = 0
        max_reformat_iterations_per_sql = 3 # Max retries for reformatting a single SQL
        total_reformat_iterations = 0
        index_length=len(sql_candidates)
        while current_processing_index < index_length:
            original_sql, original_reasoning, original_result = sql_candidates[current_processing_index]
            
            reformat_retries = 0
            found_valid_reformat = False
            while reformat_retries < max_reformat_iterations_per_sql:
                reformat_task = Prompts.ANSWER_REFORMAT_TASK.format(
                    question=node.question,
                    evidence=additional_context,
                    sql_output_format=original_sql,
                    sql_result=original_result,
                    analysis_based_on_few_shot_logic=analysis_based_on_few_shot_logic,
                )
                self.user_proxy.initiate_chat(self.aligner, message=reformat_task)
                reformat_result_msg = self.user_proxy.last_message(self.aligner)
                reformatted_sql = AgentHelpers.extract_xml_tag(reformat_result_msg, "SQL")
                if reformatted_sql:
                    # 执行新的SQL查询获取结果
                    reformatted_result = self._execute_sql_query(db_name, reformatted_sql)
                    # 检查结果是否与原始SQL结果相同
                    if not SQLValidator.are_results_equivalent(reformatted_result, original_result):
                        # 只有当结果不同时才添加新的SQL
                        if add_sql_candidate(reformatted_sql, f"基于问题需求重新格式化的SQL (基于SQL {current_processing_index+1})"):
                            total_reformat_iterations += 1
                            break  # 找到有效的重新格式化SQL，退出重试循环
                    elif "执行错误" in reformatted_result:
                        a=1
                    else:
                        break
                
                reformat_retries += 1
                
            current_processing_index += 1  # 处理下一个SQL
            
        # Use Aligner to select the best SQL
        best_sql = None
        existence = None
        reason = None
        max_selection_attempts = 5 # Max attempts for Aligner to select a "True" SQL

        for attempt in range(max_selection_attempts):
            aligner_task = Prompts.FINAL_ALIGNER_TASK.format(
                question=node.question,
                evidence=additional_context,
                tables_schema=schema_info,
                tables_schema_first_three=tables_schema_first_three,
                additional_context=additional_context if additional_context else "None",
                sql_candidates="\n\n".join([
                    f"SQL {i+1}:\n{sql}\n执行结果：{result}"
                    for i, (sql, reasoning, result) in enumerate(sql_candidates)
                ]),
                analysis_based_on_few_shot_logic=analysis_based_on_few_shot_logic,
                verified_code_records="\n\n".join([
                    f"验证通过的代码 {i+1}:\n{record['code']}\n执行结果：{record['result']}"
                    for i, record in enumerate(top3_code_records) if record.get('is_success', False)
                ])
            )

            self.user_proxy.initiate_chat(self.aligner, message=aligner_task)
            alignment_result_msg = self.user_proxy.last_message(self.aligner)

            current_best_sql_candidate = AgentHelpers.extract_xml_tag(alignment_result_msg, "SQL")
            if not current_best_sql_candidate:
                current_best_sql_candidate = AgentHelpers.extract_xml_tag(alignment_result_msg, "sql")
            
            current_existence_candidate = "True"
            current_reason_candidate = AgentHelpers.extract_xml_tag(alignment_result_msg, "reason")

            # Try to execute the Aligner's chosen SQL to verify its correctness
            if current_best_sql_candidate:
                executed_result = self._execute_sql_query(db_name, current_best_sql_candidate)
                
                # Update candidates list if this is a new SQL from Aligner
                add_sql_candidate(current_best_sql_candidate, f"Aligner生成的探索性SQL (尝试 {attempt+1})")

                # If the Aligner claims it's True and it executes successfully
                if current_existence_candidate and current_existence_candidate.strip().lower() == "true" and "error" not in executed_result.lower() and "无法连接" not in executed_result:
                    best_sql = current_best_sql_candidate
                    existence = current_existence_candidate
                    reason = current_reason_candidate
                    break # Found a definitively correct SQL, exit loop

                # If it's the last attempt, take whatever the Aligner proposed as the best
                if attempt == max_selection_attempts - 1:
                    best_sql = current_best_sql_candidate
                    existence = current_existence_candidate
                    reason = current_reason_candidate
            elif attempt == max_selection_attempts - 1: # No SQL candidate and reached max attempts
                best_sql = None
                existence = current_existence_candidate # Still capture existence if Aligner said False
                reason = current_reason_candidate
            
        # Update node's evidence with the best SQL
        if best_sql:
            node.update_evidences([best_sql])

        # Prepare results for the node
        existence_str = f"存在正确SQL: {existence}" if existence else ""
        reason_str = f"\n判断理由: {reason}" if reason else ""

        result_str = ""
        actions_str = ""

        for i, (sql, reasoning, result) in enumerate(sql_candidates):
            source_label = ""
            if "Aligner生成" in reasoning:
                source_label = " (Aligner生成)"
            elif "外部生成" in reasoning:
                source_label = " (外部生成)"
            elif "重新格式化" in reasoning:
                source_label = " (重新格式化)"

            sql_label = f"SQL {i+1}{source_label}"

            result_str += f"{sql_label}:\n{sql}\n执行结果 {i+1}: {result}\n推理过程 {i+1}: {reasoning}\n\n"
            actions_str += f"#SQL{i+1}\n{sql}\n\n"

        result_str += existence_str + reason_str

        node.update_cell_value(result_str)
        node.update_actions(actions_str.strip())