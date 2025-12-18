import json
import os
import sys
import yaml
from pathlib import Path
from tqdm import tqdm
import pandas as pd
import argparse

# 添加项目根目录到系统路径
sys.path.append(str(Path(__file__).parent.parent))

from agents.autogen_agents import AgentSystem
from core.database_connector import DatabaseConnector
from core.reasoning_tree import ReasoningTree

def load_dataset(file_path):
    """加载数据集"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data

def load_config(config_path):
    """加载配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config

def setup_llm_config(config):
    """根据配置文件设置LLM配置"""
    llm_config = {
        "config_list": [
            {
                "model": config.get("model", "gpt-4o-mini"),
                "api_key": config.get("api", ""),
                "base_url": config.get("base_url", "https://api.chsdw.top/v1/chat/completions")
            }
        ],
        "temperature": 0.7,
    }
    return llm_config

def extract_first_example(data):
    """从每个问题的examples列表中提取第一个示例"""
    processed_data = []
    for item in data:
        if item.get('examples') and len(item['examples']) > 0:
            first_example = item['examples'][0]
            processed_item = {
                'question_id': item['question_id'],
                'db_id': item['db_id'],
                'question': item['question'],
                'related_db_id': first_example.get('db_id', ''),
                'related_question': first_example.get('question', ''),
                'related_Gold_sql': first_example.get('sql', ''),
                'related_evidence': first_example.get('evidence', '')
            }
            processed_data.append(processed_item)
    return processed_data

def main(run_full_dataset=True):
    # 加载配置文件
    config_path = Path(__file__).parent / "config" / "config.yaml"
    config = load_config(config_path)
    
    # 设置输出文件夹
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/generate_pandas")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 加载数据集
    dataset_path = "/home/shenshuyu/SQL_tool/data/example/sub_few_shot.json"
    raw_dataset = load_dataset(dataset_path)
    
    # 提取每个问题的第一个示例
    dataset = extract_first_example(raw_dataset)
    
    # 如果只运行前5个样本
    if not run_full_dataset:
        dataset = dataset[:5]
        print("仅运行前5个数据集样本")
    else:
        print(f"运行全部数据集 ({len(dataset)} 个样本)")
    
    # 设置LLM配置
    llm_config = setup_llm_config(config)
    
    # 创建结果存储列表
    results = []
    
    # 添加解决方案缓存
    solution_cache = {}
    
    # 遍历数据集中的所有问题
    for i, item in enumerate(tqdm(dataset, desc="处理问题")):
        question_id = item["question_id"]
        related_db_id = item["related_db_id"]
        related_question = item["related_question"]
        related_Gold_sql = item["related_Gold_sql"]
        related_evidence = item["related_evidence"]
        
        print(f"\n处理问题 {i+1}/{len(dataset)}:  (ID: {question_id})")
        
        # 检查是否有缓存的解决方案
        cache_key = (related_question, related_db_id)
        if cache_key in solution_cache:
            cached_solution = solution_cache[cache_key]
            print(f"找到缓存的解决方案，直接使用")
            result = {
                "question_id": question_id,
                "related_db_id": related_db_id,
                "related_question": related_question,
                "is_solved": True,
                "related_python_code": cached_solution["code"],
                "related_python_result": cached_solution["result"],
                "related_Gold_sql": related_Gold_sql,
                "related_evidence": related_evidence,
                "used_cached_solution": True  # 标记这是使用缓存的解决方案
            }
            results.append(result)
            continue

        db_connector = DatabaseConnector(related_db_id)
        reasoning_tree = ReasoningTree(related_question)
        agent_system = AgentSystem(llm_config, db_connector)
        
        try:
            is_solved = agent_system._solve_generate_function(
                node=reasoning_tree.root,
                tree=reasoning_tree,
                db_name=related_db_id,
                Gold_sql=related_Gold_sql,
                additional_context=related_evidence
            )
            
            cell_value = reasoning_tree.root.cell_value
            python_code = reasoning_tree.root.actions
            
            # 如果解决成功，添加到缓存
            if is_solved:
                solution_cache[cache_key] = {
                    "code": python_code,
                    "result": cell_value
                }
            
            result = {
                "question_id": question_id,
                "related_db_id": related_db_id,
                "related_question": related_question,
                "is_solved": is_solved,
                "related_python_code": python_code,
                "related_python_result": cell_value,
                "related_Gold_sql": related_Gold_sql,
                "related_evidence": related_evidence,
                "unfixable_reason": getattr(reasoning_tree.root, 'unfixable_reason', None),
                "used_cached_solution": False  # 标记这不是使用缓存的解决方案
            }
            
            if hasattr(reasoning_tree.root, 'diagnosis'):
                result["diagnosis"] = reasoning_tree.root.diagnosis
            results.append(result)
            
            print(f"问题已解决: {is_solved}")
            print(f"Python代码: {python_code}")
            print(f"执行结果: {cell_value}")
            
            # 每处理5个问题保存一次中间结果
            if (i + 1) % 5 == 0:
                with open(output_dir / "test_results_generate_function_ongoing.json", "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=4)
                print(f"已保存中间结果 (已完成 {i+1}/{len(dataset)} 个问题)")
            
        except Exception as e:
            print(f"处理问题时出错: {e}")
            result = {
                "question_id": question_id,
                "related_db_id": related_db_id,
                "related_question": related_question,
                "is_solved": False,
                "error": str(e),
                "related_python_code": None,
                "related_python_result": None,
                "related_Gold_sql": related_Gold_sql,
                "related_evidence": related_evidence,
                "unfixable_reason": getattr(reasoning_tree.root, 'unfixable_reason', None),
                "used_cached_solution": False
            }
            if hasattr(reasoning_tree.root, 'diagnosis'):
                result["diagnosis"] = reasoning_tree.root.diagnosis
            results.append(result)
            
            # 发生错误时保存中间结果
            with open(output_dir / "test_results_generate_function_ongoing.json", "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=4)
            print(f"处理问题 {i+1} 时出错，已保存中间结果")
    
    # 将最终结果保存为json文件
    with open(output_dir / "test_results_generate_function_full.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    
    # 关闭数据库连接
    db_connector.disconnect()
    
    # 打印统计信息
    solved_count = sum(1 for r in results if r["is_solved"])
    total_count = len(results)
    print(f"\n测试完成! 成功解决: {solved_count}/{total_count} ({solved_count/total_count*100:.2f}%)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='测试生成函数工作流')
    parser.add_argument('--sample', action='store_true', help='仅运行前5个样本')
    args = parser.parse_args()
    
    main(run_full_dataset=not args.sample)
