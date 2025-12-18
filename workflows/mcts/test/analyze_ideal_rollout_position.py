#!/usr/bin/env python3
"""
分析理想情况下（ideal strategy）正确SQL出现的rollout位置分布

统计正确SQL在哪个rollout中出现，并绘制分布图
"""

import json
import sys
import argparse
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional
import matplotlib.pyplot as plt
import numpy as np

# 添加项目路径
sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from core.database_connector import DatabaseConnector
from workflows.mcts.test.test_mcts import compare_with_gold, build_db_connector


def load_gold_sqls(gold_file: str) -> Dict[str, str]:
    """加载gold SQL文件"""
    print(f"📂 Loading Gold file: {gold_file}")
    gold_sqls = {}
    
    try:
        with open(gold_file, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
            
        items = []
        if isinstance(gold_data, list):
            items = gold_data
        elif isinstance(gold_data, dict):
            for k, v in gold_data.items():
                if isinstance(v, dict):
                    v['question_id'] = k
                    items.append(v)
        
        count = 0
        for item in items:
            qid = item.get('question_id') or item.get('id')
            if qid is None:
                continue
            qid = str(qid).strip()
            
            sql = item.get('SQL') or item.get('sql') or item.get('query') or item.get('question_sql')
            
            if qid and sql:
                gold_sqls[qid] = sql
                count += 1
                
        print(f"✅ Loaded {count} gold SQLs")
        
    except Exception as e:
        print(f"❌ Failed to load Gold file: {e}")
        
    return gold_sqls


def find_correct_sql_rollout_positions(data: Dict[str, Any], gold_sqls: Dict[str, str], 
                                       ppl_file: str = None) -> Dict[str, List[int]]:
    """
    找到每个问题中正确SQL出现的rollout位置
    
    Returns:
        Dict[qid -> List[rollout_ids]]: 每个问题中正确SQL出现的rollout ID列表
    """
    print("\n🔍 Analyzing rollout positions of correct SQLs...")
    
    # 加载数据库映射
    db_info_map = {}
    if ppl_file:
        try:
            with open(ppl_file, 'r', encoding='utf-8') as f:
                ppls = json.load(f)
            items = ppls if isinstance(ppls, list) else list(ppls.values())
            for item in items:
                qid = item.get('question_id') or item.get('id')
                if qid is None:
                    continue
                qid = str(qid).strip()
                db_name = item.get('db') or item.get('db_id')
                if qid and db_name:
                    db_info_map[qid] = db_name
            print(f"✅ Loaded {len(db_info_map)} database mappings")
        except Exception as e:
            print(f"⚠️ Failed to load database info: {e}")
    
    correct_rollout_positions = {}
    total_questions = 0
    questions_with_correct_sql = 0
    
    for qid, item in data.items():
        qid_str = str(qid).strip()
        gold_sql = gold_sqls.get(qid_str, '')
        
        if not gold_sql:
            continue
        
        total_questions += 1
        db_name = db_info_map.get(qid_str, '')
        db_connector = None
        if db_name:
            try:
                db_connector = build_db_connector(db_name)
            except Exception as e:
                print(f"⚠️ Failed to connect to database {db_name}: {e}")
                continue
        
        # 获取所有rollout的SQL
        rollout_stats = item.get('rollout_stats', [])
        correct_rollout_ids = []
        
        # 方法1: 从rollout_stats中查找selected_sql
        for rollout in rollout_stats:
            rollout_id = rollout.get('rollout_id', 0)
            selected_sql = rollout.get('selected_sql')
            
            if selected_sql and db_connector:
                try:
                    is_correct = compare_with_gold(selected_sql, gold_sql, db_connector=db_connector)
                    if is_correct:
                        correct_rollout_ids.append(rollout_id)
                except Exception:
                    pass
        
        # 方法2: 从all_sqls_with_attributes中查找（如果有rollout_id字段）
        all_sqls = item.get('all_sqls_with_attributes', [])
        if all_sqls:
            for sql_info in all_sqls:
                sql = sql_info.get('sql', '')
                rollout_id = sql_info.get('rollout_id')
                
                if sql and rollout_id is not None and db_connector:
                    try:
                        is_correct = compare_with_gold(sql, gold_sql, db_connector=db_connector)
                        if is_correct and rollout_id not in correct_rollout_ids:
                            correct_rollout_ids.append(rollout_id)
                    except Exception:
                        pass
            # 如果all_sqls_with_attributes中没有rollout_id，尝试通过索引推断
            # 假设all_sqls的顺序对应rollout的顺序
            if not correct_rollout_ids:
                for idx, sql_info in enumerate(all_sqls, 1):
                    sql = sql_info.get('sql', '')
                    if sql and db_connector:
                        try:
                            is_correct = compare_with_gold(sql, gold_sql, db_connector=db_connector)
                            if is_correct:
                                # 尝试从rollout_stats中找到对应的rollout_id
                                if idx <= len(rollout_stats):
                                    rollout_id = rollout_stats[idx - 1].get('rollout_id', idx)
                                    if rollout_id not in correct_rollout_ids:
                                        correct_rollout_ids.append(rollout_id)
                        except Exception:
                            pass
        
        if correct_rollout_ids:
            questions_with_correct_sql += 1
            correct_rollout_positions[qid_str] = sorted(correct_rollout_ids)
        
        if db_connector:
            db_connector.disconnect()
    
    print(f"\n📊 Statistics:")
    print(f"  Total questions: {total_questions}")
    print(f"  Questions with correct SQL: {questions_with_correct_sql}")
    print(f"  Coverage: {questions_with_correct_sql/total_questions*100:.2f}%")
    
    return correct_rollout_positions


def plot_rollout_position_distribution(correct_rollout_positions: Dict[str, List[int]], 
                                      output_file: str = "rollout_position_distribution.png"):
    """
    绘制正确SQL出现的rollout位置分布图
    """
    print("\n📈 Plotting rollout position distribution...")
    
    # 收集所有rollout位置（使用第一个正确SQL的位置）
    first_occurrence_positions = []
    all_occurrence_positions = []
    
    for qid, rollout_ids in correct_rollout_positions.items():
        if rollout_ids:
            # 第一个出现的位置（1-indexed）
            first_pos = rollout_ids[0]
            first_occurrence_positions.append(first_pos)
            # 所有出现的位置
            all_occurrence_positions.extend(rollout_ids)
    
    if not first_occurrence_positions:
        print("⚠️ No correct SQLs found, cannot plot distribution")
        return
    
    # 统计分布
    first_pos_counter = Counter(first_occurrence_positions)
    all_pos_counter = Counter(all_occurrence_positions)
    
    max_rollout = max(all_occurrence_positions) if all_occurrence_positions else 10
    rollout_range = range(1, max_rollout + 1)
    
    # 创建图表
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    
    # 子图1: 第一个正确SQL出现的rollout位置分布（柱状图）
    first_pos_counts = [first_pos_counter.get(pos, 0) for pos in rollout_range]
    bars1 = ax1.bar(rollout_range, first_pos_counts, alpha=0.7, color='steelblue', edgecolor='black')
    ax1.set_xlabel('Rollout Position (First Occurrence)', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Number of Questions', fontsize=12, fontweight='bold')
    ax1.set_title('Distribution of First Correct SQL Occurrence\nin Rollout Sequence', 
                  fontsize=13, fontweight='bold')
    ax1.set_xticks(rollout_range)
    ax1.grid(axis='y', alpha=0.3, linestyle='--')
    
    # 添加数值标签
    for bar in bars1:
        height = bar.get_height()
        if height > 0:
            ax1.text(bar.get_x() + bar.get_width()/2., height,
                    f'{int(height)}',
                    ha='center', va='bottom', fontsize=9)
    
    # 子图2: 所有正确SQL出现的rollout位置分布（柱状图）
    all_pos_counts = [all_pos_counter.get(pos, 0) for pos in rollout_range]
    bars2 = ax2.bar(rollout_range, all_pos_counts, alpha=0.7, color='coral', edgecolor='black')
    ax2.set_xlabel('Rollout Position (All Occurrences)', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Number of Correct SQLs', fontsize=12, fontweight='bold')
    ax2.set_title('Distribution of All Correct SQL Occurrences\nin Rollout Sequence', 
                  fontsize=13, fontweight='bold')
    ax2.set_xticks(rollout_range)
    ax2.grid(axis='y', alpha=0.3, linestyle='--')
    
    # 添加数值标签
    for bar in bars2:
        height = bar.get_height()
        if height > 0:
            ax2.text(bar.get_x() + bar.get_width()/2., height,
                    f'{int(height)}',
                    ha='center', va='bottom', fontsize=9)
    
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✅ Plot saved to: {output_file}")
    
    # 打印统计信息
    print("\n📊 Distribution Statistics:")
    print(f"\nFirst Occurrence:")
    print(f"  Mean position: {np.mean(first_occurrence_positions):.2f}")
    print(f"  Median position: {np.median(first_occurrence_positions):.2f}")
    print(f"  Min position: {min(first_occurrence_positions)}")
    print(f"  Max position: {max(first_occurrence_positions)}")
    
    print(f"\nAll Occurrences:")
    print(f"  Mean position: {np.mean(all_occurrence_positions):.2f}")
    print(f"  Median position: {np.median(all_occurrence_positions):.2f}")
    print(f"  Min position: {min(all_occurrence_positions)}")
    print(f"  Max position: {max(all_occurrence_positions)}")
    
    # 计算累积分布
    print(f"\nCumulative Distribution (First Occurrence):")
    sorted_first = sorted(first_occurrence_positions)
    for percentile in [25, 50, 75, 90, 95]:
        idx = int(len(sorted_first) * percentile / 100)
        if idx < len(sorted_first):
            print(f"  {percentile}th percentile: Rollout {sorted_first[idx]}")


def main():
    parser = argparse.ArgumentParser(description="Analyze rollout positions of correct SQLs (ideal strategy)")
    parser.add_argument("--json_file", type=str, required=True, help="Result JSON file")
    parser.add_argument("--gold_file", type=str, required=True, help="Gold SQL file")
    parser.add_argument("--ppl_file", type=str, default=None, help="Database mapping file")
    parser.add_argument("--output_file", type=str, default="rollout_position_distribution.png", 
                       help="Output plot file")
    args = parser.parse_args()
    
    print("="*80)
    print("Ideal Strategy: Rollout Position Analysis")
    print("="*80)
    
    # 加载数据
    print(f"\n📂 Loading JSON file: {args.json_file}")
    with open(args.json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    gold_sqls = load_gold_sqls(args.gold_file)
    ppl_file_path = args.ppl_file if args.ppl_file else args.gold_file
    
    # 分析rollout位置
    correct_rollout_positions = find_correct_sql_rollout_positions(
        data, gold_sqls, ppl_file_path
    )
    
    # 绘制分布图
    plot_rollout_position_distribution(correct_rollout_positions, args.output_file)
    
    # 保存详细结果
    output_json = args.output_file.replace('.png', '.json')
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump({
            'correct_rollout_positions': correct_rollout_positions,
            'statistics': {
                'total_questions': len(gold_sqls),
                'questions_with_correct_sql': len(correct_rollout_positions),
                'coverage': len(correct_rollout_positions) / len(gold_sqls) * 100 if gold_sqls else 0
            }
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 Detailed results saved to: {output_json}")
    print("="*80)


if __name__ == "__main__":
    main()

