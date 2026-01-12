"""
找出reward最低的"都没生成对的"案例
"""

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='找出reward最低的案例')
    parser.add_argument('--error_analysis_file', type=str, required=True,
                        help='错误分析结果文件路径')
    parser.add_argument('--result_file', type=str, required=True,
                        help='原始结果文件路径')
    parser.add_argument('--eval_file', type=str, required=True,
                        help='评估结果文件路径')
    
    args = parser.parse_args()
    
    # 加载错误分析结果
    print("[加载] 正在加载错误分析结果...")
    with open(args.error_analysis_file, 'r', encoding='utf-8') as f:
        error_analysis = json.load(f)
    
    # 加载原始结果
    print("[加载] 正在加载原始结果...")
    with open(args.result_file, 'r', encoding='utf-8') as f:
        result_data = json.load(f)
    
    # 加载评估结果
    print("[加载] 正在加载评估结果...")
    with open(args.eval_file, 'r', encoding='utf-8') as f:
        eval_data = json.load(f)
    
    # 获取"都没生成对的"案例
    no_match_cases = error_analysis.get('no_match_generated_analysis', {}).get('cases', [])
    
    # 按max_reward排序，找出最低的3个
    cases_sorted = sorted(no_match_cases, key=lambda x: x['max_reward'])
    lowest_3 = cases_sorted[:3]
    
    print("\n" + "=" * 80)
    print(f"找到 {len(lowest_3)} 个最低reward的案例")
    print("=" * 80)
    
    evaluation_results = {r['question_id']: r for r in eval_data.get('evaluation_results', [])}
    
    for i, case in enumerate(lowest_3, 1):
        question_id = case['question_id']
        print(f"\n{'='*80}")
        print(f"案例 {i}: 问题 {question_id}")
        print(f"{'='*80}")
        
        print(f"\n基本信息:")
        print(f"  - 最高reward: {case['max_reward']:.4f}")
        print(f"  - 平均reward: {case['avg_reward']:.4f}")
        print(f"  - 总SQL数: {case['total_sqls']}")
        print(f"  - Rollout数量: {case['total_rollouts']}")
        print(f"  - Reward分布: 高({case['reward_distribution']['high_reward_count']}) "
              f"中({case['reward_distribution']['medium_reward_count']}) "
              f"低({case['reward_distribution']['low_reward_count']})")
        
        # 获取原始结果数据
        original_result = result_data.get(question_id, {})
        stats = original_result.get('stats', {})
        rollout_stats = original_result.get('rollout_stats', [])
        
        print(f"\nGold SQL:")
        gold_sql = stats.get('gold_sql', case.get('gold_sql', ''))
        print(f"  {gold_sql}")
        
        print(f"\nSelected SQL (最终选择的):")
        selected_sql = case.get('selected_sql', '')
        if selected_sql:
            print(f"  {selected_sql[:500]}..." if len(selected_sql) > 500 else f"  {selected_sql}")
        else:
            print("  (无)")
        
        print(f"\nBest SQL (reward最高的):")
        best_sql = case.get('best_sql', '')
        if best_sql:
            print(f"  {best_sql[:500]}..." if len(best_sql) > 500 else f"  {best_sql}")
        else:
            print("  (无)")
        
        # 获取评估结果中的所有SQL
        eval_result = evaluation_results.get(question_id, {})
        matches = eval_result.get('matches', [])
        
        print(f"\n所有SQL的reward分布:")
        if matches:
            # 按reward排序
            matches_sorted = sorted(matches, key=lambda x: x.get('reward', 0.0), reverse=True)
            print(f"  前5个最高reward的SQL:")
            for j, m in enumerate(matches_sorted[:5], 1):
                sql_preview = m['sql'][:150] + '...' if len(m['sql']) > 150 else m['sql']
                print(f"    {j}. Reward={m.get('reward', 0.0):.4f}, Source={m.get('source', 'unknown')}")
                print(f"       {sql_preview}")
        
        # 分析rollout情况
        print(f"\nRollout分析:")
        if rollout_stats:
            print(f"  前3个rollout的reward:")
            for j, rollout in enumerate(rollout_stats[:3], 1):
                reward = rollout.get('reward', 0.0)
                selected_sql_in_rollout = rollout.get('selected_sql', '')
                sql_variants = rollout.get('all_sql_variants', [])
                print(f"    Rollout {j}: reward={reward:.4f}, SQL变体数={len(sql_variants)}")
                if selected_sql_in_rollout:
                    sql_preview = selected_sql_in_rollout[:100] + '...' if len(selected_sql_in_rollout) > 100 else selected_sql_in_rollout
                    print(f"      Selected SQL: {sql_preview}")
        
        # 检查是否有执行错误
        print(f"\n执行错误检查:")
        if matches:
            errors = [m.get('error') for m in matches if m.get('error')]
            if errors:
                print(f"  发现 {len(errors)} 个执行错误:")
                for error in errors[:3]:
                    print(f"    - {error}")
            else:
                print("  未发现执行错误（所有SQL都能执行）")
    
    print("\n" + "=" * 80)
    print("分析完成")
    print("=" * 80)


if __name__ == '__main__':
    main()
