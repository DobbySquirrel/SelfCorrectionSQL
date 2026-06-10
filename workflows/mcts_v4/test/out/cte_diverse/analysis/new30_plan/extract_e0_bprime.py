#!/usr/bin/env python3
"""Stage 1: E0 B′ baseline on new30 from 498 cache."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as met  # noqa: E402

OUT = Path(__file__).resolve().parents[2]
PLAN_DIR = Path(__file__).resolve().parent


def load_b2_498() -> dict:
    data = {}
    for i in range(4):
        p = OUT / f"v4_diverse_b2_n3_sv5_498q_coder_rollouts12_w{i}.json"
        data.update(json.loads(p.read_text(encoding="utf-8")))
    return data


def main() -> None:
    manifest = json.loads((PLAN_DIR / "manifest.json").read_text(encoding="utf-8"))
    b2 = load_b2_498()
    qids = [r["qid"] for r in manifest["questions"]]
    missing = [q for q in qids if q not in b2]
    if missing:
        raise SystemExit(f"cache miss: {missing}")

    per_q = {}
    by_bucket: dict = {}
    for row in manifest["questions"]:
        qid = row["qid"]
        bucket = row["bucket"]
        rec = b2[qid]
        ev = met.eval_record(rec)
        timing = (rec.get("stats") or {}).get("timing") or {}
        per_q[qid] = {**ev, "bucket": bucket, "runtime_s": timing.get("total_s")}
        by_bucket.setdefault(bucket, []).append(per_q[qid])

    def agg(items):
        n = len(items)
        return {
            "n": n,
            "recall": sum(int(x["recall"]) for x in items),
            "hit1_r3": sum(int(x["hit1_r3"]) for x in items),
            "hit1_r2": sum(int(x["hit1_r2"]) for x in items),
            "hit3": sum(int(x["hit3"]) for x in items),
            "hit5": sum(int(x["hit5"]) for x in items),
            "hit8": sum(int(x["hit8"]) for x in items),
            "mean_runtime_s": sum(x["runtime_s"] or 0 for x in items) / n if n else 0,
        }

    overall = agg(list(per_q.values()))
    bucket_agg = {b: agg(v) for b, v in by_bucket.items()}

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": "v4_diverse_b2_n3_sv5_498q_coder_rollouts12 (cache)",
        "cache_miss": [],
        "overall": overall,
        "by_bucket": bucket_agg,
        "per_question": per_q,
    }
    (PLAN_DIR / "e0_bprime_baseline.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md = [
        "# E0 B′ baseline (new30, cache)",
        "",
        f"Generated: {out['generated']}",
        "",
        "## Overall",
        "",
        f"- Recall: **{overall['recall']}/{overall['n']}**",
        f"- Hit@1 R3: **{overall['hit1_r3']}/{overall['n']}**",
        f"- Hit@1 R2: **{overall['hit1_r2']}/{overall['n']}**",
        f"- Hit@3/5/8: **{overall['hit3']}/{overall['hit5']}/{overall['hit8']}**",
        f"- Mean runtime: **{overall['mean_runtime_s']:.1f}s**",
        "",
        "## By bucket",
        "",
        "| bucket | recall | Hit@1 R3 | Hit@8 |",
        "|---|---:|---:|---:|",
    ]
    for b, a in sorted(bucket_agg.items()):
        md.append(f"| {b} | {a['recall']}/{a['n']} | {a['hit1_r3']}/{a['n']} | {a['hit8']}/{a['n']} |")
    (PLAN_DIR / "e0_bprime_baseline.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("Stage 1 OK:", overall)


if __name__ == "__main__":
    main()
