from utils.agent_helpers import AgentHelpers
from core.database_connector import DatabaseConnector
from utils.prompts import Prompts
import re
from difflib import SequenceMatcher


class SQLGenerator:
    def __init__(self, user_proxy, generator, judger):
        self.user_proxy = user_proxy
        self.generator = generator
        self.judger = judger
        self.helpers = AgentHelpers()
        self.sql_history = []  # 存储所有SQL尝试记录
        self.failed_sql_list = []  # 存储失败的SQL尝试

    def _calculate_similarity(self, str1, str2):
        """计算两个字符串的相似度，针对长字符串优化"""
        # 预处理：移除空白字符
        str1 = re.sub(r'\s+', '', str1)
        str2 = re.sub(r'\s+', '', str2)
        
        # 如果字符串太长，取样本进行比较
        MAX_LENGTH = 1000
        if len(str1) > MAX_LENGTH or len(str2) > MAX_LENGTH:
            chunk_size = MAX_LENGTH // 3
            str1_sample = str1[:chunk_size] + str1[len(str1)//2-chunk_size//2:len(str1)//2+chunk_size//2] + str1[-chunk_size:]
            str2_sample = str2[:chunk_size] + str2[len(str2)//2-chunk_size//2:len(str2)//2+chunk_size//2] + str2[-chunk_size:]
            return SequenceMatcher(None, str1_sample, str2_sample).ratio()
        
        return SequenceMatcher(None, str1, str2).ratio()

    def _generate_reflection(self, node, failed_sql, error_message, schema_info, 
                           additional_context, tables_schema_first_three, df_list, 
                           python_code, python_result):
        """生成SQL执行失败的反思"""
        reflection_prompt = Prompts.SQL_REFLECTION_TASK.format(
            question=node.question,
            additional_context=additional_context,
            tables_schema=schema_info,
            tables_schema_first_three=tables_schema_first_three,
            failed_sql=failed_sql,
            error_message=error_message
        )
        
        self.user_proxy.initiate_chat(self.generator, message=reflection_prompt)
        reflection = self.user_proxy.last_message(self.generator)
        return AgentHelpers.extract_xml_tag(reflection, "reflection")

    def _generate_insight(self, node, schema_info, additional_context, 
                         tables_schema_first_three, df_list, python_code, 
                         python_result):
        """基于失败的SQL尝试生成洞察"""
        if not self.failed_sql_list:
            return None
        # 获取最近3次失败的SQL及其错误原因
        recent_failures = self.failed_sql_list[-3:]
        formatted_failures = [f"SQL: {failure['sql']}\n执行结果: {failure['sql_result']}\n错误原因: {failure['error_reason']}" 
                            for failure in recent_failures]
        insight_prompt = Prompts.SQL_INSIGHT_GENERATION.format(
            question=node.question,
            additional_context=additional_context,
            df_list=df_list,
            python_code=python_code,
            python_result=python_result,
            tables_schema=schema_info,
            top3_failed_sql_list="\n".join(formatted_failures)  # 包含SQL和错误原因
        )
            # tables_schema_first_three=tables_schema_first_three,
        
        self.user_proxy.initiate_chat(self.generator, message=insight_prompt)
        insight = self.user_proxy.last_message(self.generator)
        return AgentHelpers.extract_xml_tag(insight, "insight")

    def conduct_2sql(self, node, python_code, python_result, schema_info, 
                    additional_context, db_name, tables_schema_first_three, 
                    example_data, df_list):
        """进行SQL生成和评估过程"""
        # 重置代理状态
        for agent in [self.generator, self.judger]:
            if hasattr(agent, '_messages'):
                agent._messages = {}
        
        # 不重置sql_history和failed_sql_list，保留历史记录用于生成insight
        
        max_retries = 6
        current_retry = 0
        best_sql = None
        best_thinking = None
        sql = None
        sql_result = "未执行SQL"
        reason = "需要生成有效的SQL查询"
        reflection = None
        insight = self._generate_insight(node, schema_info, additional_context,
                                       tables_schema_first_three, df_list,
                                       python_code, python_result) if self.failed_sql_list else None

        while current_retry < max_retries:
            current_retry += 1
            
            # 准备SQL生成提示
            if current_retry == 1:
                # 第一次尝试，包含历史insight
                sql_generation_prompt = Prompts.SQL_GENERATION_TASK.format(
                    example_data=example_data,
                    question=node.question,
                    additional_context=additional_context,
                    df_list=df_list,
                    python_code=python_code,
                    python_result=python_result,
                    tables_schema=schema_info,
                    insight=insight if insight else "No insight available"
                )
                    # tables_schema_first_three=tables_schema_first_three,
            else:
                # 后续尝试
                sql_generation_prompt = Prompts.SQL_REGENERATE_TASK.format(
                    example_data=example_data,
                    question=node.question,
                    additional_context=additional_context,
                    df_list=df_list,
                    python_code=python_code,
                    python_result=python_result,
                    tables_schema=schema_info,
                    reflection=reflection if reflection else "No reflection available",
                    insight=insight if insight else "No insight available"
                )
                    # tables_schema_first_three=tables_schema_first_three,
            self.user_proxy.initiate_chat(self.generator, message=sql_generation_prompt)
            response = self.user_proxy.last_message(self.generator)
            
            # 从响应中提取SQL
            sql = AgentHelpers.extract_xml_tag(response, "sql")
 
            try:
                db_connector = DatabaseConnector(db_name)
                if db_connector.connect():
                    sql_result_df, error_message = db_connector.execute_query(sql)
                    if sql_result_df is not None:
                        # 处理成功的SQL执行
                        formatted_result = str(sql_result_df)
                        sql_result_dict = {'result': formatted_result, 'stdout': '', 'stderr': '', 'error': None}
                        sql_result = AgentHelpers.format_execution_result(sql_result_dict)
                        similarity_score = self._calculate_similarity(python_result, sql_result)
                        
                        self.sql_history.append({
                            'sql': sql,
                            'result': formatted_result,
                            'similarity_score': similarity_score
                        })
                    else:
                        # 处理SQL执行错误
                        sql_result = f"SQL执行错误: {error_message}"
                        # self.failed_sql_list.append({
                        #     'sql': sql,
                        #     'sql_result': sql_result,
                        #     'error_reason': f"SQL执行错误"
                        # })
                        
                        # 只在执行失败时生成reflection
                        reflection = self._generate_reflection(
                            node, sql, error_message, schema_info, 
                            additional_context, tables_schema_first_three,
                            df_list, python_code, python_result
                        )
                        db_connector.disconnect()
                        continue
                    
                    db_connector.disconnect()
            except Exception as e:
                # 异常处理部分保持不变...
                continue
            
            # 评估SQL结果
            judger_prompt = Prompts.SQL_JUDGER_TASK.format(
                example_data=example_data,
                question=node.question,
                additional_context=additional_context,
                tables_schema=schema_info,
                code=python_code,
                result=python_result,
                sql=sql,
                sql_result=sql_result
            )
            
            self.user_proxy.initiate_chat(self.judger, message=judger_prompt)
            judgment = self.user_proxy.last_message(self.judger)
            
            qa_result = AgentHelpers.extract_xml_tag(judgment, "QA")
            reason = AgentHelpers.extract_xml_tag(judgment, "reason")
            score_str = AgentHelpers.extract_xml_tag(judgment, "score")
            
            try:
                agent_score = float(score_str) if score_str else 0.0
            except ValueError:
                agent_score = 0.0
            
            # 更新历史记录
            if self.sql_history:
                self.sql_history[-1]['agent_score'] = agent_score
                self.sql_history[-1]['combined_score'] = float(self.sql_history[-1]['agent_score'])

            
            # 如果找到正确的SQL，记录并退出
            if qa_result and "true" in qa_result.lower():
                best_sql = sql
                best_thinking = reason
                break
            
            self.failed_sql_list.append({
                'sql': sql,
                'sql_result': sql_result,
                'error_reason': reason
            })
            # insight = self._generate_insight(
            #     node, schema_info, additional_context,
            #     tables_schema_first_three, df_list,
            #     python_code, python_result
            # )
            insight = self._generate_insight(
                node, schema_info, additional_context,
                tables_schema_first_three, df_list,
                python_code, python_result
            )
            reason = f"SQL未能完全满足要求: {reason}"
        
        # 如果没有找到完全正确的SQL，返回最佳尝试
        if not best_sql and self.sql_history:
            best_record = max(
                self.sql_history,
                key=lambda x: x.get('combined_score', 0.0) if x.get('sql') else 0.0
            )
            best_sql = best_record.get('sql')
            best_thinking = f""
        
        return best_sql, best_thinking