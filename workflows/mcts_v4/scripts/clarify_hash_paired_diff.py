#!/usr/bin/env python3
"""A2c: legacy vs v2 hash paired merge/split stats."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    merge_pairs = 0
    split_cases = 0
    steps = 0
    cluster_legacy = 0
    cluster_v2 = 0
    merge_examples = []
    split_examples = []

    for qid, rec in data.items():
        for r in rec.get("rollout_stats") or []:
            for step in r.get("cte_buckets_per_node") or []:
                buckets = step.get("buckets") or []
                if not buckets:
                    continue
                steps += 1
                leg = [b.get("result_signature_legacy") for b in buckets]
                v2 = [b.get("result_signature_v2") for b in buckets]
                cluster_legacy += len(set(leg))
                cluster_v2 += len(set(v2))
                if len(set(leg)) != len(set(v2)):
                    if len(set(v2)) < len(set(leg)):
                        merge_pairs += len(set(leg)) - len(set(v2))
                        if len(merge_examples) < 5:
                            merge_examples.append(
                                {"qid": qid, "step": step.get("expansion_step_id"), "legacy_n": len(set(leg)), "v2_n": len(set(v2))}
                            )
                    else:
                        split_cases += len(set(v2)) - len(set(leg))
                        if len(split_examples) < 5:
                            split_examples.append(
                                {"qid": qid, "step": step.get("expansion_step_id"), "legacy_n": len(set(leg)), "v2_n": len(set(v2))}
                            )

    lines = [
        "# A2c Hash paired diff (30q)\n",
        f"- Expansion steps: {steps}",
        f"- Cluster count legacy sum: {cluster_legacy}",
        f"- Cluster count v2 sum: {cluster_v2}",
        f"- Merge events (legacy split → v2 merge): {merge_pairs}",
        f"- Split events (legacy merge → v2 split): {split_cases}",
        "\n## Merge examples",
        json.dumps(merge_examples, indent=2, ensure_ascii=False),
        "\n## Split examples",
        json.dumps(split_examples, indent=2, ensure_ascii=False),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
