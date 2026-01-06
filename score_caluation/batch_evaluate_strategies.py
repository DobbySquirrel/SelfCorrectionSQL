#!/usr/bin/env python3
"""
批量评估所有策略的输出结果

1. 将每个策略的 .txt 文件转换为 .json 格式
2. 计算每个策略的准确度
"""

import subprocess
import sys
import os
from pathlib import Path

# 策略列表
STRATEGIES = [
    "force_s1",
    "force_s2", 
    "force_s3",
    "force_s4",
    "llm_pick_once"
]

def main():
    # 基础路径
    base_dir = Path("/home/shenshuyu/SQL_tool_multiAgent")
    out_dir = base_dir / "workflows/mcts_v1/test/out"
    
    # 输入文件路径
    dev_set_path = base_dir / "data/sub_sampled_bird_dev_set.json"
    ground_truth_path = base_dir / "data/"
    db_root_path = "/home/shenshuyu/RSL_SQL/RSL-SQL/database/dev_databases/"
    diff_json_path = "/home/shenshuyu/RSL_SQL/RSL-SQL/data/sub_sampled_bird_dev_set.json"
    
    # 脚本路径
    txt2json_script = base_dir / "score_caluation/txt2json.py"
    compute_intersection_script = base_dir / "score_caluation/compute_intersection.py"
    evaluation_script = base_dir / "score_caluation/evaluation.py"
    
    print("="*80)
    print("开始批量评估所有策略的输出结果")
    print("="*80)
    
    # 步骤1: 将每个策略的 .txt 转换为 .json
    print("\n【步骤1】将 .txt 文件转换为 .json 格式...")
    print("-"*80)
    
    for strategy in STRATEGIES:
        txt_file = out_dir / f"test_single_rollout_{strategy}.txt"
        json_file = out_dir / f"test_single_rollout_{strategy}.json"
        
        if not txt_file.exists():
            print(f"⚠️  跳过 {strategy}: {txt_file} 不存在")
            continue
        
        print(f"\n处理策略: {strategy}")
        print(f"  输入: {txt_file}")
        print(f"  输出: {json_file}")
        
        cmd = [
            sys.executable,
            str(txt2json_script),
            "--dev_set", str(dev_set_path),
            "--txt_sqls", str(txt_file),
            "--output", str(json_file)
        ]
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"  ✅ 转换成功")
            if result.stdout:
                print(f"  {result.stdout.strip()}")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ 转换失败: {e}")
            print(f"  {e.stderr}")
            continue
    
    # 步骤2: 计算每个策略的准确度
    print("\n\n【步骤2】计算每个策略的准确度...")
    print("-"*80)
    
    for strategy in STRATEGIES:
        json_file = out_dir / f"test_single_rollout_{strategy}.json"
        
        if not json_file.exists():
            print(f"⚠️  跳过 {strategy}: {json_file} 不存在")
            continue
        
        print(f"\n评估策略: {strategy}")
        print(f"  预测结果: {json_file}")
        
        cmd = [
            sys.executable,
            str(compute_intersection_script),
            "--straightforward_path", str(json_file),
            "--ground_truth_path", str(ground_truth_path),
            "--data_mode", "sub_sampled_dev_gold.sql",
            "--db_root_path", db_root_path,
            "--diff_json_path", diff_json_path
        ]
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"  ✅ 评估完成")
            # 提取并打印关键统计信息
            if result.stdout:
                lines = result.stdout.strip().split('\n')
                in_stats_section = False
                for line in lines:
                    # 提取准确率统计部分
                    if 'Straightforward方法准确率统计' in line:
                        in_stats_section = True
                        print(f"  {line}")
                    elif '难度分类统计' in line:
                        print(f"  {line}")
                    elif in_stats_section and ('准确率' in line or '总数' in line or 'straightforward' in line or line.strip().startswith('simple') or line.strip().startswith('moderate') or line.strip().startswith('challenging') or line.strip().startswith('total')):
                        if line.strip() and not line.strip().startswith('='):
                            print(f"  {line}")
                    elif line.strip().startswith('难度') and '总数' in line:
                        print(f"  {line}")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ 评估失败: {e}")
            if e.stderr:
                print(f"  {e.stderr}")
            continue
    
    print("\n" + "="*80)
    print("批量评估完成！")
    print("="*80)
    print("\n所有结果文件保存在:")
    print(f"  {out_dir}")
    print("\n生成的 JSON 文件:")
    for strategy in STRATEGIES:
        json_file = out_dir / f"test_single_rollout_{strategy}.json"
        if json_file.exists():
            print(f"  ✅ {json_file.name}")
    print("\n生成的错误分析文件:")
    for strategy in STRATEGIES:
        error_file = out_dir / f"error_analysis_test_single_rollout_{strategy}_acc.json"
        if error_file.exists():
            print(f"  ✅ {error_file.name}")


if __name__ == "__main__":
    main()

