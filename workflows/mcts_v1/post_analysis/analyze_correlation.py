#!/usr/bin/env python3
"""
分析 average_reward 和时间指标与准确度（success）的相关性
"""

import json
import numpy as np
import pandas as pd
from scipy import stats
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial Unicode MS', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

def load_data(acc_file, timing_file):
    """加载并合并数据"""
    print("正在加载数据...")
    
    # 加载准确度数据
    with open(acc_file, 'r', encoding='utf-8') as f:
        acc_data = json.load(f)
    
    # 加载时间数据
    with open(timing_file, 'r', encoding='utf-8') as f:
        timing_data = json.load(f)
    
    # 提取数据
    records = []
    
    # 从准确度文件中提取 success 信息
    acc_records = {}
    for item in acc_data.get('error_details', []):
        idx = item.get('idx', '')
        success = item.get('success', 0)
        acc_records[idx] = success
    
    # 从时间文件中提取 average_reward 和时间信息
    for idx, data in timing_data.items():
        if idx in acc_records:
            stats_info = data.get('stats', {})
            timing_info = stats_info.get('timing', {})
            
            record = {
                'idx': idx,
                'success': acc_records[idx],
                'average_reward': stats_info.get('average_reward', 0),
                'total_visits': stats_info.get('total_visits', 0),
                'elapsed_time': stats_info.get('elapsed_time', 0),
                'total_s': timing_info.get('total_s', 0),
                'rollout_s': timing_info.get('rollout_s', 0),
                'cte_gen_s': timing_info.get('cte_gen_s', 0),
                'sql_gen_s': timing_info.get('sql_gen_s', 0),
                'db_exec_s': timing_info.get('db_exec_s', 0),
                'rollout_count': timing_info.get('rollout_count', 0),
            }
            records.append(record)
    
    df = pd.DataFrame(records)
    print(f"成功加载 {len(df)} 条记录")
    return df

def analyze_correlation(df):
    """分析相关性"""
    print("\n" + "="*80)
    print("相关性分析结果")
    print("="*80)
    
    # 基本统计信息
    print("\n【基本统计信息】")
    print(f"总记录数: {len(df)}")
    print(f"成功数 (success=1): {df['success'].sum()}")
    print(f"失败数 (success=0): {(df['success']==0).sum()}")
    print(f"成功率: {df['success'].mean()*100:.2f}%")
    
    print(f"\n平均奖励 (average_reward):")
    print(f"  均值: {df['average_reward'].mean():.4f}")
    print(f"  中位数: {df['average_reward'].median():.4f}")
    print(f"  标准差: {df['average_reward'].std():.4f}")
    print(f"  最小值: {df['average_reward'].min():.4f}")
    print(f"  最大值: {df['average_reward'].max():.4f}")
    
    # 按成功/失败分组统计
    print("\n【按成功/失败分组统计】")
    success_stats = df.groupby('success').agg({
        'average_reward': ['mean', 'median', 'std', 'count'],
        'total_s': ['mean', 'median', 'std'],
        'rollout_s': ['mean', 'median', 'std'],
        'sql_gen_s': ['mean', 'median', 'std'],
        'db_exec_s': ['mean', 'median', 'std'],
    })
    print(success_stats)
    
    # 相关性分析
    print("\n【相关性分析】")
    
    # 与 success 的相关性
    numeric_cols = ['average_reward', 'total_s', 'rollout_s', 'cte_gen_s', 
                    'sql_gen_s', 'db_exec_s', 'total_visits', 'rollout_count']
    
    correlations = []
    for col in numeric_cols:
        if col in df.columns:
            # Pearson 相关系数
            pearson_r, pearson_p = stats.pearsonr(df[col], df['success'])
            # Spearman 相关系数
            spearman_r, spearman_p = stats.spearmanr(df[col], df['success'])
            
            correlations.append({
                '指标': col,
                'Pearson_r': pearson_r,
                'Pearson_p': pearson_p,
                'Spearman_r': spearman_r,
                'Spearman_p': spearman_p,
                '显著(Pearson)': '是' if pearson_p < 0.05 else '否',
                '显著(Spearman)': '是' if spearman_p < 0.05 else '否',
            })
    
    corr_df = pd.DataFrame(correlations)
    print(corr_df.to_string(index=False))
    
    # 分析 average_reward 的分布
    print("\n【Average Reward 分布分析】")
    success_rewards = df[df['success']==1]['average_reward']
    fail_rewards = df[df['success']==0]['average_reward']
    
    print(f"成功案例的平均奖励:")
    print(f"  均值: {success_rewards.mean():.4f}")
    print(f"  中位数: {success_rewards.median():.4f}")
    print(f"  标准差: {success_rewards.std():.4f}")
    
    print(f"\n失败案例的平均奖励:")
    print(f"  均值: {fail_rewards.mean():.4f}")
    print(f"  中位数: {fail_rewards.median():.4f}")
    print(f"  标准差: {fail_rewards.std():.4f}")
    
    # 统计检验
    if len(success_rewards) > 0 and len(fail_rewards) > 0:
        t_stat, t_p = stats.ttest_ind(success_rewards, fail_rewards)
        print(f"\n【统计显著性检验】")
        print(f"t检验 (成功 vs 失败): t={t_stat:.4f}, p={t_p:.4f}")
        print(f"差异是否显著: {'是' if t_p < 0.05 else '否'}")
        
        # Mann-Whitney U 检验（非参数检验）
        u_stat, u_p = stats.mannwhitneyu(success_rewards, fail_rewards, alternative='two-sided')
        print(f"Mann-Whitney U检验: U={u_stat:.4f}, p={u_p:.4f}")
        print(f"差异是否显著: {'是' if u_p < 0.05 else '否'}")
    
    return corr_df, df

def create_visualizations(df, output_dir):
    """创建可视化图表"""
    print("\n正在生成可视化图表...")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Average Reward vs Success 箱线图
    plt.figure(figsize=(10, 6))
    df.boxplot(column='average_reward', by='success', ax=plt.gca())
    plt.title('Average Reward 分布对比 (Success vs Failure)')
    plt.suptitle('')  # 移除默认标题
    plt.xlabel('Success (0=失败, 1=成功)')
    plt.ylabel('Average Reward')
    plt.savefig(output_dir / 'reward_vs_success_boxplot.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 2. Average Reward 直方图（按成功/失败分组）
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    df[df['success']==1]['average_reward'].hist(bins=30, ax=axes[0], alpha=0.7, color='green')
    axes[0].set_title('成功案例 (Success=1)')
    axes[0].set_xlabel('Average Reward')
    axes[0].set_ylabel('频数')
    
    df[df['success']==0]['average_reward'].hist(bins=30, ax=axes[1], alpha=0.7, color='red')
    axes[1].set_title('失败案例 (Success=0)')
    axes[1].set_xlabel('Average Reward')
    axes[1].set_ylabel('频数')
    plt.tight_layout()
    plt.savefig(output_dir / 'reward_distribution.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 3. 时间指标 vs Success
    time_cols = ['total_s', 'rollout_s', 'sql_gen_s', 'db_exec_s']
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()
    
    for i, col in enumerate(time_cols):
        if col in df.columns:
            df.boxplot(column=col, by='success', ax=axes[i])
            axes[i].set_title(f'{col} vs Success')
            axes[i].set_xlabel('Success (0=失败, 1=成功)')
            axes[i].set_ylabel('时间 (秒)')
    
    plt.suptitle('')
    plt.tight_layout()
    plt.savefig(output_dir / 'timing_vs_success.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 4. 相关性热力图
    numeric_cols = ['success', 'average_reward', 'total_s', 'rollout_s', 
                    'sql_gen_s', 'db_exec_s', 'total_visits']
    corr_matrix = df[numeric_cols].corr()
    
    plt.figure(figsize=(10, 8))
    sns.heatmap(corr_matrix, annot=True, fmt='.3f', cmap='coolwarm', 
                center=0, square=True, linewidths=1, cbar_kws={"shrink": 0.8})
    plt.title('相关性热力图')
    plt.tight_layout()
    plt.savefig(output_dir / 'correlation_heatmap.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 5. Average Reward 阈值分析
    thresholds = np.arange(0, 1.1, 0.1)
    precision_at_threshold = []
    recall_at_threshold = []
    
    for threshold in thresholds:
        predicted_success = (df['average_reward'] >= threshold).astype(int)
        actual_success = df['success']
        
        tp = ((predicted_success == 1) & (actual_success == 1)).sum()
        fp = ((predicted_success == 1) & (actual_success == 0)).sum()
        fn = ((predicted_success == 0) & (actual_success == 1)).sum()
        
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        
        precision_at_threshold.append(precision)
        recall_at_threshold.append(recall)
    
    plt.figure(figsize=(10, 6))
    plt.plot(thresholds, precision_at_threshold, marker='o', label='Precision', linewidth=2)
    plt.plot(thresholds, recall_at_threshold, marker='s', label='Recall', linewidth=2)
    plt.xlabel('Average Reward 阈值')
    plt.ylabel('Precision / Recall')
    plt.title('Average Reward 作为成功预测指标的性能')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_dir / 'reward_threshold_analysis.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"可视化图表已保存到: {output_dir}")

def main():
    acc_file = '/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/11_17_acc.json'
    timing_file = '/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/11_17_timing.json'
    output_dir = '/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/correlation_analysis'
    
    # 加载数据
    df = load_data(acc_file, timing_file)
    
    # 分析相关性
    corr_df, df = analyze_correlation(df)
    
    # 创建可视化
    create_visualizations(df, output_dir)
    
    # 保存结果
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    corr_df.to_csv(output_dir / 'correlation_results.csv', index=False, encoding='utf-8-sig')
    df.to_csv(output_dir / 'merged_data.csv', index=False, encoding='utf-8-sig')
    
    print(f"\n分析结果已保存到: {output_dir}")
    print("\n分析完成！")

if __name__ == '__main__':
    main()

