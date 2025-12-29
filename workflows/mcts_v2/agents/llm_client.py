import os
import time
from typing import List, Dict, Any, Optional
import openai # 假设使用 OpenAI 兼容接口

class LLMClient:
    def __init__(self, config: Dict):
        """
        config: {
            'model': 'gpt-4o',
            'api_key': '...',
            'base_url': '...' (Optional)
            'timeout': 120 (Optional, 默认120秒)
        }
        """
        self.model = config.get('model', 'gpt-3.5-turbo')
        self.timeout = config.get('timeout', 120)  # 默认120秒超时
        self.client = openai.OpenAI(
            api_key=config.get('api_key', os.getenv("OPENAI_API_KEY")),
            base_url=config.get('base_url'),
            timeout=self.timeout  # 添加超时设置
        )

    def chat(self, messages: List[Dict], temperature: float = 0.7, n: int = 1, stop: Optional[List[str]] = None) -> List[str]:
        """
        核心生成函数。
        支持 n > 1 (用于 Self-Consistency 采样)。
        """
        try:
            print(f"  [LLM] 调用模型 {self.model}, n={n}, timeout={self.timeout}s...")
            start_time = time.time()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                n=n,
                stop=stop,
                timeout=self.timeout  # 在API调用时也设置超时
            )
            elapsed = time.time() - start_time
            print(f"  [LLM] 响应完成，耗时 {elapsed:.2f}s")
            return [choice.message.content for choice in response.choices]
        except openai.APITimeoutError as e:
            print(f"  [LLM] ⚠️ 超时错误 (timeout={self.timeout}s): {e}")
            return []
        except Exception as e:
            print(f"  [LLM] ⚠️ 调用错误: {e}")
            return []