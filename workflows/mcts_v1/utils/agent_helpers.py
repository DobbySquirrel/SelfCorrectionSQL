import re
class AgentHelpers:
    """Agent系统的辅助方法集合"""
    
    @staticmethod
    def extract_multi_xml_tag(message, tag_name):
        """从代理消息中提取指定XML标签的内容。

        Args:
            message: 代理消息字典或字符串。
            tag_name: 要提取的XML标签名称。

        Returns:
            list: 包含所有找到的标签内容的列表。
                  如果没有找到，则返回空列表。
        """
        # This is a placeholder implementation. 
        # You'll need to replace this with your actual XML parsing logic.
        # For demonstration, let's assume it can handle simple cases.
        import re
        if isinstance(message, dict) and 'content' in message:
            message_str = message['content']
        elif isinstance(message, str):
            message_str = message
        else:
            return []

        # Regex to find all occurrences of <tag_name>...</tag_name>
        pattern = rf"<{tag_name}>(.*?)</{tag_name}>"
        return re.findall(pattern, message_str, re.DOTALL)
    
    def extract_xml_tag(message, tag_name):
        """从消息中提取指定XML标签的内容
        
        Args:
            message: 代理消息字典或字符串
            tag_name: 要提取的标签名称
            
        Returns:
            str: 提取的标签内容，如果没有找到则返回空字符串
        """
        import re
        
        # 处理消息可能是字典的情况
        content = message["content"] if isinstance(message, dict) else message
        
        # 从指定标签中提取内容
        pattern = rf'<{tag_name}>(.*?)</{tag_name}>'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(1).strip()
            
        return ""
    
    @staticmethod
    def extract_code_from_message(message):
        """从代理消息中提取代码块
        
        Args:
            message: 代理消息字典或字符串
            
        Returns:
            str: 提取的代码，如果没有找到则返回空字符串
        """
        
        
        # 处理消息可能是字典的情况
        content = message["content"] if isinstance(message, dict) else message
        
        # 尝试提取XML标签中的代码
        xml_code = AgentHelpers.extract_xml_tag(message, "code")
        if xml_code:
            return xml_code
            
        # 尝试提取Markdown代码块
        # 匹配 ```language\n code \n``` 格式
        pattern = r'```(?:\w+)?\n(.*?)\n```'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(1).strip()
            
        return ""
    def extract_multi_code_from_message(message):
        """从代理消息中提取代码块

        Args:
            message: 代理消息字典或字符串

        Returns:
            str: 提取的代码，如果没有找到则返回空字符串
        """
        # 调用extract_multi_xml_tag提取code标签内容
        code_segments = AgentHelpers.extract_xml_tag(message, "code")
        # main_code_segments = AgentHelpers.extract_xml_tag(message, "main_code")
        # 如果有多段code则拼接在一起
        return {"code":code_segments}
    @staticmethod
    def truncate_text(text, max_length=1000):
        """智能截断文本，保留头部、中部和尾部内容
        
        Args:
            text: 要截断的文本
            max_length: 最大长度限制
            
        Returns:
            截断后的文本
        """
        if len(text) <= max_length:
            return text
            
        # 为每个部分分配的长度
        part_length = max_length // 3
        
        # 提取头部、中部和尾部
        head = text[:part_length]
        mid_start = (len(text) - part_length) // 2
        middle = text[mid_start:mid_start + part_length]
        tail = text[-part_length:]
        
        return f"{head}\n... [截断了{len(text) - max_length}个字符] ...\n{middle}\n... ...\n{tail}"

    @staticmethod
    def format_execution_result(execution_result):
        """格式化执行结果，保留关键信息"""
        result_str = ""
        
        # 处理主要结果
        if execution_result['result'] is not None:
            result_text = str(execution_result['result'])
            result_str += f"{AgentHelpers.truncate_text(result_text, 1000)}\n"
            
        # 处理标准输出
        if execution_result['stdout'] and str(execution_result['stdout']) != str(execution_result['result']):
            stdout_text = str(execution_result['stdout'])
            result_str += f"标准输出: {AgentHelpers.truncate_text(stdout_text, 1000)}\n"
            
        # 处理标准错误
        if execution_result['stderr']:
            stderr_text = str(execution_result['stderr'])
            result_str += f"标准错误: {AgentHelpers.truncate_text(stderr_text, 1000)}\n"
            
        # 处理执行错误
        if execution_result['error']:
            error_text = str(execution_result['error']['message'])
            result_str += f"执行错误: {AgentHelpers.truncate_text(error_text, 1000)}\n"
        
        # 确保总长度不超过限制
        if len(result_str.encode('utf-8')) > 3000:
            # 对整体结果进行截断，保留头部和尾部的关键信息
            encoded = result_str.encode('utf-8')
            while len(encoded) > 2500:
                # 找到最后一个完整的行
                last_newline = result_str.rindex('\n', 0, len(result_str) - 1)
                result_str = result_str[:last_newline]
                encoded = result_str.encode('utf-8')
            result_str += "\n... [部分输出被截断]"
        
        return result_str