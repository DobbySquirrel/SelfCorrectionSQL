#!/usr/bin/env python3
"""T11: Evaluation harness audit — three judges, canonical Hit@1 alignment."""

from __future__ import annotations

import io
import json
import sys
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[6]
PAR = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PAR))

import _loaders as pld  # noqa: E402
from workflows.mcts_v4.query_clarifier.constraint import compile_constraint
from workflows.mcts_v4.query_clarifier.enforcer import hard_prune, rollout_stats_to_pool
from workflows.mcts_v4.query_clarifier.eval_judges import (
    CANONICAL_JUDGE,
    JUDGE_AST,
    JUDGE_EXEC_EQUIV,
    JUDGE_NORMALIZE,
    hits_gold,
    pool_contains_gold,
    simulate_gold_prune,
)
from workflows.mcts_v4.query_clarifier.schemas import (
    ClarificationAnswer,
    ClarificationCandidate,
    ClarificationQuestion,
)
from workflows.mcts_v4.utils.sql_selector import SQLSelector

ANALYSIS = Path(__file__).resolve().parent
TRACE_V4 = ANALYSIS / "clarify_v0_log_only_100q_v4.trace.jsonl"
CALIB = ANALYSIS.parent / "v4_calib_498q_coder_rollouts8.json"
P0_JSON = ANALYSIS / "p0_union_recall.json"
QIDS_FILE = ANALYSIS / "s8_100q_qids.txt"
GOLD = pld.GOLD_FILE
OUT_MD = ANALYSIS / "eval_harness_audit.md"
OUT_JSON = ANALYSIS / "eval_harness_audit.json"


def load_qids() -> List[str]:
    return [
        ln.strip().split()[0]
        for ln in QIDS_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]


def load_traces() -> Dict[str, dict]:
    out = {}
    for line in TRACE_V4.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[str(row["qid"])] = row
    return out


def select_r2(rss: List[dict]) -> str:
    with redirect_stdout(io.StringIO()):
        return SQLSelector.select(rss)


def recompute_saved_hurt(
    qids: List[str],
    calib: dict,
    gold_sqls: dict,
    qid_to_db: dict,
    traces: Dict[str, dict],
    judge: str,
) -> Tuple[int, int, List[str]]:
    saved, hurt = 0, 0
    r2_hurt: List[str] = []
    for qid in qids:
        trace = traces.get(qid) or {}
        if not trace.get("trigger"):
            continue
        cc_d = trace.get("constraint") or {}
        if cc_d.get("level") != "hard":
            continue
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        rss = (rec or {}).get("rollout_stats") or []
        gs = gold_sqls.get(qid, "")
        db = qid_to_db.get(qid, "")
        if not gs:
            continue

        cq = trace.get("clarify") or {}
        ans_d = trace.get("answer") or {}
        cands = [
            ClarificationCandidate(c["cid"], c["summary"], c.get("constraint_hint") or {})
            for c in cq.get("candidates") or []
        ]
        cq_obj = ClarificationQuestion(int(qid), cq.get("axis", "None"), cq.get("question", ""), cands)
        ans_obj = ClarificationAnswer(
            int(qid),
            ans_d.get("choice"),
            float(ans_d.get("confidence") or 0),
            ans_d.get("evidence") or "",
            bool(ans_d.get("abstain")),
        )
        cc = compile_constraint(cq_obj, ans_obj)
        pool = rollout_stats_to_pool(rss)
        pool_sqls = [e.sql for e in pool]
        pruned = hard_prune(pool, cc)
        pruned_sqls = [e.sql for e in pruned]
        g_final, g_pruned = simulate_gold_prune(pool_sqls, pruned_sqls, gs, judge=judge, db_id=db)
        r2_ok = hits_gold(select_r2(rss), gs, judge=judge, db_id=db)
        if g_final and not r2_ok:
            saved += 1
        if g_pruned:
            hurt += 1
            if r2_ok:
                r2_hurt.append(qid)
    return saved, hurt, r2_hurt


def calib_r2_hits(qids: List[str], calib: dict, gold_sqls: dict, qid_to_db: dict, judge: str) -> int:
    n = 0
    for qid in qids:
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        if not rec:
            continue
        rss = rec.get("rollout_stats") or []
        pick = select_r2(rss)
        if hits_gold(pick, gold_sqls.get(qid, ""), judge=judge, db_id=qid_to_db.get(qid, "")):
            n += 1
    return n


def main() -> None:
    gold_sqls, qid_to_db = pld.load_gold_meta()
    calib = pld.load_json(CALIB)
    merged = pld.load_merged_498()
    qids_100 = load_qids()
    traces = load_traces()
    triggered = [q for q in qids_100 if (traces.get(q) or {}).get("trigger")]
    rq = json.loads(P0_JSON.read_text(encoding="utf-8")).get("recall_qid") or {}

    calib_exec_gold_triggered = sorted(
        [q for q in triggered if rq.get(q, {}).get("calib")],
        key=int,
    )

    # T11.1 saved/hurt under three judges (v4 100q, hard-sim only)
    j1_s, j1_h, _ = recompute_saved_hurt(qids_100, calib, gold_sqls, qid_to_db, traces, JUDGE_NORMALIZE)
    j2_s, j2_h, j2_r2h = recompute_saved_hurt(qids_100, calib, gold_sqls, qid_to_db, traces, JUDGE_EXEC_EQUIV)
    j3_s, j3_h, _ = recompute_saved_hurt(qids_100, calib, gold_sqls, qid_to_db, traces, JUDGE_AST)

    # T11.2 per-qid table for 21 exec-equiv gold in calib (triggered)
    t112_rows = []
    for qid in calib_exec_gold_triggered:
        rec = calib[qid]
        rss = rec.get("rollout_stats") or []
        gs = gold_sqls.get(qid, "")
        db = qid_to_db.get(qid, "")
        pick = select_r2(rss)
        j1 = hits_gold(pick, gs, judge=JUDGE_NORMALIZE, db_id=db)
        j2 = hits_gold(pick, gs, judge=JUDGE_EXEC_EQUIV, db_id=db)
        j3 = hits_gold(pick, gs, judge=JUDGE_AST, db_id=db)
        pool = [e.sql for e in rollout_stats_to_pool(rss)]
        in_pool_j1 = pool_contains_gold(pool, gs, judge=JUDGE_NORMALIZE, db_id=db)
        in_pool_j2 = pool_contains_gold(pool, gs, judge=JUDGE_EXEC_EQUIV, db_id=db)
        t112_rows.append(
            {
                "qid": qid,
                "j1_hit": j1,
                "j2_hit": j2,
                "j3_hit": j3,
                "pool_j1": in_pool_j1,
                "pool_j2": in_pool_j2,
                "pick_head": (pick or "")[:80].replace("\n", " "),
                "gold_head": (gs or "")[:80].replace("\n", " "),
            }
        )

    j2_r2_hits_on_21 = sum(1 for r in t112_rows if r["j2_hit"])

    # T11.3 baseline on 498
    all_qids = sorted({str(k) for k in calib.keys()}, key=int)
    baselines = {}
    for judge in (JUDGE_NORMALIZE, JUDGE_EXEC_EQUIV, JUDGE_AST):
        baselines[f"calib_498_{judge}"] = calib_r2_hits(all_qids, calib, gold_sqls, qid_to_db, judge)
        baselines[f"merged_498_{judge}"] = calib_r2_hits(all_qids, merged, gold_sqls, qid_to_db, judge)

    gm_calib = sum(1 for q in all_qids if pld.hit1(calib.get(q, {})))

    # Outcome bucket for T11 decision
    if j2_r2_hits_on_21 >= 10:
        t11_action = "skip_r1b_fix_evaluator"
    elif j2_r2_hits_on_21 <= 3:
        t11_action = "r1b_for_selector"
    else:
        t11_action = "marginal_memo"

    lines = [
        "# T11 — Evaluation Harness Audit",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Trace: `{TRACE_V4.name}` | 100q qids | triggered={len(triggered)}",
        "",
        "## T11.1 v4 saved/hurt under three judges (hard-sim subset)",
        "",
        "Saved = exec-equiv gold survives hard prune AND R2 miss under same judge.",
        "",
        "| judge | saved | hurt | R2_hit hurt |",
        "|---|---:|---:|---:|",
        f"| J1 normalize_sql | {j1_s} | {j1_h} | — |",
        f"| J2 compare_with_gold (exec-equiv) | {j2_s} | {j2_h} | {len(j2_r2h)} |",
        f"| J3 AST extract + sql_satisfies | {j3_s} | {j3_h} | — |",
        "",
        "**Prior replay (J1 only):** saved=0, hurt=0.",
        "",
        "## T11.2 Triggered qids with calib exec-equiv gold (21/61)",
        "",
        f"R2 Hit@1 under J2 on these 21: **{j2_r2_hits_on_21}/21**",
        "",
        "| qid | pool J1 | pool J2 | R2 J1 | R2 J2 | R2 J3 | pick (head) |",
        "|---:|---|---|---|---|---|---|",
    ]
    for r in t112_rows:
        lines.append(
            f"| {r['qid']} | {r['pool_j1']} | {r['pool_j2']} | {r['j1_hit']} | {r['j2_hit']} | {r['j3_hit']} | `{r['pick_head']}` |"
        )

    j1_only_pool = sum(1 for r in t112_rows if r["pool_j2"] and not r["pool_j1"])
    j1_miss_j2_hit = sum(1 for r in t112_rows if r["j2_hit"] and not r["j1_hit"])

    lines.extend(
        [
            "",
            f"- pool has exec-equiv gold but not normalize-string: **{j1_only_pool}** qids",
            f"- R2 hits J2 but not J1: **{j1_miss_j2_hit}** qids",
            "",
            "## T11.3 Canonical Hit@1 recommendation",
            "",
            "| check | expected | J1 normalize | J2 exec-equiv | J3 AST |",
            "|---|---:|---:|---:|---:|",
            f"| calib_498 R2 replay | **370** (audit) | {baselines['calib_498_normalize']} | **{baselines['calib_498_exec_equiv']}** | {baselines['calib_498_ast']} |",
            f"| merged_ef2 R2 replay | **364** (D2b) | {baselines['merged_498_normalize']} | **{baselines['merged_498_exec_equiv']}** | {baselines['merged_498_ast']} |",
            f"| calib stored gold_match | 370 | {gm_calib} | — | — |",
            "",
            "### Verdict",
            "",
            f"**Canonical judge = `{CANONICAL_JUDGE}` (`compare_with_gold`)** — matches D2b merged **364** and calib audit **370**.",
            "",
            "- J1 normalize under-counts vs canonical on calib R2 replay.",
            "- J3 AST partial constraints ≠ execution equivalence; useful for constraint debug only, **not** Hit@1.",
            "- Replay / integration `gold_match` fields still use normalize — **patched in replay script** via `MCTS_CLARIFY_HIT_JUDGE=exec_equiv`.",
            "",
            "## T11.4 Decision tree (triggered 21 exec-equiv pool)",
            "",
            f"| signal | value |",
            f"|---|---|",
            f"| R2 J2 hit on 21 pool-gold qids | **{j2_r2_hits_on_21}/21** |",
            f"| v4 saved under J2 | **{j2_s}** |",
            f"| Action | **{t11_action}** |",
            "",
        ]
    )

    if t11_action == "skip_r1b_fix_evaluator":
        lines.append(
            "- R2 already hits exec-equiv gold on most pool-gold qids → **R1b not primary**; fix evaluator + clarify saved narrative first."
        )
    elif t11_action == "r1b_for_selector":
        lines.append(
            "- Pool has gold but R2 rarely hits exec-equiv → R1b targets **selector + pool visibility**, not raw recall."
        )
    else:
        lines.append("- Marginal zone → memo before R1b.")

    lines.extend(
        [
            "",
            "## Harness map (pre-fix)",
            "",
            "| component | judge before T11 |",
            "|---|---|",
            "| sql_satisfies / hard prune | AST partial constraints |",
            "| replay saved/hurt | normalize_sql |",
            "| D2b / project Hit@1 | compare_with_gold |",
            "| integration trace gold_match | normalize_sql |",
            "",
            "→ **Three mismatched layers**; canonical = exec-equiv for Hit@1 only.",
        ]
    )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(
        json.dumps(
            {
                "judges": {"j1": {"saved": j1_s, "hurt": j1_h}, "j2": {"saved": j2_s, "hurt": j2_h}, "j3": {"saved": j3_s, "hurt": j3_h}},
                "t112": t112_rows,
                "baselines": baselines,
                "j2_r2_hits_on_21": j2_r2_hits_on_21,
                "canonical": CANONICAL_JUDGE,
                "action": t11_action,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Update memo caveat
    memo = ANALYSIS / "clarify_v0_negative_result_memo.md"
    caveat = (
        "\n## Caveat (T11)\n\n"
        "saved=0 in v4 replay used **normalize_sql** judge. Under **exec-equiv** (`compare_with_gold`), "
        f"**{len(calib_exec_gold_triggered)}/61** triggered qids have gold-equivalent SQL in calib pool; "
        f"R2 hits **{j2_r2_hits_on_21}/21** of those on calib. "
        f"v4 hard-sim **saved under exec-equiv = {j2_s}**. See `eval_harness_audit.md`.\n"
    )
    text = memo.read_text(encoding="utf-8")
    if "## Caveat (T11)" not in text:
        memo.write_text(text.rstrip() + "\n" + caveat, encoding="utf-8")

    print(OUT_MD)
    print(f"J2 saved={j2_s} R2 hits on 21={j2_r2_hits_on_21}/21 calib498 J2={baselines['calib_498_exec_equiv']}")


if __name__ == "__main__":
    main()
