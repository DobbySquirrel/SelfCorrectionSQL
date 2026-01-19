"""
API测试脚本

用于测试LLM API的连接和调用，包括：
1. 测试API端点连接
2. 测试模型列表获取
3. 测试简单的chat completion调用
4. 测试CTE生成和SQL生成的API调用
5. 显示详细的错误信息和性能指标
"""

import os
import sys
import time
import argparse
from pathlib import Path
from typing import List, Optional
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

# 添加项目路径
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from workflows.mcts_v1.utils.model_utils import pick_model, get_llm_config


def test_api_connection(base_url: str, api_key: str, timeout: int = 30) -> bool:
    """测试API端点连接"""
    print(f"\n{'='*80}")
    print(f"测试API连接")
    print(f"{'='*80}")
    print(f"端点: {base_url}")
    print(f"超时: {timeout}秒")
    
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        start_time = time.time()
        models = client.models.list()
        elapsed = time.time() - start_time
        
        print(f"✅ 连接成功 (耗时: {elapsed:.2f}秒)")
        print(f"可用模型数量: {len(models.data)}")
        for model in models.data:
            print(f"  - {model.id}")
        return True
    except Exception as e:
        print(f"❌ 连接失败: {type(e).__name__}: {e}")
        return False


def test_model_pick(base_url: str, api_key: str) -> Optional[str]:
    """测试模型选择"""
    print(f"\n{'='*80}")
    print(f"测试模型选择")
    print(f"{'='*80}")
    
    try:
        model = pick_model(base_url, api_key)
        print(f"✅ 成功选择模型: {model}")
        return model
    except Exception as e:
        print(f"❌ 模型选择失败: {type(e).__name__}: {e}")
        return None


def test_simple_chat(base_url: str, api_key: str, model: str, timeout: int = 60) -> bool:
    """测试简单的chat completion调用"""
    print(f"\n{'='*80}")
    print(f"测试简单Chat Completion")
    print(f"{'='*80}")
    print(f"模型: {model}")
    print(f"超时: {timeout}秒")
    
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        start_time = time.time()
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "Hello, please respond with 'API test successful'"}
            ],
            temperature=0.7,
            n=1
        )
        
        elapsed = time.time() - start_time
        content = response.choices[0].message.content
        
        print(f"✅ 调用成功 (耗时: {elapsed:.2f}秒)")
        print(f"响应: {content[:200]}")
        return True
    except Exception as e:
        print(f"❌ 调用失败: {type(e).__name__}: {e}")
        return False


def test_parallel_chat(base_url: str, api_key: str, model: str, num_requests: int = 4, timeout: int = 120) -> bool:
    """测试并行chat completion调用"""
    print(f"\n{'='*80}")
    print(f"测试并行Chat Completion")
    print(f"{'='*80}")
    print(f"模型: {model}")
    print(f"并发数: {num_requests}")
    print(f"超时: {timeout}秒")
    
    def single_request(request_id: int, temperature: float):
        """单个请求"""
        try:
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
            start_time = time.time()
            
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": f"Say 'Request {request_id} completed'"}
                ],
                temperature=temperature,
                n=1
            )
            
            elapsed = time.time() - start_time
            content = response.choices[0].message.content
            return {
                'success': True,
                'request_id': request_id,
                'temperature': temperature,
                'elapsed': elapsed,
                'content': content[:100]
            }
        except Exception as e:
            return {
                'success': False,
                'request_id': request_id,
                'temperature': temperature,
                'error': f"{type(e).__name__}: {e}"
            }
    
    # 并行执行请求
    temperatures = [0.0, 0.3, 0.6, 0.9]
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = {
            executor.submit(single_request, i, temperatures[i % len(temperatures)]): i 
            for i in range(num_requests)
        }
        
        results = []
        for future in as_completed(futures):
            results.append(future.result())
    
    total_elapsed = time.time() - start_time
    
    # 统计结果
    success_count = sum(1 for r in results if r['success'])
    failed_count = num_requests - success_count
    
    print(f"\n总耗时: {total_elapsed:.2f}秒")
    print(f"成功: {success_count}/{num_requests}")
    print(f"失败: {failed_count}/{num_requests}")
    
    if success_count > 0:
        avg_elapsed = sum(r['elapsed'] for r in results if r['success']) / success_count
        print(f"平均响应时间: {avg_elapsed:.2f}秒")
    
    # 打印详细结果
    print(f"\n详细结果:")
    for result in sorted(results, key=lambda x: x['request_id']):
        if result['success']:
            print(f"  ✅ Request {result['request_id']} (temp={result['temperature']}): "
                  f"{result['elapsed']:.2f}秒 - {result['content']}")
        else:
            print(f"  ❌ Request {result['request_id']} (temp={result['temperature']}): "
                  f"{result['error']}")
    
    return failed_count == 0


def test_cte_generation_api(base_url: str, api_key: str, model: str, timeout: int = 120) -> bool:
    """测试CTE生成的API调用（模拟实际使用场景）"""
    print(f"\n{'='*80}")
    print(f"测试CTE生成API调用")
    print(f"{'='*80}")
    print(f"模型: {model}")
    print(f"超时: {timeout}秒")
    
    # 模拟CTE生成的prompt
    system_message = "You are an SQL expert. Generate CTE queries."
    user_message = """
**Input**:
* **Natural language question**: Find all schools in California
* **Database schema**: 
  schools(id, name, city, state)
* **Preceding CTE and Results**: None
* **Depth information**: Step 1/8

Please generate a CTE query.
"""
    
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        start_time = time.time()
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            n=1
        )
        
        elapsed = time.time() - start_time
        content = response.choices[0].message.content
        
        print(f"✅ CTE生成调用成功 (耗时: {elapsed:.2f}秒)")
        print(f"响应长度: {len(content)} 字符")
        print(f"响应预览: {content[:300]}...")
        return True
    except Exception as e:
        print(f"❌ CTE生成调用失败: {type(e).__name__}: {e}")
        return False


def test_sql_generation_api(base_url: str, api_key: str, model: str, timeout: int = 120) -> bool:
    """测试SQL生成的API调用（模拟实际使用场景）"""
    print(f"\n{'='*80}")
    print(f"测试SQL生成API调用")
    print(f"{'='*80}")
    print(f"模型: {model}")
    print(f"超时: {timeout}秒")
    
    # 模拟SQL生成的prompt
    system_message = "You are an SQL expert. Generate complete SQL queries."
    user_message = """
**Natural language question**: Find all schools in California
**Database schema**: 
  schools(id, name, city, state)
**Existing CTE and Results**: None

Please generate a complete SQL query.
"""
    
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        start_time = time.time()
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            n=1
        )
        
        elapsed = time.time() - start_time
        content = response.choices[0].message.content
        
        print(f"✅ SQL生成调用成功 (耗时: {elapsed:.2f}秒)")
        print(f"响应长度: {len(content)} 字符")
        print(f"响应预览: {content[:300]}...")
        return True
    except Exception as e:
        print(f"❌ SQL生成调用失败: {type(e).__name__}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="测试LLM API连接和调用")
    parser.add_argument("--base_url", type=str, default=None,
                        help="API base URL (默认: 从环境变量VLLM_API_URL获取或http://localhost:8000/v1)")
    parser.add_argument("--api_key", type=str, default=None,
                        help="API key (默认: 从环境变量VLLM_API_KEY获取或dummy-key)")
    parser.add_argument("--model", type=str, default=None,
                        help="模型名称 (默认: 自动选择)")
    parser.add_argument("--timeout", type=int, default=120,
                        help="超时时间（秒）(默认: 120)")
    parser.add_argument("--test", type=str, choices=['all', 'connection', 'model', 'simple', 'parallel', 'cte', 'sql'],
                        default='all', help="要运行的测试 (默认: all)")
    parser.add_argument("--parallel_requests", type=int, default=4,
                        help="并行请求数量 (默认: 4)")
    
    args = parser.parse_args()
    
    # 获取配置
    base_url = args.base_url or os.environ.get("VLLM_API_URL", "http://localhost:8000/v1")
    api_key = args.api_key or os.environ.get("VLLM_API_KEY", "dummy-key")
    timeout = args.timeout
    
    print(f"\n{'='*80}")
    print(f"API测试配置")
    print(f"{'='*80}")
    print(f"端点: {base_url}")
    print(f"API Key: {api_key[:10]}..." if len(api_key) > 10 else f"API Key: {api_key}")
    print(f"超时: {timeout}秒")
    print(f"测试类型: {args.test}")
    
    # 运行测试
    results = {}
    
    if args.test in ['all', 'connection']:
        results['connection'] = test_api_connection(base_url, api_key, timeout=min(timeout, 30))
    
    if args.test in ['all', 'model']:
        model = test_model_pick(base_url, api_key)
        if model:
            args.model = model
        elif args.model:
            print(f"\n使用指定的模型: {args.model}")
        else:
            print(f"\n⚠️  无法获取模型，将使用环境变量VLLM_MODEL或跳过需要模型的测试")
    
    if args.model:
        if args.test in ['all', 'simple']:
            results['simple'] = test_simple_chat(base_url, api_key, args.model, timeout)
        
        if args.test in ['all', 'parallel']:
            results['parallel'] = test_parallel_chat(
                base_url, api_key, args.model, 
                num_requests=args.parallel_requests, 
                timeout=timeout
            )
        
        if args.test in ['all', 'cte']:
            results['cte'] = test_cte_generation_api(base_url, api_key, args.model, timeout)
        
        if args.test in ['all', 'sql']:
            results['sql'] = test_sql_generation_api(base_url, api_key, args.model, timeout)
    
    # 打印总结
    print(f"\n{'='*80}")
    print(f"测试总结")
    print(f"{'='*80}")
    
    if results:
        for test_name, success in results.items():
            status = "✅ 通过" if success else "❌ 失败"
            print(f"{test_name:20s}: {status}")
        
        all_passed = all(results.values())
        print(f"\n总体结果: {'✅ 所有测试通过' if all_passed else '❌ 部分测试失败'}")
    else:
        print("未运行任何测试")


if __name__ == "__main__":
    main()

