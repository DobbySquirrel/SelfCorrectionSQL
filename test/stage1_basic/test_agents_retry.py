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

def main():
    # 加载配置文件
    config_path = Path(__file__).parent / "config" / "config.yaml"
    config = load_config(config_path)
    
    # 设置输出文件夹
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/5_25")
    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 加载原始结果文件，找出失败的样例
    results_path = "/home/shenshuyu/SQL_tool/Output/5_25/updated_full_results.json"
    results = load_dataset(results_path)
         
    failed_ids = [r["question_id"] for r in results if not r["is_solved"]]
    print(f"找到 {len(failed_ids)} 个失败的样例需要重新运行")
    
    if not failed_ids:
        print("没有失败的样例需要重新运行")
        return
    
    # 加载原始数据集
    dataset_path = "/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json"
    dataset = load_dataset(dataset_path)
    
    # 加载原始数据集以获取正确的question_id
    original_dataset_path = "/home/shenshuyu/SQL_tool/data/sub_sampled_bird_dev_set.json"
    original_dataset = load_dataset(original_dataset_path)
    
    # 创建question_id到数据集索引的映射
    id_to_index = {}
    for i, item in enumerate(original_dataset):
        id_to_index[item["question_id"]] = i
    
    # 加载预先生成的SQL结果
    sql_pandas_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_pandas.json"
    sql_salchemy_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_sqlalchemy.json"
    
    try:
        with open(sql_pandas_path, 'r', encoding='utf-8') as f:
            sql_pandas = json.load(f)
    except Exception as e:
        print(f"加载SQL Pandas文件时出错: {e}")
        sql_pandas = {}
        
    try:
        with open(sql_salchemy_path, 'r', encoding='utf-8') as f:
            sql_salchemy = json.load(f)
    except Exception as e:
        print(f"加载SQL SQLAlchemy文件时出错: {e}")
        sql_salchemy = {}
    
    # 设置LLM配置
    llm_config = setup_llm_config(config)
    
    # 创建结果存储列表
    retry_results = []
    
    # 遍历失败的样例
    for i, question_id in enumerate(tqdm(failed_ids, desc="重新处理失败的问题")):
        if question_id not in id_to_index:
            print(f"警告: 找不到question_id为 {question_id} 的样例")
            continue
            
        idx = id_to_index[question_id]
        if idx >= len(dataset):
            print(f"警告: 索引 {idx} 超出数据集范围")
            continue
            
        item = dataset[idx]
        original_item = original_dataset[idx]
        
        db_name = item["db"]
        question = item["question"]
        schema_info = item["simplified_ddl"]
        foreign_key = item["foreign_key"]
        evidence = item["evidence"]
        index = original_item["question_id"]
        tables_schema_first_three = item["ddl_data"]
        example_data = item["example"]
        related_python_code = item["related_python_code"]
        
        print(f"\n重新处理问题 {i+1}/{len(failed_ids)}: {question} (ID: {index})")

        db_path = db_name
            
        db_connector = DatabaseConnector(db_path)
        
        # 创建推理树
        reasoning_tree = ReasoningTree(question)
        
        # 创建Agent系统
        agent_system = AgentSystem(llm_config, db_connector)
        
        try:
            # 调用_solve_straightforward方法
            agent_system._solve_straightforward(
                reasoning_tree.root, 
                reasoning_tree,
                sql_pandas=sql_pandas.get(str(index), ""),
                sql_salchemy=sql_salchemy.get(str(index), ""),
                db_name=db_name,
                tables_schema_first_three=tables_schema_first_three,
                schema_info="db_name:"+db_name+"\n"+schema_info+"\nforeign_key:"+foreign_key,
                additional_context=evidence,
                example_data=example_data+related_python_code,
                related_python_code=related_python_code
            )
            
            # 获取结果
            is_solved = reasoning_tree.root.is_solved
            cell_value = reasoning_tree.root.cell_value
            evidences = reasoning_tree.root.evidences
            action = reasoning_tree.root.actions
            
            # 执行SQL查询并获取结果
            sql_result = []
            sql_error = None
            if evidences:
                formatted_sql = " ".join(evidences[0].strip().split())
                query_result, error_message = db_connector.execute_query(formatted_sql)
                
                # 将DataFrame转换为字典列表
                if query_result is not None:
                    sql_result = query_result.to_dict(orient='records')
                else:
                    sql_error = error_message
            
            # 存储结果
            retry_results.append({
                "question_id": index,
                "db": db_name,
                "question": question,
                "is_solved": is_solved,
                "python_code": action,
                "python_result": cell_value,
                "sql": evidences[0] if evidences else None,
                "sql_result": sql_result,
                "sql_error": sql_error
            })
            
            print(f"问题已解决: {is_solved}")
            print(f"结果: {sql_result[:200]}..." if len(sql_result) > 200 else f"结果: {sql_result}")
            print(f"SQL: {evidences[0] if evidences else None}")
            
            # 每处理5个问题保存一次中间结果
            if (i + 1) % 5 == 0 or (i + 1) == len(failed_ids):
                # 将中间结果保存为json文件
                with open(output_dir / "retry_results_ongoing.json", "w", encoding="utf-8") as f:
                    json.dump(retry_results, f, ensure_ascii=False, indent=4)
                print(f"已保存中间结果到 {output_dir}/retry_results_ongoing.json (已完成 {i+1}/{len(failed_ids)} 个问题)")
            
        except Exception as e:
            print(f"处理问题时出错: {e}")
            retry_results.append({
                "question_id": index,
                "db": db_name,
                "question": question,
                "is_solved": False,
                "python_code": None,
                "python_result": None,
                "error": str(e),
                "sql": None,
                "sql_result": [],
                "sql_error": None
            })
            
            # 每次发生错误时也保存中间结果
            with open(output_dir / "retry_results_ongoing.json", "w", encoding="utf-8") as f:
                json.dump(retry_results, f, ensure_ascii=False, indent=4)
            print(f"处理问题 {i+1} 时出错，已保存中间结果")
    
    # 将最终结果保存为json文件
    with open(output_dir / "retry_results_final.json", "w", encoding="utf-8") as f:
        json.dump(retry_results, f, ensure_ascii=False, indent=4)
    
    # 关闭数据库连接
    db_connector.disconnect()
    
    # 打印统计信息
    solved_count = sum(1 for r in retry_results if r["is_solved"])
    total_count = len(retry_results)
    print(f"\n重试完成! 成功解决: {solved_count}/{total_count} ({solved_count/total_count*100:.2f}%)")

    # 更新原始结果文件
    updated_results = results.copy()
    retry_dict = {r["question_id"]: r for r in retry_results}
    
    for i, result in enumerate(updated_results):
        if result["question_id"] in retry_dict:
            updated_results[i] = retry_dict[result["question_id"]]
    
    # 保存更新后的完整结果
    with open(output_dir / "updated_full_results.json", "w", encoding="utf-8") as f:
        json.dump(updated_results, f, ensure_ascii=False, indent=4)
    print(f"已保存更新后的完整结果到 {output_dir}/updated_full_results.json")

if __name__ == "__main__":
    main()