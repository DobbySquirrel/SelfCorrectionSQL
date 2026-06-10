#!/usr/bin/env python3
"""Stage 0: build new30_plan manifest from existing resolution 30q."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as met  # noqa: E402

OUT = Path(__file__).resolve().parents[2]  # cte_diverse
PLAN_DIR = Path(__file__).resolve().parent
RES30 = OUT / "qids_resolution_30.json"

BUCKET_LABEL = {
    "A_search_miss_other_hits": "A_search_miss_recoverable",
    "B_selection_miss": "B_selection_miss",
    "C_both_no_recall": "C_both_no_recall",
    "D_false_consensus": "D_s7_false_consensus",
}


def load_b2_498() -> dict:
    data = {}
    for i in range(4):
        p = OUT / f"v4_diverse_b2_n3_sv5_498q_coder_rollouts12_w{i}.json"
        data.update(json.loads(p.read_text(encoding="utf-8")))
    return data


def main() -> None:
    src = json.loads(RES30.read_text(encoding="utf-8"))
    b2 = load_b2_498()
    b2pp = {}
    for i in range(4):
        p = OUT / f"v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15_w{i}.json"
        b2pp.update(json.loads(p.read_text(encoding="utf-8")))

    buckets = {}
    for old, new in BUCKET_LABEL.items():
        buckets[new] = src["buckets"][old]

    all_q = sorted({q for qs in buckets.values() for q in qs}, key=int)
    assert len(all_q) == 30 and len(set(all_q)) == 30
    for name, qs in buckets.items():
        assert len(qs) == {"A_search_miss_recoverable": 10, "B_selection_miss": 10,
                             "C_both_no_recall": 5, "D_s7_false_consensus": 5}[name]

    rows = []
    for bucket, qids in buckets.items():
        for qid in qids:
            rb = b2.get(qid, {})
            rp = b2pp.get(qid, {})
            prof = src["profiles"][qid]
            rank = met.gold_cluster_rank(rb)
            ranked = met.r3_ranked_clusters(rb)
            top1_sig = ranked[0][0][:16] if ranked else ""
            gs = met.gold_sigs(rb)
            top1_has_gold = bool(ranked and ranked[0][0] in gs)
            rows.append({
                "qid": qid,
                "bucket": bucket,
                "b2_recall": bool(prof["rec_b2"]),
                "b2pp_recall": bool(prof["rec_b2pp"]),
                "union_recall": bool(prof["rec_union"]),
                "b2_hit_r3": bool(prof["hit_b2"]),
                "b2pp_hit_r3": bool(prof["hit_b2pp"]),
                "union_hit_r3": bool(prof["hit_union"]),
                "gold_cluster_best_rank_b2": rank,
                "gold_rank_bucket": met.gold_rank_bucket(rank),
                "r3_top1_has_gold": top1_has_gold,
                "s7_like_b2": met.s7_like_trigger(rb),
                "high_reward_n_b2": met._high_reward_count(rb.get("rollout_stats") or []),  # noqa: SLF001
                "n_clusters_b2": len(met.build_clusters(rb.get("rollout_stats") or [])),
                "r3_top1_sig": top1_sig,
            })

    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "seed": src["seed"],
        "sha256": src["sha256"],
        "buckets": buckets,
        "questions": rows,
    }
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    (PLAN_DIR / "qids.txt").write_text("\n".join(all_q) + "\n", encoding="utf-8")
    (PLAN_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md = [
        "# new30_plan manifest",
        "",
        f"Generated: {manifest['generated']}",
        f"Seed: {src['seed']} | n=30 | SHA256: `{src['sha256']}`",
        "",
        "## Buckets",
        "",
        "| Bucket | n | Definition |",
        "|---|---:|---|",
        "| A_search_miss_recoverable | 10 | B′ no recall, B″/union has recall |",
        "| B_selection_miss | 10 | B′ recall, R3 miss |",
        "| C_both_no_recall | 5 | all no recall |",
        "| D_s7_false_consensus | 5 | S7-like false consensus |",
        "",
        "## Questions",
        "",
        "| qid | bucket | B′rec | B″rec | Urec | B′R3 | B″R3 | UR3 | gold rank (B′) | top1 gold? |",
        "|---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]
    for r in rows:
        md.append(
            f"| {r['qid']} | {r['bucket']} | {int(r['b2_recall'])} | {int(r['b2pp_recall'])} | "
            f"{int(r['union_recall'])} | {int(r['b2_hit_r3'])} | {int(r['b2pp_hit_r3'])} | "
            f"{int(r['union_hit_r3'])} | {r['gold_cluster_best_rank_b2'] or '—'} | "
            f"{int(r['r3_top1_has_gold'])} |"
        )
    (PLAN_DIR / "manifest.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Stage 0 OK: {len(all_q)} qids -> {PLAN_DIR}")


if __name__ == "__main__":
    main()
