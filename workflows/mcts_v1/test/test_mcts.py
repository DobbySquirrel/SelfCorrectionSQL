"""
MCTS 测试脚本（单样本/多样本测试）

使用标准的 MCTS 算法运行测试，支持：
- 单个样本测试（--qid）
- 多个样本测试（--qids）
- 全量测试（不指定 --qid/--qids）

从 --ppl_file 读取样本（与 test_mcts_workflow 的样本格式一致）。
"""

import json
import sys
import argparse
import time
import threading
import os
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
from tqdm import tqdm
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # If python-dotenv is not installed, skip

from workflows.mcts_v1.mcts_workflow import MCTSWorkflow
from workflows.mcts_v1.core.database_connector import DatabaseConnector
from workflows.mcts_v1.utils.model_utils import get_llm_config, pick_model
import logging
logging.getLogger("autogen.oai.client").setLevel(logging.ERROR)


def build_db_connector(db_name: str) -> DatabaseConnector:
    """构建数据库连接器（使用环境变量或相对路径，不再硬编码绝对路径）"""
    # 直接传入数据库名称，DatabaseConnector 会自动从环境变量或相对路径查找
    db_connector = DatabaseConnector(db_name)
    if not db_connector.connect():
        raise RuntimeError(f"数据库连接失败: {db_connector.db_path}")
    return db_connector


def run_once(sample: dict, parallel_workers: int = 5, multi_base_urls: List[str] = None, 
             mcts_config: dict = None, strategy_mode: Optional[str] = None) -> dict:
    db_name = sample["db"]
    question = sample["question"]
    schema_info = sample["simplified_ddl"]
    foreign_key = sample.get("foreign_key", "")
    # 只使用 evidence，不使用 combine_evidence（因为 combine_evidence 包含不需要的 "Evidence from other related questions" 部分）
    evidence_to_use = sample.get("evidence", "")

    db = build_db_connector(db_name)
    
    # 根据 multi_base_urls 构建 llm_config
    if multi_base_urls and len(multi_base_urls) > 0:
        # 使用多个端点：为每个端点创建一个配置项
        api_key = os.environ.get("VLLM_API_KEY", "dummy-key")
        config_list = []
        for base_url in multi_base_urls:
            # 为每个端点获取模型名称
            try:
                model = pick_model(base_url, api_key)
            except Exception:
                # 如果获取失败，使用环境变量或默认值
                model = os.environ.get("VLLM_MODEL", "unknown")
            config_list.append({
                "model": model,
                "api_key": api_key,
                "base_url": base_url
            })
        llm_config = {
            "config_list": config_list,
            "temperature": 0.2,  # 降低temperature以提高生成质量
        }
    else:
        # 使用单个端点（默认行为）
        llm_config = get_llm_config(temperature=0.2, auto_select=True)  # 降低temperature以提高生成质量

    # 使用parallel_workers参数设置MCTS内部的max_workers
    w = MCTSWorkflow(llm_config, db, max_workers=parallel_workers, strategy_mode=strategy_mode)
    
    # 应用MCTS配置（如果提供）
    if mcts_config:
        if 'rollouts_per_iteration' in mcts_config:
            w.rollouts_per_iteration = mcts_config['rollouts_per_iteration']
        if 'max_depth' in mcts_config:
            w.max_depth = mcts_config['max_depth']
            # 更新CTE生成器的max_depth
            w.cte_generator.max_depth = mcts_config['max_depth']
        if 'max_cte_nodes_per_iteration' in mcts_config:
            w.max_cte_nodes_per_iteration = mcts_config['max_cte_nodes_per_iteration']
        if 'num_sql_variants' in mcts_config:
            w.num_sql_variants = mcts_config['num_sql_variants']
        if 'exploration_constant' in mcts_config:
            w.exploration_constant = mcts_config['exploration_constant']
        if 'cold_start_strategy' in mcts_config:
            w.cold_start_strategy = mcts_config['cold_start_strategy']
    
    res = w.solve(
        question=question,
        schema_info=f"db_name:{db_name}\n{schema_info}\nforeign_key:{foreign_key}",
        additional_context=f"{evidence_to_use}"
    )

    optimal_sql = res.get("optimal_sql", "")
    stats = res.get("statistics", {})
    all_sqls_with_attributes = res.get("all_sqls_with_attributes", [])
    rollout_stats = res.get("rollout_stats", [])  # 每个rollout的详细统计信息
    db.disconnect()
    return {
        "sql": optimal_sql, 
        "stats": stats, 
        "all_sqls_with_attributes": all_sqls_with_attributes,
        "rollout_stats": rollout_stats
    }

def process_single_task(args_tuple):
    """处理单个任务的包装函数，用于并行执行"""
    idx, sample, parallel_workers, gold_sqls, ppls, multi_base_urls, mcts_config, strategy_mode = args_tuple
    try:
        qid = str(sample.get('question_id', idx))
        print(f"\n{'='*80}")
        print(f">>> 样本#{idx} (question_id={qid}) | DB={sample['db']}")
        print(f"{'='*80}")

        result = run_once(sample, parallel_workers=parallel_workers, multi_base_urls=multi_base_urls, mcts_config=mcts_config, strategy_mode=strategy_mode)


        # 与gold SQL对比（如果提供）
        predicted_sql = result['sql']
        print(f"[Gold验证] 准备验证，选择的SQL: {predicted_sql[:300] if predicted_sql else 'None'}...")
        gold_match = None
        gold_sql = None
        if gold_sqls and qid in gold_sqls:
            gold_sql = gold_sqls[qid]
            print(f"[Gold验证] Gold SQL: {gold_sql[:300] if gold_sql else 'None'}...")
            db_connector = build_db_connector(sample['db'])
            try:
                gold_match = compare_with_gold(predicted_sql, gold_sql, db_connector=db_connector)
                if gold_match:
                    print(f"\n✅ [样本#{idx}] [Gold验证] question_id={qid}: 匹配成功！")
                else:
                    print(f"\n❌ [样本#{idx}] [Gold验证] question_id={qid}: 不匹配")
            finally:
                # 关闭数据库连接
                if db_connector:
                    db_connector.disconnect()
        elif gold_sqls:
            print(f"\n⚠️ [样本#{idx}] [Gold验证] question_id={qid}: 未找到对应的gold SQL")
        
        # 对所有SQL进行gold验证（用于相关性分析）
        all_sqls_with_gold = []
        if gold_sql and result.get('all_sqls_with_attributes'):
            db_connector = build_db_connector(sample['db'])
            try:
                for sql_info in result['all_sqls_with_attributes']:
                    sql = sql_info.get('sql', '')
                    if sql:
                        try:
                            sql_match = compare_with_gold(sql, gold_sql, db_connector=db_connector)
                            sql_info['is_correct'] = sql_match
                        except Exception as e:
                            # 如果比较失败，标记为False（可能是语法错误或执行错误）
                            print(f"[Gold验证] ⚠️ SQL比较失败，标记为False: {e}")
                            sql_info['is_correct'] = False
                    else:
                        sql_info['is_correct'] = False
                    all_sqls_with_gold.append(sql_info)
            finally:
                if db_connector:
                    db_connector.disconnect()
        else:
            all_sqls_with_gold = result.get('all_sqls_with_attributes', [])
            for sql_info in all_sqls_with_gold:
                sql_info['is_correct'] = None  # 没有gold SQL，无法判断
        
        # 构建返回结果
        stats_obj = {
            'average_reward': result['stats'].get('average_reward', 0),
            'total_visits': result['stats'].get('total_visits', 0)
        }
        if isinstance(result.get('stats'), dict) and 'timing' in result['stats']:
            stats_obj['timing'] = result['stats']['timing']
        
        if gold_sqls and qid in gold_sqls:
            stats_obj['gold_match'] = gold_match
            stats_obj['gold_sql'] = gold_sqls[qid]
        
        return {
            'idx': idx,
            'qid': qid,
            'sql': result['sql'],
            'stats': stats_obj,
            'gold_match': gold_match,
            'all_sqls_with_attributes': all_sqls_with_gold,  # 包含gold验证结果的所有SQL
            'rollout_stats': result.get('rollout_stats', []),  # 每个rollout的详细统计信息
        }
    except Exception as e:
        print(f"\n❌ [样本#{idx}] 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            'idx': idx,
            'qid': str(sample.get('question_id', idx)),
            'sql': '',
            'stats': {},
            'gold_match': None,
            'error': str(e),
        }


def load_sample(ppl_file: str, index: int) -> dict:
    with open(ppl_file, 'r', encoding='utf-8') as f:
        ppls = json.load(f)
    if index < 0 or index >= len(ppls):
        raise IndexError(f"index 超界: {index}/{len(ppls)}")
    required_fields = ["db", "question", "simplified_ddl", "foreign_key"]
    missing = [k for k in required_fields if k not in ppls[index]]
    if missing:
        raise KeyError(f"样本缺少字段: {missing}")
    return ppls[index]


def load_gold_sqls(gold_file: str) -> dict:
    """加载gold SQL文件，返回 {question_id: gold_sql} 的字典"""
    gold_sqls = {}
    try:
        with open(gold_file, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
        for item in gold_data:
            qid = item.get('question_id')
            sql = item.get('SQL', '')
            if qid is not None:
                gold_sqls[str(qid)] = sql
        print(f"[Gold] 从 {gold_file} 加载了 {len(gold_sqls)} 条gold SQL")
    except Exception as e:
        print(f"[警告] 加载gold文件失败: {e}")
    return gold_sqls



def compare_with_gold(predicted_sql: str, gold_sql: str, db_connector: DatabaseConnector = None) -> bool:
    """
    比较预测SQL和gold SQL的执行结果是否相同
    
    Args:
        predicted_sql: 预测的SQL
        gold_sql: 标准答案SQL
        db_connector: 数据库连接器（如果提供，则执行SQL比较结果；否则回退到字符串比较）
    
    Returns:
        bool: 如果结果匹配则为True，否则为False
    """
    # 如果提供了数据库连接器，执行SQL并比较结果
    if db_connector is not None:
        try:
            # 执行gold SQL - execute_query返回(DataFrame, error)或(None, error)
            gold_result, gold_error = db_connector.execute_query(gold_sql)
            # 执行predicted SQL
            predicted_result, predicted_error = db_connector.execute_query(predicted_sql)
            
            # 检查执行是否成功
            if gold_error is not None:
                print(f"[Gold验证] ⚠️ Gold SQL执行失败: {gold_error}")
                return False
            
            if predicted_error is not None:
                print(f"[Gold验证] ⚠️ Predicted SQL执行失败: {predicted_error}")
                return False
            
            if gold_result is None:
                print(f"[Gold验证] ⚠️ Gold SQL返回None（可能执行失败）")
                return False
            
            if predicted_result is None:
                print(f"[Gold验证] ⚠️ Predicted SQL返回None（可能执行失败）")
                return False
            
            # 比较结果（转换为集合进行比较，忽略顺序）
            import pandas as pd
            import numpy as np
            
            # 转换为字典列表格式（统一格式）
            def normalize_result(result):
                """将结果标准化为字典列表格式"""
                if result is None:
                    return []
                if isinstance(result, pd.DataFrame):
                    return result.to_dict('records')
                if isinstance(result, list):
                    # 如果是字典列表，直接返回
                    if result and isinstance(result[0], dict):
                        return result
                    # 如果是元组列表，转换为字典列表
                    if result and isinstance(result[0], (tuple, list)):
                        # 尝试获取列名
                        if hasattr(result, 'columns'):
                            columns = result.columns
                        else:
                            # 如果没有列名，使用索引
                            columns = [f'col_{i}' for i in range(len(result[0]))]
                        return [dict(zip(columns, row)) for row in result]
                return []
            
            gold_normalized = normalize_result(gold_result)
            predicted_normalized = normalize_result(predicted_result)
            
            # 检查是否都为空结果（需要特别处理）
            if len(gold_normalized) == 0 and len(predicted_normalized) == 0:
                # 两个都返回空结果，视为匹配
                print(f"[Gold验证] ✅ 两个SQL都返回空结果，视为匹配")
                return True
            
            # 转换为可比较的格式（处理NaN、None等，忽略列名差异）
            def normalize_row(row):
                """标准化行数据，处理NaN、None等，忽略列名差异（只基于值）"""
                # 提取所有值（忽略列名），排序以确保稳定性
                values = []
                for k, v in row.items():
                    if pd.isna(v) or v is None:
                        values.append(None)
                    elif isinstance(v, (np.integer, np.floating)):
                        values.append(float(v) if isinstance(v, float) else int(v))
                    elif isinstance(v, (int, float)):
                        values.append(float(v) if isinstance(v, float) else int(v))
                    else:
                        # 字符串统一转换为小写并去除首尾空格
                        values.append(str(v).strip().lower())
                
                # 对值排序以确保稳定性（忽略列名和列的顺序）
                values.sort(key=lambda x: (
                    0 if x is None else 1,  # None在前
                    str(type(x).__name__),  # 按类型名排序
                    str(x) if x is not None else ''  # 按值排序
                ))
                return tuple(values)
            
            gold_set = {normalize_row(row) for row in gold_normalized}
            predicted_set = {normalize_row(row) for row in predicted_normalized}
            
            # 比较集合
            is_match = gold_set == predicted_set
            
            if not is_match:
                print(f"[Gold验证] ❌ 结果不匹配:")
                print(f"  Gold结果行数: {len(gold_set)}")
                print(f"  Predicted结果行数: {len(predicted_set)}")
                if len(gold_set) <= 3:
                    print(f"  Gold结果示例: {list(gold_set)[:3]}")
                if len(predicted_set) <= 3:
                    print(f"  Predicted结果示例: {list(predicted_set)[:3]}")
                
                # 打印详细的原始结果用于调试
                # print(f"[Gold验证] 详细调试信息:")
                # print(f"  Gold原始结果类型: {type(gold_result)}")
                # print(f"  Predicted原始结果类型: {type(predicted_result)}")
                if hasattr(gold_result, 'head'):
                    print(f"  Gold结果前3行:\n{gold_result.head(3)}")
                if hasattr(predicted_result, 'head'):
                    print(f"  Predicted结果前3行:\n{predicted_result.head(3)}")
                print(f"  Gold标准化结果: {gold_normalized[:3] if len(gold_normalized) > 0 else '[]'}")
                print(f"  Predicted标准化结果: {predicted_normalized[:3] if len(predicted_normalized) > 0 else '[]'}")
                print(f"  Gold SQL: {gold_sql}")
                print(f"  Predicted SQL: {predicted_sql}")
            else:
                print(f"[Gold验证] ✅ 结果匹配 (行数: {len(gold_set)})")
            
            return is_match
            
        except Exception as e:
            print(f"[Gold验证] ⚠️ 执行SQL比较时出错: {e}")
            return False
    return False


def main():
    parser = argparse.ArgumentParser(description="并行 rollout 测试")
    parser.add_argument("--ppl_file", type=str, required=True, help="样本文件（JSON 数组）")
    parser.add_argument("--index", type=int, default=None, help="只跑第 index 个样本（可选）")
    parser.add_argument("--qid", type=str, default=None, help="按 question_id 精确定位并只跑该条（优先于 --index）")
    parser.add_argument("--qids", type=str, default=None, help="多个 question_id，用逗号分隔，如 '29,31,32'（优先于 --qid）")
    parser.add_argument("--gold_file", type=str, default=None, help="Gold SQL文件路径（用于验证）")
    parser.add_argument("--sql_out", type=str, default=None, help="SQL输出TXT")
    parser.add_argument("--json_out", type=str, default=None, help="保存结果的JSON")
    parser.add_argument("--parallel_workers", type=int, default=5, help="MCTS内部并行工作线程数（用于CTE/SQL生成，默认5）")
    parser.add_argument("--max_workers", type=int, default=1, help="并行处理多个问题的工作线程数（默认1）")
    parser.add_argument("--multi_base_urls", type=str, default=None, help="多个模型端点URL，用逗号分隔，例如：'http://localhost:8009/v1,http://localhost:8010/v1'")
    parser.add_argument("--max_cte_nodes", type=int, default=8, help="每次扩展节点时生成的CTE变体数量（默认8）")
    parser.add_argument("--max_depth", type=int, default=None, help="MCTS树最大深度/CTE最大步数（默认8，如果提供则覆盖）")
    parser.add_argument("--rollouts_per_iteration", type=int, default=8, help="每次迭代的rollout数量（默认8）")
    parser.add_argument("--num_sql_variants", type=int, default=10, help="每个rollout末尾生成的SQL变体数量（默认10）")
    parser.add_argument("--exploration_constant", type=float, default=2.5, 
                       help="UCB1探索常数（默认2.5，sqrt(2)≈1.414是经典值）")
    parser.add_argument("--cold_start_strategy", type=str, default="FIRST_UNVISITED",
                       choices=["FIRST_UNVISITED", "RANDOM_UNVISITED", "UCB_UNVISITED", "ROUND_ROBIN", "BEST_PRIOR"],
                       help="冷启动策略：FIRST_UNVISITED(第一个), RANDOM_UNVISITED(随机), UCB_UNVISITED(UCB+先验), ROUND_ROBIN(轮询), BEST_PRIOR(基于bucket_count)")
    parser.add_argument("--strategy_mode", type=str, default=None, 
                       help="策略模式：FORCE_S1/S2/S3/S4/S5, NONE, LLM_PICK_ONCE（默认None，使用全局配置FORCE_S4）")
    parser.add_argument("--task_timeout", type=int, default=1800, 
                       help="单个任务的最大超时时间（秒），默认1800秒（30分钟）。8个rollout时建议设置为1800秒以上")
    args = parser.parse_args()
    
    # MCTS配置
    mcts_config = {
        'max_cte_nodes_per_iteration': args.max_cte_nodes,  # 从命令行参数获取
        'rollouts_per_iteration': args.rollouts_per_iteration,  # 从命令行参数获取
        'num_sql_variants': args.num_sql_variants,  # 从命令行参数获取
        'exploration_constant': args.exploration_constant,  # UCB探索常数
        'cold_start_strategy': args.cold_start_strategy,  # 冷启动策略
    }
    if args.max_depth is not None:
        mcts_config['max_depth'] = args.max_depth

    # 解析多模型端点
    multi_base_urls = None
    if args.multi_base_urls:
        multi_base_urls = [url.strip() for url in args.multi_base_urls.split(',') if url.strip()]
        print(f"[配置] 启用多模型并行: {len(multi_base_urls)} 个端点")
        for i, url in enumerate(multi_base_urls):
            print(f"  端点 {i+1}: {url}")
    else:
        # 如果没有提供 --multi_base_urls，检查环境变量 VLLM_API_URL
        env_url = os.environ.get("VLLM_API_URL")
        if env_url:
            multi_base_urls = [env_url.strip()]
            print(f"[配置] 使用环境变量 VLLM_API_URL: {env_url}")
        else:
            print(f"[配置] 未指定端点，将使用默认配置（从环境变量或默认值）")

    print(f"并行rollout工作线程数: {args.parallel_workers}")
    print(f"并行处理问题数: {args.max_workers}")

    # 加载gold SQL（如果提供）
    gold_sqls = {}
    if args.gold_file:
        gold_sqls = load_gold_sqls(args.gold_file)

    with open(args.ppl_file, 'r', encoding='utf-8') as f:
        ppls = json.load(f)

    # 优先级：--qids > --qid > --index > 全量
    if args.qids is not None:
        # 解析多个qid
        qid_list = [q.strip() for q in args.qids.split(',') if q.strip()]
        indices = []
        qid_to_idx = {}
        for i, item in enumerate(ppls):
            qid_val = item.get('question_id', None)
            if qid_val is not None:
                qid_str = str(qid_val)
                qid_to_idx[qid_str] = i
        
        for qid in qid_list:
            if qid in qid_to_idx:
                indices.append(qid_to_idx[qid])
            else:
                print(f"[警告] 未找到 question_id={qid} 的样本，跳过")
        
        if not indices:
            raise ValueError(f"未找到任何指定的 question_id: {args.qids}")
        print(f"定位到 {len(indices)} 个样本: question_id={args.qids}")
    elif args.qid is not None:
        target_idx = None
        for i, item in enumerate(ppls):
            qid_val = item.get('question_id', None)
            if qid_val is None:
                continue
            # 支持字符串/整数两种形式的等价匹配
            try:
                if str(qid_val) == str(args.qid):
                    target_idx = i
                    break
            except Exception:
                pass
        if target_idx is None:
            raise ValueError(f"未在 {args.ppl_file} 中找到 question_id={args.qid} 的样本")
        indices = [target_idx]
        print(f"定位到 question_id={args.qid} 于索引 {target_idx}")
    else:
        # 若指定 index，则仅跑该样本
        indices = [args.index] if args.index is not None else list(range(len(ppls)))

    results = {}
    results_with_stats = {}  # 保存完整的统计信息
    processed_indices = []
    correct_count = 0
    total_count = 0
    
    # 准备任务列表
    tasks = []
    for idx in indices:
        sample = load_sample(args.ppl_file, idx)
        tasks.append((idx, sample, args.parallel_workers, gold_sqls, ppls, multi_base_urls, mcts_config, args.strategy_mode))
    
    # 统一使用并行处理模式（max_workers=1时也是并行处理，只是单线程）
    print(f"\n处理 {len(tasks)} 个样本（{args.max_workers} 个worker）...")
    save_lock = threading.Lock()
    
    # 使用线程池并行处理
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_to_task = {executor.submit(process_single_task, task): task for task in tasks}
        
        completed_count = 0
        for future in tqdm(as_completed(future_to_task), total=len(tasks), desc="处理样本"):
            try:
                # 添加超时处理，避免任务卡住
                try:
                    result_dict = future.result(timeout=args.task_timeout)
                except FutureTimeoutError:
                    # 获取任务信息以便记录
                    task = future_to_task.get(future)
                    if task:
                        idx = task[0]
                        qid = str(task[1].get('question_id', idx))
                        print(f"\n⏱️ [样本#{idx}] 任务超时（>{args.task_timeout}秒），跳过该样本")
                        result_dict = {
                            'idx': idx,
                            'qid': qid,
                            'sql': '',
                            'stats': {},
                            'gold_match': None,
                            'error': f'任务超时（>{args.task_timeout}秒）',
                            'all_sqls_with_attributes': [],
                            'rollout_stats': [],
                        }
                    else:
                        # 如果无法获取任务信息，创建一个默认的错误结果
                        print(f"\n⏱️ 任务超时（>{args.task_timeout}秒），跳过该任务")
                        continue
                
                idx = result_dict['idx']
                qid = result_dict['qid']
                results[qid] = result_dict['sql']
                results_with_stats[qid] = {
                    'sql': result_dict['sql'],
                    'stats': result_dict['stats'],
                    'all_sqls_with_attributes': result_dict.get('all_sqls_with_attributes', []),  # 保存所有SQL及其属性
                    'rollout_stats': result_dict.get('rollout_stats', []),  # 保存每个rollout的详细统计信息
                }
                processed_indices.append(idx)
                completed_count += 1
                
                # 统计gold验证结果
                if result_dict.get('gold_match') is not None:
                    total_count += 1
                    if result_dict['gold_match']:
                        correct_count += 1
                
                # 每完成一个任务或全部完成时保存一次
                if completed_count % 1 == 0 or completed_count == len(tasks):
                    with save_lock:
                        # 确定输出索引
                        if args.qids is not None:
                            all_indices_for_output = sorted(processed_indices)
                        else:
                            all_indices_for_output = indices if args.index is not None else list(range(len(ppls)))

                        # 保存TXT
                        if args.sql_out:
                            Path(args.sql_out).parent.mkdir(parents=True, exist_ok=True)
                            with open(args.sql_out, 'w', encoding='utf-8') as fw:
                                for j in all_indices_for_output:
                                    key = str(ppls[j].get('question_id', j))
                                    sql = results.get(key, "") if (j in processed_indices) else ""
                                    if sql:
                                        sql = ' '.join(sql.split())
                                    fw.write(str(sql) + "\n")
                            print(f"[保存] SQL -> {args.sql_out} (已处理 {len(processed_indices)}/{len(all_indices_for_output)})")

                        # 保存JSON
                        if args.json_out:
                            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
                            out_obj = {}
                            for j in processed_indices:
                                key = str(ppls[j].get('question_id', j))
                                out_obj[key] = results_with_stats.get(key, {'sql': '', 'stats': {}})
                            with open(args.json_out, 'w', encoding='utf-8') as fw:
                                json.dump(out_obj, fw, ensure_ascii=False, indent=2)
                            print(f"[保存] JSON -> {args.json_out} (已处理 {len(processed_indices)})")
                        
            except Exception as e:
                print(f"\n❌ 处理任务时出错: {e}")
                import traceback
                traceback.print_exc()
    
    # 打印最终统计
    if gold_sqls and total_count > 0:
        print(f"\n{'='*80}")
        print(f"[最终统计] Gold验证: {correct_count}/{total_count} 正确 (准确率: {correct_count/total_count*100:.2f}%)")
        print(f"{'='*80}\n")


if __name__ == "__main__":
    main()


# 使用示例（从项目根目录运行，使用相对路径）
# 
# 基本用法：
# python workflows/mcts_v1/test/test_mcts.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --sql_out workflows/mcts_v1/test/out/test_single.txt \
#   --json_out workflows/mcts_v1/test/out/test_single.json \
#   --qid 25 \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5
#
# 多模型并行：
# python workflows/mcts_v1/test/test_mcts.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --sql_out workflows/mcts_v1/test/out/12_6all.txt \
#   --json_out workflows/mcts_v1/test/out/test_single.json \
#   --qid 25 \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5 \
#   --multi_base_urls "http://localhost:8011/v1,http://localhost:8009/v1"
#
# 后台运行示例：
# nohup python workflows/mcts_v1/test/test_mcts.py \
#   --max_workers 10 \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --sql_out workflows/mcts_v1/test/out/12_11.txt \
#   --json_out workflows/mcts_v1/test/out/12_11.json \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 15 \
#   --multi_base_urls "http://localhost:8009/v1,http://localhost:8011/v1,http://localhost:8012/v1" \
#   --max_cte_nodes 15 \
#   > workflows/mcts_v1/test/out/12_11.log 2>&1 &
#
# 多个问题ID测试：
# python workflows/mcts_v1/test/test_mcts.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --qids "81,287,479" \
#   --sql_out workflows/mcts_v1/test/out/test_q81_287_479_sql.txt \
#   --json_out workflows/mcts_v1/test/out/test_q81_287_479_result.json \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5 \
#   --max_workers 1 \
#   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1"
#
# 策略模式测试：
# python workflows/mcts_v1/test/test_mcts.py \
#   --ppl_file data/subset_ppl_dev_python.json \
#   --sql_out workflows/mcts_v1/test/out/1_6_test_no_strategy_sql.txt \
#   --json_out workflows/mcts_v1/test/out/1_6_test_no_strategy_result.json \
#   --gold_file data/sub_sampled_bird_dev_set.json \
#   --parallel_workers 5 \
#   --strategy_mode NONE \
#   --multi_base_urls "http://localhost:8009/v1,http://localhost:8010/v1,http://localhost:8012/v1"


