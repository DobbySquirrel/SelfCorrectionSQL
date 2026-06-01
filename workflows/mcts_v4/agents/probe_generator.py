"""
Probe SQL生成器智能体

负责在生成CTE之前，先分析问题并生成Probe SQL来探测数据库内容，
以消除数据歧义和不确定性。
"""

import autogen
import json
import re
from typing import Dict, List, Any, Optional
import threading
from .sql_executor import SQLExecutor


class ProbeGenerator:
    """Probe SQL生成器智能体"""
    
    def __init__(self, llm_config: Dict, db_connector, multi_model_configs: List[Dict] = None):
        """
        初始化Probe生成器
        
        Args:
            llm_config: LLM配置
            db_connector: 数据库连接器
            multi_model_configs: 多个模型配置列表（用于多模型并行加速）
        """
        self.llm_config = llm_config
        self.db_connector = db_connector
        self.multi_model_configs = multi_model_configs or []
        self.sql_executor = SQLExecutor(db_connector)
        # 线程锁：保护 agent 创建过程
        self._agent_lock = threading.Lock()
        # 加载probe模板
        self.probe_templates = self._load_probe_templates()
        self.setup_agent()
    
    def _load_probe_templates(self) -> List[Dict]:
        """加载probe模板"""
        import os
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'utils', 'search_sql_templete.py'
        )
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                content = f.read()
                # 文件是纯JSON格式，直接解析
                # 提取JSON部分（去掉可能的Python注释或代码）
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_content = content[json_start:json_end]
                    data = json.loads(json_content)
                    return data.get('probe_templates', [])
        except Exception as e:
            print(f"[Probe生成器] 警告：加载probe模板失败: {e}")
            import traceback
            traceback.print_exc()
        return []
    
    def setup_agent(self, temperature: float = 0.2):
        """
        设置Probe生成智能体（线程安全）
        
        Args:
            temperature: 温度参数
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
            
            self.probe_agent = autogen.AssistantAgent(
                name="ProbeGenerator",
                llm_config=llm_config_with_temp,
                system_message=self._get_probe_system_message()
            )
            
            self.user_proxy = autogen.UserProxyAgent(
                name="ProbeUserProxy",
                human_input_mode="NEVER",
                max_consecutive_auto_reply=0,
                code_execution_config=False
            )
    
    def _get_probe_system_message(self) -> str:
        """获取Probe生成器的系统消息"""
        # 构建工具库描述
        tool_library = []
        for template in self.probe_templates:
            tool_library.append({
                "id": template['id'],
                "desc": template['description'] + f" Params: {', '.join(template['parameters'])}"
            })
        
        tool_library_json = json.dumps(tool_library, ensure_ascii=False, indent=2)
        
        return f"""### Role
You are a Database Investigator within a Text-to-SQL system. Your task is NOT to write the final SQL query, but to identify data ambiguities and select appropriate **Probing Tools** to inspect the database content.

### Task
1. Analyze the **User Question** and the provided **External Knowledge (BIRD Evidence)**.
2. Map the user's intent to the **Database Schema**.
3. Identify "Risks" or "Uncertainties" that External Knowledge has not fully resolved (e.g., exact string formats, existence of values, join path validity).
4. Select tools from the **Tool Library** to resolve these specific uncertainties.

### Input Data
1. **User Question**: The natural language query.
2. **External Knowledge**: Critical domain knowledge or hints provided by the user (e.g., "GDP refers to the 'gross_product' column", "Year means 2020-2023").
3. **Database Schema**: Tables and columns definition.

### Tool Library
{tool_library_json}

### Guidelines for Using External Knowledge
- **Use Knowledge to Narrow Search**: If External Knowledge states "HK refers to Hong Kong", do not probe for "HK"; instead, use `value_fuzzy_match` to verify if "Hong Kong" exists in the city column.
- **Verify Column Hints**: If External Knowledge points to a specific column (e.g., "Use `status_id` for Status"), use that column for your probes instead of guessing others.
- **Resolve Logic vs. Data**: External Knowledge gives you the logic (e.g., "Active users have status 1"). You still need to use `value_existence_check` to ensure `1` is a valid value in the database, or `dist_null_density` if the column is nullable.

### Output Format
Return a JSON object containing a "thought" and a list of "actions".
- "thought": Explain your reasoning. Explicitly mention how you used the External Knowledge to decide what to probe.
- "actions": A list of tool calls.

### Example
**Schema**: Tables [schools(id, name, type), enrollment(school_id, count)]
**User Question**: "List the average enrollment for public schools."
**External Knowledge**: "Public schools are defined by the `type` column containing the string 'Gov'."

**Output**:
{{
  "thought": "The External Knowledge clarifies that 'public schools' maps to the `type` column with the value 'Gov'. I need to verify if 'Gov' is the exact string stored in the database or if it is an abbreviation like 'Government'. I also need to verify the join between schools and enrollment.",
  "actions": [
    {{
      "tool_id": "value_fuzzy_match",
      "parameters": {{"table": "schools", "column": "type", "keyword": "Gov"}}
    }},
    {{
      "tool_id": "struct_join_validity",
      "parameters": {{"table_a": "schools", "key_a": "id", "table_b": "enrollment", "key_b": "school_id"}}
    }}
  ]
}}

**Important**: 
- Only output the JSON object, no additional text or markdown code blocks.
- Generate 3-5 probe actions per request to cover different uncertainties.
- Focus on the most critical ambiguities that could affect SQL generation.
"""
    
    def generate_probe_actions(self, question: str, schema_info: str, additional_context: str = "") -> List[Dict[str, Any]]:
        """
        生成Probe Actions（工具调用列表）
        
        Args:
            question: 自然语言问题
            schema_info: 数据库模式信息
            additional_context: 额外上下文（BIRD Evidence等）
            
        Returns:
            Probe actions列表，每个action包含tool_id和parameters
        """
        # 构建用户输入
        user_input = f"""### Current Context
**Database Schema**:
{schema_info}

**External Knowledge (BIRD Evidence)**:
{additional_context if additional_context else "None"}

**User Question**:
"{question}"

### Your Response"""
        
        # 使用智能体生成Probe Actions
        messages = [
            {
                "role": "user",
                "content": user_input
            }
        ]
        
        response = self.probe_agent.generate_reply(messages)
        
        # 解析JSON响应
        try:
            # 尝试从代码块中提取JSON
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接提取JSON对象
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    json_str = response
            
            probe_data = json.loads(json_str)
            actions = probe_data.get('actions', [])
            
            if not isinstance(actions, list):
                return []
            
            return actions
        except Exception as e:
            print(f"[Probe生成器] 解析响应失败: {e}")
            print(f"[Probe生成器] 原始响应: {response[:500]}")
            return []
    
    def build_probe_sql(self, tool_id: str, parameters: Dict[str, Any]) -> Optional[str]:
        """
        根据tool_id和parameters构建Probe SQL
        
        Args:
            tool_id: 工具ID
            parameters: 参数字典
            
        Returns:
            Probe SQL字符串，如果失败返回None
        """
        # 查找对应的模板
        template = None
        for t in self.probe_templates:
            if t['id'] == tool_id:
                template = t
                break
        
        if not template:
            print(f"[Probe生成器] 警告：未找到工具模板: {tool_id}")
            return None
        
        # 获取SQL模板
        sql_template = template['sql_template']
        required_params = template['parameters']
        default_values = template.get('default_values', {})
        
        # 合并默认值
        final_params = default_values.copy()
        final_params.update(parameters)
        
        # 检查必需参数
        missing_params = [p for p in required_params if p not in final_params]
        if missing_params:
            print(f"[Probe生成器] 警告：缺少必需参数 {missing_params} for tool {tool_id}")
            return None
        
        # 替换模板中的占位符
        try:
            sql = sql_template.format(**final_params)
            return sql
        except KeyError as e:
            print(f"[Probe生成器] 警告：模板参数替换失败: {e}")
            return None
    
    def execute_probe_sql(self, sql: str, timeout_s: float = 30.0) -> Dict[str, Any]:
        """
        执行Probe SQL并返回结果
        
        Args:
            sql: Probe SQL语句
            timeout_s: 超时时间（秒）
            
        Returns:
            执行结果字典，包含valid, query_result, error等字段
        """
        return self.sql_executor._execute_single_query(sql, timeout_s=timeout_s)
    
    def generate_and_execute_probes(self, question: str, schema_info: str, additional_context: str = "", 
                                     timeout_s: float = 30.0) -> Dict[str, Any]:
        """
        生成并执行Probe SQL，返回完整的probe结果
        
        Args:
            question: 自然语言问题
            schema_info: 数据库模式信息
            additional_context: 额外上下文
            timeout_s: 每个probe的超时时间
            
        Returns:
            包含probe actions和执行结果的字典
        """
        print(f"[Probe生成] 开始生成Probe Actions...")
        
        # 生成Probe Actions
        actions = self.generate_probe_actions(question, schema_info, additional_context)
        
        if not actions:
            print(f"[Probe生成] 未生成任何Probe Actions")
            return {
                'actions': [],
                'results': [],
                'summary': '未生成任何Probe Actions'
            }
        
        print(f"[Probe生成] 生成了 {len(actions)} 个Probe Actions")
        
        # 构建并执行Probe SQL
        probe_results = []
        for i, action in enumerate(actions):
            tool_id = action.get('tool_id', '')
            parameters = action.get('parameters', {})
            
            print(f"[Probe执行] [{i+1}/{len(actions)}] 工具: {tool_id}, 参数: {parameters}")
            
            # 构建SQL
            sql = self.build_probe_sql(tool_id, parameters)
            if not sql:
                probe_results.append({
                    'action': action,
                    'sql': None,
                    'result': {'valid': False, 'error': '构建SQL失败'},
                    'success': False
                })
                continue
            
            # 执行SQL
            result = self.execute_probe_sql(sql, timeout_s=timeout_s)
            
            success = result.get('valid', False)
            if success:
                query_result = result.get('query_result', [])
                result_count = len(query_result) if isinstance(query_result, list) else 0
                print(f"[Probe执行] ✓ 成功，返回 {result_count} 行结果")
            else:
                error = result.get('error', '未知错误')
                print(f"[Probe执行] ✗ 失败: {error[:200]}")
            
            probe_results.append({
                'action': action,
                'sql': sql,
                'result': result,
                'success': success
            })
        
        # 构建摘要
        success_count = sum(1 for r in probe_results if r['success'])
        summary = f"执行了 {len(probe_results)} 个Probe，成功 {success_count} 个，失败 {len(probe_results) - success_count} 个"
        
        return {
            'actions': actions,
            'results': probe_results,
            'summary': summary
        }
    
    def format_probe_results_for_cte(self, probe_data: Dict[str, Any]) -> str:
        """
        将Probe结果格式化为字符串，用于传递给CTE生成器
        
        Args:
            probe_data: generate_and_execute_probes返回的数据
            
        Returns:
            格式化的字符串
        """
        if not probe_data or not probe_data.get('results'):
            return ""
        
        lines = ["### Probe Results (Database Investigation)"]
        lines.append("")
        
        results = probe_data['results']
        for i, probe_result in enumerate(results, 1):
            action = probe_result.get('action', {})
            tool_id = action.get('tool_id', 'unknown')
            parameters = action.get('parameters', {})
            sql = probe_result.get('sql', '')
            result = probe_result.get('result', {})
            success = probe_result.get('success', False)
            
            lines.append(f"**Probe {i}: {tool_id}**")
            lines.append(f"- Parameters: {parameters}")
            if sql:
                lines.append(f"- SQL: `{sql[:200]}{'...' if len(sql) > 200 else ''}`")
            
            if success:
                query_result = result.get('query_result', [])
                if isinstance(query_result, list) and len(query_result) > 0:
                    # 显示前3行结果
                    sample_size = min(3, len(query_result))
                    lines.append(f"- Result: Success, returned {len(query_result)} rows")
                    lines.append(f"- Sample data (first {sample_size} rows):")
                    for j, row in enumerate(query_result[:sample_size]):
                        lines.append(f"  Row {j+1}: {row}")
                else:
                    lines.append(f"- Result: Success, but returned empty result")
            else:
                error = result.get('error', '未知错误')
                lines.append(f"- Result: Failed - {error[:200]}")
            
            lines.append("")
        
        lines.append(f"**Summary**: {probe_data.get('summary', '')}")
        
        return "\n".join(lines)

