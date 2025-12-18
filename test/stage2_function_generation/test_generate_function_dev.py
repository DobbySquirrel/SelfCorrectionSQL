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

def extract_all_examples(data):
    """从每个问题的examples列表中提取所有示例"""
    processed_data = []
    for item in data:
        if item.get('examples') and len(item['examples']) > 0:
            for example in item['examples']:
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

def main(run_full_dataset=True, start_index=0):
    # 加载配置文件
    config_path = Path(__file__).parent / "config" / "config.yaml"
    config = load_config(config_path)
    
    # 设置输出文件夹
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/generate_function_6_5_dev")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 加载数据集
    dataset_path = "/home/shenshuyu/SQL_tool/data/dev.json"
    raw_dataset = load_dataset(dataset_path)
    
    # 提取每个问题的所有示例
    dataset = raw_dataset  # 直接使用数据集，因为新格式不需要提取示例
    
    # 如果只运行前5个样本
    if not run_full_dataset:
        dataset = dataset[:5]
        print("仅运行前5个数据集样本")
    else:
        # 从指定位置开始运行
        dataset = dataset[start_index:]
        print(f"从第 {start_index} 个样本开始运行，共 {len(dataset)} 个样本")
    
    # 设置LLM配置
    llm_config = setup_llm_config(config)
    
    # 创建结果存储列表
    results = []
    
    # 遍历数据集中的所有问题
    for i, item in enumerate(tqdm(dataset, desc="处理问题")):
        question_id = item["question_id"]
        db_id = item["db_id"]
        question = item["question"]
        evidence = item.get("evidence", "")
        gold_sql = item.get("SQL", "")
        
        print(f"\n处理问题 {i+1}/{len(dataset)}:  (ID: {question_id})")

        db_connector = DatabaseConnector(db_id)
        
        # 创建推理树
        reasoning_tree = ReasoningTree(question)
        
        # 创建Agent系统
        agent_system = AgentSystem(llm_config, db_connector)
        
        try:
            # 加载历史结果文件
            history_path = "/home/shenshuyu/SQL_tool/Output/generate_function_test/test_results_generate_function_full.json"
            related_py = None
            related_sql = None
            
            if os.path.exists(history_path):
                with open(history_path, 'r', encoding='utf-8') as f:
                    history_data = json.load(f)
                    # 查找匹配的历史记录
                    for record in history_data:
                        if record['question_id'] == question_id:
                            related_py = record.get('related_python_code')
                            related_sql = record.get('related_Gold_sql')
                            break
            
            # 第一轮尝试
            is_solved, python_code, execution_result, evaluator_feedback = agent_system._solve_generate_function(
                node=reasoning_tree.root,
                tree=reasoning_tree,
                db_name=db_id,
                Gold_sql=gold_sql,
                additional_context=evidence,
                related_python_code=related_py,
                related_sql=related_sql
            )
            
            # 如果第一轮失败，进行第二轮尝试
            if not is_solved:
                print("第一轮生成失败，开始第二轮尝试...")
                # 重置推理树
                reasoning_tree = ReasoningTree(question)
                is_solved, python_code, execution_result, evaluator_feedback = agent_system._solve_generate_function(
                    node=reasoning_tree.root,
                    tree=reasoning_tree,
                    db_name=db_id,
                    Gold_sql=gold_sql,
                    additional_context=evidence,
                    related_python_code=related_py,
                    related_sql=related_sql
                )
            
            # 获取最终结果
            cell_value = execution_result
            # 执行 Gold_sql 并获取结果
            gold_sql_result = []
            gold_sql_error = None
            if gold_sql:
                try:
                    query_result, error_message = db_connector.execute_query(gold_sql)
                    if query_result is not None:
                        gold_sql_result = query_result.to_dict(orient='records')
                    else:
                        gold_sql_error = error_message
                except Exception as e:
                    gold_sql_error = str(e)
            
            # 存储结果
            result = {
                "question_id": question_id,
                "db_id": db_id,
                "question": question,
                "evidence": evidence,
                "is_solved": is_solved,
                "related_python_code": python_code,
                "related_python_result": cell_value,
                "evaluator_feedback": evaluator_feedback,
                "Gold_sql": gold_sql,
                "Gold_sql_result": gold_sql_result
            }
            
            # 如果有无法修复的原因，添加到结果中
            if hasattr(reasoning_tree.root, 'unfixable_reason'):
                result["unfixable_reason"] = reasoning_tree.root.unfixable_reason
            results.append(result)
            
            print(f"问题已解决: {is_solved}")
            print(f"Python代码: {python_code}")
            print(f"执行结果: {cell_value}")
            print(f"评估反馈: {evaluator_feedback}")
            
            # 每处理5个问题保存一次中间结果
            if (i + 1) % 5 == 0:
                with open(output_dir / "test_results_generate_function_ongoing.json", "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=4)
                print(f"已保存中间结果 (已完成 {i+1}/{len(dataset)} 个问题)")
            
        except Exception as e:
            print(f"处理问题时出错: {e}")
            # 如果有无法修复的原因，添加到结果中
            result = {
                "question_id": question_id,
                "db_id": db_id,
                "question": question,
                "evidence": evidence,
                "is_solved": False,
                "error": str(e),
                "related_python_code": None,
                "related_python_result": None,
                "evaluator_feedback": None,
                "Gold_sql": gold_sql,
                "Gold_sql_result": gold_sql_result,
            }
            if hasattr(reasoning_tree.root, 'unfixable_reason'):
                result["unfixable_reason"] = reasoning_tree.root.unfixable_reason
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
    parser.add_argument('--start', type=int, default=0, help='从第几个样本开始运行（从0开始计数）')
    args = parser.parse_args()
    
    main(run_full_dataset=not args.sample, start_index=args.start)
