"""Form-enumerated D-axis generator (beam-CTE stage 2)."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from workflows.mcts_v5.taxonomy.axes import SKIP_CTE_MARKER
from workflows.mcts_v5.utils.baselines import extract_sql

from .a_axis_generator import (
    _extract_cte_name,
    _probe_candidate,
    _truncate_schema,
)
from .c_axis_generator import _parent_chain_text
from .types import AxisCandidate

D_FORM_ORDER: tuple[str, ...] = (
    "SKIP",
    "ORDER_LIMIT",
    "EXTREMUM_SUBQUERY",
    "WINDOW_RANK",
)

D_FORM_SPECS = {
    "SKIP": {
        "instruction": "Output <SKIP> when the question requires no ranking/ordering.",
        "example_hint": "<SKIP>",
    },
    "ORDER_LIMIT": {
        "instruction": (
            "Use ORDER BY <col> ASC|DESC LIMIT k. Suitable when the question "
            "asks for 'top k' / 'first k' / 'last k' results."
        ),
        "example_hint": (
            "WITH d_top1 AS (SELECT * FROM c_xxx ORDER BY col DESC LIMIT 1)"
        ),
    },
    "EXTREMUM_SUBQUERY": {
        "instruction": (
            "Filter rows where a column equals its MAX/MIN over a subset. "
            "REQUIRED when the question asks for 'youngest/oldest/highest/"
            "lowest' AND the answer may have multiple rows tied at the "
            "extremum, OR when the extremum must be computed over a "
            "subset different from the row scope."
        ),
        "example_hint": (
            "WITH d_youngest AS (SELECT * FROM c_xxx "
            "WHERE Birthday = (SELECT MAX(Birthday) FROM c_xxx))"
        ),
    },
    "WINDOW_RANK": {
        "instruction": (
            "Use ROW_NUMBER() / RANK() / DENSE_RANK() OVER (...). Required "
            "when the question explicitly asks for 'rank' or for partition-"
            "wise top-k."
        ),
        "example_hint": (
            "WITH d_rank AS (SELECT *, RANK() OVER (ORDER BY col DESC) "
            "AS rk FROM c_xxx)"
        ),
    },
}

_D_AXIS_SYSTEM_TEMPLATE = """You generate ONE SQLite CTE for the Ranking Target axis (axis D).
You MUST follow the specified ranking form: {form_tag}.

Form-specific instruction:
{form_instruction}

Hard rules:
- Output exactly one WITH clause named with prefix "d_", or output <SKIP>.
- Append a probe: SELECT * FROM <cte_name> LIMIT 15 (skip probe if <SKIP>).
- Use FROM based on the most recent CTE in the parent chain.
- Do NOT change aggregation formulas from axis C.
- Output format: ```sql\\n...\\n```
/no_think"""

_D_AXIS_USER_TEMPLATE = """Question: {question}
Evidence: {evidence}
Database schema:
{schema_text}

Parent CTE chain (most recent last):
{parent_chain_text}

Form to use: {form_tag}
Hint: {example_hint}

Output the WITH ... + probe only, or <SKIP>."""


def select_d_forms_to_generate(question: str, evidence: str) -> List[str]:
    q = f"{question} {evidence or ''}".lower()
    forms: List[str] = ["SKIP"]

    extremum_kw = (
        "youngest",
        "oldest",
        "earliest",
        "latest",
        "highest",
        "lowest",
        "largest",
        "smallest",
        "most",
        "least",
        "maximum",
        "minimum",
        " max ",
        " min ",
    )
    has_extremum = any(kw in q for kw in extremum_kw)

    has_topk = any(kw in q for kw in ("top ", "first ", "last "))
    has_rank = "rank" in q or "ranking" in q

    if has_extremum:
        forms.extend(["EXTREMUM_SUBQUERY", "ORDER_LIMIT"])

    if has_topk:
        if "ORDER_LIMIT" not in forms:
            forms.append("ORDER_LIMIT")

    if has_rank:
        forms.append("WINDOW_RANK")

    if len(forms) == 1:
        return forms

    seen: set[str] = set()
    ordered: List[str] = []
    for f in forms:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered


def _build_d_axis_messages(
    *,
    question: str,
    evidence: str,
    schema_text: str,
    form_tag: str,
    parent_chain_text: str,
) -> List[dict]:
    spec = D_FORM_SPECS[form_tag]
    system = _D_AXIS_SYSTEM_TEMPLATE.format(
        form_tag=form_tag,
        form_instruction=spec["instruction"],
    )
    user = _D_AXIS_USER_TEMPLATE.format(
        question=question,
        evidence=evidence or "(none)",
        schema_text=_truncate_schema(schema_text),
        parent_chain_text=parent_chain_text,
        form_tag=form_tag,
        example_hint=spec.get("example_hint", ""),
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _placeholder_d(form_tag: str, error: str) -> AxisCandidate:
    return AxisCandidate(
        axis_id="D",
        form_tag=form_tag,
        cte_sql="",
        is_valid=False,
        is_skip=False,
        error=error,
    )


def dedup_d_candidates_by_probe(
    candidates: List[AxisCandidate],
    *,
    k: int = 3,
) -> List[AxisCandidate]:
    seen: set[str] = set()
    out: List[AxisCandidate] = []
    for c in candidates:
        if c.probe_hash and c.probe_hash in seen:
            continue
        if c.probe_hash:
            seen.add(c.probe_hash)
        out.append(c)
    valid = [c for c in out if c.is_valid or c.is_skip]
    invalid = [c for c in out if not c.is_valid and not c.is_skip]
    return (valid + invalid)[:k]


def generate_axis_d_candidates(
    chat_llm,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    prior_axis_rows: List[dict],
    db_executor: Any,
    max_tokens: int = 600,
) -> List[AxisCandidate]:
    form_tags = select_d_forms_to_generate(question, evidence)
    chain = _parent_chain_text(prior_axis_rows)
    out: List[AxisCandidate] = []

    for form_tag in form_tags:
        if form_tag not in D_FORM_SPECS:
            continue
        if form_tag == "SKIP":
            out.append(
                AxisCandidate(
                    axis_id="D",
                    form_tag="SKIP",
                    cte_sql=SKIP_CTE_MARKER,
                    is_valid=True,
                    is_skip=True,
                    error="skip",
                )
            )
            continue

        messages = _build_d_axis_messages(
            question=question,
            evidence=evidence,
            schema_text=schema_text,
            form_tag=form_tag,
            parent_chain_text=chain,
        )
        try:
            resp = chat_llm.complete(
                messages, temperature=0.3, max_tokens=max_tokens
            )
            raw = (resp.text or "").strip()
        except Exception as e:
            out.append(_placeholder_d(form_tag, f"llm_error: {e}"))
            continue

        if raw in (SKIP_CTE_MARKER, "<SKIP>"):
            out.append(
                AxisCandidate(
                    axis_id="D",
                    form_tag=form_tag,
                    cte_sql=SKIP_CTE_MARKER,
                    is_valid=True,
                    is_skip=True,
                    error="skip",
                )
            )
            continue

        cte_sql = extract_sql(raw)
        if not cte_sql:
            out.append(_placeholder_d(form_tag, "llm_no_sql"))
            continue

        cte_name = _extract_cte_name(db_executor, cte_sql)
        rows, cols, phash, valid, perr = _probe_candidate(
            db_executor,
            question=question,
            evidence=evidence,
            schema_text=schema_text,
            cte_sql=cte_sql,
        )
        out.append(
            AxisCandidate(
                axis_id="D",
                form_tag=form_tag,
                cte_sql=cte_sql,
                cte_name=cte_name,
                probe_rows=rows,
                probe_columns=cols,
                probe_hash=phash,
                is_valid=valid,
                is_skip=False,
                error=perr,
            )
        )

    return out


def d_form_index(form_tag: str) -> int:
    try:
        return D_FORM_ORDER.index(form_tag)
    except ValueError:
        return 99
