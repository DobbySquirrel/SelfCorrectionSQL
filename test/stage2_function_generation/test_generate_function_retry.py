import json
import os
import sys
import yaml
from pathlib import Path
from tqdm import tqdm
import pandas as pd
import argparse
import concurrent.futures
from threading import Lock

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

def extract_all_examples(data):
    """从每个问题的examples列表中提取所有示例"""
    processed_data = []
    for item in data:
        if item.get('examples') and len(item['examples']) > 0:
            for example in item['examples']:  # 遍历所有示例
                processed_item = {
                    'question_id': item['question_id'],
                    'db_id': item['db_id'],
                    'question': item['question'],
                    'related_db_id': example.get('db_id', ''),
                    'related_question': example.get('question', ''),
                    'related_Gold_sql': example.get('sql', ''),
                    'related_evidence': example.get('evidence', '')
                }
                processed_data.append(processed_item)
    return processed_data

def process_single_item(item, llm_config, results_map, history_path):
    """处理单个数据项的函数"""
    question_id = item["question_id"]
    related_db_id = item["related_db_id"]
    related_question = item["related_question"]
    related_Gold_sql = item["related_Gold_sql"]
    related_evidence = item["related_evidence"]
    
    print(f"\n处理问题: (ID: {question_id})")
    
    db_connector = DatabaseConnector(related_db_id)
    reasoning_tree = ReasoningTree(related_question)
    agent_system = AgentSystem(llm_config, db_connector)
    
    # 获取或创建结果条目
    current_result_entry = results_map.get((question_id, related_question), {
        "question_id": question_id,
        "related_db_id": related_db_id,
        "related_question": related_question,
        "is_solved": False,
        "related_python_code": None,
        "related_python_result": None,
        "related_Gold_sql": related_Gold_sql,
        "related_evidence": related_evidence
    })
    
    # 加载历史结果
    related_py = None
    related_sql = None
    if os.path.exists(history_path):
        with open(history_path, 'r', encoding='utf-8') as f:
            history_data = json.load(f)
            for record in history_data:
                if record['question_id'] == question_id and record['related_question'] == related_question:
                    related_py = record.get('related_python_code')
                    related_sql = record.get('related_Gold_sql')
                    break
    
    # 处理问题
    is_solved = agent_system._solve_generate_function(
        node=reasoning_tree.root,
        tree=reasoning_tree,
        db_name=related_db_id,
        Gold_sql=related_Gold_sql,
        additional_context=related_evidence,
        related_python_code=related_py,
        related_sql=related_sql
    )
    
    # 更新结果
    result = {
        **current_result_entry,
        "is_solved": is_solved,
        "related_python_code": reasoning_tree.root.actions,
        "related_python_result": reasoning_tree.root.cell_value,
        "error": None
    }
    
    if hasattr(reasoning_tree.root, 'unfixable_reason'):
        result["unfixable_reason"] = reasoning_tree.root.unfixable_reason
    
    db_connector.disconnect()
    return result

def main(run_full_dataset=True, rerun_unsolved=False):
    # 加载配置文件
    config_path = Path(__file__).parent / "config" / "config.yaml"
    config = load_config(config_path)
    
    # 设置输出文件夹
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/generate_function_6_3")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 加载数据集
    dataset_path = "/home/shenshuyu/SQL_tool/data/example/sub_few_shot.json"
    raw_dataset = load_dataset(dataset_path)
    
    # 提取每个问题的所有示例
    dataset = extract_all_examples(raw_dataset)

    # Load existing results if rerunning unsolved
    existing_results_path = output_dir / "test_results_generate_function_full.json"
    if rerun_unsolved and existing_results_path.exists():
        with open(existing_results_path, 'r', encoding='utf-8') as f:
            all_results = json.load(f)
        
        # Create a dictionary for quick lookup by question_id and related_question
        results_map = {(r['question_id'], r['related_question']): r for r in all_results}
        
        # Filter for unsolved items from the original dataset, using the existing results
        unsolved_dataset = []
        for item in dataset:
            key = (item['question_id'], item['related_question'])
            if key in results_map and not results_map[key]['is_solved']:
                unsolved_dataset.append(item)
        
        dataset_to_process = unsolved_dataset
        print(f"重新运行 {len(unsolved_dataset)} 个未解决的样本。")
        if not unsolved_dataset:
            print("没有找到未解决的样本，程序结束。")
            return
    else:
        dataset_to_process = dataset
        # 如果只运行前5个样本
        if not run_full_dataset:
            dataset_to_process = dataset_to_process[:5]
            print("仅运行前5个数据集样本")
        else:
            print(f"运行全部数据集 ({len(dataset_to_process)} 个样本)")
        
        # Initialize results for a fresh run
        all_results = []
        # Populate initial results with placeholders if running full dataset for the first time
        if not rerun_unsolved:
            for item in dataset_to_process:
                all_results.append({
                    "question_id": item['question_id'],
                    "related_db_id": item['related_db_id'],
                    "related_question": item['related_question'],
                    "is_solved": False, # Placeholder, will be updated
                    "related_python_code": None,
                    "related_python_result": None,
                    "related_Gold_sql": item['related_Gold_sql'],
                    "related_evidence": item['related_evidence'],
                    "error": "Not processed yet" # Initial state
                })
            results_map = {(r['question_id'], r['related_question']): r for r in all_results}


    # 设置LLM配置
    llm_config = setup_llm_config(config)
    
    # 加载历史结果文件 - 使用6_2目录的结果作为参考
    history_path = "/home/shenshuyu/SQL_tool/Output/generate_function_6_2/test_results_generate_function_full.json"
    
    # 创建线程安全的结果映射
    results_lock = Lock()
    
    # 设置线程池大小
    max_workers = 1  # 可以根据需要调整线程数
    
    def update_results(future):
        """处理完成的任务回调函数"""
        result = future.result()
        with results_lock:
            key = (result['question_id'], result['related_question'])
            results_map[key] = result
            
            # 保存中间结果
            with open(output_dir / "test_results_generate_function_ongoing.json", "w", encoding="utf-8") as f:
                json.dump(list(results_map.values()), f, ensure_ascii=False, indent=4)
    
    # 使用线程池执行任务
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for item in dataset_to_process:
            future = executor.submit(
                process_single_item,
                item,
                llm_config,
                results_map,
                history_path
            )
            future.add_done_callback(update_results)
            futures.append(future)
        
        # 等待所有任务完成
        concurrent.futures.wait(futures)
    
    # 保存最终结果
    all_results = list(results_map.values())
    with open(output_dir / "test_results_generate_function_full.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=4)
    
    # 打印统计信息
    solved_count = sum(1 for r in all_results if r["is_solved"])
    total_count = len(all_results)
    print(f"\n测试完成! 成功解决: {solved_count}/{total_count} ({solved_count/total_count*100:.2f}%)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='测试生成函数工作流')
    parser.add_argument('--sample', action='store_true', help='仅运行前5个样本')
    parser.add_argument('--rerun-unsolved', action='store_true',default=True, help='重新运行 test_results_generate_function_full.json 中 is_solved 为 false 的条目')
    args = parser.parse_args()
    
    if args.rerun_unsolved:
        main(run_full_dataset=False, rerun_unsolved=True) # run_full_dataset doesn't apply when rerunning unsolved
    else:
        main(run_full_dataset=not args.sample, rerun_unsolved=False)