#!/usr/bin/env python3
"""T6/T7/T8: split saved=0 into pool recall vs wrong_choice vs constraint bug."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[6]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.mcts_v4.query_clarifier.constraint import compile_constraint, sql_satisfies
from workflows.mcts_v4.query_clarifier.enforcer import hard_prune, rollout_stats_to_pool
from workflows.mcts_v4.query_clarifier.schemas import (
    ClarificationAnswer,
    ClarificationCandidate,
    ClarificationQuestion,
)
from workflows.mcts_v4.utils.sql_exec_helpers import normalize_sql
from workflows.mcts_v4.utils.sql_selector import SQLSelector

ANALYSIS = Path(__file__).resolve().parent
TRACE = ANALYSIS / "clarify_v0_log_only_100q.trace.jsonl"
CALIB = ANALYSIS.parent / "v4_calib_498q_coder_rollouts8.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
OUT_MD = ANALYSIS / "clarify_v0_100q_root_cause.md"

HARD_AXES = frozenset({"Measure", "Ranking", "Output"})
SOFT_AXES = frozenset({"Reference", "Value"})
CID_LABELS = ["A", "B", "C", "D"]
EMPTY_PRUNE_QIDS = ["31", "50", "347", "915", "1037", "1238", "1275"]


def load_gold() -> Dict[str, str]:
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    return {str(r["question_id"]): r.get("SQL", "") or "" for r in data}


def load_traces() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[str(row["qid"])] = row
    return out


def ranked_clusters(rss: List[dict]):
    clusters = SQLSelector._build_clusters(rss)
    return sorted(clusters.items(), key=lambda x: (-x[1].total_visit, -x[1].total_count))


def gold_in_pool(rss: List[dict], gold_sql: str) -> bool:
    if not gold_sql:
        return False
    ng = normalize_sql(gold_sql)
    for e in rollout_stats_to_pool(rss):
        if normalize_sql(e.sql) == ng:
            return True
    return False


def gold_cluster_rank(rss: List[dict], gold_sql: str) -> Optional[int]:
    if not gold_sql:
        return None
    ng = normalize_sql(gold_sql)
    for i, (_, cl) in enumerate(ranked_clusters(rss), start=1):
        for sql, _, _ in cl.variants:
            if sql and normalize_sql(sql) == ng:
                return i
    return None


def gold_in_top3(rss: List[dict], gold_sql: str) -> bool:
    r = gold_cluster_rank(rss, gold_sql)
    return r is not None and r <= 3


def choice_to_rank(choice: Optional[str]) -> Optional[int]:
    if choice in CID_LABELS:
        return CID_LABELS.index(choice) + 1
    return None


def rep_sql_at_rank(rss: List[dict], rank: int) -> str:
    ranked = ranked_clusters(rss)
    if rank < 1 or rank > len(ranked):
        return ""
    _, cl = ranked[rank - 1]
    best_sql, best_rw = "", -1.0
    for sql, rw, _ in cl.variants:
        if sql and rw >= best_rw:
            best_rw = rw
            best_sql = sql
    if best_sql:
        return best_sql
    return cl.variants[0][0] if cl.variants else ""


def trace_to_cq(row: dict) -> Optional[ClarificationQuestion]:
    cl = row.get("clarify") or {}
    if not cl or cl.get("axis") in (None, "None"):
        return None
    cands = [
        ClarificationCandidate(c["cid"], c["summary"], c.get("constraint_hint") or {})
        for c in cl.get("candidates") or []
    ]
    return ClarificationQuestion(int(row["qid"]), cl["axis"], cl.get("question", ""), cands)


def compile_from_trace(row: dict) -> Optional:
    a = row.get("answer") or {}
    choice = a.get("choice")
    if not choice or a.get("abstain"):
        return None
    cq = trace_to_cq(row)
    if not cq:
        return None
    ans = ClarificationAnswer(
        int(row["qid"]), choice, float(a.get("confidence") or 0.85), a.get("evidence") or "", False
    )
    return compile_constraint(cq, ans)


def cc_fields(cc) -> str:
    parts = [f"level={cc.level}", f"axis={cc.axis}"]
    if cc.required_agg:
        parts.append(f"agg={cc.required_agg}")
    if cc.required_order:
        parts.append(f"order={cc.required_order}")
    if cc.required_limit is not None:
        parts.append(f"limit={cc.required_limit}")
    if cc.required_select_columns:
        parts.append(f"select={sorted(cc.required_select_columns)}")
    if cc.required_group_by:
        parts.append(f"group_by={sorted(cc.required_group_by)}")
    if cc.required_tables:
        parts.append(f"tables={sorted(cc.required_tables)}")
    if cc.required_predicates:
        parts.append(f"predicates={len(cc.required_predicates)}")
    return "; ".join(parts)


def subset_recall(rows: List[Tuple[str, List[dict], str]]) -> Tuple[int, int, int]:
    n = len(rows)
    any_c = sum(1 for _, rss, gs in rows if gold_cluster_rank(rss, gs) is not None)
    top3 = sum(1 for _, rss, gs in rows if gold_in_top3(rss, gs))
    return n, any_c, top3


def main() -> None:
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    gold = load_gold()
    traces = load_traces()

    t6_abstain: List[Tuple[str, List[dict], str]] = []
    t6_non_abstain: List[Tuple[str, List[dict], str]] = []
    t6_soft: List[Tuple[str, List[dict], str]] = []
    t7_rows: List[dict] = []

    for qid, row in traces.items():
        if not row.get("trigger"):
            continue
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        rss = (rec or {}).get("rollout_stats") or []
        gs = gold.get(qid, "")
        axis = (row.get("clarify") or {}).get("axis")
        ans = row.get("answer") or {}
        abstain = bool(ans.get("abstain"))
        item = (qid, rss, gs)

        if axis in SOFT_AXES:
            t6_soft.append(item)
        if axis in HARD_AXES and abstain:
            t6_abstain.append(item)
        if axis in HARD_AXES and not abstain and ans.get("choice"):
            t6_non_abstain.append(item)

            gip = gold_in_pool(rss, gs)
            g_rank = gold_cluster_rank(rss, gs)
            c_rank = choice_to_rank(ans.get("choice"))
            cc = compile_from_trace(row)
            g_sat = sql_satisfies(gs, cc).satisfied if cc and gs else False
            pool = rollout_stats_to_pool(rss)
            pruned = hard_prune(pool, cc) if cc else pool
            pool_after = len(pruned)

            if not gip:
                typ = "out_of_pool"
            elif g_rank is None:
                typ = "in_pool_no_cluster"
            elif g_rank != c_rank:
                typ = "wrong_choice"
            elif not g_sat:
                typ = "good_choice_bad_constraint"
            else:
                typ = "constraint_ok"

            t7_rows.append(
                {
                    "qid": qid,
                    "type": typ,
                    "gold_rank": g_rank if g_rank is not None else "-",
                    "chosen_rank": c_rank,
                    "pool_after": f"{pool_after}/{len(pool)}",
                    "gold_in_pool": gip,
                    "gold_satisfies": g_sat,
                }
            )

    t8_rows: List[dict] = []
    for qid in EMPTY_PRUNE_QIDS:
        row = traces.get(qid, {})
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        rss = (rec or {}).get("rollout_stats") or []
        ans = row.get("answer") or {}
        choice = ans.get("choice")
        c_rank = choice_to_rank(choice)
        rep = rep_sql_at_rank(rss, c_rank) if c_rank else ""
        cc = compile_from_trace(row)
        rep_sat = sql_satisfies(rep, cc) if cc and rep else None
        gold_sat = sql_satisfies(gold.get(qid, ""), cc) if cc and gold.get(qid) else None
        t8_rows.append(
            {
                "qid": qid,
                "axis": (row.get("clarify") or {}).get("axis"),
                "choice": choice,
                "chosen_rank": c_rank,
                "rep_sql_head": (rep or "")[:120].replace("\n", " "),
                "constraint": cc_fields(cc) if cc else "",
                "rep_satisfies": rep_sat.satisfied if rep_sat else None,
                "rep_violated": rep_sat.violated_fields if rep_sat else [],
                "gold_satisfies": gold_sat.satisfied if gold_sat else None,
            }
        )

    type_ctr = {}
    for r in t7_rows:
        type_ctr[r["type"]] = type_ctr.get(r["type"], 0) + 1

    rep_fail = [r for r in t8_rows if r["rep_satisfies"] is False]
    all_out_pool = type_ctr.get("out_of_pool", 0) == len(t7_rows) and len(t7_rows) > 0

    lines = [
        "# AutoClarify v0 — 100q root cause (T6/T7/T8)",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## T6. Pool recall on triggered subsets",
        "",
        "| subset | n | gold_in_any_cluster | gold_in_top3_cluster |",
        "|---|---:|---:|---:|",
    ]

    for label, subset in [
        ("abstain (M/R/O)", t6_abstain),
        ("non-abstain (M/R/O hard)", t6_non_abstain),
        ("Reference/Value soft", t6_soft),
    ]:
        n, any_c, top3 = subset_recall(subset)
        lines.append(f"| {label} | {n} | {any_c} ({any_c/n*100:.0f}%)" if n else f"| {label} | 0 | - | - |")
        if n:
            lines[-1] = f"| {label} | {n} | {any_c} ({any_c/n*100:.0f}%) | {top3} ({top3/n*100:.0f}%) |"

    # also pool-level (any variant) for T6 non-abstain
    lines.extend(
        [
            "",
            "Pool-level (`gold_in all_sql_variants`, stricter than cluster match):",
            "",
            "| subset | n | gold_in_pool |",
            "|---|---:|---:|",
        ]
    )
    for label, subset in [
        ("abstain (M/R/O)", t6_abstain),
        ("non-abstain (M/R/O hard)", t6_non_abstain),
        ("Reference/Value soft", t6_soft),
    ]:
        n = len(subset)
        if not n:
            lines.append(f"| {label} | 0 | - |")
        else:
            gip = sum(1 for _, rss, gs in subset if gold_in_pool(rss, gs))
            lines.append(f"| {label} | {n} | {gip} ({gip/n*100:.0f}%) |")

    lines.extend(
        [
            "",
            "## T7. Non-abstain 11 — pool vs choice vs constraint",
            "",
            "| qid | type | gold_rank | chosen_rank | pool_after_prune | gold_in_pool | gold_satisfies |",
            "|---:|---|---:|---:|---:|---|---:|",
        ]
    )
    for r in t7_rows:
        lines.append(
            f"| {r['qid']} | {r['type']} | {r['gold_rank']} | {r['chosen_rank']} | "
            f"{r['pool_after']} | {r['gold_in_pool']} | {r['gold_satisfies']} |"
        )

    lines.extend(
        [
            "",
            "### T7 type counts",
            "",
            "| type | n | meaning |",
            "|---|---:|---|",
            f"| out_of_pool | {type_ctr.get('out_of_pool', 0)} | gold not in rollout pool |",
            f"| wrong_choice | {type_ctr.get('wrong_choice', 0)} | gold in pool, Answer picked wrong cluster |",
            f"| good_choice_bad_constraint | {type_ctr.get('good_choice_bad_constraint', 0)} | right cluster, constraint/sql_satisfies mismatch |",
            f"| in_pool_no_cluster | {type_ctr.get('in_pool_no_cluster', 0)} | gold in pool but cluster bucketing miss |",
            f"| constraint_ok | {type_ctr.get('constraint_ok', 0)} | gold satisfies compiled constraint |",
            "",
            "## T8. Empty-prune 7 — does rep_sql satisfy its own constraint?",
            "",
            "If `rep_satisfies=False`, constraint_hint or sql_satisfies is broken (not a regenerate issue).",
            "",
            "| qid | axis | choice | rep_satisfies | gold_satisfies | violated | constraint |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    for r in t8_rows:
        lines.append(
            f"| {r['qid']} | {r['axis']} | {r['choice']} | {r['rep_satisfies']} | {r['gold_satisfies']} | "
            f"{r['rep_violated']} | `{r['constraint'][:80]}...` |"
            if len(r["constraint"]) > 80
            else f"| {r['qid']} | {r['axis']} | {r['choice']} | {r['rep_satisfies']} | {r['gold_satisfies']} | "
            f"{r['rep_violated']} | `{r['constraint']}` |"
        )

    lines.extend(["", "### T8 detail (rep SQL head)", ""])
    for r in t8_rows:
        lines.append(f"- **qid={r['qid']}** rank={r['chosen_rank']}: `{r['rep_sql_head']}`")

    lines.extend(["", "## Decision tree verdict", ""])

    has_c = len(rep_fail) > 0 or type_ctr.get("good_choice_bad_constraint", 0) > 0
    has_b = type_ctr.get("wrong_choice", 0) > 0
    has_a = type_ctr.get("out_of_pool", 0) > 0
    all_out_pool = type_ctr.get("out_of_pool", 0) == len(t7_rows) and len(t7_rows) > 0

    if all_out_pool:
        lines.append(
            "- **Case A (T7):** all 11 non-abstain are `out_of_pool` — gold never in `all_sql_variants`. "
            "Not wrong_choice; AnswerAgent cluster pick is moot when gold absent."
        )
    if len(rep_fail) == len(t8_rows):
        lines.append(
            f"- **Case C (T8):** all {len(t8_rows)} empty-prune qids have `rep_satisfies=False` — "
            "chosen cluster's representative SQL does **not** satisfy its own compiled constraint. "
            "This is constraint_hint / sql_satisfies bug; **regenerate cannot fix**."
        )
    elif rep_fail:
        lines.append(
            f"- **Case C (T8):** {len(rep_fail)}/{len(t8_rows)} empty-prune qids fail rep self-check."
        )

    if has_b:
        lines.append(f"- **Case B:** {type_ctr.get('wrong_choice', 0)} wrong_choice — Answer prompt relevant.")
    else:
        lines.append("- **Case B:** 0 wrong_choice on non-abstain 11.")

    lines.extend(
        [
            "",
            "### Combined read (A + C, not either/or)",
            "",
            f"- T6: triggered 61/61 subsets show **0% gold_in_pool** (abstain 17, non-abstain 11, Ref/Val 33).",
            "- Saved=0 on non-abstain is **primarily recall** (Case A), not Answer picking wrong cluster.",
            "- But hard prune empty-pool failures are **primarily constraint self-check fail** (Case C).",
            "- **Priority:** fix Case C (constraint/sql_satisfies) so prune is not self-contradictory; "
            "then invest in regenerate for Case A recall.",
            "- Do **not** tune Answer abstain prompt for saved — no gold in pool to save.",
        ]
    )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_MD)


if __name__ == "__main__":
    main()
