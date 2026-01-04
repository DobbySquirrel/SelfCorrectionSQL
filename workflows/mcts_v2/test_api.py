"""
API & 组件集成测试脚本 (test_api.py)
适配 CoCTE-MCTS 架构
"""

import os
import sys
import time
import argparse
import logging
from typing import List

# 确保能导入本地模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入我们写的组件
from agents.llm_client import LLMClient
# from agents.cte_generator import CTEGenerator
from mcts.node import MCTSNode, ActionType

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_raw_connectivity(base_url: str, api_key: str, model: str):
    """Level 1: 测试最基础的 OpenAI SDK 连接"""
    print(f"\n{'='*60}")
    print(f"🚀 Level 1: Raw Connectivity Test")
    print(f"{'='*60}")
    print(f"URL: {base_url}")
    print(f"Model: {model}")

    from openai import OpenAI
    
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        start = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Return the number 1."}],
            max_tokens=10
        )
        duration = time.time() - start
        content = response.choices[0].message.content
        print(f"✅ Success ({duration:.2f}s): {content}")
        return True
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False

def test_llm_client_wrapper(config: dict):
    """Level 2: 测试我们的 LLMClient 封装 (包含 n=k 并行采样)"""
    print(f"\n{'='*60}")
    print(f"🛠️ Level 2: LLMClient Wrapper Test")
    print(f"{'='*60}")

    try:
        client = LLMClient(config)
        messages = [{"role": "user", "content": "Generate a random color name."}]
        
        print("Testing single generation (n=1)...")
        res = client.chat(messages, temperature=0.7, n=1)
        print(f"Result: {res}")
        if not res: raise Exception("Empty response")

        print("Testing parallel generation (n=3)...")
        start = time.time()
        res_multi = client.chat(messages, temperature=0.9, n=3)
        duration = time.time() - start
        print(f"Results ({duration:.2f}s): {res_multi}")
        
        if len(res_multi) != 3:
            print(f"⚠️ Warning: Requested 3, got {len(res_multi)}")
        
        print(f"✅ LLMClient Validated")
        return True
    except Exception as e:
        print(f"❌ LLMClient Failed: {e}")
        return False

# def test_cte_generator_integration(config: dict):
#     """Level 3: 测试 CTEGenerator 和 Prompts 集成"""
#     print(f"\n{'='*60}")
#     print(f"🧠 Level 3: CTEGenerator & Prompt Integration")
#     print(f"{'='*60}")

#     try:
#         # 1. Mock 一个 MCTS 节点
#         node = MCTSNode(action_type=ActionType.BUILD)
#         node.question = "Show me the director of the movie 'Hero'."
#         node.schema_info = "Table: movies\nColumns: id, title, director, year"
#         node.accumulated_sql = "" # Empty start
#         node.knowledge.verified_values = {"Hero": "Hero (2002)"} # 模拟知识
        
#         # 2. 实例化生成器
#         generator = CTEGenerator(config)
        
#         # 3. 生成
#         print("Generating CTEs...")
#         start = time.time()
#         ctes = generator.generate_ctes(node, k=2)
#         duration = time.time() - start
        
#         print(f"Generated {len(ctes)} CTEs in {duration:.2f}s:")
#         for i, cte in enumerate(ctes):
#             print(f"\n--- Candidate {i+1} ---")
#             print(cte)
            
#         if ctes:
#             print(f"✅ CTEGenerator Validated")
#             return True
#         else:
#             print(f"❌ Generated list is empty")
#             return False

#     except Exception as e:
#         print(f"❌ CTEGenerator Failed: {e}")
#         import traceback
#         traceback.print_exc()
#         return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base_url", type=str, default="http://localhost:8011/v1")
    parser.add_argument("--api_key", type=str, default="dummy")
    parser.add_argument("--model", type=str, default="deepseek-coder") # 或 meta-llama/Llama-3-70b-instruct
    args = parser.parse_args()

    config = {
        "model": args.model,
        "base_url": args.base_url,
        "api_key": args.api_key
    }

    # 1. 基础连接
    if not test_raw_connectivity(args.base_url, args.api_key, args.model):
        print("\n⛔ Critical: Raw connection failed. Check your vLLM/Service.")
        return

    # 2. 客户端封装
    if not test_llm_client_wrapper(config):
        print("\n⛔ Critical: LLMClient wrapper failed.")
        return

    # # 3. 业务逻辑
    # test_cte_generator_integration(config)

if __name__ == "__main__":
    main()