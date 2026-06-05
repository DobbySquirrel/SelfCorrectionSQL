"""ClarifyAgent — generate structured clarification questions."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from workflows.mcts_v4.query_clarifier.constraint import candidate_passes_self_check, rep_sql_for_rank
from workflows.mcts_v4.query_clarifier.extractor import extract_constraint_hint
from workflows.mcts_v4.query_clarifier.llm_client import call_llm_json, load_prompt
from workflows.mcts_v4.query_clarifier.schemas import (
    AXIS_PRIORITY,
    Axis,
    ClarificationCandidate,
    ClarificationQuestion,
    ClarifyInput,
    ClusterSummary,
)

VALID_AXES = {"Reference", "Value", "Measure", "Ranking", "Output", "None"}


def _format_cluster_table(clusters: List[ClusterSummary]) -> str:
    lines = ["rank | visit | reward | rep_sql | result_signature"]
    for c in clusters:
        sql_short = (c.representative_sql or "")[:120].replace("\n", " ")
        lines.append(
            f"{c.rank} | {c.visit} | {c.reward:.2f} | {sql_short} | {c.result_signature[:24]}"
        )
    return "\n".join(lines)


def _normalize_axis(raw: str) -> Axis:
    if raw in VALID_AXES:
        return raw  # type: ignore[return-value]
    return "None"


def _parse_candidates(raw: List[Dict[str, Any]], n_clusters: int) -> List[ClarificationCandidate]:
    out: List[ClarificationCandidate] = []
    cid_default_rank = {"A": 1, "B": 2, "C": 3, "D": 4}
    for item in raw or []:
        cid = str(item.get("cid", "")).strip()
        summary = str(item.get("summary", "")).strip()
        if not cid or not summary:
            continue
        try:
            rank = int(item.get("maps_to_cluster_rank") or cid_default_rank.get(cid, 0))
        except (TypeError, ValueError):
            rank = cid_default_rank.get(cid, 0)
        out.append(
            ClarificationCandidate(
                cid=cid,
                summary=summary,
                constraint_hint={},
                maps_to_cluster_rank=rank,
            )
        )
    max_c = min(max(n_clusters, 2), 4)
    return out[:max_c]


def _attach_extracted_hints(
    cands: List[ClarificationCandidate],
    axis: str,
    top_clusters: List[ClusterSummary],
) -> List[ClarificationCandidate]:
    out: List[ClarificationCandidate] = []
    for c in cands:
        rank = c.maps_to_cluster_rank
        sql = rep_sql_for_rank(top_clusters, rank)
        if not sql:
            continue
        hint = extract_constraint_hint(axis, sql)
        if hint is None:
            continue
        c.constraint_hint = hint
        out.append(c)
    return out


def parse_clarify_response(
    qid: int,
    obj: Dict[str, Any],
    n_clusters: int,
    top_clusters: Optional[List[ClusterSummary]] = None,
) -> ClarificationQuestion:
    axis = _normalize_axis(str(obj.get("axis", "None")))
    if axis == "None":
        return ClarificationQuestion(qid=qid, axis="None", question="", candidates=[], rationale="")
    cands = _parse_candidates(obj.get("candidates") or [], n_clusters)
    if top_clusters:
        cands = _attach_extracted_hints(cands, axis, top_clusters)
        cands = [c for c in cands if candidate_passes_self_check(c, axis, top_clusters)]
    if len(cands) < 2:
        return ClarificationQuestion(
            qid=qid, axis="None", question="", candidates=[], rationale="extract/self-check failed"
        )
    return ClarificationQuestion(
        qid=qid,
        axis=axis,
        question=str(obj.get("question", "")).strip(),
        candidates=cands,
        rationale=str(obj.get("rationale", "")).strip(),
    )


def empty_question(qid: int, reason: str = "") -> ClarificationQuestion:
    return ClarificationQuestion(qid=qid, axis="None", question="", candidates=[], rationale=reason)


def ask(
    inp: ClarifyInput,
    *,
    mock_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    client=None,
    model: Optional[str] = None,
) -> ClarificationQuestion:
    n_clusters = len(inp.top_clusters)
    if n_clusters < 2:
        return empty_question(inp.qid, "fewer than 2 clusters")

    tmpl = load_prompt("clarify_template.txt")
    prompt = tmpl.format(
        nl_question=inp.nl_question,
        schema_ddl=inp.schema_ddl[:8000],
        cluster_table=_format_cluster_table(inp.top_clusters),
    )
    obj = call_llm_json(prompt, mock_fn=mock_fn, client=client, model=model)
    if not obj:
        return empty_question(inp.qid, "parse fail")
    return parse_clarify_response(inp.qid, obj, n_clusters, top_clusters=inp.top_clusters)


def pick_dominant_axis(axes: List[str]) -> Axis:
    for a in AXIS_PRIORITY:
        if a in axes:
            return a  # type: ignore[return-value]
    return "None"
