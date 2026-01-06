#!/usr/bin/env python3
"""
汇总所有策略的评估结果
"""

import json
from pathlib import Path

STRATEGIES = [
    "force_s1",
    "force_s2", 
    "force_s3",
    "force_s4",
    "llm_pick_once"
]

def load_error_analysis(file_path):
    """加载错误分析文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return None

def main():
    base_dir = Path("/home/shenshuyu/SQL_tool_multiAgent")
    out_dir = base_dir / "workflows/mcts_v1/test/out"
    
    print("="*80)
    print("所有策略评估结果汇总")
    print("="*80)
    print()
    
    results = {}
    
    for strategy in STRATEGIES:
        error_file = out_dir / f"error_analysis_test_single_rollout_{strategy}_acc.json"
        
        if not error_file.exists():
            print(f"⚠️  {strategy}: 错误分析文件不存在")
            continue
        
        data = load_error_analysis(error_file)
        if not data or 'stats' not in data:
            print(f"⚠️  {strategy}: 无法读取统计信息")
            continue
        
        stats = data['stats']
        total = stats.get('success', 0) + stats.get('wrong_result', 0) + stats.get('syntax_error', 0) + stats.get('timeout', 0)
        accuracy = (stats.get('success', 0) / total * 100) if total > 0 else 0
        
        results[strategy] = {
            'accuracy': accuracy,
            'success': stats.get('success', 0),
            'wrong_result': stats.get('wrong_result', 0),
            'syntax_error': stats.get('syntax_error', 0),
            'timeout': stats.get('timeout', 0),
            'total': total
        }
    
    # 打印汇总表格
    print(f"{'策略':<20} {'准确率':<12} {'正确':<8} {'错误':<8} {'语法错误':<10} {'超时':<8} {'总数':<8}")
    print("-"*80)
    
    for strategy, res in sorted(results.items()):
        print(f"{strategy:<20} {res['accuracy']:>10.2f}%  {res['success']:<8} {res['wrong_result']:<8} {res['syntax_error']:<10} {res['timeout']:<8} {res['total']:<8}")
    
    print()
    print("="*80)
    
    # 找出最佳策略
    if results:
        best_strategy = max(results.items(), key=lambda x: x[1]['accuracy'])
        print(f"最佳策略: {best_strategy[0]} (准确率: {best_strategy[1]['accuracy']:.2f}%)")
        print("="*80)

if __name__ == "__main__":
    main()



