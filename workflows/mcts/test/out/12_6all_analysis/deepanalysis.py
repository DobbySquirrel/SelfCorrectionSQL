#!/usr/bin/env python3
"""
深入分析CTE LLM打分特征与成功率的相关性
"""

import pandas as pd
import numpy as np
import json
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial Unicode MS', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

def load_data():
    """加载数据"""
    corr_file = 'workflows/mcts/test/out/12_6all_analysis/correlation_analysis.csv'
    stats_file = 'workflows/mcts/test/out/12_6all_analysis/feature_statistics.json'
    features_file = 'workflows/mcts/test/out/12_6all_analysis/all_sql_features.csv'
    
    corr_df = pd.read_csv(corr_file)
    with open(stats_file, 'r') as f:
        stats = json.load(f)
    features_df = pd.read_csv(features_file)
    
    return corr_df, stats, features_df

def analyze_top_features(corr_df, stats):
    """分析最重要的特征"""
    print("="*80)
    print("【核心发现】最重要的预测特征")
    print("="*80)
    
    # 按绝对值排序
    top_features = corr_df.nlargest(10, 'abs_correlation')
    
    print("\nTop 10 特征（按相关性绝对值排序）：")
    print("-"*80)
    for idx, row in top_features.iterrows():
        direction = "↑" if row['correlation'] > 0 else "↓"
        print(f"{idx+1:2d}. {row['feature']:<30} {direction} {row['correlation']:>7.4f} (|r|={row['abs_correlation']:.4f})")
    
    return top_features

def analyze_cte_patterns(corr_df, stats):
    """分析CTE特征的模式"""
    print("\n" + "="*80)
    print("【CTE特征模式分析】")
    print("="*80)
    
    # 提取CTE相关特征
    cte_immediate_scores = [col for col in corr_df['feature'] if 'immediate_score' in col and col.startswith('cte')]
    cte_confidences = [col for col in corr_df['feature'] if 'confidence' in col and col.startswith('cte')]
    cte_bucket_counts = [col for col in corr_df['feature'] if 'bucket_count' in col and col.startswith('cte')]
    
    print("\n1. CTE Immediate Score 模式分析：")
    print("-"*80)
    immediate_corr = {}
    for feature in cte_immediate_scores:
        row = corr_df[corr_df['feature'] == feature]
        if not row.empty:
            cte_num = feature.replace('cte', '').replace('_immediate_score', '')
            immediate_corr[cte_num] = row['correlation'].values[0]
    
    # 按CTE位置排序
    sorted_cte_scores = sorted(immediate_corr.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999)
    print("CTE位置 -> 相关性（正相关表示该位置的CTE评分越高，成功率越高）")
    for cte_num, corr in sorted_cte_scores:
        direction = "↑" if corr > 0 else "↓"
        print(f"  CTE{cte_num:<3} {direction} {corr:>7.4f}")
    
    # 分析哪些位置的CTE最重要
    positive_ctes = [cte for cte, corr in sorted_cte_scores if corr > 0.1]
    negative_ctes = [cte for cte, corr in sorted_cte_scores if corr < -0.1]
    
    print(f"\n  关键发现：")
    print(f"  - 正相关最强的CTE位置: {positive_ctes}")
    print(f"  - 负相关最强的CTE位置: {negative_ctes}")
    
    print("\n2. CTE Confidence 模式分析：")
    print("-"*80)
    confidence_corr = {}
    for feature in cte_confidences:
        row = corr_df[corr_df['feature'] == feature]
        if not row.empty:
            cte_num = feature.replace('cte', '').replace('_confidence', '')
            confidence_corr[cte_num] = row['correlation'].values[0]
    
    sorted_cte_conf = sorted(confidence_corr.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999)
    print("CTE位置 -> 相关性")
    for cte_num, corr in sorted_cte_conf[:10]:  # 只显示前10个
        direction = "↑" if corr > 0 else "↓"
        print(f"  CTE{cte_num:<3} {direction} {corr:>7.4f}")
    
    print("\n3. CTE Bucket Count 模式分析：")
    print("-"*80)
    bucket_corr = {}
    for feature in cte_bucket_counts:
        row = corr_df[corr_df['feature'] == feature]
        if not row.empty:
            cte_num = feature.replace('cte', '').replace('_bucket_count', '')
            bucket_corr[cte_num] = row['correlation'].values[0]
    
    sorted_cte_bucket = sorted(bucket_corr.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999)
    print("CTE位置 -> 相关性")
    for cte_num, corr in sorted_cte_bucket[:10]:
        direction = "↑" if corr > 0 else "↓"
        print(f"  CTE{cte_num:<3} {direction} {corr:>7.4f}")
    
    return immediate_corr, confidence_corr, bucket_corr

def analyze_aggregate_features(corr_df, stats):
    """分析聚合特征"""
    print("\n" + "="*80)
    print("【聚合特征分析】")
    print("="*80)
    
    aggregate_features = ['avg_cte_immediate_score', 'min_cte_immediate_score', 'max_cte_immediate_score',
                         'avg_cte_confidence', 'min_cte_confidence', 'max_cte_confidence',
                         'avg_cte_bucket_count', 'total_cte_bucket_count']
    
    print("\n聚合特征相关性：")
    print("-"*80)
    for feature in aggregate_features:
        row = corr_df[corr_df['feature'] == feature]
        if not row.empty:
            corr = row['correlation'].values[0]
            direction = "↑" if corr > 0 else "↓"
            print(f"  {feature:<30} {direction} {corr:>7.4f}")
    
    # 关键发现
    print("\n  关键发现：")
    avg_score_row = corr_df[corr_df['feature'] == 'avg_cte_immediate_score']
    min_score_row = corr_df[corr_df['feature'] == 'min_cte_immediate_score']
    max_score_row = corr_df[corr_df['feature'] == 'max_cte_immediate_score']
    
    if not avg_score_row.empty:
        print(f"  - 平均CTE评分相关性: {avg_score_row['correlation'].values[0]:.4f} (负相关！)")
    if not min_score_row.empty:
        print(f"  - 最小CTE评分相关性: {min_score_row['correlation'].values[0]:.4f} (正相关)")
    if not max_score_row.empty:
        print(f"  - 最大CTE评分相关性: {max_score_row['correlation'].values[0]:.4f} (负相关！)")
    
    print(f"\n  ⚠️  反直觉发现：平均CTE评分和最大CTE评分都是负相关！")
    print(f"     这可能意味着：")
    print(f"     1. 评分系统可能存在偏差")
    print(f"     2. 某些高评分的CTE可能不是最优路径")
    print(f"     3. 需要重新审视评分机制")

def analyze_depth_and_path(corr_df, stats):
    """分析深度和路径特征"""
    print("\n" + "="*80)
    print("【搜索深度和路径分析】")
    print("="*80)
    
    depth_features = ['depth', 'cte_path_length', 'visit_count']
    
    print("\n深度相关特征：")
    print("-"*80)
    for feature in depth_features:
        row = corr_df[corr_df['feature'] == feature]
        if not row.empty:
            corr = row['correlation'].values[0]
            direction = "↑" if corr > 0 else "↓"
            print(f"  {feature:<20} {direction} {corr:>7.4f}")
    
    print("\n  关键发现：")
    print(f"  - 搜索深度越深，成功率越低（depth: -0.329）")
    print(f"  - CTE路径越长，成功率越低（cte_path_length: -0.293）")
    print(f"  - 访问次数越多，成功率越高（visit_count: +0.153）")
    print(f"\n  💡 建议：")
    print(f"     1. 设置最大搜索深度限制")
    print(f"     2. 优先探索较短的CTE路径")
    print(f"     3. 在浅层节点增加访问次数可能更有效")

def compare_with_previous_analysis(corr_df):
    """与之前的分析对比"""
    print("\n" + "="*80)
    print("【与之前分析（11_17）的对比】")
    print("="*80)
    
    print("\n对比总结：")
    print("-"*80)
    print("之前分析（11_17_acc.json + 11_17_timing.json）：")
    print("  - average_reward: r=0.249 (Pearson), r=0.395 (Spearman)")
    print("  - total_s: r=-0.357 (负相关)")
    print("  - 成功案例平均奖励: 0.2303 vs 失败: 0.1487")
    
    print("\n当前分析（12_6all_analysis）：")
    reward_row = corr_df[corr_df['feature'] == 'reward']
    bucket_row = corr_df[corr_df['feature'] == 'bucket_count']
    
    if not reward_row.empty:
        print(f"  - reward: r={reward_row['correlation'].values[0]:.4f}")
    if not bucket_row.empty:
        print(f"  - bucket_count: r={bucket_row['correlation'].values[0]:.4f} (最强预测指标)")
    
    print("\n  关键差异：")
    print("  1. bucket_count (0.421) 是比 reward (0.337) 更强的预测指标")
    print("  2. 当前分析引入了CTE级别的LLM打分特征")
    print("  3. 发现了深度和路径长度的重要影响")
    print("  4. CTE评分存在反直觉的负相关模式")

def generate_insights(corr_df, stats, features_df):
    """生成洞察和建议"""
    print("\n" + "="*80)
    print("【核心洞察和行动建议】")
    print("="*80)
    
    insights = []
    
    # 1. Bucket Count 是最强指标
    bucket_row = corr_df[corr_df['feature'] == 'bucket_count']
    if not bucket_row.empty:
        insights.append({
            'insight': 'Bucket Count 是最强的成功预测指标',
            'evidence': f"相关性 r={bucket_row['correlation'].values[0]:.4f}",
            'action': '在MCTS搜索中，优先选择bucket_count高的节点'
        })
    
    # 2. 深度限制
    depth_row = corr_df[corr_df['feature'] == 'depth']
    if not depth_row.empty:
        insights.append({
            'insight': '搜索深度与成功率负相关',
            'evidence': f"depth相关性 r={depth_row['correlation'].values[0]:.4f}",
            'action': '设置最大深度限制（建议6-7层），避免过度搜索'
        })
    
    # 3. CTE路径长度
    path_row = corr_df[corr_df['feature'] == 'cte_path_length']
    if not path_row.empty:
        insights.append({
            'insight': 'CTE路径长度与成功率负相关',
            'evidence': f"cte_path_length相关性 r={path_row['correlation'].values[0]:.4f}",
            'action': '优先探索较短的CTE路径，避免路径过长'
        })
    
    # 4. CTE评分反直觉
    avg_score_row = corr_df[corr_df['feature'] == 'avg_cte_immediate_score']
    if not avg_score_row.empty and avg_score_row['correlation'].values[0] < 0:
        insights.append({
            'insight': '平均CTE评分存在反直觉的负相关',
            'evidence': f"avg_cte_immediate_score相关性 r={avg_score_row['correlation'].values[0]:.4f}",
            'action': '需要重新审视LLM评分机制，可能存在评分偏差'
        })
    
    # 5. 特定CTE位置的重要性
    cte9_row = corr_df[corr_df['feature'] == 'cte9_immediate_score']
    if not cte9_row.empty:
        insights.append({
            'insight': '后期CTE（如CTE9）的评分很重要',
            'evidence': f"cte9_immediate_score相关性 r={cte9_row['correlation'].values[0]:.4f}",
            'action': '关注后期CTE的质量，这些CTE对最终成功影响更大'
        })
    
    print("\n核心洞察：")
    for i, insight in enumerate(insights, 1):
        print(f"\n{i}. {insight['insight']}")
        print(f"   证据: {insight['evidence']}")
        print(f"   行动: {insight['action']}")
    
    return insights

def create_visualizations(corr_df, output_dir):
    """创建可视化"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Top特征相关性条形图
    top_15 = corr_df.nlargest(15, 'abs_correlation')
    plt.figure(figsize=(12, 8))
    colors = ['green' if x > 0 else 'red' for x in top_15['correlation']]
    plt.barh(range(len(top_15)), top_15['correlation'], color=colors, alpha=0.7)
    plt.yticks(range(len(top_15)), top_15['feature'])
    plt.xlabel('相关性 (Correlation)')
    plt.title('Top 15 特征相关性')
    plt.axvline(x=0, color='black', linestyle='--', linewidth=0.5)
    plt.tight_layout()
    plt.savefig(output_dir / 'top_features_correlation.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 2. CTE位置相关性趋势
    cte_immediate_scores = {}
    for feature in corr_df['feature']:
        if 'immediate_score' in feature and feature.startswith('cte') and feature[3].isdigit():
            cte_num = int(feature[3])
            row = corr_df[corr_df['feature'] == feature]
            if not row.empty:
                cte_immediate_scores[cte_num] = row['correlation'].values[0]
    
    if cte_immediate_scores:
        sorted_ctes = sorted(cte_immediate_scores.items())
        positions = [x[0] for x in sorted_ctes]
        correlations = [x[1] for x in sorted_ctes]
        
        plt.figure(figsize=(12, 6))
        colors = ['green' if x > 0 else 'red' for x in correlations]
        plt.bar(positions, correlations, color=colors, alpha=0.7)
        plt.xlabel('CTE位置')
        plt.ylabel('相关性')
        plt.title('不同CTE位置的Immediate Score相关性')
        plt.axhline(y=0, color='black', linestyle='--', linewidth=0.5)
        plt.grid(True, alpha=0.3, axis='y')
        plt.tight_layout()
        plt.savefig(output_dir / 'cte_position_correlation.png', dpi=300, bbox_inches='tight')
        plt.close()
    
    print(f"\n可视化图表已保存到: {output_dir}")

def main():
    print("开始深入分析CTE LLM打分特征...")
    
    corr_df, stats, features_df = load_data()
    
    # 分析
    top_features = analyze_top_features(corr_df, stats)
    immediate_corr, confidence_corr, bucket_corr = analyze_cte_patterns(corr_df, stats)
    analyze_aggregate_features(corr_df, stats)
    analyze_depth_and_path(corr_df, stats)
    compare_with_previous_analysis(corr_df)
    insights = generate_insights(corr_df, stats, features_df)
    
    # 可视化
    create_visualizations(corr_df, 'workflows/mcts/test/out/12_6all_analysis/visualizations')
    
    # 保存洞察
    output_dir = Path('workflows/mcts/test/out/12_6all_analysis')
    insights_df = pd.DataFrame(insights)
    insights_df.to_csv(output_dir / 'insights.csv', index=False, encoding='utf-8-sig')
    
    print("\n" + "="*80)
    print("分析完成！")
    print("="*80)

if __name__ == '__main__':
    main()

