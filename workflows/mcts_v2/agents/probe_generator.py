import json
from typing import List
from mcts.node import MCTSNode
from .llm_client import LLMClient
from .prompts import PROBE_SELECTION_PROMPT, PROBE_TEMPLATES_JSON
from typing import Dict, List
class ProbeGenerator:
    def __init__(self, llm_config, db_connector):
        self.llm = LLMClient(llm_config)
        self.db = db_connector
        
        # 预处理 Tool Library Description
        self.tool_desc = ""
        self.template_map = {}
        for t in PROBE_TEMPLATES_JSON:
            self.template_map[t['id']] = t
            self.tool_desc += f"- ID: {t['id']}\n  Desc: {t['description']}\n  Params: {t['parameters']}\n"

    def generate_probes(self, node: MCTSNode, k: int = 5) -> List[str]:
        """
        生成 K 个 Probe SQL。
        流程：LLM 选工具 (JSON) -> Python 填模板 -> 返回 SQL 列表。
        """
        # 1. 组装 Prompt
        obs = node.observation
        prompt = PROBE_SELECTION_PROMPT.format(
            status=obs.get('status'),
            error=obs.get('error_message', 'None'),
            tool_library_desc=self.tool_desc
        )
        
        messages = [
            {"role": "system", "content": "You are a helpful DB assistant."},
            {"role": "user", "content": prompt}
        ]
        
        # 2. 调用 LLM (让它一次生成一组工具调用)
        # 这里只需要 n=1，因为我们让 LLM 在一个 JSON 里返回多个 tool calls
        try:
            responses = self.llm.chat(messages, temperature=0.5, n=1)
            if not responses:
                print(f"  [Probe] ⚠️ LLM 返回空响应")
                return []
            response = responses[0]
            tool_calls = self._parse_json_response(response)
        except IndexError as e:
            print(f"  [Probe] ⚠️ LLM 响应格式错误: {e}")
            return []
        except Exception as e:
            print(f"  [Probe] ⚠️ 生成错误: {e}")
            return []

        # 3. 填模板生成 SQL
        probes = []
        for call in tool_calls:
            tool_id = call.get('tool_id')
            params = call.get('params', {})
            
            if tool_id in self.template_map:
                template = self.template_map[tool_id]
                sql_tmpl = template['sql_template']
                defaults = template.get('default_values', {})
                
                # 合并参数 (默认值 + LLM参数)
                final_params = defaults.copy()
                final_params.update(params)
                
                try:
                    # 渲染 SQL
                    sql = sql_tmpl.format(**final_params)
                    probes.append(sql)
                except KeyError as e:
                    print(f"Missing param for tool {tool_id}: {e}")
                    
        # 截取前 K 个
        return probes[:k]

    def _parse_json_response(self, text: str) -> List[Dict]:
        """从 LLM 回复中提取 JSON"""
        try:
            # 简单的清理
            text = text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            
            return json.loads(text)
        except:
            return []