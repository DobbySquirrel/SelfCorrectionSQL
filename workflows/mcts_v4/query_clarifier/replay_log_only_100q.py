#!/usr/bin/env python3
"""S8: stratified offline replay (log_only) for AutoClarify v0."""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import sys
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier.config import ENV_ENABLE, ENV_MODE, ENV_TRACE_PATH
from workflows.mcts_v4.query_clarifier.constraint import compile_constraint
from workflows.mcts_v4.query_clarifier.enforcer import hard_prune, rollout_stats_to_pool
from workflows.mcts_v4.query_clarifier.eval_judges import (
    CANONICAL_JUDGE,
    ENV_HIT_JUDGE,
    hits_gold,
    resolve_judge,
    simulate_gold_prune,
)
from workflows.mcts_v4.query_clarifier.integration import maybe_apply_clarify
from workflows.mcts_v4.query_clarifier.logging_utils import set_trace_path
from workflows.mcts_v4.query_clarifier.triggers import build_node_stats, reset_run_clarify_count, should_clarify
from workflows.mcts_v4.utils.sql_exec_helpers import normalize_sql
from workflows.mcts_v4.utils.sql_selector import SQLSelector

DEFAULT_GOLD = _ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
ANALYSIS = _ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis"


from workflows.mcts_v4.query_clarifier.schemas import ClarifyTraceRecord

HARD_AXES = frozenset({"Measure", "Ranking", "Output"})


def load_qid_to_db(gold_path: Path) -> Dict[str, str]:
    if not gold_path.is_file():
        return {}
    data = json.loads(gold_path.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}
    if isinstance(data, list):
        for r in data:
            qid = str(r.get("question_id", r.get("qid", "")))
            out[qid] = (r.get("db_id") or r.get("db") or "").strip()
    return out


def load_trace_records(path: Path) -> Dict[str, ClarifyTraceRecord]:
    out: Dict[str, ClarifyTraceRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rec = ClarifyTraceRecord(
            qid=int(row["qid"]),
            trigger=bool(row.get("trigger")),
            trigger_reason=row.get("trigger_reason") or "",
        )
        rec.clarify = row.get("clarify")
        rec.answer = row.get("answer")
        rec.constraint = row.get("constraint")
        rec.enforcement = row.get("enforcement")
        rec.outcome = row.get("outcome")
        out[str(row["qid"])] = rec
    return out


def load_gold(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {str(r.get("question_id", r.get("qid", ""))): r.get("SQL", r.get("sql", "")) for r in data}
    return {str(k): (v.get("sql") or v.get("SQL") or "") for k, v in data.items()}


def load_qids_from_file(path: Path) -> List[str]:
    qids: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        qids.append(line.split()[0])
    return qids


def stratified_sample_qids(calib: dict, n: int, seed: int) -> List[str]:
    trigger, other = [], []
    for qid, rec in calib.items():
        rss = rec.get("rollout_stats") or []
        ns = build_node_stats(rss)
        ok, _ = should_clarify(ns)
        (trigger if ok else other).append(str(qid))
    random.seed(seed)
    half = n // 2
    t_pick = random.sample(trigger, min(half, len(trigger))) if trigger else []
    o_pick = random.sample(other, min(n - len(t_pick), len(other))) if other else []
    picked = t_pick + o_pick
    if len(picked) < n:
        rest = [q for q in calib if str(q) not in picked]
        random.shuffle(rest)
        picked.extend(str(q) for q in rest[: n - len(picked)])
    return sorted(picked, key=int)


def resolve_qids(
    calib: dict,
    *,
    qids_file: Optional[Path],
    n_q: int,
    seed: int,
) -> List[str]:
    if qids_file is not None:
        if not qids_file.is_file():
            raise SystemExit(f"[replay] fatal: qids_file not found: {qids_file}")
        qids = load_qids_from_file(qids_file)
        missing = [q for q in qids if calib.get(q) is None and calib.get(int(q)) is None]  # type: ignore[arg-type]
        if missing:
            raise SystemExit(f"[replay] fatal: {len(missing)} qid(s) missing from calib: {', '.join(missing)}")
        print(f"[replay] using qids_file={qids_file} (N={len(qids)})", flush=True)
        return qids
    qids = stratified_sample_qids(calib, n_q, seed)
    print(f"[replay] using stratified_sample_qids (N={len(qids)}, seed={seed})", flush=True)
    return qids


def mock_clarify_factory(clusters: int):
    def _mock():
        cands = []
        for i, label in enumerate(["A", "B", "C", "D"][: max(2, min(clusters, 4))]):
            cands.append(
                {
                    "cid": label,
                    "summary": f"Interpretation {label}",
                    "maps_to_cluster_rank": i + 1,
                }
            )
        return {
            "axis": "Measure",
            "question": "Should we count all rows or distinct values?",
            "candidates": cands,
            "rationale": "mock replay",
        }

    return _mock


def mock_answer_factory(abstain_rate: float, seed: int):
    rng = random.Random(seed)

    def _mock():
        if rng.random() < abstain_rate:
            return {"choice": None, "confidence": 0.5, "evidence": "ambiguous NL", "abstain": True}
        return {"choice": "A", "confidence": 0.82, "evidence": "mock evidence", "abstain": False}

    return _mock


def select_sql(rss: List[dict]) -> str:
    with redirect_stdout(io.StringIO()):
        return SQLSelector.select(rss)


def simulate_hard_gold_prune(pool, cc, gold_sql: str, *, db_id: str = "", judge: Optional[str] = None) -> Tuple[bool, bool]:
    if not gold_sql:
        return False, False
    pruned = hard_prune(pool, cc)
    pool_sqls = [e.sql for e in pool]
    pruned_sqls = [e.sql for e in pruned]
    return simulate_gold_prune(pool_sqls, pruned_sqls, gold_sql, judge=judge, db_id=db_id)


def r2_hits_gold(rss: List[dict], gold_sql: str, *, db_id: str = "", judge: Optional[str] = None) -> bool:
    if not gold_sql:
        return False
    r2_sql = select_sql(rss)
    return hits_gold(r2_sql, gold_sql, judge=judge, db_id=db_id)


def run_replay(
    calib_path: Path,
    out_md: Path,
    *,
    qids_file: Optional[Path] = None,
    trace_path: Optional[Path] = None,
    n_q: int = 100,
    seed: int = 42,
    gold_path: Optional[Path] = None,
    mock: bool = True,
    abstain_rate: float = 0.35,
    judge: Optional[str] = None,
    eval_from_trace: bool = False,
) -> None:
    hit_judge = resolve_judge(judge)
    os.environ[ENV_HIT_JUDGE] = hit_judge
    os.environ[ENV_ENABLE] = "1"
    os.environ[ENV_MODE] = "log_only"
    reset_run_clarify_count()

    trace_out = trace_path or (out_md.parent / "clarify_trace.jsonl")
    set_trace_path(trace_out)
    os.environ[ENV_TRACE_PATH] = str(trace_out)

    calib = json.loads(calib_path.read_text(encoding="utf-8"))
    gold_path = gold_path or DEFAULT_GOLD
    gold = load_gold(gold_path)
    qid_to_db = load_qid_to_db(gold_path)
    qids = resolve_qids(calib, qids_file=qids_file, n_q=n_q, seed=seed)
    frozen_traces = load_trace_records(trace_out) if eval_from_trace else {}

    rows: List[Dict[str, Any]] = []
    axis_ctr: Counter = Counter()
    confs: List[float] = []
    abstains = 0
    triggered = 0
    clarify_parse_ok = 0
    clarify_parse_attempts = 0
    evidence_nonempty = 0
    answer_count = 0
    saved, hurt, fallback = 0, 0, 0
    self_check_failed_n = 0
    hard_would_apply = 0
    empty_prune_n = 0
    non_abstain_mro = 0
    r2_hurt_qids: List[str] = []
    axis_saved: Counter = Counter()
    axis_hurt: Counter = Counter()
    baseline_final: Dict[str, str] = {}
    clarify_final: Dict[str, str] = {}

    for qid in qids:
        rec = calib.get(qid) or calib.get(int(qid))  # type: ignore[arg-type]
        rss = rec.get("rollout_stats") or []
        question = rec.get("question") or ""
        ddl = rec.get("ddl_data") or rec.get("schema") or ""
        ns = build_node_stats(rss)
        trig, treason = should_clarify(ns)
        n_clusters = ns.n_nonempty_result_buckets

        mock_c = mock_clarify_factory(n_clusters) if mock else None
        mock_a = mock_answer_factory(abstain_rate, seed + int(qid)) if mock else None

        if trig:
            triggered += 1
            clarify_parse_attempts += 1

        print(f"[replay] qid={qid} trigger={trig}", flush=True)
        if eval_from_trace and qid in frozen_traces:
            trace = frozen_traces[qid]
            rss_out = rss
        else:
            rss_out, trace = maybe_apply_clarify(
                rss,
                qid=int(qid),
                nl_question=question,
                schema_ddl=ddl,
                mock_clarify_fn=mock_c,
                mock_answer_fn=mock_a,
                gold_sql=gold.get(qid),
            )
        baseline_final[qid] = select_sql(rss)
        clarify_final[qid] = select_sql(rss_out)

        if trig and trace and trace.clarify:
            ax = trace.clarify.get("axis") or "None"
            if ax != "None" and trace.clarify.get("candidates"):
                clarify_parse_ok += 1
                axis_ctr[ax] += 1

        if trace and trace.answer:
            answer_count += 1
            if trace.answer.get("abstain"):
                abstains += 1
            else:
                confs.append(float(trace.answer.get("confidence") or 0))
            ev = (trace.answer.get("evidence") or "").strip()
            if ev:
                evidence_nonempty += 1

        if trace and trace.answer and not trace.answer.get("abstain"):
            ax = (trace.clarify or {}).get("axis")
            if ax in HARD_AXES:
                non_abstain_mro += 1

        if trace and trace.constraint:
            if trace.constraint.get("self_check_failed"):
                self_check_failed_n += 1
            if trace.constraint.get("level") == "hard" and not trace.constraint.get("self_check_failed"):
                hard_would_apply += 1

        if (
            trace
            and trace.constraint
            and trace.constraint.get("level") == "hard"
            and trace.enforcement
            and int(trace.enforcement.get("pool_after") or 0) == 0
        ):
            empty_prune_n += 1

        if trace and trace.constraint and trace.constraint.get("level") == "hard":
            from workflows.mcts_v4.query_clarifier.schemas import (
                ClarificationAnswer,
                ClarificationCandidate,
                ClarificationQuestion,
            )

            cq = trace.clarify or {}
            ans_d = trace.answer or {}
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
            db_id = qid_to_db.get(qid, "")
            g_final, g_pruned = simulate_hard_gold_prune(
                pool, cc, gold.get(qid, ""), db_id=db_id, judge=hit_judge
            )
            ax = cq.get("axis") or "None"
            r2_ok = r2_hits_gold(rss, gold.get(qid, ""), db_id=db_id, judge=hit_judge)
            if g_final and not r2_ok:
                saved += 1
                axis_saved[ax] += 1
            if g_pruned:
                hurt += 1
                axis_hurt[ax] += 1
                if r2_ok:
                    r2_hurt_qids.append(qid)
        if trace and trace.enforcement and trace.enforcement.get("safety_fallback"):
            fallback += 1

        rows.append({"qid": qid, "trigger": trig, "trigger_reason": treason, "trace": trace.to_dict() if trace else {}})

    paired_diff = [qid for qid in qids if baseline_final.get(qid) != clarify_final.get(qid)]
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    abstain_rate_actual = abstains / max(triggered, 1)
    parse_rate = clarify_parse_ok / max(clarify_parse_attempts, 1)
    evidence_rate = evidence_nonempty / max(answer_count, 1)
    trigger_rate = triggered / max(len(qids), 1)
    top_axis_pct = (max(axis_ctr.values()) / max(sum(axis_ctr.values()), 1)) if axis_ctr else 0.0

    title = f"log_only {len(qids)}q replay"
    if qids_file and "smoke" in qids_file.name:
        title = f"smoke {len(qids)}q replay"

    lines = [
        f"# AutoClarify v0 — {title}",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Source: `{calib_path}`",
        f"- Qids: `{qids_file}`" if qids_file else f"- Sample: stratified seed={seed}, n={n_q}",
        f"- Mode: log_only | mock_llm={mock} | hit_judge={hit_judge}",
        f"- eval_from_trace={eval_from_trace}",
        "",
        "## Summary metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| triggered / {len(qids)} | {triggered} ({trigger_rate:.1%}) |",
        f"| ClarifyAgent parse success (triggered) | {clarify_parse_ok}/{clarify_parse_attempts} ({parse_rate:.1%}) |",
        f"| AnswerAgent abstain rate (triggered) | {abstain_rate_actual:.1%} |",
        f"| mean confidence (non-abstain) | {mean_conf:.3f} |",
        f"| evidence non-empty (answers) | {evidence_nonempty}/{answer_count} ({evidence_rate:.1%}) |",
        "",
        "## Per-axis distribution",
        "",
        "| Axis | Count |",
        "|---|---:|",
    ]
    for ax, cnt in axis_ctr.most_common():
        lines.append(f"| {ax} | {cnt} |")
    if axis_ctr:
        lines.append(f"\n- Top axis share: {top_axis_pct:.1%} (watch if >70%)")
    lines.extend(
        [
            "",
            "## Simulated hard enforcement",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| saved (gold would enter final, R2 missed) | {saved} |",
            f"| hurt (gold hard-pruned) | {hurt} |",
            f"| **R2_hit hurt** (hard prune removed R2-correct SQL) | {len(r2_hurt_qids)} |",
            f"| safety fallback | {fallback} |",
            f"| saved - hurt | {saved - hurt} |",
            "",
        ]
    )
    if r2_hurt_qids:
        lines.append(f"- ⚠️ R2_hit hurt qids: `{', '.join(r2_hurt_qids)}`")
        lines.append("")
    lines.extend(
        [
            "### Per-axis saved / hurt",
            "",
            "| Axis | saved | hurt |",
            "|---|---:|---:|",
        ]
    )
    all_axes = sorted(set(axis_saved) | set(axis_hurt) | set(axis_ctr.keys()))
    for ax in all_axes:
        lines.append(f"| {ax} | {axis_saved[ax]} | {axis_hurt[ax]} |")
    lines.extend(
        [
            "",
            "## R2 baseline paired diff (log_only: should be empty)",
            "",
            f"- changed qids: {len(paired_diff)}",
        ]
    )
    if paired_diff[:20]:
        lines.append(f"- examples: {', '.join(paired_diff[:20])}")
    lines.extend(
        [
            "",
            "## Smoke / gate checklist",
            "",
            "| Check | Target | Actual |",
            "|---|---|---:|",
            f"| ClarifyAgent parse | ≥95% | {parse_rate:.1%} |",
            f"| abstain rate | 30–70% | {abstain_rate_actual:.1%} |",
            f"| top axis share | ≤70% | {top_axis_pct:.1%} |",
            f"| evidence non-empty | high | {evidence_rate:.1%} |",
            f"| trigger rate (~56% calib) | ~56% | {trigger_rate:.1%} |",
        f"| R2_hit hurt | 0 | {len(r2_hurt_qids)} |",
        "",
        "## v3 Case-C metrics",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| self_check_failed | {self_check_failed_n} |",
        f"| hard prune would apply (level=hard, self-check pass) | {hard_would_apply} |",
        f"| empty pool (simulated hard, pool_after=0) | {empty_prune_n} |",
        f"| non-abstain M/R/O | {non_abstain_mro} |",
        "",
        "## v3 vs v4 comparison (v3 = LLM constraint_hint + self-check filter)",
        "",
        "| metric | v3 | v4 |",
        "|---|---:|---:|",
        f"| triggered | 61 | {triggered} |",
        f"| clarify valid parse | 11 | {clarify_parse_ok} |",
        f"| non-abstain (M/R/O) | 3 | {non_abstain_mro} |",
        f"| hard prune would apply | 3 | {hard_would_apply} |",
        f"| self_check_failed | 0 | {self_check_failed_n} |",
        f"| empty pool fallbacks | 0 | {empty_prune_n} |",
        f"| saved | 0 | {saved} |",
        f"| R2_hit hurt | 0 | {len(r2_hurt_qids)} |",
        "",
        "## v2 vs v3 comparison (v2 = pre self-check baseline)",
        "",
        "| metric | v2 | v3 |",
        "|---|---:|---:|",
        f"| triggered | 61 | {triggered} |",
        f"| non-abstain (M/R/O) | 11 | {non_abstain_mro} |",
        f"| self_check_failed | n/a | {self_check_failed_n} |",
        f"| hard prune would apply | 11 | {hard_would_apply} |",
        f"| empty pool fallbacks | 7 | {empty_prune_n} |",
        f"| saved | 0 | {saved} |",
        f"| hurt | 0 | {hurt} |",
        f"| R2_hit hurt | 0 | {len(r2_hurt_qids)} |",
        "",
        f"Trace: `{trace_out}`",
        ]
    )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_md.with_suffix(".json")).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(out_md, flush=True)


def main():
    ap = argparse.ArgumentParser(description="AutoClarify v0 log_only replay")
    ap.add_argument(
        "--calib_json",
        type=Path,
        default=_ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_498q_coder_rollouts8.json",
    )
    ap.add_argument("--out_md", "--output", type=Path, dest="out_md", default=ANALYSIS / "clarify_v0_log_only_100q.md")
    ap.add_argument("--trace_path", type=Path, default=None)
    ap.add_argument("--qids_file", type=Path, default=None, help="Fixed qid list (one per line); ignores -n")
    ap.add_argument("--gold_json", type=Path, default=DEFAULT_GOLD)
    ap.add_argument("-n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--real_llm", action="store_true", help="Use live LLM instead of mock")
    ap.add_argument("--abstain_rate", type=float, default=0.35, help="Mock abstain rate")
    ap.add_argument(
        "--judge",
        choices=("normalize", "exec_equiv", "ast"),
        default=CANONICAL_JUDGE,
        help=f"Hit@1 judge for saved/hurt (default {CANONICAL_JUDGE})",
    )
    ap.add_argument(
        "--eval_from_trace",
        action="store_true",
        help="Reuse existing trace JSONL; recompute metrics only (no LLM)",
    )
    args = ap.parse_args()
    run_replay(
        args.calib_json,
        args.out_md,
        qids_file=args.qids_file,
        trace_path=args.trace_path,
        n_q=args.n,
        seed=args.seed,
        gold_path=args.gold_json,
        mock=not args.real_llm,
        abstain_rate=args.abstain_rate,
        judge=args.judge,
        eval_from_trace=args.eval_from_trace,
    )


if __name__ == "__main__":
    main()
