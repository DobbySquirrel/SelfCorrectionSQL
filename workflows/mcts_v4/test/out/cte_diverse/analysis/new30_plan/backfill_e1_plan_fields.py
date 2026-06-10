#!/usr/bin/env python3
"""Backfill union_rollout_stats / per_plan_rollout_stats from rollout_stats plan_id."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

E1 = Path(__file__).resolve().parents[2] / "v4_plan_e1_new30_coder_rollouts12.json"


def enrich(rec: dict) -> dict:
    rss = rec.get("rollout_stats") or []
    if not rss:
        return rec
    per: dict = defaultdict(list)
    for rs in rss:
        pid = rs.get("plan_id") or "unknown"
        per[pid].append(rs)
    rec["union_rollout_stats"] = list(rss)
    rec["per_plan_rollout_stats"] = dict(per)
    hashes = {rs.get("plan_hash") for rs in rss if rs.get("plan_hash")}
    rec["plan_dedup_count"] = len(hashes) if hashes else None
    return rec


def main() -> None:
    data = json.loads(E1.read_text(encoding="utf-8"))
    for qid in data:
        enrich(data[qid])
    E1.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Backfilled {len(data)} records -> {E1}")


if __name__ == "__main__":
    main()
