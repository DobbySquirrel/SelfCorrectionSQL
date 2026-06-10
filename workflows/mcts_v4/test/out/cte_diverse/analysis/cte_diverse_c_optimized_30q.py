#!/usr/bin/env python3
"""30q report: calib vs diverse-C 3call+Mverify vs diverse-C 2call+noMverify."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

import selector_replay as sr  # noqa: E402

MANIFEST = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_qwen32/qids_30_manifest.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
PPL = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"
CALIB = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_30q_coder_rollouts8.json"
DIVERSE_3 = ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_30q_coder_rollouts8.json"
OPT = ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_opt_30q_coder_rollouts8.json"
REPORT = ROOT / "workflows/mcts_v4/test/out/cte_diverse/analysis/cte_diverse_c_optimized_30q.md"


def _load_gold_sqls() -> dict:
    raw = json.loads(GOLD.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    out = {}
    for item in raw:
        qid = str(item.get("question_id", item.get("qid", "")))
        sql = item.get("SQL") or item.get("sql") or item.get("gold_sql")
        if qid and sql:
            out[qid] = sql
    return out


def _hit_recall(data: dict, qids: list, gold_sqls: dict, qid_to_db: dict, cache: dict):
    hit1 = recall = 0
    for qid in qids:
        rec = data.get(qid) or {}
        rss = rec.get("rollout_stats") or []
        sql = (rec.get("optimal_sql") or rec.get("sql") or "").strip()
        if not sql and rss:
            sql = sr.select_sql("R2_max_cluster_visit", rss)
        ok = sr.eval_hit1_sql(sql, qid, gold_sqls, qid_to_db, cache) if sql else False
        if ok:
            hit1 += 1
        any_ok = any(s.get("is_correct") for s in (rec.get("all_sqls_with_attributes") or []))
        if not any_ok and rss:
            gs = gold_sqls.get(qid, "")
            db = qid_to_db.get(qid, "")
            if gs and db:
                for v in rss:
                    for info in v.get("all_sql_variants") or []:
                        s = (info.get("sql") or "").strip()
                        if s and sr.eval_hit1_sql(s, qid, gold_sqls, qid_to_db, cache):
                            any_ok = True
                            break
                    if any_ok:
                        break
        if any_ok:
            recall += 1
    return hit1, recall


def _timing(data: dict, qids: list) -> dict:
    keys = ("total_s", "rollout_s", "cte_gen_s", "sql_gen_s", "db_exec_s")
    out = {k: [] for k in keys}
    for qid in qids:
        t = (data.get(qid) or {}).get("stats", {}).get("timing") or {}
        for k in keys:
            if t.get(k) is not None:
                out[k].append(float(t[k]))
    def agg(xs):
        return {"mean": mean(xs), "median": median(xs)} if xs else {}
    return {k: agg(v) for k, v in out.items()}


def _trace_stats(data: dict, qids: list) -> dict:
    traces = []
    for qid in qids:
        traces.extend((data.get(qid) or {}).get("decompose_expand_traces") or [])
    if not traces:
        return {"mean_cands": None, "fallback_rate": None, "m_verify_skipped_rate": None, "n_traces": 0}
    fallbacks = sum(1 for t in traces if t.get("diverse_fallback"))
    skipped = sum(1 for t in traces if t.get("m_verify_skipped"))
    return {
        "mean_cands": mean([t.get("n_candidates", 0) for t in traces]),
        "fallback_rate": fallbacks / len(traces),
        "m_verify_skipped_rate": skipped / len(traces),
        "n_traces": len(traces),
        "mean_llm_calls": mean([t.get("n_llm_calls", 0) for t in traces if t.get("mode") == "C"] or [0]),
    }


def _row(label: str, data: dict | None, qids: list, gold, qdb, cache) -> dict:
    if not data:
        return {"label": label, "recall": None, "hit1": None}
    h, r = _hit_recall(data, qids, gold, qdb, cache)
    t = _timing(data, qids)
    ts = _trace_stats(data, qids)
    return {
        "label": label,
        "recall": r,
        "hit1": h,
        "time_mean": t.get("total_s", {}).get("mean"),
        "cte_gen_mean": t.get("cte_gen_s", {}).get("mean"),
        "sql_gen_mean": t.get("sql_gen_s", {}).get("mean"),
        "db_exec_mean": t.get("db_exec_s", {}).get("mean"),
        **ts,
    }


def main():
    opt_path = Path(sys.argv[1]) if len(sys.argv) > 1 else OPT
    qids = json.loads(MANIFEST.read_text(encoding="utf-8"))["qids"]
    n = len(qids)
    gold = _load_gold_sqls()
    qdb = {str(x["question_id"]): x.get("db", "") for x in json.loads(PPL.read_text(encoding="utf-8"))}
    cache: dict = {}

    calib = json.loads(CALIB.read_text(encoding="utf-8")) if CALIB.is_file() else None
    div3 = json.loads(DIVERSE_3.read_text(encoding="utf-8")) if DIVERSE_3.is_file() else None
    opt = json.loads(opt_path.read_text(encoding="utf-8")) if opt_path.is_file() else None

    rows = [
        _row("calib (temp+Mverify)", calib, qids, gold, qdb, cache),
        _row("diverse-C 3call+Mverify", div3, qids, gold, qdb, cache),
        _row("diverse-C 2call+noMverify", opt, qids, gold, qdb, cache),
    ]

    opt_row = rows[2]
    gate_recall = opt_row.get("recall") is not None and opt_row["recall"] >= 26
    gate_hit = opt_row.get("hit1") is not None and opt_row["hit1"] >= 20
    gate_time = opt_row.get("time_mean") is not None and opt_row["time_mean"] <= 150
    overall = gate_recall and gate_hit and gate_time

    def fmt(v, suffix=""):
        if v is None:
            return "—"
        if isinstance(v, float):
            return f"{v:.1f}{suffix}"
        return str(v)

    lines = [
        "# Diverse-C optimized — 30q sanity (2-call + skip M_verify)",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Optimized JSON: `{opt_path.relative_to(ROOT) if opt_path.is_file() else opt_path}`",
        "",
        "## Three-way comparison",
        "",
        "| metric | calib | diverse-C 3call+Mverify | **2call+noMverify** |",
        "|---|---:|---:|---:|",
    ]
    metrics = [
        ("recall", "recall", lambda r: f"{r['recall']}/{n}"),
        ("Hit@1 (R2)", "hit1", lambda r: f"{r['hit1']}/{n}"),
        ("mean time/qid (s)", "time_mean", lambda r: fmt(r.get("time_mean"), "s")),
        ("mean cte_gen_s", "cte_gen_mean", lambda r: fmt(r.get("cte_gen_mean"), "s")),
        ("mean sql_gen_s", "sql_gen_mean", lambda r: fmt(r.get("sql_gen_mean"), "s")),
        ("mean db_exec_s", "db_exec_mean", lambda r: fmt(r.get("db_exec_mean"), "s")),
        ("mean CTE/expand (trace)", "mean_cands", lambda r: fmt(r.get("mean_cands"))),
        ("fallback rate", "fallback_rate", lambda r: fmt(r.get("fallback_rate") * 100 if r.get("fallback_rate") is not None else None, "%")),
        ("m_verify_skipped (trace)", "m_verify_skipped_rate", lambda r: fmt(r.get("m_verify_skipped_rate") * 100 if r.get("m_verify_skipped_rate") is not None else None, "%")),
    ]
    key_map = {"recall": "recall", "Hit@1 (R2)": "hit1", "mean time/qid (s)": "time_mean"}
    for title, key, fn in metrics:
        vals = [fn(r) for r in rows]
        lines.append(f"| {title} | {vals[0]} | {vals[1]} | **{vals[2]}** |")

    calib_t = rows[0].get("time_mean") or 111
    opt_t = opt_row.get("time_mean")
    lines.extend([
        "",
        "## Gates (optimized config)",
        "",
        f"- recall >= 26: {'PASS' if gate_recall else 'FAIL'} ({opt_row.get('recall')}/{n})",
        f"- Hit@1 >= 20: {'PASS' if gate_hit else 'FAIL'} ({opt_row.get('hit1')}/{n})",
        f"- time/qid <= 150s (≤1.4× calib ~111s): {'PASS' if gate_time else 'FAIL'} ({fmt(opt_t)}s)",
        "",
        f"**Overall: {'PASS' if overall else 'FAIL'}**",
        "",
        "## Cost model check",
        "",
        "Per expand (theoretical LLM-equiv): calib ~11, diverse 3call ~23, **optimized ~9** (2 diverse + 0 M_verify).",
        "",
    ])
    if opt_t and calib_t:
        ratio = opt_t / calib_t
        lines.append(f"- Optimized / calib wall time ratio: **{ratio:.2f}×**")
    if opt_row.get("cte_gen_mean") is not None:
        lines.append(f"- Optimized mean cte_gen_s: **{opt_row['cte_gen_mean']:.1f}s/qid** (includes diverse LLM; calib was un-metered in old runs)")
    lines.append("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
