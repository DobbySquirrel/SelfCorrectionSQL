

import os
from typing import Optional
from openai import OpenAI


def pick_model(base_url: str = None, api_key: str = None, preferred: Optional[str] = None) -> str:
    """
    自动选择可用的模型
    
    Args:
        base_url: API base URL，如果不提供则从环境变量读取
        api_key: API key，如果不提供则从环境变量读取
        preferred: 首选模型名称，如果不提供则从环境变量读取
        
    Returns:
        可用的模型名称
        
    Raises:
        RuntimeError: 如果没有可用模型
    """
    # 使用默认值或从环境变量获取
    base_url = base_url or os.environ.get("VLLM_API_URL", "http://localhost:8009/v1")
    api_key = api_key or os.environ.get("VLLM_API_KEY", "dummy-key")
    preferred = preferred or os.environ.get("VLLM_MODEL")
    
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        models = client.models.list().data
        
        if not models:
            raise RuntimeError(
                "No models served by vLLM. "
                "Check vLLM logs / --served-model-name."
            )
        
        ids = sorted([m.id for m in models])
        
        # 如果指定了preferred且存在，使用它
        if preferred and preferred in ids:
            return preferred
        
        # 否则使用第一个可用模型
        return ids[0]
        
    except Exception as e:
        # 如果获取失败，尝试使用preferred
        if preferred:
            return preferred
        raise RuntimeError(f"Failed to get model list: {e}")


def get_llm_config(
    model: str = None,
    base_url: str = None,
    api_key: str = None,
    temperature: float = 0.7,
    auto_select: bool = True
) -> dict:
    """
    获取LLM配置
    
    Args:
        model: 模型名称，如果不提供且auto_select=True则自动选择
        base_url: API base URL
        api_key: API key
        temperature: 温度参数
        auto_select: 是否自动选择模型
        
    Returns:
        LLM配置字典
    """
    base_url = base_url or os.environ.get("VLLM_API_URL", "http://localhost:8009/v1")
    api_key = api_key or os.environ.get("VLLM_API_KEY", "dummy-key")
    
    # 如果没有提供model且开启auto_select，自动选择
    if not model and auto_select:
        model = pick_model(base_url, api_key)
    elif not model:
        # 如果没有model且不自动选择，从环境变量获取
        model = os.environ.get("VLLM_MODEL")
        if not model:
            raise ValueError(
                "Model not specified. "
                "Please provide model name or set VLLM_MODEL environment variable."
            )
    
    return {
        "config_list": [
            {
                "model": model,
                "api_key": api_key,
                "base_url": base_url
            }
        ],
        "temperature": temperature,
    }


def print_model_info(base_url: str = None, api_key: str = None):
    """
    打印可用的模型信息
    
    Args:
        base_url: API base URL
        api_key: API key
    """
    base_url = base_url or os.environ.get("VLLM_API_URL", "http://localhost:8009/v1")
    api_key = api_key or os.environ.get("VLLM_API_KEY", "dummy-key")
    
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        models = client.models.list().data
        
        if not models:
            print("✗ 没有可用的模型")
            return
        
        print("✓ 可用模型:")
        for model in models:
            print(f"  - {model.id}")
            
    except Exception as e:
        print(f"✗ 获取模型列表失败: {e}")


# python workflows/mcts/test/test_bc_parallel_rollout_backup11_5.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --qid 1040 \
#   --json_out /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/single_1040_timing_parallel.json \
#   --parallel_workers 5