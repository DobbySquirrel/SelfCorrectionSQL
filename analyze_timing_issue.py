#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析MCTS工作流时间差异问题
"""

import json
from pathlib import Path

def analyze_timing_difference(file1_path, file2_path):
    """分析两个文件的时间差异"""
    
    # 加载文件
    with open(file1_path, 'r', encoding='utf-8') as f:
        data1 = json.load(f)
    
    with open(file2_path, 'r', encoding='utf-8') as f:
        data2 = json.load(f)
    
    # 提取第一个问题的统计数据
    key1 = list(data1.keys())[0]
    key2 = list(data2.keys())[0]
    
    stats1 = data1[key1].get('stats', {}).get('timing', {})
    stats2 = data2[key2].get('stats', {}).get('timing', {})
    
    print("=" * 80)
    print("时间差异分析报告")
    print("=" * 80)
    print(f"\n文件1: {Path(file1_path).name}")
    print(f"  问题ID: {key1}")
    print(f"  总时间: {stats1.get('total_s', 0):.2f}秒")
    print(f"  Rollout时间: {stats1.get('rollout_s', 0):.2f}秒")
    print(f"  CTE生成时间: {stats1.get('cte_gen_s', 0):.2f}秒")
    print(f"  SQL生成时间: {stats1.get('sql_gen_s', 0):.2f}秒")
    print(f"  数据库执行时间: {stats1.get('db_exec_s', 0):.2f}秒")
    print(f"  Rollout数量: {stats1.get('rollout_count', 0)}")
    
    print(f"\n文件2: {Path(file2_path).name}")
    print(f"  问题ID: {key2}")
    print(f"  总时间: {stats2.get('total_s', 0):.2f}秒")
    print(f"  Rollout时间: {stats2.get('rollout_s', 0):.2f}秒")
    print(f"  CTE生成时间: {stats2.get('cte_gen_s', 0):.2f}秒")
    print(f"  SQL生成时间: {stats2.get('sql_gen_s', 0):.2f}秒")
    print(f"  数据库执行时间: {stats2.get('db_exec_s', 0):.2f}秒")
    print(f"  Rollout数量: {stats2.get('rollout_count', 0)}")
    
    # 计算差异
    rollout_count1 = stats1.get('rollout_count', 1)
    rollout_count2 = stats2.get('rollout_count', 1)
    
    print("\n" + "=" * 80)
    print("平均每个Rollout的时间")
    print("=" * 80)
    
    avg_total1 = stats1.get('total_s', 0) / rollout_count1
    avg_total2 = stats2.get('total_s', 0) / rollout_count2
    avg_cte1 = stats1.get('cte_gen_s', 0) / rollout_count1
    avg_cte2 = stats2.get('cte_gen_s', 0) / rollout_count2
    avg_sql1 = stats1.get('sql_gen_s', 0) / rollout_count1
    avg_sql2 = stats2.get('sql_gen_s', 0) / rollout_count2
    avg_db1 = stats1.get('db_exec_s', 0) / rollout_count1
    avg_db2 = stats2.get('db_exec_s', 0) / rollout_count2
    
    print(f"\n文件1 (平均每个rollout):")
    print(f"  总时间: {avg_total1:.2f}秒")
    print(f"  CTE生成: {avg_cte1:.2f}秒")
    print(f"  SQL生成: {avg_sql1:.2f}秒")
    print(f"  数据库执行: {avg_db1:.2f}秒")
    
    print(f"\n文件2 (平均每个rollout):")
    print(f"  总时间: {avg_total2:.2f}秒")
    print(f"  CTE生成: {avg_cte2:.2f}秒")
    print(f"  SQL生成: {avg_sql2:.2f}秒")
    print(f"  数据库执行: {avg_db2:.2f}秒")
    
    print("\n" + "=" * 80)
    print("差异倍数分析")
    print("=" * 80)
    
    ratio_total = avg_total1 / avg_total2 if avg_total2 > 0 else 0
    ratio_cte = avg_cte1 / avg_cte2 if avg_cte2 > 0 else 0
    ratio_sql = avg_sql1 / avg_sql2 if avg_sql2 > 0 else 0
    ratio_db = avg_db1 / avg_db2 if avg_db2 > 0 else 0
    
    print(f"\n文件1 / 文件2 的倍数:")
    print(f"  总时间: {ratio_total:.2f}x")
    print(f"  CTE生成: {ratio_cte:.2f}x ⚠️ 差异最大")
    print(f"  SQL生成: {ratio_sql:.2f}x")
    print(f"  数据库执行: {ratio_db:.2f}x")
    
    print("\n" + "=" * 80)
    print("问题诊断")
    print("=" * 80)
    
    print("\n🔍 发现的问题:")
    print("\n1. **锁的范围过大** (第633-876行)")
    print("   - `with self.mcts_tree.lock:` 锁住了整个子节点创建过程")
    print("   - 包括：CTE变体排序、遍历所有CTE、创建所有子节点")
    print("   - 如果有多个rollout并行执行，会串行化等待这个锁")
    print("   - 这会导致CTE生成时间大幅增加")
    
    print("\n2. **等待循环可能不够** (第288行)")
    print("   - `while current.is_expanding and wait_count < 10:`")
    print("   - 最多等待10次，但每次等待时间很短（没有sleep）")
    print("   - 如果其他线程正在执行LLM调用（可能需要几十秒），等待会失败")
    
    print("\n3. **锁内操作耗时**")
    print("   - 第633-876行的锁内包含大量操作")
    print("   - 如果启用了MASTER评估（虽然默认关闭），会在锁内调用LLM")
    print("   - 即使没有MASTER评估，创建多个子节点也需要时间")
    
    print("\n" + "=" * 80)
    print("建议的优化方案")
    print("=" * 80)
    
    print("\n1. **缩小锁的范围**")
    print("   - 只在真正需要保护的关键操作上加锁")
    print("   - 将CTE变体排序和评估移到锁外")
    print("   - 只在创建子节点和更新状态时加锁")
    
    print("\n2. **改进等待机制**")
    print("   - 使用条件变量（Condition）替代忙等待")
    print("   - 或者增加等待时间，使用time.sleep()")
    
    print("\n3. **减少锁内操作**")
    print("   - 将MASTER评估移到锁外（如果启用）")
    print("   - 批量创建子节点，减少锁持有时间")
    
    print("\n4. **检查并行配置**")
    print("   - 确认两个运行的`rollouts_per_iteration`是否相同")
    print("   - 确认`max_workers`配置是否相同")
    print("   - 确认是否真的在并行执行rollout")

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("用法: python analyze_timing_issue.py <文件1路径> <文件2路径>")
        sys.exit(1)
    
    analyze_timing_difference(sys.argv[1], sys.argv[2])

