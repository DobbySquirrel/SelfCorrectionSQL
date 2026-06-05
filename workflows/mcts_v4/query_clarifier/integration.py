"""MCTS integration orchestrator for AutoClarify v0."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from workflows.mcts_v4.query_clarifier import answer_agent, clarify_agent, constraint
from workflows.mcts_v4.query_clarifier.config import clarify_enabled, regen_budget
from workflows.mcts_v4.query_clarifier.enforcer import apply_pool_to_rollouts, enforce, rollout_stats_to_pool
from workflows.mcts_v4.query_clarifier.logging_utils import write_trace_record
from workflows.mcts_v4.query_clarifier.schemas import (
    AnswerInput,
    ClarifyInput,
    ClarifyTraceRecord,
    ClarificationQuestion,
    CompiledConstraint,
    EnforcementResult,
)
from workflows.mcts_v4.query_clarifier.triggers import (
    build_cluster_summaries,
    build_node_stats,
    increment_run_clarify_count,
    should_clarify,
)
from workflows.mcts_v4.utils.sql_selector import SQLSelector

KNOWN_LIMITATIONS = [
    "AnswerAgent uses same Coder backbone as SQL generation (same-bias risk).",
    "Reference/Value axes default to soft enforcement in v0.",
    "Hard prune falls back to original pool when empty or all low reward.",
]


def noop_clarify(
    rollout_stats_list: List[Dict[str, Any]],
    *,
    qid: int = 0,
) -> Tuple[List[Dict[str, Any]], Optional[ClarifyTraceRecord]]:
    return rollout_stats_list, None


def _cq_to_dict(cq: ClarificationQuestion) -> Dict[str, Any]:
    return {
        "axis": cq.axis,
        "question": cq.question,
        "candidates": [
            {
                "cid": c.cid,
                "summary": c.summary,
                "constraint_hint": c.constraint_hint,
                "maps_to_cluster_rank": c.maps_to_cluster_rank,
            }
            for c in cq.candidates
        ],
        "rationale": cq.rationale,
    }


def _cc_to_dict(cc: CompiledConstraint) -> Dict[str, Any]:
    d: Dict[str, Any] = {"level": cc.level, "axis": cc.axis}
    if cc.required_agg:
        d["required_agg"] = list(cc.required_agg)
    if cc.required_order:
        d["required_order"] = cc.required_order
    if cc.required_limit is not None:
        d["required_limit"] = cc.required_limit
    if cc.required_select_columns:
        d["required_select_columns"] = sorted(cc.required_select_columns)
    if cc.required_group_by:
        d["required_group_by"] = sorted(cc.required_group_by)
    if cc.required_tables:
        d["required_tables"] = sorted(cc.required_tables)
    if cc.banned_tables:
        d["banned_tables"] = sorted(cc.banned_tables)
    d["self_check_failed"] = cc.self_check_failed
    if cc.self_check_rep_rank is not None:
        d["self_check_rep_rank"] = cc.self_check_rep_rank
    if cc.violated_fields:
        d["violated_fields"] = list(cc.violated_fields)
    return d


def maybe_apply_clarify(
    rollout_stats_list: List[Dict[str, Any]],
    *,
    qid: int,
    nl_question: str,
    schema_ddl: str,
    exhaustion_triggered: bool = False,
    mock_clarify_fn: Optional[Callable] = None,
    mock_answer_fn: Optional[Callable] = None,
    client=None,
    model: Optional[str] = None,
    gold_sql: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[ClarifyTraceRecord]]:
    if not clarify_enabled():
        return noop_clarify(rollout_stats_list, qid=qid)

    node_stats = build_node_stats(rollout_stats_list, exhaustion_triggered=exhaustion_triggered)
    triggered, reason = should_clarify(node_stats)
    record = ClarifyTraceRecord(qid=qid, trigger=triggered, trigger_reason=reason, known_limitations=list(KNOWN_LIMITATIONS))

    if not triggered:
        write_trace_record(record)
        return rollout_stats_list, record

    increment_run_clarify_count()
    clusters = build_cluster_summaries(rollout_stats_list)
    cin = ClarifyInput(
        qid=qid,
        nl_question=nl_question,
        schema_ddl=schema_ddl,
        top_clusters=clusters,
        rollout_stats=node_stats,
    )
    cq = clarify_agent.ask(cin, mock_fn=mock_clarify_fn, client=client, model=model)
    record.clarify = _cq_to_dict(cq)

    if cq.axis == "None":
        write_trace_record(record)
        return rollout_stats_list, record

    ans = answer_agent.answer(
        AnswerInput(qid=qid, nl_question=nl_question, schema_ddl=schema_ddl, clarification=cq),
        mock_fn=mock_answer_fn,
        client=client,
        model=model,
    )
    record.answer = {
        "choice": ans.choice,
        "confidence": ans.confidence,
        "evidence": ans.evidence,
        "abstain": ans.abstain,
    }

    cc = constraint.compile_constraint(cq, ans, top_clusters=clusters)
    record.constraint = _cc_to_dict(cc)

    pool = rollout_stats_to_pool(rollout_stats_list)
    pool_out, er = enforce(pool, cc, regen_budget=regen_budget())
    record.enforcement = {
        "applied": er.applied,
        "pool_before": er.pool_before,
        "pool_after": er.pool_after,
        "regenerate_used": er.regenerate_used,
        "safety_fallback": er.safety_fallback,
    }

    rss_for_select = apply_pool_to_rollouts(rollout_stats_list, pool_out, cc)
    final_sql = SQLSelector.select(rss_for_select)
    gold_match = None
    if gold_sql:
        from workflows.mcts_v4.utils.sql_exec_helpers import normalize_sql

        gold_match = normalize_sql(final_sql) == normalize_sql(gold_sql)
    record.outcome = {
        "selector": SQLSelector.resolve_strategy(),
        "final_sql": final_sql,
        "gold_match": gold_match,
    }
    write_trace_record(record)
    return rss_for_select, record
