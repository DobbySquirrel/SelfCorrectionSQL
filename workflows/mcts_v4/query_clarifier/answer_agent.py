"""AnswerAgent — choose candidate or abstain (no SQL visibility)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from workflows.mcts_v4.query_clarifier.llm_client import call_llm_json, load_prompt
from workflows.mcts_v4.query_clarifier.schemas import (
    AnswerInput,
    ClarificationAnswer,
    ClarificationQuestion,
)


def _format_candidates_block(cq: ClarificationQuestion) -> str:
    lines = []
    for c in cq.candidates:
        lines.append(f"{c.cid}) {c.summary}")
    return "\n".join(lines)


def abstain_answer(qid: int, reason: str = "") -> ClarificationAnswer:
    return ClarificationAnswer(qid=qid, choice=None, confidence=0.0, evidence=reason, abstain=True)


def parse_answer_response(qid: int, obj: Dict[str, Any], valid_cids: set[str]) -> ClarificationAnswer:
    abstain = bool(obj.get("abstain", False))
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    choice = obj.get("choice")
    if choice is not None:
        choice = str(choice).strip()
        if choice.lower() in ("null", "none", ""):
            choice = None
    evidence = str(obj.get("evidence", "")).strip()
    if confidence < 0.60:
        abstain = True
        choice = None
    if abstain or choice is None:
        return ClarificationAnswer(qid=qid, choice=None, confidence=confidence, evidence=evidence, abstain=True)
    if choice not in valid_cids:
        return abstain_answer(qid, f"invalid choice {choice}")
    return ClarificationAnswer(qid=qid, choice=choice, confidence=confidence, evidence=evidence, abstain=False)


def answer(
    inp: AnswerInput,
    *,
    mock_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    client=None,
    model: Optional[str] = None,
) -> ClarificationAnswer:
    cq = inp.clarification
    if cq.axis == "None" or len(cq.candidates) < 2:
        return abstain_answer(inp.qid, "no valid clarification")
    valid_cids = {c.cid for c in cq.candidates}
    tmpl = load_prompt("answer_template.txt")
    prompt = tmpl.format(
        nl_question=inp.nl_question,
        schema_ddl=inp.schema_ddl[:8000],
        axis=cq.axis,
        question=cq.question,
        candidates_block=_format_candidates_block(cq),
    )
    obj = call_llm_json(prompt, mock_fn=mock_fn, client=client, model=model)
    if not obj:
        return abstain_answer(inp.qid, "parse fail")
    return parse_answer_response(inp.qid, obj, valid_cids)
