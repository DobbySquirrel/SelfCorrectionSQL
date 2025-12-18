from openai import OpenAI
import dotenv
import os
from typing import List, Optional
from alphasql.llm_call.cost_recoder import CostRecorder
import time

# 使用 override=False，优先使用已存在的环境变量（脚本中通过 export 设置的）
# 这样脚本中设置的环境变量不会被 .env 文件覆盖
dotenv.load_dotenv(override=False)

DEFAULT_COST_RECORDER = CostRecorder(model="gpt-3.5-turbo")

MAX_RETRYING_TIMES = 5

# MAX_TIMEOUT = 60

N_CALLING_STRATEGY_SINGLE = "single"
N_CALLING_STRATEGY_MULTIPLE = "multiple"

def call_openai(prompt: str,
                model: str,
                temperature: float = 0.0,
                top_p: float = 1.0,
                n: int = 1,
                max_tokens: int = 512,
                stop: List[str] = None,
                base_url: str = None,
                api_key: str = None,
                n_strategy: str = N_CALLING_STRATEGY_SINGLE,
                cost_recorder: Optional[CostRecorder] = DEFAULT_COST_RECORDER) -> str:
    # 如果参数未提供，从环境变量读取
    # 注意：MCTS 运行阶段应该使用环境变量 OPENAI_API_BASE 和 OPENAI_API_KEY（本地 vLLM）
    # 预处理阶段会显式传递 base_url 和 api_key，所以这里只作为最后的回退
    if base_url is None or base_url == "":
        # MCTS 阶段优先使用 OPENAI_API_BASE 或 VLLM_API_URL（本地服务）
        base_url = os.environ.get("OPENAI_API_BASE") or os.environ.get("VLLM_API_URL")
        # 如果都没有，才使用预处理阶段的默认配置（仅作为最后回退，用于预处理阶段）
        if not base_url:
            try:
                from alphasql.runner.preprocessor import DEFAULT_PREPROCESSOR_API_BASE
                base_url = DEFAULT_PREPROCESSOR_API_BASE
            except ImportError:
                pass
    if api_key is None or api_key == "":
        # MCTS 阶段优先使用 OPENAI_API_KEY 或 VLLM_API_KEY（本地服务）
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("VLLM_API_KEY")
        # 如果都没有，使用默认值
        if not api_key:
            try:
                from alphasql.runner.preprocessor import DEFAULT_PREPROCESSOR_API_KEY
                api_key = DEFAULT_PREPROCESSOR_API_KEY
            except ImportError:
                api_key = "dummy-key"  # 本地 vLLM 默认使用 dummy-key
    
    # 创建 OpenAI 客户端
    if base_url:
        client = OpenAI(base_url=base_url, api_key=api_key)
    else:
        client = OpenAI(api_key=api_key) if api_key else OpenAI()
    retrying = 0
    while retrying < MAX_RETRYING_TIMES:
        try:
            if n == 1 or (n > 1 and n_strategy == N_CALLING_STRATEGY_SINGLE):
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    n=n,
                    top_p=top_p,
                    stop=stop,
                    # timeout=MAX_TIMEOUT,
                )
                if cost_recorder is not None and response.usage is not None:
                    cost_recorder.update_cost(response.usage.prompt_tokens, response.usage.completion_tokens)
                contents = [choice.message.content for choice in response.choices]
                break
            elif n > 1 and n_strategy == N_CALLING_STRATEGY_MULTIPLE:
                contents = []
                for _ in range(n):
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature,
                        max_tokens=max_tokens,
                        n=1,
                        top_p=top_p,
                        stop=stop,
                        # timeout=MAX_TIMEOUT,
                    )
                    if cost_recorder is not None and response.usage is not None:
                        cost_recorder.update_cost(response.usage.prompt_tokens, response.usage.completion_tokens)
                    contents.append(response.choices[0].message.content)
                break
            else:
                raise ValueError(f"Invalid n_strategy: {n_strategy} for n: {n}")
        except Exception as e:
            print("-" * 100)
            print(f"Error calling OpenAI: {e}")
            print(f"Start retrying {retrying + 1} times")
            print("-" * 100)
            retrying += 1
            if retrying == MAX_RETRYING_TIMES:
                raise e
            # sleep for 10 seconds
            time.sleep(10)
    # print(contents)
    return contents

