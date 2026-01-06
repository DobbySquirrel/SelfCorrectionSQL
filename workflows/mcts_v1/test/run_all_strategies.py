#!/usr/bin/env python
"""
批量运行所有策略模式的脚本

自动运行 5 种策略模式（FORCE_S1, FORCE_S2, FORCE_S3, FORCE_S4, LLM_PICK_ONCE），
并为每种模式生成独立的输出文件。
支持多接口并行运行，自动分配不同的 base_url 给不同策略。
"""

import subprocess
import sys
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

# 所有策略模式（移除 NONE）
STRATEGIES = [
    "FORCE_S1",
    "FORCE_S2", 
    "FORCE_S3",
    "FORCE_S4",
    "LLM_PICK_ONCE"
]

def run_strategy(strategy_mode: str, base_args: dict, base_url: str = None):
    """
    运行单个策略模式
    
    Args:
        strategy_mode: 策略模式名称
        base_args: 基础参数字典
        base_url: 分配给该策略的 base_url（如果提供，会覆盖 multi_base_urls）
    
    Returns:
        (strategy_mode, success: bool)
    """
    print(f"\n{'='*80}")
    print(f"开始运行策略模式: {strategy_mode}" + (f" (使用接口: {base_url})" if base_url else ""))
    print(f"{'='*80}\n")
    
    # 构建输出文件路径（添加策略后缀）
    sql_out = base_args.get('sql_out', '')
    json_out = base_args.get('json_out', '')
    
    if sql_out:
        base_path = Path(sql_out)
        sql_out = str(base_path.parent / f"{base_path.stem}_{strategy_mode.lower()}{base_path.suffix}")
    
    if json_out:
        base_path = Path(json_out)
        json_out = str(base_path.parent / f"{base_path.stem}_{strategy_mode.lower()}{base_path.suffix}")
    
    # 构建命令
    cmd = [
        sys.executable,
        "/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts_v1/test/test_single_mcts.py",
        "--ppl_file", base_args['ppl_file'],
        "--strategy_mode", strategy_mode,
    ]
    
    # 添加可选参数
    if base_args.get('qid'):
        cmd.extend(["--qid", base_args['qid']])
    if base_args.get('qids'):
        cmd.extend(["--qids", base_args['qids']])
    if base_args.get('index') is not None:
        cmd.extend(["--index", str(base_args['index'])])
    if base_args.get('gold_file'):
        cmd.extend(["--gold_file", base_args['gold_file']])
    if sql_out:
        cmd.extend(["--sql_out", sql_out])
    if json_out:
        cmd.extend(["--json_out", json_out])
    if base_args.get('parallel_workers'):
        cmd.extend(["--parallel_workers", str(base_args['parallel_workers'])])
    if base_args.get('max_workers'):
        cmd.extend(["--max_workers", str(base_args['max_workers'])])
    
    # 如果提供了 base_url，使用它；否则使用原来的 multi_base_urls
    if base_url:
        cmd.extend(["--multi_base_urls", base_url])
    elif base_args.get('multi_base_urls'):
        cmd.extend(["--multi_base_urls", base_args['multi_base_urls']])
    
    if base_args.get('max_cte_nodes'):
        cmd.extend(["--max_cte_nodes", str(base_args['max_cte_nodes'])])
    if base_args.get('max_depth'):
        cmd.extend(["--max_depth", str(base_args['max_depth'])])
    if base_args.get('num_sql_variants'):
        cmd.extend(["--num_sql_variants", str(base_args['num_sql_variants'])])
    
    print(f"执行命令: {' '.join(cmd)}\n")
    
    # 运行命令
    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        print(f"\n✅ 策略模式 {strategy_mode} 运行完成")
        return (strategy_mode, True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 策略模式 {strategy_mode} 运行失败: {e}")
        return (strategy_mode, False)
    except KeyboardInterrupt:
        print(f"\n⚠️ 策略模式 {strategy_mode} 被用户中断")
        return (strategy_mode, False)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="批量运行所有策略模式")
    
    # 必需参数
    parser.add_argument("--ppl_file", type=str, required=True, help="样本文件（JSON 数组）")
    parser.add_argument("--sql_out", type=str, required=True, help="SQL输出TXT（会自动添加策略后缀）")
    parser.add_argument("--json_out", type=str, required=True, help="保存结果的JSON（会自动添加策略后缀）")
    
    # 可选参数（与 test_single_mcts.py 保持一致）
    parser.add_argument("--qid", type=str, default=None, help="按 question_id 精确定位并只跑该条")
    parser.add_argument("--qids", type=str, default=None, help="多个 question_id，用逗号分隔")
    parser.add_argument("--index", type=int, default=None, help="只跑第 index 个样本")
    parser.add_argument("--gold_file", type=str, default=None, help="Gold SQL文件路径")
    parser.add_argument("--parallel_workers", type=int, default=5, help="工作流内部并行工作线程数")
    parser.add_argument("--max_workers", type=int, default=1, help="并行处理多个问题的工作线程数")
    parser.add_argument("--multi_base_urls", type=str, default=None, help="多个模型端点URL，用逗号分隔")
    parser.add_argument("--max_cte_nodes", type=int, default=5, help="每次生成的CTE变体数量")
    parser.add_argument("--max_depth", type=int, default=8, help="CTE链最大深度")
    parser.add_argument("--num_sql_variants", type=int, default=5, help="最终生成的SQL变体数量")
    
    # 策略选择
    parser.add_argument("--strategies", type=str, default=None,
                        help="要运行的策略模式，用逗号分隔（默认运行所有5种）")
    parser.add_argument("--skip_strategies", type=str, default=None,
                        help="要跳过的策略模式，用逗号分隔")
    
    # 并行运行配置
    parser.add_argument("--parallel", action="store_true", default=True,
                        help="并行运行多个策略（默认启用）")
    parser.add_argument("--sequential", action="store_true", default=False,
                        help="顺序运行策略（覆盖 --parallel）")
    
    args = parser.parse_args()
    
    # 如果指定了 sequential，则禁用 parallel
    if args.sequential:
        args.parallel = False
    
    # 确定要运行的策略
    if args.strategies:
        strategies_to_run = [s.strip().upper() for s in args.strategies.split(',')]
        # 验证策略名称
        invalid = [s for s in strategies_to_run if s not in STRATEGIES]
        if invalid:
            print(f"❌ 无效的策略模式: {invalid}")
            print(f"有效的策略模式: {', '.join(STRATEGIES)}")
            sys.exit(1)
    else:
        strategies_to_run = STRATEGIES.copy()
    
    # 跳过指定的策略
    if args.skip_strategies:
        skip_list = [s.strip().upper() for s in args.skip_strategies.split(',')]
        strategies_to_run = [s for s in strategies_to_run if s not in skip_list]
    
    if not strategies_to_run:
        print("❌ 没有要运行的策略模式")
        sys.exit(1)
    
    # 解析多个 base_urls（用于并行分配）
    base_urls = []
    if args.multi_base_urls:
        base_urls = [url.strip() for url in args.multi_base_urls.split(',') if url.strip()]
    
    print(f"\n{'='*80}")
    print(f"批量运行策略模式实验")
    print(f"{'='*80}")
    print(f"要运行的策略: {', '.join(strategies_to_run)}")
    print(f"总共 {len(strategies_to_run)} 个策略模式")
    if args.parallel and base_urls:
        print(f"并行模式: 启用（使用 {len(base_urls)} 个接口: {', '.join(base_urls)}）")
    elif args.parallel:
        print(f"并行模式: 启用（使用共享接口）")
    else:
        print(f"并行模式: 禁用（顺序运行）")
    print(f"{'='*80}\n")
    
    # 构建基础参数
    base_args = {
        'ppl_file': args.ppl_file,
        'sql_out': args.sql_out,
        'json_out': args.json_out,
        'qid': args.qid,
        'qids': args.qids,
        'index': args.index,
        'gold_file': args.gold_file,
        'parallel_workers': args.parallel_workers,
        'max_workers': args.max_workers,
        'multi_base_urls': args.multi_base_urls,  # 保留原始值，用于非并行模式
        'max_cte_nodes': args.max_cte_nodes,
        'max_depth': args.max_depth,
        'num_sql_variants': args.num_sql_variants,
    }
    
    # 运行所有策略
    results = {}
    
    if args.parallel and base_urls:
        # 并行运行：为每个策略分配一个 base_url
        print(f"🚀 开始并行运行 {len(strategies_to_run)} 个策略...\n")
        
        with ThreadPoolExecutor(max_workers=len(strategies_to_run)) as executor:
            # 为每个策略分配 base_url（轮询分配）
            futures = {}
            for i, strategy in enumerate(strategies_to_run):
                assigned_url = base_urls[i % len(base_urls)]  # 轮询分配
                future = executor.submit(run_strategy, strategy, base_args, assigned_url)
                futures[future] = strategy
            
            # 等待所有任务完成
            for future in as_completed(futures):
                strategy, success = future.result()
                results[strategy] = success
    else:
        # 顺序运行
        for strategy in strategies_to_run:
            strategy_name, success = run_strategy(strategy, base_args, None)
            results[strategy_name] = success
    
    # 打印总结
    print(f"\n{'='*80}")
    print(f"实验总结")
    print(f"{'='*80}")
    for strategy, success in results.items():
        status = "✅ 成功" if success else "❌ 失败"
        print(f"  {strategy:15s}: {status}")
    
    success_count = sum(1 for s in results.values() if s)
    total_count = len(results)
    print(f"\n总计: {success_count}/{total_count} 个策略模式运行成功")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()

