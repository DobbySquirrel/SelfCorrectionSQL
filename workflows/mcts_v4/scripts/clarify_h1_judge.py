#!/usr/bin/env python3
"""A2b: H1 LLM judge on sibling bucket pairs (requires Qwen3-Coder-30B endpoint)."""

import argparse
import json
import random
from collections import defaultdict
from itertools import combinations
from pathlib import Path

HOMOGENEOUS = {"Reference", "Value", "Measure", "Ranking", "Output"}

JUDGE_PROMPT = """你是 SQL 语义分析专家。给定同一题在 NL2SQL 搜索树中、同一父节点同一次扩展产生的两个
sibling 候选 CTE，判断它们的语义差异主要落在以下哪一轴：
- Reference: 表/列选择不同
- Value: filter 字面量/范围不同
- Measure: 聚合方式不同
- Ranking: 排序/Top-K 不同
- Output: 输出列/分组粒度不同
- Incomparable: 两者在做完全不同的子任务，无法对齐
- Mixed: 多轴同时差异且无主导轴

输入：
- NL question: {q}
- Parent depth: {depth}
- Sibling A CTE: {cte_a}
- Sibling A 执行结果前 10 行: {rows_a}
- Sibling B CTE: {cte_b}
- Sibling B 执行结果前 10 行: {rows_b}

输出 JSON：
{{"axis": "<one of above>", "reason": "<=80 字>", "confidence": <0-1>}}
"""


def collect_expansions(data: dict):
    for qid, rec in data.items():
        q = rec.get("question") or ""
        for r in rec.get("rollout_stats") or []:
            for step in r.get("cte_buckets_per_node") or []:
                buckets = step.get("buckets") or []
                if len(buckets) < 2:
                    continue
                yield {
                    "qid": qid,
                    "question": q,
                    "depth": step.get("depth"),
                    "expansion_step_id": step.get("expansion_step_id"),
                    "buckets": buckets,
                }


def sample_pairs(expansions, seed: int, max_total: int = 60):
    random.seed(seed)
    by_depth = defaultdict(list)
    for e in expansions:
        by_depth[e["depth"]].append(e)
    picked = []
    for d in sorted(by_depth):
        pool = by_depth[d][:20]
        picked.extend(pool)
    if len(picked) > max_total:
        picked = random.sample(picked, max_total)
    pairs = []
    for e in picked:
        bs = e["buckets"]
        for a, b in combinations(range(len(bs)), 2):
            if sum(1 for _ in combinations(range(len(bs)), 2)) > 5 and len(pairs) % 5 == 0:
                break
            pairs.append((e, bs[a], bs[b]))
    return pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20240601)
    parser.add_argument("--dry_run", action="store_true", help="Only write sampling plan, no LLM")
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    exps = list(collect_expansions(data))
    pairs = sample_pairs(exps, args.seed)

    lines = [
        "# A2b H1 Judge\n",
        f"- Multi-bucket expansion calls: {len(exps)}",
        f"- Sampled pairs: {len(pairs)}",
        "\n> Run with LLM: implement autogen call using JUDGE_PROMPT in this script.\n",
    ]

    if args.dry_run:
        for i, (e, a, b) in enumerate(pairs[:10]):
            lines.append(
                f"- pair {i}: qid={e['qid']} step={e['expansion_step_id']} "
                f"legacy=({a.get('result_signature_legacy')},{b.get('result_signature_legacy')})"
            )
        args.output.write_text("\n".join(lines), encoding="utf-8")
        print(args.output)
        return

    lines.append("\n**Status:** LLM judge not invoked in dry pipeline — set `--dry_run` false after wiring endpoint.\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
