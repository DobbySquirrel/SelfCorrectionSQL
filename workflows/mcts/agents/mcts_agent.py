"""
MCTS 主控制器智能体

实现 MASTER 框架的 Critic 评估机制，用于对生成的 CTE 进行即时打分。
"""

import autogen
import re
from typing import Dict, Optional, Tuple, Any
import threading


class MCTSAgent:
    """MCTS 主控制器智能体，实现 MASTER 框架的评估功能"""

    def __init__(self, llm_config: Dict):
        self.llm_config = llm_config
        self._agent_lock = threading.Lock()
        self.setup_agent()
    
    def setup_agent(self, temperature: float = 0.3):
        """
        设置评估智能体（线程安全）
        
        Args:
            temperature: 温度参数（评估使用较低温度以提高稳定性）
        """
        with self._agent_lock:
            llm_config_with_temp = self.llm_config.copy()
            if 'config_list' in llm_config_with_temp:
                for config in llm_config_with_temp['config_list']:
                    try:
                        config['temperature'] = temperature
                    except Exception:
                        pass
                    try:
                        if isinstance(config.get('openai'), dict):
                            config['openai']['temperature'] = temperature
                    except Exception:
                        pass
            
            self.critic_agent = autogen.AssistantAgent(
                name="CTECritic",
                llm_config=llm_config_with_temp,
                system_message=self._get_critic_system_message()
            )
            
            self.user_proxy = autogen.UserProxyAgent(
                name="CriticUserProxy",
                human_input_mode="NEVER",
                max_consecutive_auto_reply=0,
                code_execution_config=False
            )
    
    def _get_critic_system_message(self) -> str:
        """获取 Critic 评估器的系统消息"""
        return """你是一个精通 SQL 的数据专家。你的任务是评估一段中间 SQL 代码 (CTE) **是否对最终 SQL 合成有帮助**。

这是一个增量式 SQL 生成过程，每个 CTE 都是构建最终 SQL 的中间步骤。你需要评估：

1. **对最终目标的贡献度**：这个 CTE 是否朝着正确解决用户问题的方向前进？
   - 是否选择了正确的表和列？
   - 是否应用了必要的筛选条件？
   - 是否建立了正确的表连接关系？

2. **Schema 正确性**：是否使用了不存在的表或列？语法是否正确？

3. **逻辑合理性**：CTE 的执行结果是否合理？
   - 如果返回空结果，是否是因为筛选条件过于严格？
   - 如果返回大量结果，是否缺少必要的筛选条件？

4. **可扩展性**：这个 CTE 是否可以作为后续 CTE 的基础，继续构建更复杂的查询？

**评分标准**：
- score = 0.0-0.3：CTE 有严重错误或对最终目标没有帮助（如使用了错误的表、语法错误、逻辑完全错误）
- score = 0.4-0.6：CTE 部分正确，但缺少关键信息或筛选条件，对最终目标的帮助有限
- score = 0.7-0.9：CTE 基本正确，对最终目标有帮助，但可能还需要进一步完善
- score = 1.0：CTE 完全正确，对最终目标非常有帮助，可以直接用于构建最终 SQL

请输出 JSON 格式：
{
    "score": 0.0 到 1.0,  // 表示这个 CTE 对最终 SQL 合成的帮助程度
    "confidence": 0.0 到 1.0, // 你对这个评分有多大把握？
    "reasoning": "简短的理由，说明为什么这个 CTE 对最终 SQL 合成有帮助或没有帮助"
}

**重要**：只输出 JSON 格式，不要输出其他内容！"""
    
    def assess_node(
        self,
        question: str,
        schema_info: str,
        cte_sql: str,
        execution_info: Optional[Dict[str, Any]] = None
    ) -> Tuple[float, float, str]:
        """
        评估 CTE 节点的质量（MASTER 框架的核心方法）
        
        Args:
            question: 用户问题
            schema_info: 数据库 Schema
            cte_sql: 当前生成的 CTE 代码
            execution_info: (可选) LIMIT 1 的试运行结果，或者是"报错信息"
            
        Returns:
            (score, confidence, reasoning) 元组
            - score: 0.0 到 1.0，表示 CTE 的质量评分
            - confidence: 0.0 到 1.0，表示对评分的信心
            - reasoning: 评估理由
        """
        # 构建执行信息描述
        execution_desc = ""
        if execution_info:
            if execution_info.get('valid', False):
                query_result = execution_info.get('query_result', [])
                if query_result and len(query_result) > 0:
                    execution_desc = f"执行成功，返回了 {len(query_result)} 行结果（示例：{str(query_result[:1])}）"
                else:
                    execution_desc = "执行成功，但返回空结果集"
            else:
                error = execution_info.get('error', '未知错误')
                execution_desc = f"执行失败：{error}"
        else:
            execution_desc = "尚未执行"
        
        # 构建用户输入
        user_input = f"""**用户问题**（最终目标）:
{question}

**数据库 Schema**:
{schema_info}

**待评估的 CTE**（中间步骤）:
```sql
{cte_sql}
```

**执行情况**:
{execution_desc}

**评估任务**：
这是一个增量式 SQL 生成过程中的中间步骤。请评估这个 CTE **是否对最终 SQL 合成有帮助**。

重点考虑：
1. 这个 CTE 是否朝着正确解决用户问题的方向前进？
2. 是否选择了正确的表和列？
3. 是否应用了必要的筛选条件？
4. 是否可以作为后续 CTE 的基础继续构建？

输出 JSON 格式：
{{
    "score": 0.0 到 1.0,  // 表示这个 CTE 对最终 SQL 合成的帮助程度
    "confidence": 0.0 到 1.0,  // 你对这个评分的信心
    "reasoning": "简短的理由，说明为什么这个 CTE 对最终 SQL 合成有帮助或没有帮助"
}}"""
        
        try:
            # 调用 LLM 进行评估
            with self._agent_lock:
                chat_result = self.user_proxy.initiate_chat(
                    self.critic_agent,
                    message=user_input,
                    max_turns=1,
                    silent=True
                )
            
            # 提取 LLM 响应
            if hasattr(chat_result, 'chat_history') and len(chat_result.chat_history) > 0:
                last_message = chat_result.chat_history[-1]
                if hasattr(last_message, 'content'):
                    response_text = last_message.content
                else:
                    response_text = str(last_message)
            else:
                response_text = str(chat_result)
            
            # 打印原始响应用于调试（仅在前200字符）
            print(f"[Critic] LLM 原始响应预览: {response_text[:200]}...")
            
            # 首先尝试解析整个响应（可能是包装结构）
            parsed_response = None
            try:
                # 使用 ast.literal_eval 解析（支持单引号和双引号）
                import ast
                parsed_response = ast.literal_eval(response_text)
            except:
                pass
            
            # 如果解析成功且包含 content 字段，提取 content
            if parsed_response and isinstance(parsed_response, dict):
                if 'content' in parsed_response:
                    response_text = str(parsed_response['content'])
                    print(f"[Critic] 从包装结构中提取 content: {response_text[:200]}...")
                elif 'text' in parsed_response:
                    response_text = str(parsed_response['text'])
                    print(f"[Critic] 从包装结构中提取 text: {response_text[:200]}...")
            
            # 尝试从响应中提取 JSON（使用更健壮的方法）
            # 方法1: 尝试找到包含 score, confidence, reasoning 的 JSON 对象
            json_str = None
            
            # 优先查找包含我们需要的字段的 JSON 对象
            # 使用正则表达式查找包含 score, confidence, reasoning 的 JSON
            pattern = r'\{[^{}]*(?:"score"|"Score"|score)[^{}]*(?:"confidence"|"Confidence"|confidence)[^{}]*(?:"reasoning"|"Reasoning"|reasoning)[^{}]*\}'
            json_match = re.search(pattern, response_text, re.DOTALL | re.IGNORECASE)
            if json_match:
                json_str = json_match.group(0)
                print(f"[Critic] 通过正则匹配找到 JSON: {json_str[:200]}...")
            
            # 方法2: 如果方法1失败，尝试找到第一个完整的 JSON 对象（支持嵌套）
            if not json_str:
                json_start = response_text.find('{')
                if json_start != -1:
                    # 从第一个 { 开始，尝试找到匹配的 }
                    brace_count = 0
                    json_end = json_start
                    for i in range(json_start, len(response_text)):
                        if response_text[i] == '{':
                            brace_count += 1
                        elif response_text[i] == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                json_end = i + 1
                                break
                    
                    if brace_count == 0:
                        json_str = response_text[json_start:json_end]
                        print(f"[Critic] 通过大括号匹配找到 JSON: {json_str[:200]}...")
            
            if json_str:
                # 直接使用 ast.literal_eval 解析（支持单引号和双引号）
                try:
                    import ast
                    result = ast.literal_eval(json_str)  # 使用原始字符串（支持单引号）
                    
                    # 检查是否是包装结构（包含 content 字段）
                    if isinstance(result, dict) and 'content' in result:
                        # 从 content 中再次提取 JSON
                        content_text = str(result['content'])
                        print(f"[Critic] 从 content 字段提取: {content_text[:200]}...")
                        # 递归尝试从 content 中提取 JSON
                        content_json_match = re.search(pattern, content_text, re.DOTALL | re.IGNORECASE)
                        if content_json_match:
                            json_str = content_json_match.group(0)
                            result = ast.literal_eval(json_str)
                    
                    # 检查是否包含我们需要的字段
                    if isinstance(result, dict):
                        # 尝试多种可能的字段名（大小写不敏感）
                        score_key = next((k for k in result.keys() if k.lower() == 'score'), None)
                        confidence_key = next((k for k in result.keys() if k.lower() == 'confidence'), None)
                        reasoning_key = next((k for k in result.keys() if k.lower() == 'reasoning'), None)
                        
                        if score_key and confidence_key:
                            score = float(result.get(score_key, 0.0))
                            confidence = float(result.get(confidence_key, 0.0))
                            reasoning = result.get(reasoning_key, '无理由') if reasoning_key else '无理由'
                            score = max(0.0, min(1.0, score))
                            confidence = max(0.0, min(1.0, confidence))
                            print(f"[Critic] ✓ 使用 ast.literal_eval 成功解析: score={score:.3f}, confidence={confidence:.3f}")
                            return score, confidence, reasoning
                        else:
                            print(f"[Critic] ⚠️ 解析的字典不包含 score/confidence/reasoning 字段")
                            print(f"[Critic] 字典键: {list(result.keys())}")
                            print(f"[Critic] 字典内容预览: {str(result)[:300]}")
                except Exception as ast_err:
                    print(f"[Critic] ⚠️ ast.literal_eval 解析失败: {ast_err}")
                    print(f"[Critic] JSON 字符串: {json_str[:500]}")
            else:
                # 如果无法找到 JSON，返回默认值
                print(f"[Critic] ⚠️ 无法从响应中提取 JSON 对象")
                print(f"[Critic] 响应内容: {response_text[:500]}")
                return 0.5, 0.5, "无法解析评估结果"
        
        except Exception as e:
            print(f"[Critic] ❌ 评估过程出错: {e}")
            import traceback
            traceback.print_exc()
            # 返回默认值
            return 0.5, 0.5, f"评估出错: {str(e)}"
    
    def get_config(self) -> Dict:
        return self.llm_config
