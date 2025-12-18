import json
import os
import sys
import yaml
from pathlib import Path
from tqdm import tqdm
import pandas as pd
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

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

def get_examples_from_dataset(question_id, dataset_path):
    """从数据集中获取对应question_id的few-shot示例"""
    with open(dataset_path, 'r', encoding='utf-8') as f:
        dataset = json.load(f)
    
    for item in dataset:
        if str(item["question_id"]) == str(question_id):
            return item.get("examples", [])
    return []

def format_examples(examples):
    """格式化few-shot示例，包含分析结果"""
    if not examples:
        return ""
    
    formatted = "\nFew-shot examples:\n"
    for ex in examples:
        formatted += f"""
Question: {ex['question']}
Evidence: {ex.get('evidence', '')}
SQL: {ex['sql']}
Analysis: {ex.get('analysis', '未提供分析')}
"""
    return formatted

def load_related_python_code(file_path):
    """加载相关的Python代码示例"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 按question_id组织数据
        organized_data = {}
        for item in data:
            qid = item['question_id']
            if qid not in organized_data:
                organized_data[qid] = []
            organized_data[qid].append({
                'question': item['related_question'],
                'evidence': item.get('related_evidence', ''),
                'python_code': item.get('related_python_code', '')
            })
        return organized_data
    except Exception as e:
        print(f"加载相关Python代码文件时出错: {e}")
        return {}

def process_single_question(args):
    """处理单个问题的函数"""
    try:
        # 解析参数
        i, item, original_item, config, output_dir, sql_pandas, sql_salchemy, few_shot_dataset_path, related_code_data = args
        
        # 提取基本信息
        db_name = item["db"]
        question = item["question"]
        schema_info = item["simplified_ddl"]
        foreign_key = item["foreign_key"]
        evidence = item["evidence"]
        index = original_item["question_id"]
        tables_schema_first_three = item["ddl_data"]
        example_data = item["example"]
        df_list = item["df_list"]
        
        # 改进示例处理
        related_examples = related_code_data.get(index, []) or []
        example_parts = []
        for idx, ex in enumerate(related_examples):
            example_parts.append(f"""
    相关问题: {ex['question']}
    证据: {ex.get('evidence', '')}
    Python代码:
    {ex.get('python_code', '')}
    ---
    """)
        combined_examples = "".join(example_parts)
        
        print(f"\n处理问题 {i+1}: {question} (ID: {index})")
        
        # 数据库处理
        db_path = db_name
        db_connector = None
        try:
            db_connector = DatabaseConnector(db_path)
            reasoning_tree = ReasoningTree(question)
            
            # 设置LLM配置
            llm_config = setup_llm_config(config)
            agent_system = AgentSystem(llm_config, db_connector)
            
            examples = get_examples_from_dataset(index, few_shot_dataset_path)
            analysis_based_on_few_shot_logic = format_examples(examples)
            
            # 执行主要处理逻辑
            agent_system._solve_straightforward(
                reasoning_tree.root,
                reasoning_tree,
                sql_pandas=sql_pandas.get(str(index), {}),
                sql_salchemy=sql_salchemy.get(str(index), {}),
                db_name=db_name,
                tables_schema_first_three=tables_schema_first_three,
                schema_info="db_name:"+db_name+"\n"+schema_info+"\nforeign_key:"+foreign_key,
                additional_context=evidence,
                example_data=example_data,
                related_python_code=combined_examples,
                analysis_based_on_few_shot_logic=analysis_based_on_few_shot_logic,
                df_list=df_list,
                id=index
            )
            
            # 获取结果
            is_solved = reasoning_tree.root.is_solved
            cell_value = reasoning_tree.root.cell_value
            evidences = reasoning_tree.root.evidences
            action = reasoning_tree.root.actions
            
            # SQL执行和结果处理
            sql_result = []
            sql_error = None
            if evidences and len(evidences) > 0:
                try:
                    formatted_sql = " ".join(evidences[0].strip().split())
                    query_result, error_message = db_connector.execute_query(formatted_sql)
                    
                    if query_result is not None:
                        sql_result = query_result.to_dict(orient='records')
                    else:
                        sql_error = error_message
                except Exception as e:
                    sql_error = f"SQL执行错误: {str(e)}"
            
            # 构建返回结果
            result = {
                "question_id": index,
                "db": db_name,
                "question": question,
                "is_solved": is_solved,
                "python_code": action,
                "python_result": cell_value,
                "sql": evidences[0] if evidences and len(evidences) > 0 else None,
                "sql_result": sql_result,
                "sql_error": sql_error
            }
            
            return result
            
        except Exception as e:
            return {
                "question_id": index,
                "db": db_name,
                "question": question,
                "is_solved": False,
                "error": f"处理过程错误: {str(e)}",
                "python_code": None,
                "python_result": None,
                "sql": None,
                "sql_result": None,
                "sql_error": str(e)
            }
        finally:
            if db_connector:
                db_connector.disconnect()
                
    except Exception as e:
        # 处理最外层异常
        return {
            "question_id": index if 'index' in locals() else None,
            "db": db_name if 'db_name' in locals() else None,
            "question": question if 'question' in locals() else None,
            "is_solved": False,
            "error": f"初始化错误: {str(e)}",
            "python_code": None,
            "python_result": None,
            "sql": None,
            "sql_result": None,
            "sql_error": str(e)
        }
        
def retry_failed_questions(results_path, dataset, original_dataset, config, output_dir, sql_pandas, sql_salchemy, few_shot_dataset_path, related_code_data):
    """重新处理失败的问题"""
    print("开始重试失败的问题...")
    
    # 加载原始结果文件
    try:
        with open(results_path, 'r', encoding='utf-8') as f:
            results = json.load(f)
    except Exception as e:
        print(f"加载结果文件时出错: {e}")
        return
    
    # 找出需要重试的问题ID
    failed_ids = set()
    processed_ids = set()
    
    for r in results:
        qid = r.get("question_id")
        if qid is not None:
            processed_ids.add(qid)
            if not r.get("is_solved", False):
                failed_ids.add(qid)
    
    # 添加未处理的问题ID
    all_ids = {item["question_id"] for item in original_dataset}
    unprocessed_ids = all_ids - processed_ids
    failed_ids.update(unprocessed_ids)
    
    if not failed_ids:
        print("没有需要重试的问题")
        return results
    
    print(f"找到 {len(failed_ids)} 个需要重试的问题")
    print(f"其中未处理的问题: {len(unprocessed_ids)}个")
    print(f"处理失败的问题: {len(failed_ids - unprocessed_ids)}个")
    
    # 创建question_id到数据集索引的映射
    id_to_index = {item["question_id"]: i for i, item in enumerate(original_dataset)}
    
    # 准备需要重试的问题
    retry_tasks = []
    for qid in failed_ids:
        if qid in id_to_index:
            idx = id_to_index[qid]
            retry_tasks.append((
                idx,
                dataset[idx],
                original_dataset[idx],
                config,
                output_dir,
                sql_pandas,
                sql_salchemy,
                few_shot_dataset_path,
                related_code_data
            ))
    
    # 使用线程池重新处理失败的问题
    retry_results = []
    save_lock = threading.Lock()
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_task = {executor.submit(process_single_question, task): task for task in retry_tasks}
        
        for future in tqdm(as_completed(future_to_task), total=len(retry_tasks), desc="重试问题"):
            result = future.result()
            
            # 打印详细信息
            print(f"\n处理问题ID: {result['question_id']}")
            print(f"问题: {result['question']}")
            print(f"是否解决: {result['is_solved']}")
            if result.get('sql'):
                print(f"SQL: {result['sql']}")
            if result.get('sql_result'):
                print(f"结果: {result['sql_result'][:200]}..." if len(str(result['sql_result'])) > 200 else f"结果: {result['sql_result']}")
            if result.get('error'):
                print(f"错误: {result['error']}")
            if result.get('sql_error'):
                print(f"SQL错误: {result['sql_error']}")
            
            with save_lock:
                retry_results.append(result)
                
                # 每处理5个问题保存一次中间结果
                if len(retry_results) % 1 == 0:
                    with open(output_dir / "retry_results_ongoing.json", "w", encoding="utf-8") as f:
                        json.dump(retry_results, f, ensure_ascii=False, indent=4)
                    print(f"\n已保存中间结果 (已完成 {len(retry_results)}/{len(retry_tasks)} 个问题)")
    
    # 更新原始结果
    retry_dict = {r["question_id"]: r for r in retry_results}
    updated_results = []
    
    # 更新现有结果并添加新结果
    for r in results:
        qid = r.get("question_id")
        if qid in retry_dict:
            updated_results.append(retry_dict.pop(qid))
        else:
            updated_results.append(r)
    
    # 添加之前未处理的结果
    for qid, result in retry_dict.items():
        updated_results.append(result)
    
    # 保存更新后的完整结果
    with open(output_dir / "updated_full_results.json", "w", encoding="utf-8") as f:
        json.dump(updated_results, f, ensure_ascii=False, indent=4)
    
    # 打印统计信息
    retry_solved_count = sum(1 for r in retry_results if r["is_solved"])
    print(f"\n重试完成! 成功解决: {retry_solved_count}/{len(retry_results)} ({retry_solved_count/len(retry_results)*100:.2f}%)")
    
    return updated_results

def main(run_full_dataset=True, specific_ids=None, retry_mode=False, results_path=None):
    # 加载配置文件
    config_path = Path(__file__).parent / "config" / "config.yaml"
    config = load_config(config_path)
    
    # 设置输出文件夹
    output_dir = Path("/home/shenshuyu/SQL_tool/Output/6_8_4")
    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)
    
    
    # 加载数据集
    dataset_path = "/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json"
    dataset = load_dataset(dataset_path)
    
    # 加载原始数据集以获取正确的question_id
    original_dataset_path = "/home/shenshuyu/SQL_tool/data/sub_sampled_bird_dev_set.json"
    original_dataset = load_dataset(original_dataset_path)
    # specific_ids="286"
    # 如果指定了特定的问题ID
    if specific_ids:
        filtered_dataset = []
        filtered_original_dataset = []
        for d, o in zip(dataset, original_dataset):
            if str(o["question_id"]) in specific_ids:
                filtered_dataset.append(d)
                filtered_original_dataset.append(o)
        dataset = filtered_dataset
        original_dataset = filtered_original_dataset
        print(f"运行指定的问题ID: {specific_ids}")
    # 如果只运行前5个样本
    elif not run_full_dataset:
        dataset = dataset[:5]
        original_dataset = original_dataset[:5]
        print("仅运行前5个数据集样本")
    else:
        print(f"运行全部数据集 ({len(dataset)} 个样本)")
    
    # 加载预先生成的SQL结果
    sql_pandas_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_pandas.json"
    sql_salchemy_path = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/preliminary_sql1_thinking_sqlalchemy.json"
    few_shot_dataset_path="/home/shenshuyu/SQL_tool/Output/6_2/analyzed_few_shot.json"
    related_python_code_path="/home/shenshuyu/SQL_tool/Output/generate_function_6_4/merged_results.json"
    
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
    
    # 加载相关的Python代码
    related_code_data = load_related_python_code(related_python_code_path)
    
    if retry_mode and results_path:
        # 重试模式
        return retry_failed_questions(
            results_path,
            dataset,
            original_dataset,
            config,
            output_dir,
            sql_pandas,
            sql_salchemy,
            few_shot_dataset_path,
            related_code_data
        )
    
    # 确保两个数据集长度相同
    assert len(dataset) == len(original_dataset), "数据集长度不匹配"
    
    # 创建线程锁用于保存结果
    save_lock = threading.Lock()
    results = []
    
    # 设置线程池
    max_workers = 5  # 可以根据需要调整线程数
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 准备任务参数，加入related_code_data
        tasks = [
            (i, item, original_item, config, output_dir, sql_pandas, sql_salchemy, few_shot_dataset_path, related_code_data)
            for i, (item, original_item) in enumerate(zip(dataset, original_dataset))
        ]
        
        # 提交所有任务
        future_to_task = {executor.submit(process_single_question, task): task for task in tasks}
        
        # 使用tqdm显示进度，同时显示详细信息
        for future in tqdm(as_completed(future_to_task), total=len(tasks), desc="处理问题"):
            result = future.result()
            
            # 打印详细信息
            print(f"\n处理问题ID: {result['question_id']}")
            print(f"问题: {result['question']}")
            print(f"是否解决: {result['is_solved']}")
            if result.get('sql'):
                print(f"SQL: {result['sql']}")
            if result.get('sql_result'):
                print(f"结果: {result['sql_result'][:200]}..." if len(str(result['sql_result'])) > 200 else f"结果: {result['sql_result']}")
            if result.get('error'):
                print(f"错误: {result['error']}")
            if result.get('sql_error'):
                print(f"SQL错误: {result['sql_error']}")
            
            # 使用线程锁保护结果的添加和保存
            with save_lock:
                results.append(result)
                
                # 每处理5个问题保存一次中间结果
                if len(results) % 1 == 0:
                    with open(output_dir / "test_results_straightforward_ongoing.json", "w", encoding="utf-8") as f:
                        json.dump(results, f, ensure_ascii=False, indent=4)
                    print(f"\n已保存中间结果 (已完成 {len(results)}/{len(dataset)} 个问题)")
    
    # 保存最终结果
    with open(output_dir / "test_results_straightforward_full.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    
    # 打印统计信息
    solved_count = sum(1 for r in results if r["is_solved"])
    total_count = len(results)
    print(f"\n测试完成! 成功解决: {solved_count}/{total_count} ({solved_count/total_count*100:.2f}%)")
    
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='测试代理系统')
    parser.add_argument('--sample', action='store_true', help='仅运行前5个样本')
    parser.add_argument('--specific_ids', nargs='+',help='指定要运行的问题ID列表')
    parser.add_argument('--retry', action='store_true', help='是否重试失败的问题')
    parser.add_argument('--results_path', type=str, help='需要重试的结果文件路径')
    args = parser.parse_args()
    
    main(
        run_full_dataset=not args.sample,
        specific_ids=args.specific_ids,
        retry_mode=args.retry,
        results_path=args.results_path
    ) 

