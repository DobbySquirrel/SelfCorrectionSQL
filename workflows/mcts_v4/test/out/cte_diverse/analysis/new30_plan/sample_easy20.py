#!/usr/bin/env python3
"""Stage 4 prep: sample Bucket E easy-baseline 20q from B′ 498 cache."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as met  # noqa: E402

OUT = Path(__file__).resolve().parents[2]
PLAN = Path(__file__).resolve().parent
RES30 = set((PLAN / "qids.txt").read_text().splitlines())
SEED = 20260610
K = 20


def load_b2_498() -> dict:
    data = {}
    for i in range(4):
        p = OUT / f"v4_diverse_b2_n3_sv5_498q_coder_rollouts12_w{i}.json"
        data.update(json.loads(p.read_text(encoding="utf-8")))
    return data


def is_easy_stable(qid: str, rec: dict) -> bool:
    if qid in RES30:
        return False
    if not met.hit1(rec, "R3"):
        return False
    if met.gold_cluster_rank(rec) != 1:
        return False
    ranked = met.r3_ranked_clusters(rec)
    if not ranked:
        return False
    top = ranked[0][1]
    # leaf_visit 在 v4 落盘常为 0；用 cluster size + high_reward 作稳题代理
    hi = met._high_reward_count(rec.get("rollout_stats") or [])  # noqa: SLF001
    return top.total_count >= 6 and hi >= 6


def main() -> None:
    b2 = load_b2_498()
    pool = sorted([q for q, rec in b2.items() if is_easy_stable(q, rec)], key=int)
    if len(pool) < K:
        raise SystemExit(f"easy pool {len(pool)} < {K}")
    rng = random.Random(SEED)
    picked = sorted(rng.sample(pool, K), key=int)

    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "bucket": "E_easy_baseline",
        "definition": (
            "B′ R3 Hit@1 + gold R3 rank=1 + top1 cluster count>=6 + high_reward>=6; "
            "exclude resolution 30q (visit 落盘常为 0，用 count/high_reward 代理)"
        ),
        "pool_size": len(pool),
        "qids": picked,
    }
    (PLAN / "qids_easy20.txt").write_text("\n".join(picked) + "\n", encoding="utf-8")
    (PLAN / "qids_easy20_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"Bucket E: {K}/{len(pool)} -> {PLAN / 'qids_easy20.txt'}")
    print(picked)


if __name__ == "__main__":
    main()
