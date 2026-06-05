#!/usr/bin/env python3
"""T9: AnswerAgent abstain root cause + counterfactual ceiling (v4 trace)."""

from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
from workflows.mcts_v4.query_clarifier.triggers import build_cluster_summaries
from workflows.mcts_v4.utils.sql_exec_helpers import normalize_sql
from workflows.mcts_v4.utils.sql_selector import SQLSelector

ANALYSIS = Path(__file__).resolve().parent
TRACE = ANALYSIS / "clarify_v0_log_only_100q_v4.trace.jsonl"
CALIB = ANALYSIS.parent / "v4_calib_498q_coder_rollouts8.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
OUT_MD = ANALYSIS / "clarify_v0_100q_v4_t9.md"

HARD_AXES = frozenset({"Measure", "Ranking", "Output"})
CID_LABELS = ["A", "B", "C", "D"]


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


def gold_candidate_cid(row: dict) -> Optional[str]:
    cl = row.get("clarify") or {}
    g_rank = row.get("_gold_rank")
    if g_rank is None:
        return None
    for c in cl.get("candidates") or []:
        try:
            rank = int(c.get("maps_to_cluster_rank") or 0)
        except (TypeError, ValueError):
            rank = 0
        if rank == g_rank:
            return c.get("cid")
    return None


def trace_to_cq(row: dict) -> Optional[ClarificationQuestion]:
    cl = row.get("clarify") or {}
    if not cl or cl.get("axis") in (None, "None"):
        return None
    cands = [
        ClarificationCandidate(
            c["cid"],
            c["summary"],
            c.get("constraint_hint") or {},
            maps_to_cluster_rank=int(c.get("maps_to_cluster_rank") or CID_LABELS.index(c["cid"]) + 1)
            if c.get("cid") in CID_LABELS
            else int(c.get("maps_to_cluster_rank") or 0),
        )
        for c in cl.get("candidates") or []
    ]
    return ClarificationQuestion(int(row["qid"]), cl["axis"], cl.get("question", ""), cands)


def compile_oracle(row: dict, choice: str, rss: List[dict]) -> Optional[Any]:
    cq = trace_to_cq(row)
    if not cq:
        return None
    ans = ClarificationAnswer(int(row["qid"]), choice, 0.9, "oracle", abstain=False)
    clusters = build_cluster_summaries(rss)
    return compile_constraint(cq, ans, top_clusters=clusters)


def select_sql(rss: List[dict]) -> str:
    with redirect_stdout(io.StringIO()):
        return SQLSelector.select(rss)


def oracle_outcome(row: dict, rss: List[dict], gold_sql: str) -> Dict[str, Any]:
    qid = str(row["qid"])
    gs = gold_sql
    gip = gold_in_pool(rss, gs)
    g_rank = gold_cluster_rank(rss, gs)
    g_top3 = g_rank is not None and g_rank <= 3
    row["_gold_rank"] = g_rank
    gc_cid = gold_candidate_cid(row) if g_top3 else None

    out: Dict[str, Any] = {
        "qid": qid,
        "gold_in_pool": gip,
        "gold_rank": g_rank,
        "gold_in_top3": g_top3,
        "gold_candidate_cid": gc_cid,
        "bucket": "unknown",
        "oracle_saved": False,
        "oracle_hurt": False,
    }

    if not gip:
        out["bucket"] = "gold_not_in_pool"
        return out
    if not g_top3:
        out["bucket"] = "gold_in_pool_not_top3"
        return out
    if not gc_cid:
        out["bucket"] = "gold_top3_no_candidate_map"
        return out

    cc = compile_oracle(row, gc_cid, rss)
    if not cc or cc.level != "hard" or cc.self_check_failed:
        out["bucket"] = "oracle_no_hard_constraint"
        return out

    pool = rollout_stats_to_pool(rss)
    ng = normalize_sql(gs)
    pruned = hard_prune(pool, cc)
    pruned_sqls = {normalize_sql(e.sql) for e in pruned}
    gold_survives = ng in pruned_sqls
    gold_pruned = gip and not gold_survives

    r2_hit = normalize_sql(select_sql(rss)) == ng
    r2_oracle = ng in pruned_sqls and normalize_sql(select_sql_from_pool(rss, pruned)) == ng

    out["gold_survives_prune"] = gold_survives
    out["gold_pruned"] = gold_pruned
    out["r2_baseline_hit"] = r2_hit

    if gold_pruned:
        out["bucket"] = "oracle_hurt"
        out["oracle_hurt"] = True
    elif r2_hit:
        out["bucket"] = "oracle_no_gain_r2_already"
    elif r2_oracle:
        out["bucket"] = "oracle_saved"
        out["oracle_saved"] = True
    else:
        out["bucket"] = "oracle_no_r2_pick"

    return out


def select_sql_from_pool(rss: List[dict], pruned_pool) -> str:
    """Simulate selector on pruned pool by filtering rollout variants."""
    pruned_norm = {normalize_sql(e.sql) for e in pruned_pool}
    filtered = []
    for r in rss:
        nr = dict(r)
        variants = []
        for v in r.get("all_sql_variants") or []:
            sql = v.get("sql") or ""
            if normalize_sql(sql) in pruned_norm:
                variants.append(v)
        if variants:
            nr["all_sql_variants"] = variants
            filtered.append(nr)
    if not filtered:
        return select_sql(rss)
    return select_sql(filtered)


def classify_abstain_reason(row: dict, question: str) -> Dict[str, bool]:
    ans = row.get("answer") or {}
    conf = float(ans.get("confidence") or 0.0)
    evidence = (ans.get("evidence") or "").strip()
    ev_l = evidence.lower()
    nl_l = (question or "").lower()

    has_quote = bool(re.search(r'["\'].+["\']', evidence)) or evidence.lower().startswith("nl:")
    if not has_quote and evidence and question:
        for chunk in re.findall(r"\w{4,}", nl_l):
            if chunk in ev_l and len(chunk) >= 5:
                has_quote = True
                break

    multi_pats = [
        "both candidate",
        "both interpretation",
        "both option",
        "two or more",
        "multiple candidate",
        "equally plausible",
        "each have at least",
        "each candidate",
        "both clusters",
        "both describe",
        "both are",
        "cannot distinguish",
        "no clear distinction",
        "ambiguous between",
    ]
    no_quote_pats = [
        "no explicit phrase",
        "cannot quote",
        "no verbatim",
        "not uniquely",
        "without such a quote",
        "cannot identify",
        "does not specify",
        "doesn't specify",
        "not stated",
        "no clear evidence",
        "insufficient",
        "unclear which",
        "cannot determine",
        "no unique",
        "without explicit",
    ]
    domain_pats = [
        "domain assumption",
        "not stated in nl",
        "implicit assumption",
        "not explicitly stated",
        "assumption not",
        "requires assumption",
        "interpretation depends",
    ]

    return {
        "confidence_lt_0.60": conf < 0.60,
        "mandatory_multiple_support": any(p in ev_l for p in multi_pats),
        "mandatory_no_verbatim_quote": (not has_quote and bool(evidence)) or any(p in ev_l for p in no_quote_pats),
        "mandatory_domain_assumption": any(p in ev_l for p in domain_pats),
        "llm_abstain_unmatched": bool(ans.get("abstain"))
        and conf < 0.60
        and not evidence
        and not any(
            p in ev_l
            for p in multi_pats + no_quote_pats + domain_pats
        ),
        "empty_evidence": not evidence.strip(),
    }


def primary_abstain_reason(flags: Dict[str, bool]) -> str:
    if flags["mandatory_multiple_support"]:
        return "mandatory: multiple support"
    if flags["mandatory_no_verbatim_quote"]:
        return "mandatory: no verbatim quote"
    if flags["mandatory_domain_assumption"]:
        return "mandatory: domain assumption"
    if flags["empty_evidence"]:
        return "empty evidence"
    if flags["confidence_lt_0.60"]:
        return "confidence < 0.60 (LLM self-rated)"
    return "LLM abstain unmatched"


def axis_none_oracle_ceiling(row: dict, rss: List[dict], gold_sql: str) -> Dict[str, Any]:
    """Upper bound: gold in top3, auto-map candidate by rank, oracle hard constraint."""
    gip = gold_in_pool(rss, gold_sql)
    g_rank = gold_cluster_rank(rss, gold_sql)
    g_top3 = g_rank is not None and g_rank <= 3
    if not gip or not g_top3:
        return {"saved": False, "reason": "no_gold_top3"}

    clusters = build_cluster_summaries(rss)
    if g_rank > len(clusters):
        return {"saved": False, "reason": "rank_beyond_top3_summaries"}

    fake_row = dict(row)
    axis = "Measure"
    fake_row["clarify"] = {
        "axis": axis,
        "question": "oracle",
        "candidates": [
            {
                "cid": "A",
                "summary": "cluster 1",
                "maps_to_cluster_rank": 1,
                "constraint_hint": {},
            },
            {
                "cid": "B",
                "summary": "cluster 2",
                "maps_to_cluster_rank": 2,
                "constraint_hint": {},
            },
        ],
    }
    if g_rank >= 3 and len(clusters) >= 3:
        fake_row["clarify"]["candidates"].append(
            {"cid": "C", "summary": "cluster 3", "maps_to_cluster_rank": 3, "constraint_hint": {}}
        )

    from workflows.mcts_v4.query_clarifier.extractor import extract_constraint_hint

    rep = clusters[g_rank - 1].representative_sql
    for cand in fake_row["clarify"]["candidates"]:
        r = int(cand["maps_to_cluster_rank"])
        sql = clusters[r - 1].representative_sql if r <= len(clusters) else ""
        hint = extract_constraint_hint(axis, sql)
        if hint:
            cand["constraint_hint"] = hint

    gc_cid = {1: "A", 2: "B", 3: "C"}.get(g_rank)
    if not gc_cid:
        return {"saved": False, "reason": "bad_rank"}

    oc = oracle_outcome(fake_row, rss, gold_sql)
    return {"saved": oc.get("oracle_saved", False), "bucket": oc.get("bucket"), "detail": oc}


def main() -> None:
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    gold = load_gold()
    traces = load_traces()

    parse_ok: List[dict] = []
    axis_none: List[dict] = []
    for qid, row in traces.items():
        if not row.get("trigger"):
            continue
        cl = row.get("clarify") or {}
        if cl.get("axis") in (None, "None") or not cl.get("candidates"):
            axis_none.append(row)
        else:
            parse_ok.append(row)

    abstain_rows = [r for r in parse_ok if (r.get("answer") or {}).get("abstain")]
    non_abstain_rows = [r for r in parse_ok if not (r.get("answer") or {}).get("abstain")]

    # T9.1
    reason_ctr = Counter()
    flag_totals = Counter()
    primary_ctr = Counter()
    for row in abstain_rows:
        qid = str(row["qid"])
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        question = (rec or {}).get("question") or ""
        flags = classify_abstain_reason(row, question)
        for k, v in flags.items():
            if v:
                flag_totals[k.replace("_", " ")] += 1
        primary_ctr[primary_abstain_reason(flags)] += 1

    # T9.2 + T9.3
    t92_rows: List[dict] = []
    t93_buckets = Counter()
    oracle_saved_qids: List[str] = []
    for row in abstain_rows:
        qid = str(row["qid"])
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        rss = (rec or {}).get("rollout_stats") or []
        gs = gold.get(qid, "")
        gip = gold_in_pool(rss, gs)
        g_rank = gold_cluster_rank(rss, gs)
        row["_gold_rank"] = g_rank
        gc = gold_candidate_cid(row) if g_rank and g_rank <= 3 else None
        t92_rows.append(
            {
                "qid": qid,
                "gold_in_pool": gip,
                "gold_in_top3": gold_in_top3(rss, gs),
                "gold_rank": g_rank if g_rank else "-",
                "gold_candidate_cid": gc or "-",
            }
        )
        oc = oracle_outcome(row, rss, gs)
        t93_buckets[oc["bucket"]] += 1
        if oc.get("oracle_saved"):
            oracle_saved_qids.append(qid)

    # T9.4
    t94_rows = []
    for row in non_abstain_rows:
        qid = str(row["qid"])
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        rss = (rec or {}).get("rollout_stats") or []
        gs = gold.get(qid, "")
        ans = row.get("answer") or {}
        choice = ans.get("choice")
        gip = gold_in_pool(rss, gs)
        g_rank = gold_cluster_rank(rss, gs)
        cl = row.get("clarify") or {}
        chosen_rank = None
        for c in cl.get("candidates") or []:
            if c.get("cid") == choice:
                chosen_rank = c.get("maps_to_cluster_rank")
        saved = False
        if gip and gs:
            cc_d = row.get("constraint") or {}
            if cc_d.get("level") == "hard" and not cc_d.get("self_check_failed"):
                pool = rollout_stats_to_pool(rss)
                pruned = hard_prune(pool, compile_oracle(row, choice, rss))
                saved = normalize_sql(gs) in {normalize_sql(e.sql) for e in pruned} and normalize_sql(
                    select_sql(rss)
                ) != normalize_sql(gs)
        t94_rows.append(
            {
                "qid": qid,
                "choice": choice,
                "confidence": ans.get("confidence"),
                "gold_in_pool": gip,
                "gold_rank": g_rank,
                "chosen_rank": chosen_rank,
                "gold_in_chosen_cluster": g_rank is not None and chosen_rank == g_rank,
                "saved": saved,
            }
        )

    # T9.5
    axis_none_gip = 0
    axis_none_top3 = 0
    axis_none_oracle_saved = 0
    t95_rows = []
    for row in axis_none:
        qid = str(row["qid"])
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        rss = (rec or {}).get("rollout_stats") or []
        gs = gold.get(qid, "")
        gip = gold_in_pool(rss, gs)
        gt3 = gold_in_top3(rss, gs)
        if gip:
            axis_none_gip += 1
        if gt3:
            axis_none_top3 += 1
        oc = axis_none_oracle_ceiling(row, rss, gs)
        if oc.get("saved"):
            axis_none_oracle_saved += 1
        t95_rows.append({"qid": qid, "gold_in_pool": gip, "gold_in_top3": gt3, "oracle_saved_if_extract": oc.get("saved", False)})

    lines = [
        "# AutoClarify v0 — T9 AnswerAgent abstain root cause (v4)",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Trace: `{TRACE.name}`",
        f"- Parse OK: {len(parse_ok)} | Abstain: {len(abstain_rows)} | axis=None: {len(axis_none)}",
        "",
        "## T9.1 Abstain reason breakdown (28 abstain qids)",
        "",
        "Non-exclusive flags (one qid may match multiple mandatory rules):",
        "",
        "| reason | n |",
        "|---|---:|",
        f"| confidence < 0.60 | {flag_totals.get('confidence lt 0.60', 0)} |",
        f"| mandatory: multiple support | {flag_totals.get('mandatory multiple support', 0)} |",
        f"| mandatory: no verbatim quote | {flag_totals.get('mandatory no verbatim quote', 0)} |",
        f"| mandatory: domain assumption | {flag_totals.get('mandatory domain assumption', 0)} |",
        f"| empty evidence | {flag_totals.get('empty evidence', 0)} |",
        f"| LLM abstain unmatched | {flag_totals.get('llm abstain unmatched', 0)} |",
        "",
        "Primary reason (mutually exclusive, priority order):",
        "",
        "| primary reason | n |",
        "|---|---:|",
    ]
    for reason, n in primary_ctr.most_common():
        lines.append(f"| {reason} | {n} |")

    lines.extend(
        [
            "",
            "Note: all 28 abstain rows have `confidence=0.0` in trace (LLM self-rated below 0.60). "
            "Mandatory-rule text in `evidence` explains *why*.",
            "",
            "## T9.2 Pool reality on 28 abstain qids",
            "",
            "| qid | gold_in_pool | gold_in_top3 | gold_rank | gold_candidate_cid |",
            "|---:|---|---|---:|---|",
        ]
    )
    for r in sorted(t92_rows, key=lambda x: int(x["qid"])):
        lines.append(
            f"| {r['qid']} | {r['gold_in_pool']} | {r['gold_in_top3']} | {r['gold_rank']} | {r['gold_candidate_cid']} |"
        )

    gip_abst = sum(1 for r in t92_rows if r["gold_in_pool"])
    gt3_abst = sum(1 for r in t92_rows if r["gold_in_top3"])
    gc_map = sum(1 for r in t92_rows if r["gold_candidate_cid"] not in ("-", None))
    lines.extend(
        [
            "",
            f"- gold_in_pool: **{gip_abst}/{len(t92_rows)}** ({100*gip_abst/len(t92_rows):.0f}%)",
            f"- gold_in_top3: **{gt3_abst}/{len(t92_rows)}**",
            f"- gold_candidate mappable: **{gc_map}/{len(t92_rows)}**",
            "",
            "## T9.3 Counterfactual ceiling on 28 abstain (oracle answer)",
            "",
            "Oracle: pick `gold_candidate_cid`, compile hard constraint, simulate hard prune + R2.",
            "",
            "```",
            f"n abstain qids = {len(abstain_rows)}",
            f"├─ gold not in pool                     = {t93_buckets.get('gold_not_in_pool', 0)}",
            f"├─ gold in pool but not top3 cluster    = {t93_buckets.get('gold_in_pool_not_top3', 0)}",
            f"├─ gold in top3, no candidate maps      = {t93_buckets.get('gold_top3_no_candidate_map', 0)}",
            f"├─ oracle no hard / self-check fail     = {t93_buckets.get('oracle_no_hard_constraint', 0)}",
            f"├─ oracle hurt (constraint excludes gold)= {t93_buckets.get('oracle_hurt', 0)}",
            f"├─ oracle no gain (R2 already hits gold)= {t93_buckets.get('oracle_no_gain_r2_already', 0)}",
            f"├─ oracle no R2 pick (survives but R2≠gold) = {t93_buckets.get('oracle_no_r2_pick', 0)}",
            f"└─ **oracle saved**                     = **{t93_buckets.get('oracle_saved', 0)}**  ← v0 ceiling",
            "```",
        ]
    )
    if oracle_saved_qids:
        lines.append(f"- oracle saved qids: `{', '.join(oracle_saved_qids)}`")
    else:
        lines.append("- oracle saved qids: *(none)*")

    lines.extend(
        [
            "",
            "## T9.4 Non-abstain qids (72, 948)",
            "",
            "| qid | choice | conf | gold_in_pool | gold_rank | chosen_rank | gold_in_chosen_cluster | saved |",
            "|---:|---|---:|---|---:|---:|---|---|",
        ]
    )
    for r in t94_rows:
        lines.append(
            f"| {r['qid']} | {r['choice']} | {r['confidence']} | {r['gold_in_pool']} | "
            f"{r['gold_rank'] if r['gold_rank'] else '-'} | {r['chosen_rank']} | "
            f"{r['gold_in_chosen_cluster']} | {r['saved']} |"
        )

    lines.extend(
        [
            "",
            "## T9.5 axis=None qids (31 extract failures)",
            "",
            f"- gold_in_pool: **{axis_none_gip}/{len(axis_none)}** ({100*axis_none_gip/len(axis_none) if axis_none else 0:.0f}%)",
            f"- gold_in_top3: **{axis_none_top3}/{len(axis_none)}**",
            f"- counterfactual oracle saved (if extract+oracle): **{axis_none_oracle_saved}/{len(axis_none)}**",
            "",
            "| qid | gold_in_pool | gold_in_top3 | oracle_saved_if_extract |",
            "|---:|---|---|---|",
        ]
    )
    for r in sorted(t95_rows, key=lambda x: int(x["qid"])):
        lines.append(
            f"| {r['qid']} | {r['gold_in_pool']} | {r['gold_in_top3']} | {r['oracle_saved_if_extract']} |"
        )

    # Decision tree
    oracle_saved = t93_buckets.get("oracle_saved", 0)
    lines.extend(["", "## Decision tree (from T9)", ""])
    if oracle_saved >= 8:
        verdict = "解释 A：prompt 过严 — AnswerAgent 能救但被 abstain 拦住"
        action = "调 answer prompt（放松 mandatory rules）"
    elif oracle_saved >= 3:
        verdict = "中间地带：部分能救"
        action = "微调 prompt + 接受较低 ceiling"
    else:
        verdict = "解释 B：候选真不可分辨 / gold 不在 pool — prompt 改不动 saved"
        action = "转 R1 regenerate（解 Case A），勿优先调 prompt"

    extract_verdict = (
        "extract 修补值得做"
        if axis_none_gip >= 8
        else "extract 31 题主要是 Case A — 不修 extractor"
    )

    lines.extend(
        [
            f"| Gate | Value | Verdict |",
            f"|---|---|---|",
            f"| T9.3 oracle saved / 28 | **{oracle_saved}** | {verdict} |",
            f"| Recommended action | | **{action}** |",
            f"| T9.5 gold_in_pool / 31 | **{axis_none_gip}** | {extract_verdict} |",
            "",
            "### Combined read",
            "",
            f"- Abstain 28/30 全部 confidence=0.0；primary 原因以 **no verbatim quote** / **multiple support** 为主 → prompt 行为符合设计。",
            f"- 但 oracle saved 仅 **{oracle_saved}/28** → {'prompt 放松可能有效' if oracle_saved >= 8 else '即使 oracle 也救不了多数 → Case A 主导'}。",
            f"- axis=None 31 题 gold_in_pool **{axis_none_gip}/31** → extract 修补边际收益 {'低' if axis_none_gip <= 3 else '待评估'}。",
        ]
    )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_MD)
    print(f"T9.3 oracle_saved={oracle_saved}/28")
    print(f"T9.5 axis_none gold_in_pool={axis_none_gip}/31")


if __name__ == "__main__":
    main()
