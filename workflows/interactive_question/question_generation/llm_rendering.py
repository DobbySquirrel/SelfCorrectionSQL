"""Step B: LLM rendering of decision axes into NL multiple-choice questions."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from .axis_aggregation import parse_unit_type
from .data_structures import DecisionAxis, RenderedQuestion
from .fidelity_validator import validate_fidelity

RENDER_PROMPT = """You are translating an internal database disambiguation \
decision into a multiple-choice question for an end user.

# Context
The user asked a natural language question over a database, but their \
intent is ambiguous: multiple SQL interpretations produce different \
execution results. You need to surface ONE specific decision point and \
phrase it as a question.

# User's Original Question
{question}

# Decision Axis
The candidate SQL interpretations differ in their `{unit_type}` clause.
Concretely, the alternatives observed in the candidate pool are:

{partition_block}

# Your Task
Generate a multiple-choice clarification question with the following parts:

1. `semantic_focus`: a SHORT phrase (2-6 words) copied or paraphrased \
   from the user's question that anchors what the question is about. \
   Examples: "highest monthly", "active users", "top customers".

2. `options`: a list of natural language descriptions, ONE PER BRANCH in \
   the decision axis above. Each option must:
   - be a single, clear, non-technical phrase the user would understand
   - faithfully describe the corresponding DSL value (do NOT invent meaning)
   - be mutually exclusive with the other options
   - NOT mention SQL syntax (no "GROUP BY", "SUM", etc.)

# Output Format
Return ONLY a JSON object with this exact structure:
{{
  "semantic_focus": "...",
  "options": [
    {{"branch_key": "<exact DSL value as given>", "nl_text": "..."}},
    ...
  ]
}}

# Constraints (CRITICAL — your output will be rejected if violated)
- The number of options MUST equal the number of branches above ({k}).
- Each `branch_key` MUST exactly match one of the DSL values given above.
- Do NOT generate a "None of the above" option (added by the system later).
- Do NOT create new options or merge branches.
"""

PARTITION_BLOCK_TEMPLATE = """Branch {idx}: DSL value = `{dsl_value}`
  (supported by worlds: {world_ids})"""


class LLMClientProtocol(Protocol):
    def complete(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        stop: list[str] | None = None,
    ) -> Any:
        ...


def _build_partition_block(axis: DecisionAxis) -> str:
    lines: list[str] = []
    for idx, (dsl_value, world_ids) in enumerate(
        sorted(axis.partition.items()), start=1,
    ):
        lines.append(PARTITION_BLOCK_TEMPLATE.format(
            idx=idx,
            dsl_value=dsl_value,
            world_ids=", ".join(world_ids),
        ))
    return "\n".join(lines)


def _parse_json_response(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def fallback_render(axis: DecisionAxis) -> RenderedQuestion:
    """DSL labels as NL text (legacy atomic-pool behavior)."""
    family, parameter = parse_unit_type(axis.unit_type)
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    options: list[dict[str, Any]] = []
    for i, (dsl_value, world_ids) in enumerate(sorted(axis.partition.items())):
        label = labels[i] if i < len(labels) else str(i)
        options.append({
            "label": label,
            "branch_key": dsl_value,
            "nl_text": dsl_value,
            "world_ids": list(world_ids),
        })
    return RenderedQuestion(
        axis_id=axis.axis_id,
        semantic_focus=axis.unit_type,
        options=options,
        fidelity_passed=False,
        raw_llm_response="",
        unit_type=axis.unit_type,
        family=family,
        parameter=parameter,
    )


def _rendered_from_parsed(
    parsed: dict,
    axis: DecisionAxis,
    raw: str,
    *,
    fidelity_passed: bool,
) -> RenderedQuestion:
    family, parameter = parse_unit_type(axis.unit_type)
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    options: list[dict[str, Any]] = []
    for i, opt in enumerate(parsed.get("options") or []):
        branch_key = opt["branch_key"]
        label = labels[i] if i < len(labels) else str(i)
        options.append({
            "label": label,
            "branch_key": branch_key,
            "nl_text": opt["nl_text"].strip(),
            "world_ids": list(axis.partition.get(branch_key, [])),
        })
    return RenderedQuestion(
        axis_id=axis.axis_id,
        semantic_focus=str(parsed.get("semantic_focus", "")).strip(),
        options=options,
        fidelity_passed=fidelity_passed,
        raw_llm_response=raw,
        unit_type=axis.unit_type,
        family=family,
        parameter=parameter,
    )


def render_question(
    axis: DecisionAxis,
    question: str,
    llm_client: LLMClientProtocol,
    *,
    temperature: float = 0.3,
    extra_instruction: str = "",
) -> tuple[RenderedQuestion, bool, str]:
    """
    Render one axis via LLM.

    Returns ``(rendered, fidelity_passed, failure_reason)``.
    """
    partition_block = _build_partition_block(axis)
    prompt = RENDER_PROMPT.format(
        question=question,
        unit_type=axis.unit_type,
        partition_block=partition_block,
        k=axis.num_branches,
    )
    if extra_instruction:
        prompt = f"{prompt}\n\n# Previous attempt failed\n{extra_instruction}"

    resp = llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=1024,
    )
    raw = getattr(resp, "text", str(resp))
    parsed = _parse_json_response(raw)
    if parsed is None:
        return fallback_render(axis), False, "invalid JSON"

    ok, reason = validate_fidelity(parsed, axis)
    rendered = _rendered_from_parsed(parsed, axis, raw, fidelity_passed=ok)
    return rendered, ok, reason


def render_with_retry(
    axis: DecisionAxis,
    question: str,
    llm_client: LLMClientProtocol,
    max_retries: int = 2,
) -> RenderedQuestion:
    """Render with retries; always returns a valid ``RenderedQuestion``."""
    last_reason = ""
    for attempt in range(max_retries):
        extra = f"Failure reason: {last_reason}" if last_reason else ""
        rendered, ok, reason = render_question(
            axis, question, llm_client,
            extra_instruction=extra if attempt > 0 else "",
        )
        if ok:
            return rendered
        last_reason = reason

    fb = fallback_render(axis)
    fb.raw_llm_response = f"fallback after {max_retries} attempts: {last_reason}"
    return fb
