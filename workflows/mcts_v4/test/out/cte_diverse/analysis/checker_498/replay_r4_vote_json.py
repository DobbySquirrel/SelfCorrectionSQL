#!/usr/bin/env python3
"""Offline R4 re-select on frozen rollout_stats JSON (no MCTS re-run).

Uses MCTS_R4_VOTE_MODE (default all_buckets via bprime_env) and pool is_correct labels.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[7]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

from selector_replay import select_sql  # noqa: E402
from workflows.mcts_v4.utils.r4_vote import cluster_vote_mode  # noqa: E402


def label_map(rec: dict) -> dict[str, bool]:
    return {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
        if (a.get("sql") or "").strip()
    }


def hit_from_pool(rec: dict, sql: str) -> bool:
    if not (sql or "").strip():
        return False
    return bool(label_map(rec).get(sql.strip()))


def replay_json(data: dict, qids: list[str] | None = None) -> dict:
    keys = sorted(qids or list(data.keys()), key=lambda x: int(x) if str(x).isdigit() else x)
    changed = 0
    hit1 = 0
    for qid in keys:
        rec = data.get(qid)
        if not rec:
            continue
        rss = rec.get("rollout_stats") or []
        if not rss:
            continue
        sql = (select_sql("R4_majority_then_reward", rss) or "").strip()
        ok = hit_from_pool(rec, sql)
        st = rec.setdefault("stats", {})
        old_sql = (rec.get("predicted_sql") or st.get("selected_sql") or "").strip()
        old_hit = bool(st.get("gold_match"))
        if sql:
            rec["predicted_sql"] = sql
            st["selected_sql"] = sql
        st["gold_match"] = ok
        st["selection_mode"] = f"offline_r4_{cluster_vote_mode()}"
        if sql != old_sql or ok != old_hit:
            changed += 1
        if ok:
            hit1 += 1
    return {"n": len(keys), "changed": changed, "hit1": hit1}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("json_in", type=Path)
    p.add_argument("-o", "--out", type=Path, help="write replayed json (default: stdout summary only)")
    p.add_argument("--qids", type=Path, help="optional manifest/json with qids list")
    args = p.parse_args()

    data = json.loads(args.json_in.read_text())
    qids = None
    if args.qids:
        manifest = json.loads(args.qids.read_text())
        qids = manifest.get("qids") or manifest

    summary = replay_json(data, qids)
    print(
        f"vote_mode={cluster_vote_mode()} n={summary['n']} "
        f"Hit@1={summary['hit1']}/{summary['n']} changed={summary['changed']}"
    )
    if args.out:
        args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
