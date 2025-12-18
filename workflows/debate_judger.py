    def conduct_debate(self, node, current_code, result_str, schema_info, additional_context, db_name, tables_schema_first_three, example_data):
        """进行辩论过程，包括运行SQL
        
        Args:
            node: 当前处理的节点
            current_code: 当前Python代码
            result_str: 执行结果
            schema_info: 表结构信息
            additional_context: 额外上下文
            db_name: 数据库名称
        
        Returns:
            tuple: (SQL建议, 推理过程)
        """
        # 重置所有代理的消息历史
        for agent in [self.debater_pro, self.debater_con, self.debate_judge]:
            if hasattr(agent, '_messages'):
                agent._messages = {}        
        # 自定义debater_pro的generate_reply方法，使其在生成包含SQL的消息后自动执行SQL
   
        # 保存原始的generate_reply方法

        
        # 创建辩论群组，只包含三个参与者
        groupchat = autogen.GroupChat(
            agents=[
                self.debater_pro,
                self.debater_con, 
                self.debate_judge
            ],
            messages=[],
            max_round=8  # 限制辩论轮数
        )
        
        # 准备辩论提示
        debate_start_prompt = Prompts.DEBATE_GROUP_START.format(
            example_data=example_data,
            question=node.question,
            code=current_code,
            result=result_str,
            tables_schema=schema_info,
            tables_schema_first_three=tables_schema_first_three,
            additional_context=additional_context
        )
        
        def is_termination_msg(message):
            """检查消息是否包含有效的终止标记和SQL"""
            content = message.get("content", "")
            
            # 检查是否包含必要的标签
            if "<summary>" in content and "<sql>" in content:
                # 提取SQL
                sql_pattern = r"<sql>(.*?)</sql>"
                sql_matches = re.findall(sql_pattern, content, re.DOTALL)
                
                # 验证SQL是否有效
                if sql_matches and len(sql_matches[0].strip()) > 10:  # 确保SQL不为空且有一定长度
                    # 检查SQL是否包含基本的SELECT语句结构
                    sql = sql_matches[0].strip()
                    if sql.upper().startswith("SELECT") and "FROM" in sql.upper():
                        print("检测到有效的终止SQL")
                        return True
            
            # 检查是否包含任务完成标记
            if "TASK_COMPLETE" in content:
                return True
            
            return False
        
        # 创建群聊管理器，只添加LLM配置，不包含message参数
        manager = autogen.GroupChatManager(
            groupchat=groupchat,
            llm_config=self.debater_pro.llm_config,  # 使用debater_pro的LLM配置
            is_termination_msg=is_termination_msg
        )
        
        # 正确调用initiate_chat方法，指定recipient为manager并提供消息和终止检查函数
        self.user_proxy.initiate_chat(
            recipient=manager,  # 指定接收者为manager
            message=debate_start_prompt,  # 在这里提供初始消息
        )
        
        # 获取辩论历史
        debate_history = groupchat.messages
        
        # 从辩论历史中提取裁判的最终判决
        final_judgment = None
        latest_pro_message = None
        
        # 首先尝试找到裁判的最终判决
        for message in reversed(debate_history):
            if message["name"] == self.debate_judge.name:
                final_judgment = message["content"]
                break
                
        # 如果没有找到裁判判决，则寻找最新的debater_pro消息
        if not final_judgment:
            for message in reversed(debate_history):
                if message["name"] == self.debater_pro.name:
                    latest_pro_message = message["content"]
                    break
        
        # 解析最终建议
        final_sql = None
        reasoning = None
        
        if final_judgment:
            # 从裁判判决中提取
            final_sql = AgentHelpers.extract_xml_tag(final_judgment, "sql")
            reasoning = AgentHelpers.extract_xml_tag(final_judgment, "summary")
        elif latest_pro_message:
            # 从最新的debater_pro消息中提取
            final_sql = AgentHelpers.extract_xml_tag(latest_pro_message, "sql")
            reasoning = AgentHelpers.extract_xml_tag(latest_pro_message, "thinking")
        
        # 返回最终SQL和推理过程
        return final_sql, reasoning