#!/usr/bin/env python3
"""Paired diff: calibrated 30q vs R2 baseline (a0/a3 frozen JSON)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

import selector_replay as sr  # noqa: E402

OUT_BASE = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder"
MANIFEST = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_qwen32/qids_30_manifest.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
BASE_A0 = OUT_BASE / "v4_a0_30q_coder_rollouts8.json"
BASE_A3 = OUT_BASE / "v4_a3_30q_coder_rollouts8.json"


def _hit_recall(path: Path, qids: list, gold_sqls: dict, qid_to_db: dict, cache: dict):
    data = json.loads(path.read_text(encoding="utf-8"))
    hit1 = recall = 0
    saved_h = hurt_h = saved_r = hurt_r = []
    sel_saved = sel_hurt = []
    for qid in qids:
        rec = data.get(qid) or {}
        rss = rec.get("rollout_stats") or []
        sql = (rec.get("optimal_sql") or rec.get("selected_sql") or "").strip()
        if not sql and rss:
            sql = sr.select_sql("R2_max_cluster_visit", rss)
        ok = sr.eval_hit1_sql(sql, qid, gold_sqls, qid_to_db, cache) if sql else False
        if ok:
            hit1 += 1
        any_ok = any(
            s.get("is_correct") for s in (rec.get("all_sqls_with_attributes") or [])
        )
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

        base = json.loads(BASE_A0.read_text(encoding="utf-8")) if path != BASE_A3 else json.loads(BASE_A3.read_text(encoding="utf-8"))
        if path == BASE_A3:
            base = json.loads(BASE_A3.read_text(encoding="utf-8"))
        else:
            base = json.loads(BASE_A0.read_text(encoding="utf-8"))
        br = base.get(qid) or {}
        brss = br.get("rollout_stats") or []
        bsql = sr.select_sql("R2_max_cluster_visit", brss) if brss else (br.get("optimal_sql") or "")
        bok = sr.eval_hit1_sql(bsql, qid, gold_sqls, qid_to_db, cache) if bsql else False
        if ok and not bok:
            saved_h.append(qid)
        if bok and not ok:
            hurt_h.append(qid)
    return hit1, recall, saved_h, hurt_h


def main():
    calib = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_BASE / "v4_calib_30q_coder_rollouts8.json"
    if not calib.is_file():
        print(f"missing {calib}", file=sys.stderr)
        sys.exit(1)

    qids = json.loads(MANIFEST.read_text(encoding="utf-8"))["qids"]
    gold_data = json.loads(GOLD.read_text(encoding="utf-8"))
    gold_sqls = {str(k): v for k, v in gold_data.items()}
    qid_to_db = {}
    ppl = json.loads(
        (ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json").read_text(
            encoding="utf-8"
        )
    )
    for item in ppl:
        qid_to_db[str(item["question_id"])] = item.get("db", "")

    cache: dict = {}
    data = json.loads(calib.read_text(encoding="utf-8"))
    hit1 = recall = 0
    saved_a0 = hurt_a0 = saved_a3 = hurt_a3 = []
    sel_qids = []

    base_a0 = json.loads(BASE_A0.read_text(encoding="utf-8"))
    base_a3 = json.loads(BASE_A3.read_text(encoding="utf-8"))

    for qid in qids:
        rec = data.get(qid) or {}
        rss = rec.get("rollout_stats") or []
        sql = (rec.get("optimal_sql") or "").strip()
        ok = sr.eval_hit1_sql(sql, qid, gold_sqls, qid_to_db, cache) if sql else False
        if ok:
            hit1 += 1
        any_ok = False
        for v in rec.get("all_sqls_with_attributes") or []:
            if v.get("is_correct"):
                any_ok = True
        if not any_ok:
            for r in rss:
                for info in r.get("all_sql_variants") or []:
                    s = (info.get("sql") or "").strip()
                    if s and sr.eval_hit1_sql(s, qid, gold_sqls, qid_to_db, cache):
                        any_ok = True
                        break
                if any_ok:
                    break
        if any_ok:
            recall += 1

        for label, base in (("a0", base_a0), ("a3", base_a3)):
            br = base.get(qid) or {}
            brss = br.get("rollout_stats") or []
            bsql = sr.select_sql("R2_max_cluster_visit", brss) if brss else ""
            bok = sr.eval_hit1_sql(bsql, qid, gold_sqls, qid_to_db, cache) if bsql else False
            if ok and not bok:
                (saved_a0 if label == "a0" else saved_a3).append(qid)
            if bok and not ok:
                (hurt_a0 if label == "a0" else hurt_a3).append(qid)

        br0 = base_a0.get(qid) or {}
        if br0.get("rollout_stats"):
            r0sql = sr.select_sql("R0_max_reward", br0["rollout_stats"])
            r2sql = sr.select_sql("R2_max_cluster_visit", br0["rollout_stats"])
            r0ok = sr.eval_hit1_sql(r0sql, qid, gold_sqls, qid_to_db, cache)
            r2ok = sr.eval_hit1_sql(r2sql, qid, gold_sqls, qid_to_db, cache)
            if r0ok and not r2ok:
                sel_qids.append(qid)

    n = len(qids)
    lines = [
        "# Stage 1 — Calibrated reward 30q sanity",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Calib JSON: `{calib.relative_to(ROOT)}`",
        "",
        "Env: `MCTS_USE_SIGNATURE_V2=1`, `MCTS_SELECTOR_STRATEGY=R2`, `MCTS_REWARD_CALIBRATED=1`",
        "",
        "## Hit@1 / Recall vs R2 baseline",
        "",
        "| Pool | Calib Hit@1 | R2 baseline | Δ Hit@1 | Calib Recall |",
        "|---|---:|---:|---:|---:|",
        f"| 30q (pipeline R2) | {hit1}/{n} | a0 **22**/30, a3 **19**/30 | a0 {hit1-22:+d}, a3 {hit1-19:+d} | {recall}/{n} |",
        "",
        "## Paired vs R2 (stored rollout_stats + R2 replay)",
        "",
        f"- **Saved vs a0 R2**: {saved_a0 or '—'}",
        f"- **Hurt vs a0 R2**: {hurt_a0 or '—'}",
        f"- **Saved vs a3 R2**: {saved_a3 or '—'}",
        f"- **Hurt vs a3 R2**: {hurt_a3 or '—'}",
        "",
        "## Selection-only on 30q (R0✓ R2✗ on baseline a0)",
        "",
        f"- qids ({len(sel_qids)}): {sel_qids or '—'}",
        f"- Calib saved on these: {[q for q in sel_qids if q in saved_a0] or '—'}",
        f"- Calib hurt on these: {[q for q in sel_qids if q in hurt_a0] or '—'}",
        "",
        "## Decision gate",
        "",
    ]
    if hit1 >= 22 and hit1 >= 19:
        gate = "✅ **PASS** — proceed to Stage 2 pending user review"
    elif hit1 <= 20 or hit1 <= 17:
        gate = "🛑 **STOP** — calibration regression ≥2 vs either baseline bound"
    else:
        gate = "⚠️ **MARGINAL** — review hurt list before Stage 2"
    lines.append(gate)
    lines.append("")
    lines.append("🛑 **Stopped for user review** (no Stage 2/498).")

    out = OUT_BASE / "analysis/calib_30q_sanity.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
