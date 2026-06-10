#!/usr/bin/env python3
"""B2-lite (N=3, sv=5, r=12) vs B2 / opt / t09 / div3 on 30q."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

import selector_replay as sr  # noqa: E402

MANIFEST = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_qwen32/qids_30_manifest.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
PPL = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"
OUT = ROOT / "workflows/mcts_v4/test/out/cte_diverse"
ABL = ROOT / "workflows/mcts_v4/test/out/cte_diverse/ablation_6x30q"
REPORT = OUT / "b2_n3_sv5_30q.md"

RUNS = [
    ("b2-lite", OUT / "v4_diverse_b2_n3_sv5_30q_coder_rollouts12.json", "3-call N=3 sv=5 r=12"),
    ("B2", ABL / "v4_diverse_abl_B2_30q_coder_rollouts12.json", "3-call N=5 sv=10 r=12"),
    ("t09", OUT / "v4_diverse_c_opt_t09_n3_30q_coder_rollouts8.json", "3-call N=3 sv=5 r=8"),
    ("opt", OUT / "v4_diverse_c_opt_30q_coder_rollouts8.json", "2-call N=5 sv=5 r=8"),
    ("div3", OUT / "v4_diverse_c_30q_coder_rollouts8.json", "3-call + M_verify"),
]


def _gold():
    return {str(x["question_id"]): x["SQL"] for x in json.loads(GOLD.read_text(encoding="utf-8"))}


def _metrics(data: dict, qids: list, gold, qdb, cache) -> dict:
    hit1 = recall = 0
    miss_r, miss_h = [], []
    times = []
    for qid in qids:
        rec = data.get(qid) or {}
        rss = rec.get("rollout_stats") or []
        sql = (rec.get("optimal_sql") or rec.get("sql") or "").strip()
        if not sql and rss:
            sql = sr.select_sql("R2_max_cluster_visit", rss)
        ok = sr.eval_hit1_sql(sql, qid, gold, qdb, cache) if sql else False
        if ok:
            hit1 += 1
        else:
            miss_h.append(qid)
        any_ok = any(s.get("is_correct") for s in (rec.get("all_sqls_with_attributes") or []))
        if not any_ok and rss:
            for v in rss:
                for info in v.get("all_sql_variants") or []:
                    s = (info.get("sql") or "").strip()
                    if s and sr.eval_hit1_sql(s, qid, gold, qdb, cache):
                        any_ok = True
                        break
                if any_ok:
                    break
        if any_ok:
            recall += 1
        else:
            miss_r.append(qid)
        t = (rec.get("stats", {}) or {}).get("timing", {}) or {}
        if t.get("total_s") is not None:
            times.append(float(t["total_s"]))
    n = len(qids)
    return {
        "recall": recall,
        "hit1": hit1,
        "n": n,
        "miss_r": miss_r,
        "miss_h": miss_h,
        "time_mean": mean(times) if times else None,
    }


def main():
    qids = json.loads(MANIFEST.read_text(encoding="utf-8"))["qids"]
    gold = _gold()
    qdb = {str(x["question_id"]): x.get("db", "") for x in json.loads(PPL.read_text(encoding="utf-8"))}
    cache: dict = {}

    lines = [
        "# B2-lite (N=3, sv=5, r=12) — 30q",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| run | config | recall | Hit@1 | mean time/qid |",
        "|---|---|---:|---:|---:|",
    ]
    rows = []
    for name, path, cfg in RUNS:
        if not path.is_file():
            lines.append(f"| {name} | {cfg} | — | — | — |")
            continue
        m = _metrics(json.loads(path.read_text(encoding="utf-8")), qids, gold, qdb, cache)
        rows.append((name, m, cfg))
        tm = f"{m['time_mean']:.1f}s" if m.get("time_mean") else "—"
        lines.append(f"| {name} | {cfg} | {m['recall']}/{m['n']} | {m['hit1']}/{m['n']} | {tm} |")

    lite = next((m for n, m, _ in rows if n == "b2-lite"), None)
    b2 = next((m for n, m, _ in rows if n == "B2"), None)
    if lite and b2:
        lines.extend([
            "",
            "## b2-lite vs B2",
            "",
            f"- recall miss b2-lite: `{lite['miss_r']}`",
            f"- recall miss B2: `{b2['miss_r']}`",
            f"- Hit@1 miss b2-lite: `{lite['miss_h']}`",
            f"- Hit@1 miss B2: `{b2['miss_h']}`",
            "",
        ])

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
