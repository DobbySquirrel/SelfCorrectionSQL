#!/usr/bin/env python3
"""6-scheme ablation report vs div3 / opt / t09 / Alpha-SQL."""

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
ABL_DIR = ROOT / "workflows/mcts_v4/test/out/cte_diverse/ablation_6x30q"
# 兼容误写到 cte_diverse 根目录的早期试跑
ABL_DIR_FALLBACK = ROOT / "workflows/mcts_v4/test/out/cte_diverse"
REPORT = ABL_DIR / "diverse_ablation_6x30q.md"

REPORT = ABL_DIR / "diverse_ablation_6x30q.md"
CTe_DIR = ROOT / "workflows/mcts_v4/test/out/cte_diverse"

SCHEMES = [
    ("A1", 8, "0.3,0.6", 5, 10, 8, "~220s", "2-call + variants10", None),
    ("A2", 8, "0.3,0.6", 5, 15, 8, "~260s", "2-call + variants15", None),
    ("A3", 12, "0.3,0.6", 5, 10, 12, "~260s", "2-call + variants10 + r12", None),
    ("B1", 8, "0.3,0.6,0.9", 5, 10, 8, "~240s", "3-call + variants10", None),
    ("B2", 12, "0.3,0.6,0.9", 5, 10, 12, "~280s", "3-call + r12", None),
    ("B2′", 12, "0.3,0.6,0.9", 3, 5, 12, "~200s", "B2 + N=3 sv=5 (lite)", CTe_DIR / "v4_diverse_b2_n3_sv5_30q_coder_rollouts12.json"),
    ("C1", 12, "0.3,0.6", 5, 5, 12, "~210s", "2-call + r12 only", None),
]

REF = [
    ("div3", ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_30q_coder_rollouts8.json"),
    ("opt", ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_opt_30q_coder_rollouts8.json"),
    ("t09", ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_opt_t09_n3_30q_coder_rollouts8.json"),
]


def _gold():
    raw = json.loads(GOLD.read_text(encoding="utf-8"))
    return {str(x["question_id"]): x["SQL"] for x in raw}


def _scheme_json(sid: str, rollouts: int, override: Path | None) -> Path:
    if override is not None:
        return override
    p = ABL_DIR / f"v4_diverse_abl_{sid}_30q_coder_rollouts{rollouts}.json"
    if not p.is_file():
        p = ABL_DIR_FALLBACK / f"v4_diverse_abl_{sid}_30q_coder_rollouts{rollouts}.json"
    return p


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
    return {
        "recall": recall,
        "hit1": hit1,
        "miss_r": miss_r,
        "miss_h": miss_h,
        "time_mean": mean(times) if times else None,
    }


def main():
    qids = json.loads(MANIFEST.read_text(encoding="utf-8"))["qids"]
    n = len(qids)
    gold = _gold()
    qdb = {str(x["question_id"]): x.get("db", "") for x in json.loads(PPL.read_text(encoding="utf-8"))}
    cache: dict = {}

    lines = [
        "# Diverse-C 6-scheme ablation (30q)",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Config",
        "",
        "| ID | temps | N | sql_var | rollout | est | hypothesis |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    rows = []
    for sid, r, temps, dn, sv, ro, est, hyp, jpath in SCHEMES:
        p = _scheme_json(sid, r, jpath)
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        m = _metrics(data, qids, gold, qdb, cache) if data else {}
        rows.append((sid, m, est, hyp))
        lines.append(f"| {sid} | {temps} | {dn} | {sv} | {ro} | {est} | {hyp} |")

    lines.extend(["", "## Results (M_verify OFF)", "", "| ID | recall | Hit@1 | mean time/qid | vs div3 recall | vs opt Hit@1 |", "|---|---:|---:|---:|---:|---:|"])

    div3 = json.loads(REF[0][1].read_text(encoding="utf-8")) if REF[0][1].is_file() else {}
    opt = json.loads(REF[1][1].read_text(encoding="utf-8")) if REF[1][1].is_file() else {}
    md3 = _metrics(div3, qids, gold, qdb, cache) if div3 else {}
    mopt = _metrics(opt, qids, gold, qdb, cache) if opt else {}

    completed = [(sid, m) for sid, m, _, _ in rows if m.get("recall") is not None]
    best_r_sid = max(completed, key=lambda x: (x[1].get("recall", -1), x[1].get("hit1", -1)))[0] if completed else "?"
    best_h_sid = max(completed, key=lambda x: (x[1].get("hit1", -1), x[1].get("recall", -1)))[0] if completed else "?"

    for sid, m, est, hyp in rows:
        if not m:
            lines.append(f"| {sid} | — | — | — | — | — |")
            continue
        dr = (m.get("recall") or 0) - (md3.get("recall") or 0)
        dh = (m.get("hit1") or 0) - (mopt.get("hit1") or 0)
        tm = f"{m['time_mean']:.1f}s" if m.get("time_mean") else "—"
        lines.append(f"| {sid} | {m['recall']}/{n} | {m['hit1']}/{n} | {tm} | {dr:+d} | {dh:+d} |")

    lines.extend([
        "",
        "## Reference (same 30q)",
        "",
        "| ID | recall | Hit@1 | mean time/qid |",
        "|---|---:|---:|---:|",
    ])
    ref_rows = [
        ("div3", md3),
        ("opt", mopt),
        ("t09", _metrics(json.loads(REF[2][1].read_text(encoding="utf-8")), qids, gold, qdb, cache) if REF[2][1].is_file() else {}),
    ]
    for name, m in ref_rows:
        if not m:
            lines.append(f"| {name} | — | — | — |")
            continue
        tm = f"{m['time_mean']:.1f}s" if m.get("time_mean") else "—"
        lines.append(f"| {name} | {m['recall']}/{n} | {m['hit1']}/{n} | {tm} |")

    lines.extend([
        "",
        f"- Alpha-SQL: recall 27/{n} Hit@1 21/{n}",
        "",
        "## Recall miss (ablation + B2′)",
        "",
    ])
    for sid, m, _, _ in rows:
        if m.get("miss_r") is not None:
            lines.append(f"- **{sid}**: `{m['miss_r']}`")

    lines.extend([
        "",
        "## Pick (auto heuristic)",
        "",
        f"- Best recall among ablation: **{best_r_sid}**",
        f"- Best Hit@1 among ablation: **{best_h_sid}**",
        "",
        "### B2′ note",
        "",
        "- B2′ = B2 with N=3, sql_var=5; ties **opt Hit@1 (23/30)** at ~201s vs B2 362s.",
        "- Recall 27/30 (−1 vs B2): loses 1482 only among B2 gains.",
        "",
    ])

    ABL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
