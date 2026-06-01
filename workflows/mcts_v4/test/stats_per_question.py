#!/usr/bin/env python3
"""
从 mcts_v4 结果 JSON 中统计：
1. 一道题生成的时间（stats.timing.total_s）：min/avg/max
2. 一道题里的路径数（len(rollout_stats)）：min/avg/max

用法：
  python workflows/mcts_v4/test/stats_per_question.py --result_file workflows/mcts_v4/test/out/v4_arcwise_full_result_rollouts_20_decompose_S1_suggestions.json
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="统计每题耗时与路径数")
    parser.add_argument("--result_file", type=Path, required=True, help="结果 JSON 路径")
    args = parser.parse_args()
    path = args.result_file
    if not path.exists():
        print(f"文件不存在: {path}")
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    times = []
    path_counts = []
    for qid, rec in data.items():
        if not isinstance(rec, dict):
            continue
        timing = (rec.get("stats") or {}).get("timing") or {}
        t = timing.get("total_s")
        if t is not None:
            times.append(float(t))
        rollout_stats = rec.get("rollout_stats") or []
        path_counts.append(len(rollout_stats))

    n = len(times)
    if not n:
        print("没有有效的 timing 数据")
    else:
        print(f"【一道题生成时间】 (来自 stats.timing.total_s，共 {n} 题)")
        print(f"  最小: {min(times):.2f}s")
        print(f"  平均: {sum(times)/n:.2f}s")
        print(f"  最大: {max(times):.2f}s")
        print(f"  总 wall-clock（若串行）: {sum(times):.2f}s = {sum(times)/3600:.2f}h")

    m = len(path_counts)
    if not m:
        print("没有有效的 rollout_stats 数据")
    else:
        print(f"\n【一道题里的路径数】 (len(rollout_stats)，共 {m} 题)")
        print(f"  最小: {min(path_counts)}")
        print(f"  平均: {sum(path_counts)/m:.2f}")
        print(f"  最大: {max(path_counts)}")


if __name__ == "__main__":
    main()
