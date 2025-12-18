#!/usr/bin/env python3
"""
评估所有rollout的SQL准确度，并找出最佳策略

1. 对所有rollout中的SQL进行gold验证
2. 计算每个策略能选出的准确度最高的SQL
3. 找出最优策略
"""

import json
import sys
import math
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# 添加项目路径
sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from core.database_connector import DatabaseConnector
from workflows.mcts.test.test_mcts import compare_with_gold, build_db_connector


def compute_path_entropy(cte_buckets_per_node: List[List[Dict[str, Any]]]) -> float:
    """
    计算CTE路径的信息熵
    
    Args:
        cte_buckets_per_node: 每个节点的CTE桶信息
            [[{'cte': str, 'count': int, ...}, ...], ...]
    
    Returns:
        路径的平均信息熵
    """
    if not cte_buckets_per_node:
        return 0.0
    
    node_entropies = []
    for node_buckets in cte_buckets_per_node:
        if not node_buckets:
            continue
        
        # 计算该节点的总count
        total_count = sum(bucket.get('count', 0) for bucket in node_buckets)
        if total_count == 0:
            continue
        
        # 计算该节点的信息熵
        entropy = 0.0
        for bucket in node_buckets:
            count = bucket.get('count', 0)
            if count > 0:
                p = count / total_count
                entropy -= p * math.log2(p + 1e-10)  # 避免log(0)
        
        node_entropies.append(entropy)
    
    # 返回平均信息熵
    return sum(node_entropies) / len(node_entropies) if node_entropies else 0.0


def load_gold_sqls(gold_file: str) -> Dict[str, str]:
    """加载gold SQL文件"""
    gold_sqls = {}
    with open(gold_file, 'r', encoding='utf-8') as f:
        gold_data = json.load(f)
        for item in gold_data:
            qid = str(item.get('question_id', ''))
            sql = item.get('SQL', '')
            if qid and sql:
                gold_sqls[qid] = sql
    return gold_sqls


def compare_with_gold_with_timeout(predicted_sql: str, gold_sql: str, db_connector, timeout_s: float = 30.0) -> bool:
    """带超时的compare_with_gold包装函数（与test_mcts.py中的逻辑保持一致）"""
    try:
        # 执行gold SQL - execute_query返回(DataFrame, error)或(None, error)
        gold_result, gold_error = db_connector.execute_query(gold_sql, timeout_s=timeout_s)
        # 执行predicted SQL
        predicted_result, predicted_error = db_connector.execute_query(predicted_sql, timeout_s=timeout_s)
        
        # 检查执行是否成功
        if gold_error is not None:
            return False
        
        if predicted_error is not None:
            return False
        
        if gold_result is None or predicted_result is None:
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
        return gold_set == predicted_set
    except Exception as e:
        return False


def evaluate_single_sql_task(args: Tuple) -> Dict[str, Any]:
    """单个SQL评估任务（用于并行化）"""
    sql, gold_sql, db_name, qid, rollout_id, sql_info, rollout, timeout_s = args
    is_correct = False
    
    # 为每个任务创建独立的数据库连接（线程安全）
    db_connector = None
    if db_name:
        try:
            db_connector = build_db_connector(db_name)
            is_correct = compare_with_gold_with_timeout(sql, gold_sql, db_connector, timeout_s=timeout_s)
        except Exception as e:
            # 静默处理错误，避免输出过多
            pass
        finally:
            if db_connector:
                try:
                    db_connector.disconnect()
                except:
                    pass
    
    return {
        'sql': sql,
        'is_correct': is_correct,
        'valid': sql_info.get('valid', False),
        'result_signature': sql_info.get('result_signature'),
        'sql_bucket_count': rollout.get('sql_bucket_count', 0),
        'reward': rollout.get('reward', 0.0),
        'avg_cte_bucket_count': sum(rollout.get('cte_bucket_counts', [])) / len(rollout.get('cte_bucket_counts', [])) if rollout.get('cte_bucket_counts') else 0.0
    }


def evaluate_all_sqls(data: Dict[str, Any], gold_sqls: Dict[str, str], 
                      ppl_file: str = None, num_workers: int = 8, timeout_s: float = 30.0) -> Dict[str, Any]:
    """
    评估所有rollout中的SQL准确度
    
    Returns:
        包含每个SQL准确度信息的字典
    """
    print("正在评估所有rollout中的SQL准确度...")
    
    # 加载数据库信息（如果需要）
    db_info_map = {}
    if ppl_file:
        try:
            with open(ppl_file, 'r', encoding='utf-8') as f:
                ppls = json.load(f)
            for item in ppls:
                qid = str(item.get('question_id', ''))
                db_name = item.get('db', '')
                if qid and db_name:
                    db_info_map[qid] = db_name
            print(f"加载了 {len(db_info_map)} 条数据库信息")
        except Exception as e:
            print(f"⚠️ 加载数据库信息失败: {e}")
    
    evaluated_data = {}
    total_sqls = 0
    correct_sqls = 0
    
    # 准备所有评估任务
    all_tasks = []
    
    for qid, item in data.items():
        gold_sql = gold_sqls.get(qid, '')
        if not gold_sql:
            continue
        
        db_name = db_info_map.get(qid, '')
        rollout_stats = item.get('rollout_stats', [])
        
        for rollout in rollout_stats:
            rollout_id = rollout.get('rollout_id', 0)
            sql_variants = rollout.get('all_sql_variants', [])
            selected_sql = rollout.get('selected_sql')
            
            # 添加所有SQL变体的评估任务
            for sql_info in sql_variants:
                sql = sql_info.get('sql', '')
                if sql:
                    all_tasks.append((sql, gold_sql, db_name, qid, rollout_id, sql_info, rollout, timeout_s))
            
            # 添加selected_sql的评估任务（如果存在且不在sql_variants中）
            if selected_sql:
                # 检查是否已经在sql_variants中
                found = False
                for sql_info in sql_variants:
                    if sql_info.get('sql', '') == selected_sql:
                        found = True
                        break
                
                if not found:
                    # 创建一个临时的sql_info用于selected_sql
                    selected_sql_info = {'valid': True, 'result_signature': None}
                    all_tasks.append((selected_sql, gold_sql, db_name, qid, rollout_id, selected_sql_info, rollout, timeout_s))
    
    # 并行执行所有SQL评估任务
    print(f"共 {len(all_tasks)} 个SQL需要评估，使用 {num_workers} 个并行 worker，超时时间 {timeout_s}秒...")
    sql_evaluations_dict = {}  # {(qid, rollout_id): [sql_evaluations]}
    failed_tasks = []  # 记录失败的任务
    
    # 计算合理的总超时时间：基于任务总数和worker数量
    # 假设每个任务最多需要 timeout_s * 2 秒（包括重试和错误处理）
    # 总超时 = (任务数 / worker数) * 每个任务超时 * 安全系数
    estimated_timeout = max(
        (len(all_tasks) / num_workers) * timeout_s * 2 * 1.5,  # 安全系数1.5
        timeout_s * 10  # 至少10倍单个任务超时
    )
    print(f"预计总超时时间: {estimated_timeout:.1f}秒 ({estimated_timeout/60:.1f}分钟)")
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        # 提交所有任务
        future_to_task = {executor.submit(evaluate_single_sql_task, task): task for task in all_tasks}
        
        # 使用tqdm显示进度（包含剩余时间估算）
        with tqdm(total=len(all_tasks), desc="评估SQL", 
                  unit="个", unit_scale=False,
                  bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]') as pbar:
            try:
                # 使用计算出的超时时间，或者不设置超时（None表示无超时）
                # 因为每个future.result()已经有自己的超时处理
                for future in as_completed(future_to_task, timeout=None):
                    task = future_to_task[future]
                    sql, gold_sql, db_name, qid, rollout_id, sql_info, rollout, timeout_s_task = task
                    
                    try:
                        # 设置future.result()的超时时间（比SQL执行超时稍长）
                        result = future.result(timeout=timeout_s_task * 2)
                        key = (qid, rollout_id)
                        if key not in sql_evaluations_dict:
                            sql_evaluations_dict[key] = []
                        sql_evaluations_dict[key].append(result)
                        
                        total_sqls += 1
                        if result['is_correct']:
                            correct_sqls += 1
                    except TimeoutError:
                        # 单个任务超时
                        failed_tasks.append((qid, rollout_id, sql[:50] + '...' if len(sql) > 50 else sql))
                        # 创建默认结果
                        key = (qid, rollout_id)
                        if key not in sql_evaluations_dict:
                            sql_evaluations_dict[key] = []
                        sql_evaluations_dict[key].append({
                            'sql': sql,
                            'is_correct': False,
                            'valid': sql_info.get('valid', False),
                            'result_signature': sql_info.get('result_signature'),
                            'sql_bucket_count': rollout.get('sql_bucket_count', 0),
                            'reward': rollout.get('reward', 0.0),
                            'avg_cte_bucket_count': sum(rollout.get('cte_bucket_counts', [])) / len(rollout.get('cte_bucket_counts', [])) if rollout.get('cte_bucket_counts') else 0.0,
                            'path_entropy': rollout.get('path_entropy', 0.0),
                            'total_visit_count': rollout.get('total_visit_count', 0),
                            'valid_count': rollout.get('valid_count', 0),
                            'result_buckets_count': rollout.get('result_buckets_count', 0),
                            'selected_sql': rollout.get('selected_sql', None)
                        })
                        total_sqls += 1
                    except Exception as e:
                        # 其他异常
                        failed_tasks.append((qid, rollout_id, sql[:50] + '...' if len(sql) > 50 else sql))
                        # 创建默认结果
                        key = (qid, rollout_id)
                        if key not in sql_evaluations_dict:
                            sql_evaluations_dict[key] = []
                        sql_evaluations_dict[key].append({
                            'sql': sql,
                            'is_correct': False,
                            'valid': sql_info.get('valid', False),
                            'result_signature': sql_info.get('result_signature'),
                            'sql_bucket_count': rollout.get('sql_bucket_count', 0),
                            'reward': rollout.get('reward', 0.0),
                            'avg_cte_bucket_count': sum(rollout.get('cte_bucket_counts', [])) / len(rollout.get('cte_bucket_counts', [])) if rollout.get('cte_bucket_counts') else 0.0,
                            'path_entropy': rollout.get('path_entropy', 0.0),
                            'total_visit_count': rollout.get('total_visit_count', 0),
                            'valid_count': rollout.get('valid_count', 0),
                            'result_buckets_count': rollout.get('result_buckets_count', 0),
                            'selected_sql': rollout.get('selected_sql', None)
                        })
                        total_sqls += 1
                    
                    pbar.update(1)
            except KeyboardInterrupt:
                print("\n⚠️ 用户中断，正在清理...")
                # 取消所有未完成的任务
                for future in future_to_task:
                    future.cancel()
                raise
    
    if failed_tasks:
        print(f"\n⚠️ 共有 {len(failed_tasks)} 个任务失败或超时")
        if len(failed_tasks) <= 10:
            for qid, rollout_id, sql in failed_tasks:
                print(f"  - qid={qid}, rollout={rollout_id}, sql={sql}")
    
    # 组织评估结果
    for qid, item in data.items():
        gold_sql = gold_sqls.get(qid, '')
        if not gold_sql:
            continue
        
        db_name = db_info_map.get(qid, '')
        rollout_stats = item.get('rollout_stats', [])
        rollout_evaluations = []
        
        for rollout in rollout_stats:
            rollout_id = rollout.get('rollout_id', 0)
            sql_variants = rollout.get('all_sql_variants', [])
            selected_sql = rollout.get('selected_sql')
            
            # 获取该rollout的所有SQL评估结果
            key = (qid, rollout_id)
            sql_evaluations = sql_evaluations_dict.get(key, [])
            
            # 处理selected_sql（如果不在sql_evaluations中，需要单独评估）
            if selected_sql:
                found = False
                for sql_eval in sql_evaluations:
                    if sql_eval['sql'] == selected_sql:
                        found = True
                        break
                
                if not found:
                    # selected_sql不在并行任务中，需要单独评估
                    is_correct = False
                    if db_name:
                        db_connector = None
                        try:
                            db_connector = build_db_connector(db_name)
                            is_correct = compare_with_gold_with_timeout(selected_sql, gold_sql, db_connector, timeout_s=timeout_s)
                        except Exception as e:
                            pass  # 静默处理
                        finally:
                            if db_connector:
                                try:
                                    db_connector.disconnect()
                                except:
                                    pass
                    
                    sql_evaluations.append({
                        'sql': selected_sql,
                        'is_correct': is_correct,
                        'valid': True,
                        'result_signature': None,
                        'sql_bucket_count': rollout.get('sql_bucket_count', 0),
                        'reward': rollout.get('reward', 0.0),
                        'avg_cte_bucket_count': sum(rollout.get('cte_bucket_counts', [])) / len(rollout.get('cte_bucket_counts', [])) if rollout.get('cte_bucket_counts') else 0.0
                    })
                    
                    total_sqls += 1
                    if is_correct:
                        correct_sqls += 1
            
            # 计算visit_counts总和
            visit_counts = rollout.get('visit_counts', [])
            total_visit_count = sum(visit_counts) if visit_counts else 0
            
            # 计算路径信息熵
            cte_buckets_per_node = rollout.get('cte_buckets_per_node', [])
            path_entropy = compute_path_entropy(cte_buckets_per_node)
            
            rollout_evaluations.append({
                'rollout_id': rollout_id,
                'sql_evaluations': sql_evaluations,
                'reward': rollout.get('reward', 0.0),
                'avg_cte_bucket_count': sum(rollout.get('cte_bucket_counts', [])) / len(rollout.get('cte_bucket_counts', [])) if rollout.get('cte_bucket_counts') else 0.0,
                'sql_bucket_count': rollout.get('sql_bucket_count', 0),
                'cte_bucket_counts': rollout.get('cte_bucket_counts', []),
                'total_visit_count': total_visit_count,
                'valid_count': rollout.get('valid_count', 0),
                'result_buckets_count': len(rollout.get('result_buckets', {})),
                'selected_sql': selected_sql,
                'path_entropy': path_entropy
            })
        
        evaluated_data[qid] = {
            'gold_sql': gold_sql,
            'rollout_evaluations': rollout_evaluations
        }
        
    # 数据库连接已在每个任务中关闭，无需额外清理
    
    print(f"✅ 评估完成：共评估 {total_sqls} 条SQL，正确 {correct_sqls} 条 ({correct_sqls/total_sqls*100:.2f}%)")
    
    return evaluated_data


def find_best_sql_by_strategy(evaluated_data: Dict[str, Any], original_data: Dict[str, Any] = None) -> Dict[str, Dict[str, Any]]:
    """
    根据不同策略找出最佳SQL，并计算准确度
    
    Returns:
        {
            'strategy1': {'sqls': {...}, 'accuracy': 0.xx},
            'strategy2': {...},
            ...
        }
    """
    # 统一分母：所有有gold SQL的question_id
    all_qids = set(evaluated_data.keys())
    total_qids = len(all_qids)
    
    strategies = {}
    
    # 策略1：最大reward（保留top2）
    strategy3_results = {}
    strategy3_correct = 0
    strategy3_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        max_reward = float('-inf')
        
        for rollout_eval in data['rollout_evaluations']:
            reward = rollout_eval['reward']
            if reward > max_reward:
                max_reward = reward
                best_rollout = rollout_eval
        
        if best_rollout:
            valid_sqls = [s for s in best_rollout['sql_evaluations'] if s.get('valid', False)]
            if valid_sqls:
                best_sql = valid_sqls[0]
                strategy3_results[qid] = best_sql['sql']
                strategy3_total += 1
                if best_sql['is_correct']:
                    strategy3_correct += 1
            else:
                strategy3_results[qid] = ""
                strategy3_total += 1
        else:
            strategy3_results[qid] = ""
            strategy3_total += 1
    
    strategies['strategy1_max_reward'] = {
        'results': strategy3_results,
        'accuracy': strategy3_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy3_correct,
        'total': total_qids,
        'selected': strategy3_total
    }
    
    # 策略2：理想策略（选择所有SQL中准确度最高的，保留top2）
    strategy8_results = {}
    strategy8_correct = 0
    strategy8_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        all_sqls = []
        for rollout_eval in data['rollout_evaluations']:
            all_sqls.extend(rollout_eval['sql_evaluations'])
        
        # 只考虑有效的SQL
        valid_sqls = [s for s in all_sqls if s.get('valid', False)]
        
        if valid_sqls:
            # 选择第一个正确的SQL，如果没有正确的，选择第一个
            best_sql = None
            for sql_eval in valid_sqls:
                if sql_eval['is_correct']:
                    best_sql = sql_eval
                    break
            
            if not best_sql:
                best_sql = valid_sqls[0]
            
            if best_sql:
                strategy8_results[qid] = best_sql['sql']
                strategy8_total += 1
                if best_sql['is_correct']:
                    strategy8_correct += 1
        else:
            strategy8_results[qid] = ""
            strategy8_total += 1
    
    strategies['strategy2_ideal'] = {
        'results': strategy8_results,
        'accuracy': strategy8_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy8_correct,
        'total': total_qids,
        'selected': strategy8_total
    }
    
    # 策略3：直接使用selected_sql（最简单直接）
    strategy3_results = {}
    strategy3_correct = 0
    strategy3_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        max_reward = float('-inf')
        
        # 选择reward最大的rollout的selected_sql
        for rollout_eval in data['rollout_evaluations']:
            reward = rollout_eval['reward']
            if reward > max_reward:
                max_reward = reward
                best_rollout = rollout_eval
        
        if best_rollout and best_rollout.get('selected_sql'):
            selected_sql = best_rollout['selected_sql']
            # 在sql_evaluations中查找这个SQL的准确度（标准化比较）
            is_correct = False
            selected_sql_normalized = ' '.join(selected_sql.split())
            for sql_eval in best_rollout['sql_evaluations']:
                sql_normalized = ' '.join(sql_eval['sql'].split())
                if sql_normalized == selected_sql_normalized:
                    is_correct = sql_eval.get('is_correct', False)
                    break
            
            strategy3_results[qid] = selected_sql
            strategy3_total += 1
            if is_correct:
                strategy3_correct += 1
        else:
            strategy3_results[qid] = ""
            strategy3_total += 1
    
    strategies['strategy3_selected_sql'] = {
        'results': strategy3_results,
        'accuracy': strategy3_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy3_correct,
        'total': total_qids,
        'selected': strategy3_total
    }
    
    # 策略4：最大visit_counts总和
    strategy4_results = {}
    strategy4_correct = 0
    strategy4_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        max_visit = -1
        
        for rollout_eval in data['rollout_evaluations']:
            total_visit = rollout_eval.get('total_visit_count', 0)
            if total_visit > max_visit:
                max_visit = total_visit
                best_rollout = rollout_eval
        
        if best_rollout:
            valid_sqls = [s for s in best_rollout['sql_evaluations'] if s.get('valid', False)]
            if valid_sqls:
                best_sql = valid_sqls[0]
                strategy4_results[qid] = best_sql['sql']
                strategy4_total += 1
                if best_sql['is_correct']:
                    strategy4_correct += 1
            else:
                strategy4_results[qid] = ""
                strategy4_total += 1
        else:
            strategy4_results[qid] = ""
            strategy4_total += 1
    
    strategies['strategy4_max_visit_count'] = {
        'results': strategy4_results,
        'accuracy': strategy4_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy4_correct,
        'total': total_qids,
        'selected': strategy4_total
    }
    
    # 策略5：最大valid_count
    strategy5_results = {}
    strategy5_correct = 0
    strategy5_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        max_valid = -1
        
        for rollout_eval in data['rollout_evaluations']:
            valid_count = rollout_eval.get('valid_count', 0)
            if valid_count > max_valid:
                max_valid = valid_count
                best_rollout = rollout_eval
        
        if best_rollout:
            valid_sqls = [s for s in best_rollout['sql_evaluations'] if s.get('valid', False)]
            if valid_sqls:
                best_sql = valid_sqls[0]
                strategy5_results[qid] = best_sql['sql']
                strategy5_total += 1
                if best_sql['is_correct']:
                    strategy5_correct += 1
            else:
                strategy5_results[qid] = ""
                strategy5_total += 1
        else:
            strategy5_results[qid] = ""
            strategy5_total += 1
    
    strategies['strategy5_max_valid_count'] = {
        'results': strategy5_results,
        'accuracy': strategy5_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy5_correct,
        'total': total_qids,
        'selected': strategy5_total
    }
    
    # 策略6：最大result_buckets数量
    strategy6_results = {}
    strategy6_correct = 0
    strategy6_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        max_buckets = -1
        
        for rollout_eval in data['rollout_evaluations']:
            buckets_count = rollout_eval.get('result_buckets_count', 0)
            if buckets_count > max_buckets:
                max_buckets = buckets_count
                best_rollout = rollout_eval
        
        if best_rollout:
            valid_sqls = [s for s in best_rollout['sql_evaluations'] if s.get('valid', False)]
            if valid_sqls:
                best_sql = valid_sqls[0]
                strategy6_results[qid] = best_sql['sql']
                strategy6_total += 1
                if best_sql['is_correct']:
                    strategy6_correct += 1
            else:
                strategy6_results[qid] = ""
                strategy6_total += 1
        else:
            strategy6_results[qid] = ""
            strategy6_total += 1
    
    strategies['strategy6_max_result_buckets'] = {
        'results': strategy6_results,
        'accuracy': strategy6_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy6_correct,
        'total': total_qids,
        'selected': strategy6_total
    }
    
    # 策略7：综合评分（reward + visit_count + valid_count）
    strategy7_results = {}
    strategy7_correct = 0
    strategy7_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        best_score = float('-inf')
        
        for rollout_eval in data['rollout_evaluations']:
            reward = rollout_eval['reward']
            visit_count = rollout_eval.get('total_visit_count', 0)
            valid_count = rollout_eval.get('valid_count', 0)
            # 综合评分：reward权重最高，visit和valid作为辅助
            score = reward * 10 + visit_count * 0.1 + valid_count * 0.1
            if score > best_score:
                best_score = score
                best_rollout = rollout_eval
        
        if best_rollout:
            valid_sqls = [s for s in best_rollout['sql_evaluations'] if s.get('valid', False)]
            if valid_sqls:
                best_sql = valid_sqls[0]
                strategy7_results[qid] = best_sql['sql']
                strategy7_total += 1
                if best_sql['is_correct']:
                    strategy7_correct += 1
            else:
                strategy7_results[qid] = ""
                strategy7_total += 1
        else:
            strategy7_results[qid] = ""
            strategy7_total += 1
    
    strategies['strategy7_combined_reward_visit_valid'] = {
        'results': strategy7_results,
        'accuracy': strategy7_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy7_correct,
        'total': total_qids,
        'selected': strategy7_total
    }
    
    # 策略8：最小信息熵（选择最确定的路径）
    strategy8_results = {}
    strategy8_correct = 0
    strategy8_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        min_entropy = float('inf')
        
        for rollout_eval in data['rollout_evaluations']:
            entropy = rollout_eval.get('path_entropy', float('inf'))
            if entropy < min_entropy:
                min_entropy = entropy
                best_rollout = rollout_eval
        
        if best_rollout and min_entropy < float('inf'):
            valid_sqls = [s for s in best_rollout['sql_evaluations'] if s.get('valid', False)]
            if valid_sqls:
                best_sql = valid_sqls[0]
                strategy8_results[qid] = best_sql['sql']
                strategy8_total += 1
                if best_sql['is_correct']:
                    strategy8_correct += 1
            else:
                strategy8_results[qid] = ""
                strategy8_total += 1
        else:
            strategy8_results[qid] = ""
            strategy8_total += 1
    
    strategies['strategy8_min_entropy'] = {
        'results': strategy8_results,
        'accuracy': strategy8_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy8_correct,
        'total': total_qids,
        'selected': strategy8_total
    }
    
    # 策略9：最大信息熵（选择探索最多的路径）
    strategy9_results = {}
    strategy9_correct = 0
    strategy9_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        max_entropy = -1
        
        for rollout_eval in data['rollout_evaluations']:
            entropy = rollout_eval.get('path_entropy', 0)
            if entropy > max_entropy:
                max_entropy = entropy
                best_rollout = rollout_eval
        
        if best_rollout and max_entropy >= 0:
            valid_sqls = [s for s in best_rollout['sql_evaluations'] if s.get('valid', False)]
            if valid_sqls:
                best_sql = valid_sqls[0]
                strategy9_results[qid] = best_sql['sql']
                strategy9_total += 1
                if best_sql['is_correct']:
                    strategy9_correct += 1
            else:
                strategy9_results[qid] = ""
                strategy9_total += 1
        else:
            strategy9_results[qid] = ""
            strategy9_total += 1
    
    strategies['strategy9_max_entropy'] = {
        'results': strategy9_results,
        'accuracy': strategy9_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy9_correct,
        'total': total_qids,
        'selected': strategy9_total
    }
    
    # 策略10：综合评分（reward + 低信息熵，即reward高且路径确定）
    strategy10_results = {}
    strategy10_correct = 0
    strategy10_total = 0
    
    for qid in all_qids:
        data = evaluated_data[qid]
        best_rollout = None
        best_score = float('-inf')
        
        for rollout_eval in data['rollout_evaluations']:
            reward = rollout_eval['reward']
            entropy = rollout_eval.get('path_entropy', 1.0)
            # 综合评分：reward高且信息熵低（更确定）的路径更好
            # 使用 1/(entropy+1) 来奖励低信息熵
            score = reward * 10 + (1.0 / (entropy + 1.0)) * 0.5
            if score > best_score:
                best_score = score
                best_rollout = rollout_eval
        
        if best_rollout:
            valid_sqls = [s for s in best_rollout['sql_evaluations'] if s.get('valid', False)]
            if valid_sqls:
                best_sql = valid_sqls[0]
                strategy10_results[qid] = best_sql['sql']
                strategy10_total += 1
                if best_sql['is_correct']:
                    strategy10_correct += 1
            else:
                strategy10_results[qid] = ""
                strategy10_total += 1
        else:
            strategy10_results[qid] = ""
            strategy10_total += 1
    
    strategies['strategy10_reward_low_entropy'] = {
        'results': strategy10_results,
        'accuracy': strategy10_correct / total_qids if total_qids > 0 else 0.0,
        'correct': strategy10_correct,
        'total': total_qids,
        'selected': strategy10_total
    }
    
    return strategies


def save_strategy_results(strategies: Dict[str, Dict[str, Any]], output_dir: Path, question_ids: List[str]):
    """保存各策略的结果到txt文件"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for strategy_name, strategy_data in strategies.items():
        output_path = output_dir / f"12_10_{strategy_name}.txt"
        results = strategy_data['results']
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for qid in question_ids:
                sql = results.get(qid, "")
                if sql:
                    sql = ' '.join(sql.split())
                f.write(sql + "\n")
            
            # 填满到147行
            current_lines = len(question_ids)
            if current_lines < 147:
                for _ in range(147 - current_lines):
                    f.write("\n")
        
        print(f"✅ {strategy_name}: 准确度={strategy_data['accuracy']:.4f} ({strategy_data['correct']}/{strategy_data['total']}) -> {output_path}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="评估所有rollout的SQL并找出最佳策略")
    parser.add_argument("--json_file", type=str, required=True, help="输入的JSON文件路径")
    parser.add_argument("--gold_file", type=str, required=True, help="Gold SQL文件路径")
    parser.add_argument("--ppl_file", type=str, default=None, help="原始数据文件（用于获取数据库信息）")
    parser.add_argument("--output_dir", type=str, default=None, help="输出目录")
    parser.add_argument("--num_workers", type=int, default=8, help="并行worker数量（默认8）")
    parser.add_argument("--timeout_s", type=float, default=30.0, help="SQL执行超时时间（秒，默认30）")
    args = parser.parse_args()
    
    # 加载数据
    print(f"正在加载JSON文件: {args.json_file}")
    with open(args.json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"正在加载Gold SQL文件: {args.gold_file}")
    gold_sqls = load_gold_sqls(args.gold_file)
    print(f"加载了 {len(gold_sqls)} 条gold SQL")
    
    # 评估所有SQL（使用并行化）
    num_workers = getattr(args, 'num_workers', 8)
    timeout_s = getattr(args, 'timeout_s', 30.0)
    evaluated_data = evaluate_all_sqls(data, gold_sqls, args.ppl_file, num_workers=num_workers, timeout_s=timeout_s)
    
    # 找出最佳策略
    print("\n正在计算各策略的准确度...")
    strategies = find_best_sql_by_strategy(evaluated_data, original_data=data)
    
    # 保存评估结果
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path(args.json_file).parent / "strategy_evaluation"
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存评估数据
    eval_output = output_dir / "all_sqls_evaluation.json"
    with open(eval_output, 'w', encoding='utf-8') as f:
        json.dump(evaluated_data, f, indent=2, ensure_ascii=False)
    print(f"✅ 评估数据已保存到: {eval_output}")
    
    # 保存策略结果
    question_ids = sorted(data.keys(), key=lambda x: int(x) if x.isdigit() else 0)
    save_strategy_results(strategies, output_dir, question_ids)
    
    # 打印策略排名
    print("\n" + "="*80)
    print("策略准确度排名 (统一分母):")
    print("="*80)
    sorted_strategies = sorted(strategies.items(), key=lambda x: x[1]['accuracy'], reverse=True)
    for idx, (name, data) in enumerate(sorted_strategies, 1):
        selected = data.get('selected', data['total'])
        print(f"{idx}. {name}: {data['accuracy']:.4f} ({data['correct']}/{data['total']}, 实际选择: {selected})")
    
    print("\n✅ 完成！")


if __name__ == "__main__":
    main()

