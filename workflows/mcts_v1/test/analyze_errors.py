"""
错误分析脚本：深入分析MCTS的错误情况

分析两类错误：
1. 生成对的但没选择 (missed_opportunities)
2. 都没生成对的 (no_match_generated)
"""

import json
import sys
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

sys.path.append(str(Path(__file__).parent.parent.parent.parent))


def load_evaluation_results(eval_file: str) -> Dict:
    """加载评估结果文件"""
    print(f"[加载] 正在加载评估结果: {eval_file}")
    with open(eval_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data


def load_result_file(result_file: str) -> Dict:
    """加载原始结果文件"""
    print(f"[加载] 正在加载原始结果: {result_file}")
    with open(result_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data


def analyze_missed_opportunities(evaluation_results: List[Dict], result_data: Dict) -> Dict:
    """
    分析"生成对的但没选择"的情况
    
    重点关注：
    1. 为什么selected SQL的reward和匹配SQL的reward相同，但还是没选到？
    2. selected SQL和匹配SQL的reward差异
    3. 匹配SQL来自哪个rollout
    """
    missed_cases = []
    
    for eval_result in evaluation_results:
        question_id = eval_result['question_id']
        selected_match = eval_result.get('selected_match', False)
        has_match = eval_result.get('has_match', False)
        
        # 只分析"生成对的但没选择"的情况
        if not selected_match and has_match:
            matches = eval_result.get('matches', [])
            
            # 找到selected SQL
            selected_sql_info = next((m for m in matches if m.get('source') == 'selected'), None)
            
            # 找到所有匹配gold的SQL
            matching_sqls = [m for m in matches if m.get('matches_gold', False)]
            
            if matching_sqls and selected_sql_info:
                # 按reward排序匹配的SQL
                matching_sqls_sorted = sorted(matching_sqls, key=lambda x: x.get('reward', 0.0), reverse=True)
                best_match = matching_sqls_sorted[0]
                
                # 获取原始结果数据
                original_result = result_data.get(question_id, {})
                rollout_stats = original_result.get('rollout_stats', [])
                
                missed_cases.append({
                    'question_id': question_id,
                    'selected_sql': selected_sql_info['sql'],
                    'selected_reward': selected_sql_info.get('reward', 0.0),
                    'selected_source': selected_sql_info.get('source', 'unknown'),
                    'best_match_sql': best_match['sql'],
                    'best_match_reward': best_match.get('reward', 0.0),
                    'best_match_source': best_match.get('source', 'unknown'),
                    'reward_gap': best_match.get('reward', 0.0) - selected_sql_info.get('reward', 0.0),
                    'total_matching_sqls': len(matching_sqls),
                    'total_rollouts': len(rollout_stats),
                    'all_matching_sqls': [
                        {
                            'sql': m['sql'][:200] + '...' if len(m['sql']) > 200 else m['sql'],
                            'reward': m.get('reward', 0.0),
                            'source': m.get('source', 'unknown')
                        }
                        for m in matching_sqls_sorted[:5]  # 只保留前5个
                    ]
                })
    
    return {
        'total_count': len(missed_cases),
        'cases': missed_cases
    }


def analyze_no_match_generated(evaluation_results: List[Dict], result_data: Dict) -> Dict:
    """
    分析"都没生成对的"的情况
    
    重点关注：
    1. 生成了多少个SQL变体
    2. 所有SQL的reward分布
    3. 是否有接近正确的SQL（reward较高但不对）
    4. 可能的原因：SQL生成问题、reward计算问题等
    """
    no_match_cases = []
    
    for eval_result in evaluation_results:
        question_id = eval_result['question_id']
        selected_match = eval_result.get('selected_match', False)
        has_match = eval_result.get('has_match', False)
        
        # 只分析"都没生成对的"的情况
        if not selected_match and not has_match:
            matches = eval_result.get('matches', [])
            total_sqls = eval_result.get('total_sqls', 0)
            
            # 获取原始结果数据
            original_result = result_data.get(question_id, {})
            rollout_stats = original_result.get('rollout_stats', [])
            stats = original_result.get('stats', {})
            gold_sql = stats.get('gold_sql', '')
            
            # 分析reward分布
            rewards = [m.get('reward', 0.0) for m in matches]
            max_reward = max(rewards) if rewards else 0.0
            avg_reward = sum(rewards) / len(rewards) if rewards else 0.0
            
            # 找到reward最高的SQL（即使不匹配）
            if matches:
                best_sql_info = max(matches, key=lambda x: x.get('reward', 0.0))
            else:
                best_sql_info = None
            
            no_match_cases.append({
                'question_id': question_id,
                'total_sqls': total_sqls,
                'total_rollouts': len(rollout_stats),
                'selected_sql': matches[0]['sql'] if matches and matches[0].get('source') == 'selected' else None,
                'selected_reward': matches[0].get('reward', 0.0) if matches and matches[0].get('source') == 'selected' else 0.0,
                'max_reward': max_reward,
                'avg_reward': avg_reward,
                'best_sql': best_sql_info['sql'] if best_sql_info else None,
                'best_reward': best_sql_info.get('reward', 0.0) if best_sql_info else 0.0,
                'best_source': best_sql_info.get('source', 'unknown') if best_sql_info else 'unknown',
                'gold_sql': gold_sql[:200] + '...' if len(gold_sql) > 200 else gold_sql,
                'reward_distribution': {
                    'high_reward_count': sum(1 for r in rewards if r >= 0.8),
                    'medium_reward_count': sum(1 for r in rewards if 0.5 <= r < 0.8),
                    'low_reward_count': sum(1 for r in rewards if r < 0.5)
                }
            })
    
    return {
        'total_count': len(no_match_cases),
        'cases': no_match_cases
    }


def print_analysis_summary(missed_analysis: Dict, no_match_analysis: Dict):
    """打印分析摘要"""
    print("\n" + "=" * 80)
    print("错误分析摘要")
    print("=" * 80)
    
    print(f"\n1. 生成对的但没选择的情况: {missed_analysis['total_count']} 个")
    if missed_analysis['total_count'] > 0:
        # 按reward gap排序
        cases_sorted = sorted(missed_analysis['cases'], key=lambda x: abs(x['reward_gap']), reverse=True)
        
        print(f"\n   前5个案例（按reward差异排序）:")
        for i, case in enumerate(cases_sorted[:5], 1):
            print(f"\n   案例 {i}: 问题 {case['question_id']}")
            print(f"   - Selected reward: {case['selected_reward']:.4f}")
            print(f"   - Best match reward: {case['best_match_reward']:.4f}")
            print(f"   - Reward差异: {case['reward_gap']:.4f}")
            print(f"   - Best match来源: {case['best_match_source']}")
            print(f"   - 匹配SQL数量: {case['total_matching_sqls']}")
            print(f"   - Rollout数量: {case['total_rollouts']}")
        
        # 统计reward相同但没选到的情况
        same_reward_count = sum(1 for c in missed_analysis['cases'] 
                                if abs(c['reward_gap']) < 0.001)
        print(f"\n   ⚠️  发现 {same_reward_count} 个reward相同但没选到的情况（可能是选择策略bug）")
    
    print(f"\n2. 都没生成对的情况: {no_match_analysis['total_count']} 个")
    if no_match_analysis['total_count'] > 0:
        # 按max_reward排序
        cases_sorted = sorted(no_match_analysis['cases'], key=lambda x: x['max_reward'], reverse=True)
        
        print(f"\n   前5个案例（按最高reward排序）:")
        for i, case in enumerate(cases_sorted[:5], 1):
            print(f"\n   案例 {i}: 问题 {case['question_id']}")
            print(f"   - 总SQL数: {case['total_sqls']}")
            print(f"   - Rollout数量: {case['total_rollouts']}")
            print(f"   - 最高reward: {case['max_reward']:.4f}")
            print(f"   - 平均reward: {case['avg_reward']:.4f}")
            print(f"   - Reward分布: 高({case['reward_distribution']['high_reward_count']}) "
                  f"中({case['reward_distribution']['medium_reward_count']}) "
                  f"低({case['reward_distribution']['low_reward_count']})")
        
        # 统计高reward但不对的情况
        high_reward_wrong = sum(1 for c in no_match_analysis['cases'] 
                                if c['max_reward'] >= 0.8)
        print(f"\n   ⚠️  发现 {high_reward_wrong} 个最高reward>=0.8但不对的情况（可能是reward计算问题）")


def save_detailed_analysis(missed_analysis: Dict, no_match_analysis: Dict, output_file: str):
    """保存详细分析结果到JSON文件"""
    output = {
        'missed_opportunities_analysis': missed_analysis,
        'no_match_generated_analysis': no_match_analysis
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n[保存] 详细分析结果已保存到: {output_file}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='分析MCTS错误情况')
    parser.add_argument('--eval_file', type=str, required=True,
                        help='评估结果文件路径')
    parser.add_argument('--result_file', type=str, required=True,
                        help='原始结果文件路径')
    parser.add_argument('--output_file', type=str, default=None,
                        help='输出分析结果文件路径（可选）')
    
    args = parser.parse_args()
    
    # 加载数据
    eval_data = load_evaluation_results(args.eval_file)
    result_data = load_result_file(args.result_file)
    
    evaluation_results = eval_data.get('evaluation_results', [])
    
    # 分析两类错误
    print("\n" + "=" * 80)
    print("开始分析错误...")
    print("=" * 80)
    
    missed_analysis = analyze_missed_opportunities(evaluation_results, result_data)
    no_match_analysis = analyze_no_match_generated(evaluation_results, result_data)
    
    # 打印摘要
    print_analysis_summary(missed_analysis, no_match_analysis)
    
    # 保存详细结果
    if args.output_file:
        save_detailed_analysis(missed_analysis, no_match_analysis, args.output_file)
    else:
        default_output = args.eval_file.replace('_evaluation.json', '_error_analysis.json')
        save_detailed_analysis(missed_analysis, no_match_analysis, default_output)
    
    print("\n" + "=" * 80)
    print("分析完成！")
    print("=" * 80)
    
    # 给出建议
    print("\n" + "=" * 80)
    print("建议")
    print("=" * 80)
    print("\n基于分析结果，建议优先处理：")
    
    if missed_analysis['total_count'] > 0:
        same_reward_count = sum(1 for c in missed_analysis['cases'] 
                                if abs(c['reward_gap']) < 0.001)
        if same_reward_count > 0:
            print(f"\n1. ⚠️  立即修复选择策略bug: {same_reward_count} 个reward相同但没选到的情况")
            print("   这可能是选择策略的实现问题，需要检查MCTS的选择逻辑")
        
        print(f"\n2. 优化选择策略: 分析reward差异，改进MCTS的选择算法")
        print("   重点关注reward相同或接近时如何选择")
    
    if no_match_analysis['total_count'] > 0:
        high_reward_wrong = sum(1 for c in no_match_analysis['cases'] 
                                if c['max_reward'] >= 0.8)
        if high_reward_wrong > 0:
            print(f"\n3. 检查reward计算: {high_reward_wrong} 个高reward但不对的情况")
            print("   可能需要改进reward函数，确保正确SQL有更高的reward")
        
        print(f"\n4. 改进SQL生成: 分析为什么没有生成正确的SQL")
        print("   可能需要增加rollout次数、改进prompt或调整生成策略")


if __name__ == '__main__':
    main()
