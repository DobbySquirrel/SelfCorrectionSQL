"""
分析bucket_count和reward的相关性

从MCTS结果文件中提取：
1. CTE的bucket_count（路径上每个节点的bucket_count）
2. 最终reward（SQL执行的一致性奖励）
3. 分析它们之间的相关性
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple
from collections import defaultdict
import statistics

sys.path.append(str(Path(__file__).parent.parent.parent.parent))


def calculate_correlation(x_list: List[float], y_list: List[float]) -> Tuple[float, int]:
    """
    计算Pearson相关系数
    
    Returns:
        (correlation, sample_count)
    """
    if len(x_list) != len(y_list) or len(x_list) < 2:
        return 0.0, 0
    
    n = len(x_list)
    
    # 计算均值
    mean_x = sum(x_list) / n
    mean_y = sum(y_list) / n
    
    # 计算协方差和方差
    covariance = sum((x_list[i] - mean_x) * (y_list[i] - mean_y) for i in range(n))
    var_x = sum((x_list[i] - mean_x) ** 2 for i in range(n))
    var_y = sum((y_list[i] - mean_y) ** 2 for i in range(n))
    
    # 计算相关系数
    if var_x == 0 or var_y == 0:
        return 0.0, n
    
    correlation = covariance / ((var_x * var_y) ** 0.5)
    return correlation, n


def calculate_correlation_binary(x_list: List[float], y_list: List[int]) -> Tuple[float, int]:
    """
    计算连续变量和二元变量的相关性（点双列相关系数）
    
    Args:
        x_list: 连续变量列表（如bucket_count）
        y_list: 二元变量列表（0或1，如gold_match）
    
    Returns:
        (correlation, sample_count)
    """
    if len(x_list) != len(y_list) or len(x_list) < 2:
        return 0.0, 0
    
    n = len(x_list)
    
    # 计算均值
    mean_x = sum(x_list) / n
    mean_y = sum(y_list) / n
    
    # 如果y全为0或全为1，无法计算相关性
    if mean_y == 0.0 or mean_y == 1.0:
        return 0.0, n
    
    # 计算标准差
    std_x = (sum((x_list[i] - mean_x) ** 2 for i in range(n)) / n) ** 0.5
    if std_x == 0:
        return 0.0, n
    
    # 计算点双列相关系数
    covariance = sum((x_list[i] - mean_x) * (y_list[i] - mean_y) for i in range(n)) / n
    correlation = covariance / (std_x * (mean_y * (1 - mean_y)) ** 0.5)
    
    return correlation, n


def analyze_bucket_reward_correlation(result_file: str, qid: str = None) -> Dict[str, Any]:
    """
    分析bucket_count和reward/gold_match的相关性
    
    Returns:
        分析结果字典
    """
    with open(result_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    if qid:
        if qid not in data:
            print(f"❌ 未找到qid={qid}")
            return {}
        data = {qid: data[qid]}
    
    all_analyses = {}
    
    for qid, result in data.items():
        rollout_stats = result.get('rollout_stats', [])
        gold_match = result.get('stats', {}).get('gold_match', None)
        
        if not rollout_stats:
            continue
        
        # 收集数据
        rewards = []
        gold_matches = []  # 每个rollout的SQL是否匹配gold（需要从all_sql_variants中检查）
        avg_cte_bucket_counts = []  # 路径上CTE bucket_count的平均值
        max_cte_bucket_counts = []  # 路径上CTE bucket_count的最大值
        min_cte_bucket_counts = []  # 路径上CTE bucket_count的最小值
        total_cte_bucket_counts = []  # 路径上CTE bucket_count的总和
        sql_bucket_counts = []  # SQL的bucket_count（最大桶的计数）
        sql_num_buckets = []  # SQL的桶数量（多样性，不同结果的数量）
        cte_num_buckets = []  # CTE的桶数量（多样性，每个节点生成的不同结果数量）
        path_lengths = []  # 路径长度
        
        for rollout in rollout_stats:
            reward = rollout.get('reward', 0.0)
            cte_bucket_counts = rollout.get('cte_bucket_counts', [])
            sql_bucket_count = rollout.get('sql_bucket_count', 0)
            result_buckets = rollout.get('result_buckets', {})  # SQL结果分桶信息
            cte_buckets_per_node = rollout.get('cte_buckets_per_node', [])  # 每个节点的所有CTE桶信息
            
            if not cte_bucket_counts:
                continue
            
            rewards.append(reward)
            sql_bucket_counts.append(sql_bucket_count)
            # 计算SQL的桶数量（多样性）
            sql_num_buckets.append(len(result_buckets) if result_buckets else 0)
            
            # 计算CTE的桶数量（多样性）- 每个节点生成的不同结果数量
            node_bucket_counts = []
            for node_buckets in cte_buckets_per_node:
                if node_buckets:
                    # 计算该节点有多少个不同的结果（桶的数量）
                    node_bucket_counts.append(len(node_buckets))
            if node_bucket_counts:
                # 使用平均值或总和
                cte_num_buckets.append(statistics.mean(node_bucket_counts))
            else:
                cte_num_buckets.append(0)
            
            avg_cte_bucket_counts.append(statistics.mean(cte_bucket_counts))
            max_cte_bucket_counts.append(max(cte_bucket_counts))
            min_cte_bucket_counts.append(min(cte_bucket_counts))
            total_cte_bucket_counts.append(sum(cte_bucket_counts))
            path_lengths.append(len(cte_bucket_counts))
            
            # 检查该rollout是否有匹配gold的SQL
            # 方法1：检查all_sql_variants中是否有is_correct标记（如果之前分析过）
            rollout_has_correct_sql = False
            all_sql_variants = rollout.get('all_sql_variants', [])
            for sql_info in all_sql_variants:
                if sql_info.get('is_correct') is True:
                    rollout_has_correct_sql = True
                    break
            
            # 方法2：如果方法1没有结果，检查selected_sql是否和最终选择的SQL相同
            if not rollout_has_correct_sql and gold_match is not None:
                selected_sql = rollout.get('selected_sql', '')
                if selected_sql is None:
                    selected_sql = ''
                else:
                    selected_sql = selected_sql.strip()
                
                final_sql = result.get('sql', '')
                if final_sql is None:
                    final_sql = ''
                else:
                    final_sql = final_sql.strip()
                
                if selected_sql == final_sql:
                    rollout_has_correct_sql = (gold_match is True)
            
            if gold_match is not None:
                gold_matches.append(1 if rollout_has_correct_sql else 0)
            else:
                gold_matches.append(None)
        
        if len(rewards) < 2:
            continue
        
        # 计算reward相关性
        corr_avg_reward, n = calculate_correlation(avg_cte_bucket_counts, rewards)
        corr_max_reward, _ = calculate_correlation(max_cte_bucket_counts, rewards)
        corr_min_reward, _ = calculate_correlation(min_cte_bucket_counts, rewards)
        corr_total_reward, _ = calculate_correlation(total_cte_bucket_counts, rewards)
        corr_path_len_reward, _ = calculate_correlation(path_lengths, rewards)
        corr_sql_bucket_reward, _ = calculate_correlation(sql_bucket_counts, rewards)
        corr_sql_num_buckets_reward, _ = calculate_correlation(sql_num_buckets, rewards)  # SQL桶数量（多样性）vs reward
        corr_cte_num_buckets_reward, _ = calculate_correlation(cte_num_buckets, rewards)  # CTE桶数量（多样性）vs reward
        
        # 计算gold_match相关性（只对有gold_match信息的问题）
        corr_avg_gold = None
        corr_max_gold = None
        corr_min_gold = None
        corr_sql_bucket_gold = None
        corr_sql_num_buckets_gold = None
        corr_cte_num_buckets_gold = None
        
        if gold_match is not None:
            # 过滤掉None值
            valid_indices = [i for i, gm in enumerate(gold_matches) if gm is not None]
            if len(valid_indices) >= 2:
                valid_avg_bc = [avg_cte_bucket_counts[i] for i in valid_indices]
                valid_max_bc = [max_cte_bucket_counts[i] for i in valid_indices]
                valid_min_bc = [min_cte_bucket_counts[i] for i in valid_indices]
                valid_sql_bc = [sql_bucket_counts[i] for i in valid_indices]
                valid_sql_nb = [sql_num_buckets[i] for i in valid_indices]
                valid_cte_nb = [cte_num_buckets[i] for i in valid_indices]
                valid_gold = [gold_matches[i] for i in valid_indices]
                
                corr_avg_gold, _ = calculate_correlation_binary(valid_avg_bc, valid_gold)
                corr_max_gold, _ = calculate_correlation_binary(valid_max_bc, valid_gold)
                corr_min_gold, _ = calculate_correlation_binary(valid_min_bc, valid_gold)
                corr_sql_bucket_gold, _ = calculate_correlation_binary(valid_sql_bc, valid_gold)
                corr_sql_num_buckets_gold, _ = calculate_correlation_binary(valid_sql_nb, valid_gold)
                corr_cte_num_buckets_gold, _ = calculate_correlation_binary(valid_cte_nb, valid_gold)
        
        # 统计信息
        stats = {
            'num_rollouts': len(rewards),
            'avg_reward': statistics.mean(rewards),
            'avg_bucket_count': statistics.mean(avg_cte_bucket_counts),
            'max_bucket_count': max(max_cte_bucket_counts) if max_cte_bucket_counts else 0,
            'min_bucket_count': min(min_cte_bucket_counts) if min_cte_bucket_counts else 0,
            'avg_sql_bucket_count': statistics.mean(sql_bucket_counts) if sql_bucket_counts else 0,
            'avg_sql_num_buckets': statistics.mean(sql_num_buckets) if sql_num_buckets else 0,  # SQL桶数量（多样性）
            'avg_cte_num_buckets': statistics.mean(cte_num_buckets) if cte_num_buckets else 0,  # CTE桶数量（多样性）
            'gold_match': gold_match,
        }
        
        # 相关性分析
        correlations = {
            'avg_bucket_count_vs_reward': corr_avg_reward,
            'max_bucket_count_vs_reward': corr_max_reward,
            'min_bucket_count_vs_reward': corr_min_reward,
            'total_bucket_count_vs_reward': corr_total_reward,
            'path_length_vs_reward': corr_path_len_reward,
            'sql_bucket_count_vs_reward': corr_sql_bucket_reward,  # SQL最大桶计数 vs reward
            'sql_num_buckets_vs_reward': corr_sql_num_buckets_reward,  # SQL桶数量（多样性）vs reward
            'cte_num_buckets_vs_reward': corr_cte_num_buckets_reward,  # CTE桶数量（多样性）vs reward
            'avg_bucket_count_vs_gold_match': corr_avg_gold,
            'max_bucket_count_vs_gold_match': corr_max_gold,
            'min_bucket_count_vs_gold_match': corr_min_gold,
            'sql_bucket_count_vs_gold_match': corr_sql_bucket_gold,  # SQL最大桶计数 vs gold_match
            'sql_num_buckets_vs_gold_match': corr_sql_num_buckets_gold,  # SQL桶数量（多样性）vs gold_match
            'cte_num_buckets_vs_gold_match': corr_cte_num_buckets_gold,  # CTE桶数量（多样性）vs gold_match
        }
        
        # 按bucket_count分组分析reward
        bucket_groups_reward = defaultdict(list)
        for i, avg_bc in enumerate(avg_cte_bucket_counts):
            if avg_bc < 3:
                group = '<3'
            elif avg_bc < 5:
                group = '3-4'
            elif avg_bc < 7:
                group = '5-6'
            else:
                group = '>=7'
            bucket_groups_reward[group].append(rewards[i])
        
        group_stats_reward = {}
        for group, group_rewards in bucket_groups_reward.items():
            if group_rewards:
                group_stats_reward[group] = {
                    'count': len(group_rewards),
                    'avg_reward': statistics.mean(group_rewards),
                    'min_reward': min(group_rewards),
                    'max_reward': max(group_rewards),
                }
        
        # 按sql_bucket_count分组分析gold_match
        sql_bucket_groups_gold = defaultdict(lambda: {'correct': 0, 'incorrect': 0})
        for i, sql_bc in enumerate(sql_bucket_counts):
            if gold_matches[i] is not None:
                if sql_bc < 3:
                    group = '<3'
                elif sql_bc < 5:
                    group = '3-5'
                elif sql_bc < 7:
                    group = '5-7'
                else:
                    group = '>=7'
                if gold_matches[i] == 1:
                    sql_bucket_groups_gold[group]['correct'] += 1
                else:
                    sql_bucket_groups_gold[group]['incorrect'] += 1
        
        sql_bucket_group_stats = {}
        for group, counts in sql_bucket_groups_gold.items():
            total = counts['correct'] + counts['incorrect']
            if total > 0:
                sql_bucket_group_stats[group] = {
                    'count': total,
                    'correct': counts['correct'],
                    'incorrect': counts['incorrect'],
                    'accuracy': counts['correct'] / total,
                }
        
        all_analyses[qid] = {
            'stats': stats,
            'correlations': correlations,
            'group_stats_reward': group_stats_reward,
            'sql_bucket_group_stats': sql_bucket_group_stats,
            'raw_data': {
                'rewards': rewards,
                'avg_bucket_counts': avg_cte_bucket_counts,
                'max_bucket_counts': max_cte_bucket_counts,
                'sql_bucket_counts': sql_bucket_counts,
                'sql_num_buckets': sql_num_buckets,  # SQL桶数量（多样性）
                'cte_num_buckets': cte_num_buckets,  # CTE桶数量（多样性）
                'gold_matches': gold_matches,
            }
        }
    
    return all_analyses


def print_analysis_report(analyses: Dict[str, Any]):
    """打印分析报告"""
    print(f"\n{'='*80}")
    print("Bucket Count 与 Reward/Gold Match 相关性分析报告")
    print(f"{'='*80}\n")
    
    total_questions = len(analyses)
    if total_questions == 0:
        print("❌ 没有数据可分析")
        return
    
    # 汇总统计 - Reward相关性
    all_corr_avg_reward = []
    all_corr_max_reward = []
    all_corr_min_reward = []
    all_corr_sql_bucket_reward = []
    all_corr_sql_num_buckets_reward = []  # SQL桶数量（多样性）vs reward
    all_corr_cte_num_buckets_reward = []  # CTE桶数量（多样性）vs reward
    
    # 汇总统计 - Gold Match相关性
    all_corr_avg_gold = []
    all_corr_max_gold = []
    all_corr_sql_bucket_gold = []
    all_corr_sql_num_buckets_gold = []  # SQL桶数量（多样性）vs gold_match
    all_corr_cte_num_buckets_gold = []  # CTE桶数量（多样性）vs gold_match
    
    questions_with_gold = 0
    
    for qid, analysis in analyses.items():
        corr = analysis['correlations']
        all_corr_avg_reward.append(corr['avg_bucket_count_vs_reward'])
        all_corr_max_reward.append(corr['max_bucket_count_vs_reward'])
        all_corr_min_reward.append(corr['min_bucket_count_vs_reward'])
        all_corr_sql_bucket_reward.append(corr['sql_bucket_count_vs_reward'])
        all_corr_sql_num_buckets_reward.append(corr['sql_num_buckets_vs_reward'])
        all_corr_cte_num_buckets_reward.append(corr['cte_num_buckets_vs_reward'])
        
        if corr['avg_bucket_count_vs_gold_match'] is not None:
            all_corr_avg_gold.append(corr['avg_bucket_count_vs_gold_match'])
            all_corr_max_gold.append(corr['max_bucket_count_vs_gold_match'])
            if corr['sql_bucket_count_vs_gold_match'] is not None:
                all_corr_sql_bucket_gold.append(corr['sql_bucket_count_vs_gold_match'])
            if corr['sql_num_buckets_vs_gold_match'] is not None:
                all_corr_sql_num_buckets_gold.append(corr['sql_num_buckets_vs_gold_match'])
            if corr['cte_num_buckets_vs_gold_match'] is not None:
                all_corr_cte_num_buckets_gold.append(corr['cte_num_buckets_vs_gold_match'])
            questions_with_gold += 1
    
    print(f"【总体统计】")
    print(f"  分析问题数: {total_questions}")
    print(f"  有gold_match信息的问题数: {questions_with_gold}")
    print()
    
    print(f"【Bucket Count vs Reward 相关性】")
    print(f"  平均相关性（avg_bucket_count vs reward）: {statistics.mean(all_corr_avg_reward):.4f}")
    print(f"  平均相关性（max_bucket_count vs reward）: {statistics.mean(all_corr_max_reward):.4f}")
    print(f"  平均相关性（min_bucket_count vs reward）: {statistics.mean(all_corr_min_reward):.4f}")
    print(f"  平均相关性（sql_bucket_count vs reward）: {statistics.mean(all_corr_sql_bucket_reward):.4f}")
    print(f"  平均相关性（sql_num_buckets vs reward）: {statistics.mean(all_corr_sql_num_buckets_reward):.4f}  # SQL桶数量（多样性）")
    print(f"  平均相关性（cte_num_buckets vs reward）: {statistics.mean(all_corr_cte_num_buckets_reward):.4f}  # CTE桶数量（多样性）")
    print()
    
    if questions_with_gold > 0:
        print(f"【Bucket Count vs Gold Match 相关性】")
        print(f"  平均相关性（avg_bucket_count vs gold_match）: {statistics.mean(all_corr_avg_gold):.4f}")
        print(f"  平均相关性（max_bucket_count vs gold_match）: {statistics.mean(all_corr_max_gold):.4f}")
        if all_corr_sql_bucket_gold:
            print(f"  平均相关性（sql_bucket_count vs gold_match）: {statistics.mean(all_corr_sql_bucket_gold):.4f}")
        if all_corr_sql_num_buckets_gold:
            print(f"  平均相关性（sql_num_buckets vs gold_match）: {statistics.mean(all_corr_sql_num_buckets_gold):.4f}  # SQL桶数量（多样性）")
        if all_corr_cte_num_buckets_gold:
            print(f"  平均相关性（cte_num_buckets vs gold_match）: {statistics.mean(all_corr_cte_num_buckets_gold):.4f}  # CTE桶数量（多样性）")
        print()
    
    # 相关性强度判断 - Reward
    avg_corr_reward = statistics.mean(all_corr_avg_reward)
    if abs(avg_corr_reward) > 0.7:
        strength_reward = "强"
    elif abs(avg_corr_reward) > 0.5:
        strength_reward = "中等"
    elif abs(avg_corr_reward) > 0.3:
        strength_reward = "弱"
    else:
        strength_reward = "很弱/无"
    
    print(f"【相关性强度评估 - Reward】")
    print(f"  平均相关性: {avg_corr_reward:.4f} ({strength_reward})")
    if avg_corr_reward > 0.5:
        print(f"  ✅ 强正相关：bucket_count高的路径，reward也高")
    elif avg_corr_reward > 0.3:
        print(f"  ⚠️ 中等正相关：有一定相关性，但不够强")
    elif avg_corr_reward > 0:
        print(f"  ⚠️ 弱正相关：相关性较弱")
    else:
        print(f"  ❌ 无相关性或负相关")
    print()
    
    # 相关性强度判断 - Gold Match
    if questions_with_gold > 0:
        avg_corr_gold = statistics.mean(all_corr_avg_gold)
        if abs(avg_corr_gold) > 0.7:
            strength_gold = "强"
        elif abs(avg_corr_gold) > 0.5:
            strength_gold = "中等"
        elif abs(avg_corr_gold) > 0.3:
            strength_gold = "弱"
        else:
            strength_gold = "很弱/无"
        
        print(f"【相关性强度评估 - Gold Match】")
        print(f"  平均相关性: {avg_corr_gold:.4f} ({strength_gold})")
        if avg_corr_gold > 0.5:
            print(f"  ✅ 强正相关：bucket_count高的路径，更可能匹配gold SQL")
        elif avg_corr_gold > 0.3:
            print(f"  ⚠️ 中等正相关：有一定相关性")
        elif avg_corr_gold > 0:
            print(f"  ⚠️ 弱正相关：相关性较弱")
        else:
            print(f"  ❌ 无相关性或负相关")
        print()
        
        # SQL bucket_count vs gold_match的统计
        if all_corr_sql_bucket_gold:
            avg_corr_sql_gold = statistics.mean(all_corr_sql_bucket_gold)
            print(f"【SQL Bucket Count vs Gold Match】")
            print(f"  平均相关性: {avg_corr_sql_gold:.4f}")
            if avg_corr_sql_gold > 0.5:
                print(f"  ✅ 强正相关：sql_bucket_count高的rollout，更可能匹配gold SQL")
            elif avg_corr_sql_gold > 0.3:
                print(f"  ⚠️ 中等正相关：有一定相关性")
            else:
                print(f"  ⚠️ 相关性较弱或无相关性")
            print()
    
    # 显示前10个问题的详细分析
    print(f"【前10个问题的详细分析】")
    print("-" * 80)
    
    count = 0
    for qid, analysis in list(analyses.items())[:10]:
        stats = analysis['stats']
        corr = analysis['correlations']
        group_stats_reward = analysis.get('group_stats_reward', {})
        sql_bucket_group_stats = analysis.get('sql_bucket_group_stats', {})
        
        print(f"\n问题 {qid}:")
        print(f"  Rollout数: {stats['num_rollouts']}")
        print(f"  平均Reward: {stats['avg_reward']:.4f}")
        print(f"  平均Bucket Count: {stats['avg_bucket_count']:.2f}  # CTE最大桶计数")
        print(f"  平均SQL Bucket Count: {stats['avg_sql_bucket_count']:.2f}  # SQL最大桶计数")
        print(f"  平均SQL桶数量（多样性）: {stats['avg_sql_num_buckets']:.2f}  # 不同结果的数量")
        print(f"  平均CTE桶数量（多样性）: {stats['avg_cte_num_buckets']:.2f}  # 不同结果的数量")
        print(f"  Gold Match: {stats['gold_match']}")
        print(f"  相关性（avg_bucket_count vs reward）: {corr['avg_bucket_count_vs_reward']:.4f}")
        print(f"  相关性（max_bucket_count vs reward）: {corr['max_bucket_count_vs_reward']:.4f}")
        print(f"  相关性（sql_num_buckets vs reward）: {corr['sql_num_buckets_vs_reward']:.4f}  # SQL桶数量（多样性）")
        if corr['avg_bucket_count_vs_gold_match'] is not None:
            print(f"  相关性（avg_bucket_count vs gold_match）: {corr['avg_bucket_count_vs_gold_match']:.4f}")
        if corr['sql_bucket_count_vs_gold_match'] is not None:
            print(f"  相关性（sql_bucket_count vs gold_match）: {corr['sql_bucket_count_vs_gold_match']:.4f}")
        if corr['sql_num_buckets_vs_gold_match'] is not None:
            print(f"  相关性（sql_num_buckets vs gold_match）: {corr['sql_num_buckets_vs_gold_match']:.4f}  # SQL桶数量（多样性）")
        
        if group_stats_reward:
            print(f"  按CTE Bucket Count分组的Reward统计:")
            for group, gs in sorted(group_stats_reward.items()):
                print(f"    {group}: count={gs['count']}, avg_reward={gs['avg_reward']:.4f}")
        
        if sql_bucket_group_stats:
            print(f"  按SQL Bucket Count分组的Gold Match统计:")
            for group, gs in sorted(sql_bucket_group_stats.items()):
                print(f"    {group}: count={gs['count']}, correct={gs['correct']}, accuracy={gs['accuracy']:.2%}")
        
        count += 1
        if count >= 10:
            break
    
    print()
    
    # 汇总SQL bucket_count vs gold_match的统计
    if questions_with_gold > 0:
        print(f"【SQL Bucket Count vs Gold Match 汇总统计】")
        print("-" * 80)
        
        # 收集所有问题的SQL bucket_count分组统计
        all_sql_bucket_groups = defaultdict(lambda: {'correct': 0, 'incorrect': 0})
        for qid, analysis in analyses.items():
            sql_bucket_group_stats = analysis.get('sql_bucket_group_stats', {})
            for group, stats in sql_bucket_group_stats.items():
                all_sql_bucket_groups[group]['correct'] += stats['correct']
                all_sql_bucket_groups[group]['incorrect'] += stats['incorrect']
        
        if all_sql_bucket_groups:
            print("  按SQL Bucket Count分组的总体准确率:")
            for group in sorted(all_sql_bucket_groups.keys()):
                counts = all_sql_bucket_groups[group]
                total = counts['correct'] + counts['incorrect']
                if total > 0:
                    accuracy = counts['correct'] / total
                    print(f"    {group}: {counts['correct']}/{total} = {accuracy:.2%}")
        print()
    
    # 建议
    print(f"【建议】")
    print("-" * 80)
    
    if questions_with_gold > 0:
        avg_corr_gold = statistics.mean(all_corr_avg_gold)
        if all_corr_sql_bucket_gold:
            avg_corr_sql_gold = statistics.mean(all_corr_sql_bucket_gold)
        else:
            avg_corr_sql_gold = 0.0
        
        print("基于Gold Match相关性:")
        if avg_corr_sql_gold > 0.5:
            print("  ✅ SQL bucket_count与gold_match强相关，可以考虑在SQL选择时优先考虑高bucket_count")
        elif avg_corr_sql_gold > 0.3:
            print("  ⚠️ SQL bucket_count与gold_match中等相关，可以作为辅助因素")
        else:
            print("  ❌ SQL bucket_count与gold_match相关性弱，不建议过度依赖")
        print()
        
        if avg_corr_gold > 0.5:
            print("基于CTE bucket_count vs gold_match相关性:")
            print("  ✅ CTE bucket_count与gold_match强相关，可以考虑在UCB1中引入")
        elif avg_corr_gold > 0.3:
            print("  ⚠️ CTE bucket_count与gold_match中等相关，可以谨慎尝试")
        else:
            print("  ❌ CTE bucket_count与gold_match相关性弱，不建议引入到UCB1")
        print()
    
    print("基于Reward相关性:")
    avg_corr_reward = statistics.mean(all_corr_avg_reward)
    if avg_corr_reward > 0.5:
        print("  ✅ 建议引入bucket_count到UCB1计算中")
        print("     推荐方案：作为衰减的先验信息（方案A）")
    elif avg_corr_reward > 0.3:
        print("  ⚠️ 可以尝试引入，但需要谨慎")
        print("     推荐方案：只在早期使用（方案B）")
    else:
        print("  ❌ 不建议引入bucket_count到UCB1计算中")
        print("     - 相关性太弱，可能引入偏差")
        print("     - 保持MCTS的标准做法：只基于实际探索结果")
    print()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="分析bucket_count和reward的相关性")
    parser.add_argument("--result_file", type=str, required=True, help="结果JSON文件")
    parser.add_argument("--qid", type=str, default=None, help="要分析的问题ID（不指定则分析全部）")
    args = parser.parse_args()
    
    analyses = analyze_bucket_reward_correlation(args.result_file, args.qid)
    print_analysis_report(analyses)


if __name__ == "__main__":
    main()
