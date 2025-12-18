import json
import os
import sys
from pathlib import Path
from tqdm import tqdm
import argparse

# 添加项目根目录到系统路径
sys.path.append(str(Path(__file__).parent.parent))

from agents.autogen_agents import AgentSystem
from core.database_connector import DatabaseConnector
from core.reasoning_tree import ReasoningTree
from test_agents import load_dataset, load_config, setup_llm_config

def main(run_full_dataset=True):
    # 加载配置文件
    config_path = Path(__file__).parent / "config" / "config.yaml"
    config = load_config(config_path)
    
    # 设置输出文件夹
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/a_star")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 加载数据集
    dataset_path = "/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json"
    dataset = load_dataset(dataset_path)
    
    # 加载原始数据集以获取正确的question_id
    original_dataset_path = "/home/shenshuyu/SQL_tool/data/sub_sampled_bird_dev_set.json"
    original_dataset = load_dataset(original_dataset_path)
    
    # 如果只运行前5个样本
    if not run_full_dataset:
        dataset = dataset[:5]
        original_dataset = original_dataset[:5]
        print("仅运行前5个数据集样本")
    else:
        print(f"运行全部数据集 ({len(dataset)} 个样本)")
    
    # 加载预先生成的SQL结果
    sql_pandas_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_pandas.json"
    sql_salchemy_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_sqlalchemy.json"
    
    sql_pandas = {}
    sql_salchemy = {}
    try:
        with open(sql_pandas_path, 'r', encoding='utf-8') as f:
            sql_pandas = json.load(f)
    except Exception as e:
        print(f"加载SQL Pandas文件时出错: {e}")
        
    try:
        with open(sql_salchemy_path, 'r', encoding='utf-8') as f:
            sql_salchemy = json.load(f)
    except Exception as e:
        print(f"加载SQL SQLAlchemy文件时出错: {e}")
    
    # 确保两个数据集长度相同
    assert len(dataset) == len(original_dataset), "数据集长度不匹配"
    
    # 设置LLM配置
    llm_config = setup_llm_config(config)
    
    # 创建结果存储列表
    results = []
    
    # 遍历数据集中的所有问题
    for i, (item, original_item) in enumerate(tqdm(zip(dataset, original_dataset), desc="处理问题", total=len(dataset))):
        db_name = item["db"]
        question = item["question"]
        schema_info = item["simplified_ddl"]
        foreign_key = item["foreign_key"]
        evidence = item["evidence"]
        index = original_item["question_id"]
        tables_schema_first_three = item["ddl_data"]
        example_data = item["example"]
        
        print(f"\n处理问题 {i+1}/{len(dataset)}: {question} (ID: {index})")

        db_connector = DatabaseConnector(db_name)
        reasoning_tree = ReasoningTree(question)
        agent_system = AgentSystem(llm_config, db_connector)
        
        try:
            # 使用A*工作流
            agent_system._solve_a_star(
                reasoning_tree.root, 
                reasoning_tree,
                sql_pandas=sql_pandas.get(str(index), ""),
                sql_salchemy=sql_salchemy.get(str(index), ""),
                db_name=db_name,
                tables_schema_first_three=tables_schema_first_three,
                schema_info="db_name:"+db_name+"\n"+schema_info+"\nforeign_key:"+foreign_key,
                additional_context=evidence,
                example_data=example_data
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
                if query_result is not None:
                    sql_result = query_result.to_dict(orient='records')
                else:
                    sql_error = error_message
            
            # 存储结果
            results.append({
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
            
            # 每处理5个问题保存一次中间结果
            if (i + 1) % 5 == 0:
                with open(output_dir / "test_results_a_star_ongoing.json", "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=4)
                print(f"已保存中间结果 (已完成 {i+1}/{len(dataset)} 个问题)")
            
        except Exception as e:
            print(f"处理问题时出错: {e}")
            results.append({
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
            
            with open(output_dir / "test_results_a_star_ongoing.json", "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=4)
    
    # 保存最终结果
    with open(output_dir / "test_results_a_star_full.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    
    # 关闭数据库连接
    db_connector.disconnect()
    
    # 打印统计信息
    solved_count = sum(1 for r in results if r["is_solved"])
    total_count = len(results)
    print(f"\n测试完成! 成功解决: {solved_count}/{total_count} ({solved_count/total_count*100:.2f}%)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='使用A*工作流测试代理系统')
    parser.add_argument('--sample', action='store_true', help='仅运行前5个样本')
    args = parser.parse_args()
    
    main(run_full_dataset=not args.sample) 