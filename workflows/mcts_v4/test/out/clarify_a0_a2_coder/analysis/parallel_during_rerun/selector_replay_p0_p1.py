#!/usr/bin/env python3
"""P0: oracle-free audit + cluster dumps. P1: full 447 ex-ef2 replay."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

PAR_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PAR_DIR))
import _loaders as pld  # noqa: E402

# reuse selector_replay module
import selector_replay as sr  # noqa: E402

RULES = sr.RULES


def cluster_audit_md(
    qid: str,
    rec: dict,
    gold_sqls: dict,
    qid_to_db: dict,
    cache: dict,
) -> str:
    rss = rec.get("rollout_stats") or []
    clusters = sr.build_clusters(rss)
    r0_sql = sr.pick_r0(rss)
    r2_sql = sr.pick_r2(clusters)
    r2_sig = None
    if clusters:
        r2_sig = max(clusters, key=lambda s: clusters[s].total_visit)

    lines = [
        f"### qid={qid} ({qid_to_db.get(qid, '?')})",
        "",
        "**Selection (oracle-free)**",
        f"- R2 picks cluster by **max total_visit** → sig `{str(r2_sig)[:16]}…`",
        f"- R0 picks sig from max-reward rollout (see R0 SQL hash below)",
        "",
        "| sig (12) | total_count | total_visit | max_rollout_r | rep rows | **R2** | R0 bucket? | gold? (eval only) |",
        "|---|---:|---:|---:|---:|---|---:|---|",
    ]
    r0_sig = None
    for r in rss:
        rb = r.get("result_buckets") or {}
        if not rb:
            continue
        mc = max(rb.values())
        for sig, c in rb.items():
            if c == mc and r.get("reward", 0) >= max(x.get("reward", 0) for x in rss) - 1e-6:
                r0_sig = sig
                break

    for sig, c in sorted(clusters.values(), key=lambda x: -x.total_visit):
        rep = sr._tiebreak_pick(c.variants)
        hit = sr.eval_hit1_sql(rep, qid, gold_sqls, qid_to_db, cache) if rep else False
        mark_r2 = "**PICK**" if sig == r2_sig else ""
        mark_r0 = "R0-top" if sig == r0_sig else ""
        rows = min((v[2] for v in c.variants), default=0)
        lines.append(
            f"| `{sig[:12]}…` | {c.total_count} | {c.total_visit} | {c.max_rollout_reward:.3f} | "
            f"{rows} | {mark_r2} | {mark_r0} | {'✓' if hit else '✗'} |"
        )

    lines += [
        "",
        f"- R2 selected SQL hit gold (eval): **{sr.eval_hit1_sql(r2_sql, qid, gold_sqls, qid_to_db, cache)}**",
        f"- R0 selected SQL hit gold (eval): **{sr.eval_hit1_sql(r0_sql, qid, gold_sqls, qid_to_db, cache)}**",
        f"- R2 used gold in selection? **NO** (only `total_visit` ordering)",
        "",
    ]
    return "\n".join(lines)


def run_447(fin: dict, qids: List[str], gold_sqls, qid_to_db) -> dict:
    print(f"[P1] replay {len(qids)} qids ...", flush=True)
    return sr.replay_dataset(fin, qids, gold_sqls, qid_to_db, None)


def main() -> None:
    sys.path.insert(0, str(pld.ROOT))
    gold_sqls, qid_to_db = pld.load_gold_meta()
    ef2 = pld.load_ef2()
    base = pld.load_json(pld.BASE_PATH)
    fin = pld.load_json(pld.FINAL_PATH)

    q447 = sorted([q for q in fin if str(q) not in ef2], key=int)
    sel35 = sr.selection_only_35(fin, base, ef2, gold_sqls, qid_to_db)
    audit_qids = sel35[:3]  # first 3 of 18 saved list from prior R2

    cache: dict = {}
    lines = [
        "# Selector Replay P0 + P1",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
        "",
        "## P0-1: Is this pure replay?",
        "",
        "**YES.** `selector_replay.py` only reads existing `rollout_stats` JSON:",
        "- `select_sql()` → `pick_r0`..`pick_r5` use `result_buckets`, `all_sql_variants`, `leaf_visit_count` only",
        "- **Gold / `compare_with_gold` is ONLY in `eval_hit1_sql()`** after selection",
        "- No MCTS re-run, no LLM calls, no tree expansion",
        "",
        "## P0-2: R2 oracle-free on 35 selection-only (+18 saved)",
        "",
        "R2 rule: `best_sig = max(clusters, key=total_visit)` then tiebreak pick variant (rows, len).",
        "No `is_correct`, no `gold_match`, no 'pick cluster containing gold'.",
        "",
    ]

    # verify R1-R5 saved same 18 on sel35
    sub = {q: fin[q] for q in sel35}
    quick = {}
    for rule in RULES:
        saved = []
        for qid in sel35:
            rss = sub[qid].get("rollout_stats") or []
            sql = sr.select_sql(rule, rss)
            r0_sql = sr.select_sql("R0_max_reward", rss)
            if sr.eval_hit1_sql(sql, qid, gold_sqls, qid_to_db, cache) and not sr.eval_hit1_sql(
                r0_sql, qid, gold_sqls, qid_to_db, cache
            ):
                saved.append(qid)
        quick[rule] = saved
    lines.append("**Note**: On 35 selection-only subset, saved-vs-R0 qid sets:")
    for rule in RULES[1:]:
        lines.append(f"- {rule}: {len(quick[rule])} saved — same set as R2? {quick[rule] == quick.get('R2_max_cluster_visit', [])}")
    lines.append("")

    for qid in ["1506", "232", "136"]:
        if qid in fin:
            lines.append(cluster_audit_md(qid, fin[qid], gold_sqls, qid_to_db, cache))

    lines += ["", "## P1: final_498 ex-ef2 (447q) full replay", ""]

    res = run_447(fin, q447, gold_sqls, qid_to_db)
    r0h = res["R0_max_reward"]["hit1"]
    lines.append("| Rule | Hit@1 | saved | hurt | **net** |")
    lines.append("|---|---:|---:|---:|---:|")
    for rule in RULES:
        r = res[rule]
        lines.append(
            f"| {rule} | {r['hit1']}/{len(q447)} ({100*r['hit1']/len(q447):.1f}%) | "
            f"{r['saved_vs_r0']} | {r['hurt_vs_r0']} | **{r['net_vs_r0']:+d}** |"
        )

    lines += [
        "",
        f"- R0 baseline: **{r0h}/{len(q447)}** (paper final ~308/447 ex-ef2 expected ballpark)",
        "",
        "### P2 decision hint",
        "",
    ]
    best = max(RULES, key=lambda r: res[r]["net_vs_r0"])
    net = res[best]["net_vs_r0"]
    if net >= 10:
        lines.append(f"- **{best}** net **{net:+d}** → strong signal to patch `sql_selector.py`")
    elif net >= 3:
        lines.append(f"- **{best}** net **{net:+d}** → patch worthwhile, moderate paper impact")
    elif net >= -2:
        lines.append(f"- Best net **{net:+d}** → selector layer near saturate")
    else:
        lines.append(f"- Best net **{net:+d}** → investigate before patch")

    lines += [
        "",
        "## Methodology corrections (vs prior agent summary)",
        "",
        "- a3_30 R2 -1 vs sel35 +18 are **different qid sets** — not contradictory",
        "- 30q is **sanity only** (n≈2-3 selection-only expected); ±1 is noise",
        "- **Do NOT re-run MCTS** for selector changes — replay is sufficient",
        "",
    ]

    out = PAR_DIR / "selector_replay_p0_p1_results.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (PAR_DIR / "selector_replay_447_cache.json").write_text(
        json.dumps(res, indent=2), encoding="utf-8"
    )
    print(out.read_text(encoding="utf-8"))
    print(f"[wrote] {out}")


if __name__ == "__main__":
    main()
