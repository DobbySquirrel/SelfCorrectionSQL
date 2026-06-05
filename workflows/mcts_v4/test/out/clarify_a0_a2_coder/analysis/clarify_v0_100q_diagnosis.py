#!/usr/bin/env python3
"""Diagnose v0 100q simulated hard saved=0 from trace + calib + gold (read-only)."""

from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[6]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.mcts_v4.query_clarifier.constraint import compile_constraint, sql_satisfies
from workflows.mcts_v4.query_clarifier.enforcer import apply_pool_to_rollouts, hard_prune, rollout_stats_to_pool
from workflows.mcts_v4.query_clarifier.schemas import (
    ClarificationAnswer,
    ClarificationCandidate,
    ClarificationQuestion,
    CompiledConstraint,
)
from workflows.mcts_v4.utils.sql_exec_helpers import normalize_sql
from workflows.mcts_v4.utils.sql_selector import SQLSelector

ANALYSIS = Path(__file__).resolve().parent
TRACE = ANALYSIS / "clarify_v0_log_only_100q.trace.jsonl"
QIDS_TXT = ANALYSIS / "s8_100q_qids.txt"
BUCKETS_JSON = ANALYSIS / "s8_100q_buckets.json"
CALIB = ANALYSIS.parent / "v4_calib_498q_coder_rollouts8.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
OUT_MD = ANALYSIS / "clarify_v0_100q_diagnosis.md"

HARD_AXES = frozenset({"Measure", "Ranking", "Output"})
SOFT_FORCE_AXES = frozenset({"Reference", "Value"})
CID_LABELS = ["A", "B", "C", "D"]


def load_gold() -> Dict[str, str]:
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    return {str(r["question_id"]): r.get("SQL", "") or "" for r in data}


def load_traces(path: Path) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        qid = str(row["qid"])
        out[qid] = row
    return out


def load_qid_buckets() -> Dict[str, str]:
    data = json.loads(BUCKETS_JSON.read_text(encoding="utf-8"))
    m: Dict[str, str] = {}
    for bucket, qids in data["buckets"].items():
        for q in qids:
            m[str(q)] = bucket
    return m


def select_r2(rss: List[dict]) -> str:
    with redirect_stdout(io.StringIO()):
        return SQLSelector.select(rss)


def r2_hits_gold(rss: List[dict], gold_sql: str) -> bool:
    if not gold_sql:
        return False
    return normalize_sql(select_r2(rss)) == normalize_sql(gold_sql)


def trace_to_cq(row: dict) -> Optional[ClarificationQuestion]:
    cl = row.get("clarify") or {}
    if not cl or cl.get("axis") in (None, "None"):
        return None
    cands = [
        ClarificationCandidate(c["cid"], c["summary"], c.get("constraint_hint") or {})
        for c in cl.get("candidates") or []
    ]
    return ClarificationQuestion(int(row["qid"]), cl["axis"], cl.get("question", ""), cands)


def trace_to_ans(row: dict) -> Optional[ClarificationAnswer]:
    a = row.get("answer")
    if not a:
        return None
    return ClarificationAnswer(
        int(row["qid"]),
        a.get("choice"),
        float(a.get("confidence") or 0),
        a.get("evidence") or "",
        bool(a.get("abstain")),
    )


def gold_cluster_cid(rss: List[dict], gold_sql: str, n_cands: int) -> Optional[str]:
    if not gold_sql:
        return None
    norm_gold = normalize_sql(gold_sql)
    clusters = SQLSelector._build_clusters(rss)
    ranked = sorted(clusters.items(), key=lambda x: (-x[1].total_visit, -x[1].total_count))
    for rank, (_, cl) in enumerate(ranked[: max(n_cands, 4)]):
        for sql, _, _ in cl.variants:
            if sql and normalize_sql(sql) == norm_gold:
                if rank < len(CID_LABELS):
                    return CID_LABELS[rank]
    return None


def compile_from_trace(row: dict, choice: Optional[str], abstain: bool, conf: float = 0.9) -> Optional[CompiledConstraint]:
    cq = trace_to_cq(row)
    if cq is None or not choice:
        return None
    ans = ClarificationAnswer(int(row["qid"]), choice, conf, "oracle", abstain=abstain)
    return compile_constraint(cq, ans)


def classify_abstain(row: dict, nl: str) -> str:
    a = row.get("answer") or {}
    if not a.get("abstain"):
        return ""
    conf = float(a.get("confidence") or 0)
    evidence = (a.get("evidence") or "").strip()
    ev_l = evidence.lower()
    nl_s = (nl or "").strip()

    if 0 < conf < 0.60:
        return "confidence_lt_0.60"

    if any(k in ev_l for k in ("domain assumption", "not stated in nl", "domain knowledge", "cannot assume")):
        return "mandatory_domain_assumption"

    if any(k in ev_l for k in ("both candidate", "two or more", "multiple candidate", "each have", "also has partial")):
        return "mandatory_multiple_support"

    if "does not explicitly" in ev_l or "not explicitly mention" in ev_l or "ambiguous" in ev_l:
        return "mandatory_no_verbatim_quote"

    if not evidence:
        return "mandatory_no_verbatim_quote"

    # Full NL question quoted → likely no unique disambiguation
    if nl_s and nl_s.lower() in ev_l and len(evidence) <= len(nl_s) + 25:
        return "mandatory_multiple_support"

    if evidence.startswith("NL:") and conf == 0.0:
        inner = evidence[3:].strip().strip("'\"")
        if inner.lower() == nl_s.lower() or len(inner) > 0.8 * len(nl_s):
            return "mandatory_multiple_support"

    if conf == 0.0:
        return "mandatory_no_verbatim_quote"

    if conf < 0.70:
        return "confidence_lt_0.60"

    return "mandatory_no_verbatim_quote"


def compute_saved(rss: List[dict], cc: CompiledConstraint, gold_sql: str) -> bool:
    if not gold_sql or cc.level != "hard":
        return False
    if r2_hits_gold(rss, gold_sql):
        return False
    pool = rollout_stats_to_pool(rss)
    pruned = hard_prune(pool, cc)
    if not pruned:
        return False
    norm_gold = normalize_sql(gold_sql)
    if norm_gold not in {normalize_sql(e.sql) for e in pruned}:
        return False
    rss_p = apply_pool_to_rollouts(rss, pruned, cc)
    return r2_hits_gold(rss_p, gold_sql)


def oracle_hurt(rss: List[dict], cc: CompiledConstraint, gold_sql: str) -> bool:
    if not gold_sql or cc.level != "hard":
        return False
    if not r2_hits_gold(rss, gold_sql):
        return False
    pool = rollout_stats_to_pool(rss)
    pruned = hard_prune(pool, cc)
    norm_gold = normalize_sql(gold_sql)
    return norm_gold in {normalize_sql(e.sql) for e in pool} and norm_gold not in {
        normalize_sql(e.sql) for e in pruned
    }


def main() -> None:
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    gold = load_gold()
    traces = load_traces(TRACE)
    qids = [ln.strip() for ln in QIDS_TXT.read_text().splitlines() if ln.strip()]
    qid_bucket = load_qid_buckets()

    # --- collect per-qid diagnostics ---
    hard_rows: List[dict] = []
    t2_reasons: Counter = Counter()
    t4_rows: List[dict] = []
    t3_oracle_satisfy = 0
    t3_oracle_saved = 0
    t3_oracle_hurt = 0
    t3_oracle_n = 0
    t3_no_gold_cluster = 0

    funnel = Counter()

    for qid in qids:
        row = traces.get(qid, {})
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        rss = (rec or {}).get("rollout_stats") or []
        gs = gold.get(qid, "")
        nl = (rec or {}).get("question") or ""
        triggered = bool(row.get("trigger"))
        axis = (row.get("clarify") or {}).get("axis")
        ans = row.get("answer") or {}
        abstain = bool(ans.get("abstain"))
        choice = ans.get("choice")

        if triggered:
            funnel["triggered"] += 1
        if triggered and axis in SOFT_FORCE_AXES:
            funnel["ref_val_soft"] += 1
        if triggered and axis in HARD_AXES:
            funnel["hard_eligible"] += 1
            hr = {"qid": qid, "axis": axis, "abstain": abstain, "choice": choice}
            hard_rows.append(hr)

            if abstain:
                funnel["hard_abstain"] += 1
                reason = classify_abstain(row, nl)
                t2_reasons[reason] += 1
                hr["abstain_reason"] = reason

                # T3 oracle counter-factual
                cq = trace_to_cq(row)
                n_c = len(cq.candidates) if cq else 0
                oc = gold_cluster_cid(rss, gs, n_c)
                if oc is None:
                    t3_no_gold_cluster += 1
                else:
                    t3_oracle_n += 1
                    cc_o = compile_from_trace(row, oc, abstain=False)
                    if cc_o and cc_o.level == "hard":
                        sat = sql_satisfies(gs, cc_o)
                        if sat.satisfied:
                            t3_oracle_satisfy += 1
                            if compute_saved(rss, cc_o, gs):
                                t3_oracle_saved += 1
                            if oracle_hurt(rss, cc_o, gs):
                                t3_oracle_hurt += 1
            else:
                funnel["hard_non_abstain"] += 1
                cc = compile_from_trace(row, choice, abstain=False, conf=float(ans.get("confidence") or 0.85))
                if cc and cc.level == "hard":
                    funnel["hard_non_abstain_hard_level"] += 1
                    pool = rollout_stats_to_pool(rss)
                    pruned = hard_prune(pool, cc)
                    if pruned:
                        funnel["hard_prune_nonempty"] += 1
                    sat = sql_satisfies(gs, cc)
                    if sat.satisfied:
                        funnel["gold_satisfies_constraint"] += 1
                    if gs and normalize_sql(gs) in {normalize_sql(e.sql) for e in pruned}:
                        funnel["gold_survives_prune"] += 1
                    if compute_saved(rss, cc, gs):
                        funnel["saved"] += 1

                    oc = gold_cluster_cid(rss, gs, len(cq.candidates) if (cq := trace_to_cq(row)) else 0)
                    t4_rows.append(
                        {
                            "qid": qid,
                            "axis": axis,
                            "choice": choice,
                            "oracle_cid": oc,
                            "gold_in_chosen_cluster": choice == oc if oc else False,
                            "gold_satisfies": sat.satisfied,
                            "violated": sat.violated_fields,
                            "prune_nonempty": bool(pruned),
                            "prune_size": len(pruned),
                            "pool_size": len(pool),
                            "r2_picks_gold": r2_hits_gold(rss, gs),
                            "saved": compute_saved(rss, cc, gs),
                        }
                    )

    # T5 bucket matrix
    bucket_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    bucket_order = ["missed_by_all", "S7_subset", "calib_only", "final_only", "R2_hit_random"]
    for qid in qids:
        b = qid_bucket.get(qid, "?")
        bucket_stats[b]["total"] += 1
        row = traces.get(qid, {})
        if row.get("trigger"):
            bucket_stats[b]["triggered"] += 1
            ans = row.get("answer") or {}
            if not ans.get("abstain") and ans.get("choice"):
                bucket_stats[b]["non_abstain"] += 1
            axis = (row.get("clarify") or {}).get("axis")
            if axis in HARD_AXES:
                bucket_stats[b]["hard_eligible"] += 1
                rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
                rss = (rec or {}).get("rollout_stats") or []
                gs = gold.get(qid, "")
                choice = ans.get("choice")
                if choice and not ans.get("abstain"):
                    cc = compile_from_trace(row, choice, False, float(ans.get("confidence") or 0.85))
                    if cc and compute_saved(rss, cc, gs):
                        bucket_stats[b]["saved"] += 1

    lines = [
        "# AutoClarify v0 — 100q diagnosis (saved=0)",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Trace: `{TRACE.name}`",
        f"- Calib: `{CALIB.name}`",
        "",
        "## Interpretation",
        "",
        "Gold is **not** passed to LLM. This report decomposes why simulated hard `saved=0`.",
        "",
        "## T1. Trigger funnel",
        "",
        "| stage | n | note |",
        "|---|---:|---|",
        f"| triggered | {funnel['triggered']} | of 100 qids |",
        f"| → Reference/Value (soft forced) | {funnel['ref_val_soft']} | v0 never simulates hard |",
        f"| → Measure/Ranking/Output (hard-eligible) | {funnel['hard_eligible']} | |",
        f"| &nbsp;&nbsp; abstain | {funnel['hard_abstain']} | AnswerAgent |",
        f"| &nbsp;&nbsp; non-abstain | {funnel['hard_non_abstain']} | |",
        f"| &nbsp;&nbsp; non-abstain → hard level | {funnel['hard_non_abstain_hard_level']} | conf≥0.80, M/R/O |",
        f"| &nbsp;&nbsp; hard prune ≠ empty | {funnel['hard_prune_nonempty']} | |",
        f"| &nbsp;&nbsp; gold satisfies constraint | {funnel['gold_satisfies_constraint']} | actual answer path |",
        f"| &nbsp;&nbsp; gold survives prune | {funnel['gold_survives_prune']} | |",
        f"| &nbsp;&nbsp; saved (R2 picks gold, baseline missed) | {funnel['saved']} | **= 0** |",
        "",
        "## T2. Abstain root cause (hard-eligible abstain only, n={})".format(funnel["hard_abstain"]),
        "",
        "| reason | n |",
        "|---|---:|",
    ]
    for reason, cnt in t2_reasons.most_common():
        lines.append(f"| {reason} | {cnt} |")

    lines.extend(
        [
            "",
            "## T3. Counter-factual upper bound (oracle = gold-cluster candidate)",
            "",
            "For each **hard-eligible abstain** qid: pick candidate matching the cluster that contains gold SQL.",
            "",
            "| metric | n |",
            "|---|---:|",
            f"| abstain qids with gold in top cluster | {t3_oracle_n} |",
            f"| abstain qids gold NOT in any top cluster | {t3_no_gold_cluster} |",
            f"| oracle choice → gold satisfies constraint | {t3_oracle_satisfy} |",
            f"| of those → saved (R2 picks gold after prune) | {t3_oracle_saved} |",
            f"| of those → R2_hit hurt (gold pruned) | {t3_oracle_hurt} |",
            "",
            "**Ceiling read:**",
        ]
    )
    if t3_oracle_saved >= 5:
        lines.append("- Oracle saved ≥ +5 → **AnswerAgent is the bottleneck**; prompt work may help.")
    elif t3_oracle_saved <= 2:
        lines.append("- Oracle saved ≤ 2 → **Framework/constraint/sql_satisfies** likely caps upside; prompt alone won't fix.")
    else:
        lines.append(f"- Oracle saved = {t3_oracle_saved} → borderline; inspect per-qid failures below.")

    if t3_oracle_hurt > 0:
        lines.append(f"- ⚠️ Oracle hurt = {t3_oracle_hurt} → hard prune design can clip gold even with perfect answer.")

    lines.extend(
        [
            "",
            "## T4. Non-abstain breakdown (hard-eligible, hard level)",
            "",
            "| qid | axis | choice | gold_cluster? | gold_satisfies? | prune_n | R2_hit? | saved? |",
            "|---:|---|---|---|---|---|---:|---:|---:|",
        ]
    )
    for r in t4_rows:
        lines.append(
            f"| {r['qid']} | {r['axis']} | {r['choice']} | {r['gold_in_chosen_cluster']} | "
            f"{r['gold_satisfies']} | {r['prune_size']}/{r['pool_size']} | {r['r2_picks_gold']} | {r['saved']} |"
        )

    lines.extend(
        [
            "",
            "## T5. Per-bucket trigger/save matrix",
            "",
            "| bucket | total | triggered | non-abstain | hard-eligible | saved |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for b in bucket_order:
        s = bucket_stats[b]
        lines.append(
            f"| {b} | {s['total']} | {s['triggered']} | {s['non_abstain']} | {s['hard_eligible']} | {s['saved']} |"
        )

    # Detail: non-abstain with violated constraints
    violated = [r for r in t4_rows if not r["gold_satisfies"]]
    empty_prune = [r for r in t4_rows if r["prune_size"] == 0]
    lines.extend(
        [
            "",
            "## Key failure modes (non-abstain hard path)",
            "",
            f"- gold **violates** compiled constraint: {len(violated)} qids → "
            + (", ".join(r["qid"] for r in violated) if violated else "(none)"),
            f"- hard prune **empties** pool: {len(empty_prune)} qids → "
            + (", ".join(r["qid"] for r in empty_prune) if empty_prune else "(none)"),
            "",
            "## Next-step decision (do NOT change prompt yet without team review)",
            "",
        ]
    )

    if t3_oracle_saved <= 2 and t3_oracle_hurt == 0:
        lines.append(
            "1. **Primary blocker:** constraint/sql_satisfies or gold-not-in-pool — not AnswerAgent abstain rate."
        )
        lines.append("2. Investigate T4 empty-prune + gold_satisfies=false qids before any prompt change.")
    elif t3_oracle_saved >= 5:
        lines.append("1. **Primary blocker:** AnswerAgent (abstain + wrong choice on non-abstain).")
        lines.append("2. Prompt tuning may help; watch R2_hit hurt if abstain is relaxed.")
    else:
        lines.append("1. Mixed bottleneck — fix constraint/prune issues on T4 qids first, then AnswerAgent.")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_MD)


if __name__ == "__main__":
    main()
