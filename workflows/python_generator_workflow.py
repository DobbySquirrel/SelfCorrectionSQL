import pandas as pd
import json

from utils.agent_helpers import AgentHelpers
from utils.prompts import Prompts

class PythonGeneratorWorkflow:
    """Python代码生成工作流，专注于生成、执行、评估和反思Python代码"""
    
    def __init__(self, user_proxy, code_generator, code_executor, semantics_evaluator):
        """初始化工作流
        Args:
            user_proxy: 用户代理
            code_generator: 负责生成Python代码的Agent
            code_executor: 代码执行器
            semantics_evaluator: 负责评估代码的Agent
        """
        self.user_proxy = user_proxy
        self.code_generator = code_generator
        self.code_executor = code_executor
        self.semantics_evaluator = semantics_evaluator
        self.helpers = AgentHelpers()
        
        # 初始化代码历史队列，每个元素为(代码,分数,执行结果,评估反馈)的元组
        self.code_history = []
        self.max_history_size = 3
        self.top3_insights = []

    def _add_to_history(self, code, score, result, feedback):
        """添加代码到历史队列
        Args:
            code: Python代码
            score: 评分
            result: 执行结果
            feedback: 评估反馈
        """
        # 创建完整的代码记录
        code_record = {
            'code': code,
            'score': score,
            'result': result,
            'feedback': feedback,
            'is_success': "true" in feedback.get('correct', '').lower()
        }
        
        self.code_history.append(code_record)
        # 按分数排序
        self.code_history.sort(key=lambda x: (x['is_success'], x['score']), reverse=True)
        
        # 保持队列最大长度
        if len(self.code_history) > self.max_history_size:
            self.code_history = self.code_history[:self.max_history_size]
        
        # 只要有代码就生成insight
        self._generate_insights()

    def _generate_insights(self):
        """生成代码改进的insights"""
        if not self.code_history:  # 如果没有代码历史，直接返回
            return
            
        code_history_str = "\n\n" + "="*40 + "\n\n"
        code_history_str = code_history_str.join([
            f"Code (Score: {record['score']}, Success: {record['is_success']}):\n{record['code']}\nExecution Result:\n{record['result']}\n Score:\n{record['score']}\nFeedback:\n{record['feedback']}" 
            for record in self.code_history
        ])
        
        insight_task = Prompts.Python_Insight_Task.format(
            tool_description=Prompts.TOOL_DESCRIPTION,
            question=self.current_question,
            Top3_code=code_history_str
        )
        
        self.user_proxy.initiate_chat(self.code_generator, message=insight_task)
        insight_result = self.user_proxy.last_message(self.code_generator)
        
        self.top3_insights.append(str(insight_result))
        if len(self.top3_insights) > 2:
            self.top3_insights.pop(0)
    def _get_best_code(self):
        """获取分数最高的代码记录"""
        if not self.code_history:
            return None
        return self.code_history[0]

    def generate_python_code(self, node, schema_info, additional_context="", df_list=None, related_python_code=None):
        """生成Python代码的主要流程
        
        Args:
            node: 当前处理的节点
            schema_info: 表结构信息
            additional_context: 额外的上下文信息
            df_list: 可用的DataFrame列表
            related_python_code: 相关的Python代码

        Returns:
            tuple: (是否找到至少一个成功解, top3代码记录列表)
        """
        # 设置当前问题，用于生成insights
        self.current_question = node.question
        
        # 定义整体最大迭代次数和反思失败容忍度
        overall_max_iterations = 4
        reflection_max_attempts = 3
        
        current_iteration = 1
        reflection_count = 0
        found_success = False
        current_python_code = ""

        while current_iteration <= overall_max_iterations:
            # 生成或重新生成代码
            if reflection_count == 0:
                # 首次生成代码
                generator_task = Prompts.SOLVE_DIRECTLY.format(
                    tool_description=Prompts.TOOL_DESCRIPTION,
                    related_python_code=related_python_code,
                    tables_schema=schema_info,
                    df_list=df_list,
                    question=node.question,
                    additional_context=additional_context if additional_context else "None",
                    top3_insights="\n".join(self.top3_insights) if self.top3_insights else "暂无历史代码分析"
                )
                self.user_proxy.initiate_chat(self.code_generator, message=generator_task)
                current_python_code = AgentHelpers.extract_code_from_message(
                    self.user_proxy.last_message(self.code_generator)
                )

            # 执行生成的代码
            node.update_actions(current_python_code)
            execution_output = self.code_executor.execute(current_python_code, "dev")
            current_execution_result_str = AgentHelpers.format_execution_result(execution_output)
            node.update_cell_value(current_execution_result_str)

            # 如果执行出错
            if execution_output.get('error'):
                reflection_count += 1
                if reflection_count >= reflection_max_attempts:
                    # 超过反思次数限制，进入下一轮迭代
                    current_iteration += 1
                    reflection_count = 0
                    continue
                
                # 执行出错且未超过反思次数限制，进行反思
                reflection_task = Prompts.REFLECTION_TASK.format(
                    tool_description=Prompts.TOOL_DESCRIPTION,
                    current_python_code=current_python_code,
                    current_execution_result_str=current_execution_result_str
                )
                self.user_proxy.initiate_chat(self.code_generator, message=reflection_task)
                reflection_feedback = self.user_proxy.last_message(self.code_generator)
                
                # 基于反思结果重新生成代码
                regenerate_task = Prompts.REGENERATE_TASK.format(
                    tool_description=Prompts.TOOL_DESCRIPTION,
                    related_python_code=related_python_code,
                    tables_schema=schema_info,
                    df_list=df_list,
                    question=node.question,
                    additional_context=additional_context if additional_context else "None",
                    previous_code=current_python_code,
                    previous_result=current_execution_result_str,
                    reflection_feedback=reflection_feedback,
                    top3_insights="\n".join(self.top3_insights) if self.top3_insights else "暂无历史代码分析"
                )
                self.user_proxy.initiate_chat(self.code_generator, message=regenerate_task)
                current_python_code = AgentHelpers.extract_code_from_message(
                    self.user_proxy.last_message(self.code_generator)
                )
                continue

            # 执行成功，进行评估
            evaluator_task = Prompts.PYTHON_EVALUATOR_TASK.format(
                tool_description=Prompts.TOOL_DESCRIPTION,
                tables_schema=schema_info,
                df_list=df_list,
                question=node.question,
                additional_context=additional_context if additional_context else "None",
                code=current_python_code,
                result=current_execution_result_str,
            )
            
            self.user_proxy.initiate_chat(self.semantics_evaluator, message=evaluator_task)
            evaluation_judgment = self.user_proxy.last_message(self.semantics_evaluator)

            # 解析评估结果
            qa_result = AgentHelpers.extract_xml_tag(evaluation_judgment, "correct")
            difficulty_score = int(AgentHelpers.extract_xml_tag(evaluation_judgment, "difficulty_score"))
            reason = AgentHelpers.extract_xml_tag(evaluation_judgment, "reason")
            
            feedback_from_evaluator = {
                "correct": qa_result,
                "reason": reason
            }

            # 将当前代码添加到历史队列
            self._add_to_history(current_python_code, difficulty_score, current_execution_result_str, feedback_from_evaluator)

            # 如果评估结果正确
            if "true" in qa_result.lower():
                found_success = True
                node.is_solved = True
                current_iteration += 1
                reflection_count = 0
                continue

            # 评估结果不正确，需要进行反思
            reflection_count += 1
            if reflection_count >= reflection_max_attempts:
                # 超过反思次数限制，进入下一轮迭代
                current_iteration += 1
                reflection_count = 0
                continue
            
            # 进行反思
            reflection_task = Prompts.REFLECTION_TASK.format(
                tool_description=Prompts.TOOL_DESCRIPTION,
                current_python_code=current_python_code,
                current_execution_result_str=current_execution_result_str
            )
            self.user_proxy.initiate_chat(self.code_generator, message=reflection_task)
            reflection_feedback = self.user_proxy.last_message(self.code_generator)
            
            # 基于反思结果重新生成代码
            regenerate_task = Prompts.REGENERATE_TASK.format(
                tool_description=Prompts.TOOL_DESCRIPTION,
                related_python_code=related_python_code,
                tables_schema=schema_info,
                df_list=df_list,
                question=node.question,
                additional_context=additional_context if additional_context else "None",
                previous_code=current_python_code,
                previous_result=current_execution_result_str,
                reflection_feedback=reflection_feedback,
                top3_insights="\n".join(self.top3_insights) if self.top3_insights else "暂无历史代码分析"
            )
            self.user_proxy.initiate_chat(self.code_generator, message=regenerate_task)
            current_python_code = AgentHelpers.extract_code_from_message(
                self.user_proxy.last_message(self.code_generator)
            )

        # 返回是否找到成功解决方案和top3代码记录
        return found_success, self.code_history[:self.max_history_size]