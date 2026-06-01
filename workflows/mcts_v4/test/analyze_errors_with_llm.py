#!/usr/bin/env python3
"""
使用LLM分析三个策略都错误的案例
"""

import json
import sys
from pathlib import Path
import os
import time
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

def load_json(file_path):
    """加载JSON文件"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def analyze_error_with_llm(question, evidence, gold_sql, pred_sql, db_name):
    """使用LLM分析SQL错误原因"""

    # 使用与test_api.py相同的配置方式
    from workflows.mcts_v1.utils.model_utils import pick_model

    try:
        # 获取可用模型（与test_api.py相同的方式）
        base_url = os.environ.get("VLLM_API_URL", "http://localhost:8000/v1")
        api_key = os.environ.get("VLLM_API_KEY", "dummy-key")
        model = pick_model(base_url, api_key)

        print(f"使用模型: {model}, 端点: {base_url}")
    except Exception as e:
        return f"无法获取模型配置: {str(e)}"

    analysis_prompt = f"""
请分析以下SQL查询的错误原因：

问题: {question}
证据: {evidence}
数据库: {db_name}

Gold SQL: {gold_sql}
预测SQL: {pred_sql}

请分析预测SQL为什么会出错，可能的原因包括：
1. 表名理解错误
2. 列名理解错误
3. JOIN条件错误
4. WHERE条件错误
5. 聚合函数使用错误
6. 自然语言理解偏差
7. 证据信息利用不足

请给出具体的分析和改进建议。回答要简洁，直接指出问题所在。
"""

    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=60.0)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个SQL专家，专门分析SQL查询错误的原因。"},
                {"role": "user", "content": analysis_prompt}
            ],
            temperature=0.1,  # 低温度以获得更确定的分析
            max_tokens=500
        )

        analysis = response.choices[0].message.content.strip()
        return analysis

    except Exception as e:
        return f"LLM分析失败: {str(e)}"

def process_single_case(case_data):
    """处理单个案例的LLM分析"""
    case, case_idx = case_data
    qid = case['qid']
    question = case['question']
    db = case['db']
    evidence = case['evidence']
    gold_sql = case['gold_sql']

    print(f"开始处理案例 {case_idx + 1}: qid={qid}")

    case_analysis = {
        'qid': qid,
        'question': question,
        'db': db,
        'evidence': evidence,
        'gold_sql': gold_sql,
        'strategy_analyses': {}
    }

    # 分析每个策略的SQL
    for strategy, strategy_data in case['strategies'].items():
        pred_sql = strategy_data['sql']
        reward = strategy_data['reward']

        # 使用LLM分析错误
        analysis = analyze_error_with_llm(question, evidence, gold_sql, pred_sql, db)

        case_analysis['strategy_analyses'][strategy] = {
            'pred_sql': pred_sql,
            'reward': reward,
            'llm_analysis': analysis
        }

    print(f"完成案例 {case_idx + 1}: qid={qid}")
    return case_analysis

def main():
    # 加载错误案例
    with open('../all_wrong_cases_analysis.json', 'r') as f:
        error_cases = json.load(f)

    print(f"开始并行分析 {len(error_cases)} 个错误案例...")
    print("使用并行处理以提高效率")

    # 为每个错误案例创建分析（并行处理）
    llm_analyses = []

    # 限制并发数量避免LLM服务过载
    max_workers = 8

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_case = {
            executor.submit(process_single_case, (case, i)): (case, i)
            for i, case in enumerate(error_cases)
        }

        # 收集结果
        for future in as_completed(future_to_case):
            case_data = future_to_case[future]
            try:
                result = future.result()
                llm_analyses.append(result)
                print(f"✅ 完成案例 {len(llm_analyses)}/{len(error_cases)}")
            except Exception as exc:
                case, idx = case_data
                print(f"❌ 案例 {idx + 1} (qid={case['qid']}) 处理失败: {exc}")

    # 按原始顺序排序
    llm_analyses.sort(key=lambda x: int(x['qid']))

    # 保存详细的LLM分析结果
    output_file = Path(__file__).parent.parent / 'llm_error_analysis.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(llm_analyses, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*80}")
    print("LLM分析完成")
    print(f"分析结果已保存到: {output_file}")
    print(f"{'='*80}")

    # 输出总结
    print(f"\n总结:")
    print(f"- 分析了 {len(llm_analyses)} 个错误案例")
    print(f"- 每个案例分析了3个策略的SQL")
    print(f"- 使用并行处理 (max_workers={max_workers})")
    print(f"- 使用LLM识别错误模式和改进建议")

    # 输出一些有趣的发现
    print(f"\n{'='*80}")
    print("快速统计:")
    print(f"{'='*80}")

    total_analyses = 0
    error_patterns = {}

    for case in llm_analyses:
        for strategy, analysis_data in case['strategy_analyses'].items():
            total_analyses += 1
            llm_text = analysis_data['llm_analysis']

            # 提取常见错误模式
            if "表名理解错误" in llm_text:
                error_patterns["表名理解错误"] = error_patterns.get("表名理解错误", 0) + 1
            if "列名理解错误" in llm_text:
                error_patterns["列名理解错误"] = error_patterns.get("列名理解错误", 0) + 1
            if "JOIN条件错误" in llm_text:
                error_patterns["JOIN条件错误"] = error_patterns.get("JOIN条件错误", 0) + 1
            if "WHERE条件错误" in llm_text:
                error_patterns["WHERE条件错误"] = error_patterns.get("WHERE条件错误", 0) + 1
            if "聚合函数" in llm_text:
                error_patterns["聚合函数错误"] = error_patterns.get("聚合函数错误", 0) + 1

    print(f"总分析数: {total_analyses}")
    print("常见错误模式:")
    for pattern, count in sorted(error_patterns.items(), key=lambda x: x[1], reverse=True):
        percentage = count / total_analyses * 100
        print(".1f")

if __name__ == "__main__":
    main()