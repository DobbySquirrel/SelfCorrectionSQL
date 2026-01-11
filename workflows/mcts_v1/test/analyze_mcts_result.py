"""
MCTS结果分析工具

分析MCTS生成的结果，检查：
1. 是否生成了正确的SQL（在所有SQL变体中查找）
2. 选择的SQL是否正确
3. visit_counts的逻辑是否正确
4. 节点选择是否合理
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import re

# 添加项目路径
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from workflows.mcts_v1.core.database_connector import DatabaseConnector


def normalize_sql(sql: str) -> str:
    """标准化SQL字符串（去除多余空格）"""
    if not sql:
        return ""
    return ' '.join(sql.split())


def compare_sql_results(sql1: str, sql2: str, db_connector: DatabaseConnector) -> bool:
    """
    比较两个SQL的执行结果是否相同
    
    Returns:
        bool: 如果结果匹配则为True
    """
    try:
        result1, error1 = db_connector.execute_query(sql1)
        result2, error2 = db_connector.execute_query(sql2)
        
        if error1 or error2:
            return False
        
        if result1 is None or result2 is None:
            return False
        
        import pandas as pd
        import numpy as np
        
        def normalize_result(result):
            """将结果标准化为字典列表格式"""
            if result is None:
                return []
            if isinstance(result, pd.DataFrame):
                return result.to_dict('records')
            if isinstance(result, list):
                if result and isinstance(result[0], dict):
                    return result
                if result and isinstance(result[0], (tuple, list)):
                    columns = result.columns if hasattr(result, 'columns') else [f'col_{i}' for i in range(len(result[0]))]
                    return [dict(zip(columns, row)) for row in result]
            return []
        
        def normalize_row(row):
            """标准化行数据"""
            values = []
            for k, v in row.items():
                if pd.isna(v) or v is None:
                    values.append(None)
                elif isinstance(v, (np.integer, np.floating)):
                    values.append(float(v) if isinstance(v, float) else int(v))
                elif isinstance(v, (int, float)):
                    values.append(float(v) if isinstance(v, float) else int(v))
                else:
                    values.append(str(v).strip().lower())
            values.sort(key=lambda x: (
                0 if x is None else 1,
                str(type(x).__name__),
                str(x) if x is None else ''
            ))
            return tuple(values)
        
        result1_norm = normalize_result(result1)
        result2_norm = normalize_result(result2)
        
        if len(result1_norm) == 0 and len(result2_norm) == 0:
            return True
        
        set1 = {normalize_row(row) for row in result1_norm}
        set2 = {normalize_row(row) for row in result2_norm}
        
        return set1 == set2
    except Exception as e:
        print(f"  ⚠️ SQL比较出错: {e}")
        return False


def analyze_visit_counts(rollout_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    分析visit_counts的逻辑
    
    检查：
    1. visit_counts是否随着rollout增加而增加
    2. 路径上的visit_counts是否合理
    3. 节点选择是否遵循UCB1逻辑
    """
    analysis = {
        'total_rollouts': len(rollout_stats),
        'visit_count_trends': [],  # 每个节点的访问次数趋势
        'path_visit_counts': [],  # 每个rollout路径上的visit_counts
        'issues': []
    }
    
    # 收集所有路径上的节点访问次数
    node_visit_map = defaultdict(list)  # {node_signature: [visit_count1, visit_count2, ...]}
    
    for rollout_id, rollout in enumerate(rollout_stats, 1):
        visit_counts = rollout.get('visit_counts', [])
        cte_path = rollout.get('cte_path', [])
        reward = rollout.get('reward', 0.0)
        
        analysis['path_visit_counts'].append({
            'rollout_id': rollout_id,
            'visit_counts': visit_counts,
            'path_length': len(cte_path),
            'reward': reward
        })
        
        # 记录每个节点的访问次数
        for i, cte in enumerate(cte_path):
            node_sig = f"depth_{i}_{hash(cte) % 10000}"
            if i < len(visit_counts):
                node_visit_map[node_sig].append({
                    'rollout_id': rollout_id,
                    'visit_count': visit_counts[i],
                    'depth': i
                })
    
    # 检查visit_counts是否递增
    for node_sig, visits in node_visit_map.items():
        if len(visits) > 1:
            visit_nums = [v['visit_count'] for v in visits]
            # 检查是否单调递增（允许相等，但不应该减少）
            for i in range(1, len(visit_nums)):
                if visit_nums[i] < visit_nums[i-1]:
                    analysis['issues'].append(
                        f"节点 {node_sig} 的visit_count在rollout {visits[i]['rollout_id']} "
                        f"时减少: {visit_nums[i-1]} -> {visit_nums[i]}"
                    )
    
    # 检查路径上的visit_counts是否合理
    for path_info in analysis['path_visit_counts']:
        visit_counts = path_info['visit_counts']
        if len(visit_counts) > 1:
            # 通常，深度越深，visit_count应该越小（因为越深的节点被访问的次数越少）
            # 但这不是绝对的，因为MCTS可能会重点探索某些路径
            pass  # 这里可以添加更详细的分析
    
    return analysis


def analyze_sql_generation(result_data: Dict[str, Any], db_connector: DatabaseConnector) -> Dict[str, Any]:
    """
    分析SQL生成情况
    
    检查：
    1. 所有生成的SQL中是否有正确的SQL
    2. 选择的SQL是否正确
    3. 选择逻辑是否合理
    """
    analysis = {
        'gold_sql': result_data.get('stats', {}).get('gold_sql', ''),
        'selected_sql': result_data.get('sql', ''),
        'gold_match': result_data.get('stats', {}).get('gold_match', None),
        'all_sqls_analysis': [],
        'correct_sqls_found': [],
        'selection_analysis': {},
        'issues': []
    }
    
    gold_sql = analysis['gold_sql']
    selected_sql = analysis['selected_sql']
    
    if not gold_sql:
        analysis['issues'].append("没有gold_sql，无法进行对比分析")
        return analysis
    
    # 分析所有rollout中的SQL
    rollout_stats = result_data.get('rollout_stats', [])
    all_sqls = set()
    correct_sqls = []
    
    for rollout_id, rollout in enumerate(rollout_stats, 1):
        all_sql_variants = rollout.get('all_sql_variants', [])
        selected_sql_in_rollout = rollout.get('selected_sql', '')
        reward = rollout.get('reward', 0.0)
        
        rollout_correct_sqls = []
        for sql_info in all_sql_variants:
            sql = sql_info.get('sql', '')
            if sql:
                sql_norm = normalize_sql(sql)
                all_sqls.add(sql_norm)
                
                # 检查是否与gold_sql匹配
                if compare_sql_results(sql, gold_sql, db_connector):
                    if sql_norm not in [s['sql_normalized'] for s in correct_sqls]:
                        correct_sqls.append({
                            'sql': sql,
                            'sql_normalized': sql_norm,
                            'rollout_id': rollout_id,
                            'valid': sql_info.get('valid', False),
                            'result_signature': sql_info.get('result_signature', ''),
                            'reward': reward
                        })
                        rollout_correct_sqls.append(sql_norm)
        
        analysis['all_sqls_analysis'].append({
            'rollout_id': rollout_id,
            'total_sqls': len(all_sql_variants),
            'valid_sqls': sum(1 for s in all_sql_variants if s.get('valid', False)),
            'correct_sqls': len(rollout_correct_sqls),
            'selected_sql': selected_sql_in_rollout,
            'selected_sql_correct': normalize_sql(selected_sql_in_rollout) in rollout_correct_sqls if selected_sql_in_rollout else False,
            'reward': reward
        })
    
    analysis['correct_sqls_found'] = correct_sqls
    
    # 分析选择逻辑
    selected_sql_norm = normalize_sql(selected_sql)
    selected_sql_correct = compare_sql_results(selected_sql, gold_sql, db_connector) if selected_sql else False
    
    # 检查选择的SQL是否在所有生成的SQL中
    selected_sql_in_all = selected_sql_norm in all_sqls if selected_sql else False
    
    # 找到选择该SQL的rollout
    selected_rollout = None
    for rollout in rollout_stats:
        if normalize_sql(rollout.get('selected_sql', '')) == selected_sql_norm:
            selected_rollout = rollout
            break
    
    analysis['selection_analysis'] = {
        'selected_sql_correct': selected_sql_correct,
        'selected_sql_in_all_generated': selected_sql_in_all,
        'selected_rollout_reward': selected_rollout.get('reward', 0.0) if selected_rollout else None,
        'selected_rollout_id': None,
        'max_reward_rollout': None,
        'max_reward': -1.0
    }
    
    if selected_rollout:
        for i, rollout in enumerate(rollout_stats, 1):
            if rollout == selected_rollout:
                analysis['selection_analysis']['selected_rollout_id'] = i
                break
    
    # 找到reward最高的rollout
    max_reward = -1.0
    max_reward_rollout = None
    for i, rollout in enumerate(rollout_stats, 1):
        reward = rollout.get('reward', 0.0)
        if reward > max_reward:
            max_reward = reward
            max_reward_rollout = i
    
    analysis['selection_analysis']['max_reward'] = max_reward
    analysis['selection_analysis']['max_reward_rollout'] = max_reward_rollout
    
    # 检查是否有问题
    if correct_sqls and not selected_sql_correct:
        analysis['issues'].append(
            f"❌ 生成了正确的SQL但没有选择它！共找到 {len(correct_sqls)} 个正确的SQL变体"
        )
    
    if not correct_sqls:
        analysis['issues'].append("❌ 没有生成任何正确的SQL")
    
    if selected_sql and not selected_sql_in_all:
        analysis['issues'].append("⚠️ 选择的SQL不在所有生成的SQL变体中（可能是快速路径）")
    
    return analysis


def print_analysis_report(analysis: Dict[str, Any], qid: str):
    """打印分析报告"""
    print(f"\n{'='*80}")
    print(f"MCTS结果分析报告 - Question ID: {qid}")
    print(f"{'='*80}\n")
    
    # 1. SQL生成分析
    print("【1. SQL生成分析】")
    print("-" * 80)
    
    sql_analysis = analysis.get('sql_analysis', {})
    gold_sql = sql_analysis.get('gold_sql', '')
    selected_sql = sql_analysis.get('selected_sql', '')
    gold_match = sql_analysis.get('gold_match', None)
    
    print(f"Gold SQL: {gold_sql[:200]}..." if len(gold_sql) > 200 else f"Gold SQL: {gold_sql}")
    print(f"Selected SQL: {selected_sql[:200]}..." if len(selected_sql) > 200 else f"Selected SQL: {selected_sql}")
    print(f"Gold Match: {gold_match}")
    print()
    
    correct_sqls = sql_analysis.get('correct_sqls_found', [])
    print(f"✅ 找到 {len(correct_sqls)} 个正确的SQL变体:")
    for i, sql_info in enumerate(correct_sqls[:5], 1):  # 只显示前5个
        print(f"  {i}. Rollout {sql_info['rollout_id']}, Reward: {sql_info['reward']:.4f}")
        print(f"     SQL: {sql_info['sql'][:150]}...")
    
    if len(correct_sqls) > 5:
        print(f"  ... 还有 {len(correct_sqls) - 5} 个正确的SQL变体")
    print()
    
    # 2. 选择逻辑分析
    print("【2. 选择逻辑分析】")
    print("-" * 80)
    
    selection = sql_analysis.get('selection_analysis', {})
    print(f"选择的SQL是否正确: {selection.get('selected_sql_correct', False)}")
    print(f"选择的SQL是否在所有生成的SQL中: {selection.get('selected_sql_in_all_generated', False)}")
    print(f"选择该SQL的Rollout ID: {selection.get('selected_rollout_id', 'N/A')}")
    print(f"选择该SQL的Rollout Reward: {selection.get('selected_rollout_reward', 'N/A')}")
    print(f"最高Reward的Rollout ID: {selection.get('max_reward_rollout', 'N/A')}")
    print(f"最高Reward: {selection.get('max_reward', 'N/A')}")
    print()
    
    # 3. 每个Rollout的SQL生成情况
    print("【3. 每个Rollout的SQL生成情况】")
    print("-" * 80)
    
    all_sqls_analysis = sql_analysis.get('all_sqls_analysis', [])
    for rollout_info in all_sqls_analysis:
        rollout_id = rollout_info['rollout_id']
        print(f"Rollout {rollout_id}:")
        print(f"  总SQL数: {rollout_info['total_sqls']}")
        print(f"  有效SQL数: {rollout_info['valid_sqls']}")
        print(f"  正确SQL数: {rollout_info['correct_sqls']}")
        print(f"  Reward: {rollout_info['reward']:.4f}")
        print(f"  选择的SQL是否正确: {rollout_info['selected_sql_correct']}")
        if rollout_info['selected_sql']:
            print(f"  选择的SQL: {rollout_info['selected_sql'][:100]}...")
        print()
    
    # 4. Visit Counts分析
    print("【4. Visit Counts分析】")
    print("-" * 80)
    
    visit_analysis = analysis.get('visit_analysis', {})
    print(f"总Rollout数: {visit_analysis.get('total_rollouts', 0)}")
    
    issues = visit_analysis.get('issues', [])
    if issues:
        print(f"⚠️ 发现 {len(issues)} 个问题:")
        for issue in issues[:10]:  # 只显示前10个
            print(f"  - {issue}")
        if len(issues) > 10:
            print(f"  ... 还有 {len(issues) - 10} 个问题")
    else:
        print("✅ Visit counts逻辑正常")
    print()
    
    # 显示路径上的visit_counts趋势
    path_visit_counts = visit_analysis.get('path_visit_counts', [])
    if path_visit_counts:
        print("路径上的Visit Counts趋势（前5个rollout）:")
        for path_info in path_visit_counts[:5]:
            print(f"  Rollout {path_info['rollout_id']}: "
                  f"visit_counts={path_info['visit_counts']}, "
                  f"reward={path_info['reward']:.4f}")
    print()
    
    # 5. 问题总结
    print("【5. 问题总结】")
    print("-" * 80)
    
    all_issues = sql_analysis.get('issues', []) + visit_analysis.get('issues', [])
    if all_issues:
        for issue in all_issues:
            print(f"  {issue}")
    else:
        print("  ✅ 未发现明显问题")
    print()


def main():
    parser = argparse.ArgumentParser(description="分析MCTS生成的结果")
    parser.add_argument("--result_file", type=str, required=True, help="结果JSON文件路径")
    parser.add_argument("--ppl_file", type=str, default=None, help="原始样本文件路径（用于获取数据库名称）")
    parser.add_argument("--db_name", type=str, default=None, help="数据库名称（如果提供则优先使用）")
    parser.add_argument("--qid", type=str, default=None, help="要分析的question_id（如果结果文件包含多个问题）")
    args = parser.parse_args()
    
    # 加载结果文件
    with open(args.result_file, 'r', encoding='utf-8') as f:
        result_data = json.load(f)
    
    # 如果结果文件包含多个问题，选择指定的qid
    if args.qid:
        if args.qid not in result_data:
            print(f"❌ 错误: 结果文件中没有找到 question_id={args.qid}")
            return
        result_data = result_data[args.qid]
        qid = args.qid
    else:
        # 如果结果文件只包含一个问题的数据
        if len(result_data) == 1:
            qid = list(result_data.keys())[0]
            result_data = result_data[qid]
        else:
            print("❌ 错误: 结果文件包含多个问题，请使用 --qid 指定要分析的问题")
            print(f"可用的question_id: {list(result_data.keys())}")
            return
    
    # 获取数据库名称
    db_name = args.db_name
    if not db_name and args.ppl_file:
        # 尝试从ppl文件中查找
        try:
            with open(args.ppl_file, 'r', encoding='utf-8') as f:
                ppls = json.load(f)
            for sample in ppls:
                if str(sample.get('question_id', '')) == qid:
                    db_name = sample.get('db', '')
                    if db_name:
                        print(f"✅ 从ppl文件中找到数据库名称: {db_name}")
                        break
        except Exception as e:
            print(f"⚠️ 从ppl文件读取失败: {e}")
    
    if not db_name:
        print("❌ 错误: 无法确定数据库名称")
        print("请使用以下方式之一提供数据库名称:")
        print("  1. --db_name 参数")
        print("  2. --ppl_file 参数（程序会自动查找）")
        return
    
    # 构建数据库连接器
    db_connector = DatabaseConnector(db_name)
    if not db_connector.connect():
        print(f"❌ 数据库连接失败: {db_connector.db_path}")
        return
    
    try:
        # 执行分析
        print("开始分析...")
        
        # SQL生成分析
        sql_analysis = analyze_sql_generation(result_data, db_connector)
        
        # Visit Counts分析
        rollout_stats = result_data.get('rollout_stats', [])
        visit_analysis = analyze_visit_counts(rollout_stats)
        
        # 合并分析结果
        analysis = {
            'qid': qid,
            'sql_analysis': sql_analysis,
            'visit_analysis': visit_analysis
        }
        
        # 打印报告
        print_analysis_report(analysis, qid)
        
    finally:
        db_connector.disconnect()


if __name__ == "__main__":
    main()
