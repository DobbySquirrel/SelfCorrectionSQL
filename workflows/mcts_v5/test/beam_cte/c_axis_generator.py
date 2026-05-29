"""Form-enumerated C-axis generator (beam-CTE stage 2)."""

from __future__ import annotations

import re
from typing import Any, List, Sequence

from workflows.mcts_v5.taxonomy.axes import SKIP_CTE_MARKER
from workflows.mcts_v5.utils.baselines import extract_sql

from .a_axis_generator import (
    _extract_cte_name,
    _probe_candidate,
    _truncate_schema,
)
from .types import AxisCandidate

C_FORM_ORDER: tuple[str, ...] = (
    "SKIP",
    "RAW_AGG",
    "DISTINCT_ENTITY",
    "CONDITIONAL_AGG",
)

C_FORM_SPECS = {
    "SKIP": {
        "instruction": (
            "Output exactly <SKIP>. Use this when the question requires no "
            "aggregation: it is a listing/filtering question, OR the column "
            "to project is already pre-aggregated in schema (column name "
            "starts with Avg/Mean/Total/Sum/Count)."
        ),
        "example_hint": "<SKIP>",
    },
    "RAW_AGG": {
        "instruction": (
            "Direct aggregation over rows: COUNT(*), SUM(col), AVG(col). "
            "Use this when the preceding rows are already at the correct "
            "grain and there are NO 1:N JOIN duplications upstream."
        ),
        "example_hint": "WITH c_count AS (SELECT COUNT(*) AS cnt FROM b_filter)",
    },
    "DISTINCT_ENTITY": {
        "instruction": (
            "Aggregation over distinct entity ID: COUNT(DISTINCT id), "
            "AVG over per-entity values. Required when (a) the question "
            "asks 'how many <entity>' AND (b) the preceding path contains "
            "a 1:N JOIN that may duplicate the entity."
        ),
        "example_hint": (
            "WITH c_count_distinct AS (SELECT COUNT(DISTINCT P.ID) AS cnt "
            "FROM b_filter P)"
        ),
    },
    "CONDITIONAL_AGG": {
        "instruction": (
            "Conditional aggregation using CASE WHEN inside SUM/COUNT/AVG. "
            "Required for percentage / ratio / 'X out of Y' questions, "
            "where filtering must NOT happen in WHERE (which would shrink "
            "the denominator), but inside the aggregation expression."
        ),
        "example_hint": (
            "WITH c_pct AS (SELECT "
            "SUM(CASE WHEN language='French' THEN 1 ELSE 0 END) * 100.0 "
            "/ COUNT(DISTINCT card_id) AS pct FROM a_left_join_X)"
        ),
    },
}

_C_AXIS_SYSTEM_TEMPLATE = """You generate ONE SQLite CTE for the Measure Construction axis (axis C).
You MUST follow the specified aggregation form: {form_tag}.

Form-specific instruction:
{form_instruction}

Hard rules:
- Output exactly one WITH clause named with prefix "c_", or output <SKIP>.
- Append a probe: SELECT * FROM <cte_name> LIMIT 15 (skip probe if <SKIP>).
- Use FROM based on the most recent CTE in the parent chain.
- Do NOT add WHERE filters that should be in axis B.
- Do NOT add ORDER BY or LIMIT (those are axis D).
- Output format: ```sql\\n...\\n```
/no_think"""

_C_AXIS_USER_TEMPLATE = """Question: {question}
Evidence: {evidence}
Database schema:
{schema_text}

Parent CTE chain (most recent last):
{parent_chain_text}

Form to use: {form_tag}
Hint: {example_hint}

Output the WITH ... + probe only, or <SKIP>."""


def parent_path_has_1n_join(a_form_tag: str) -> bool:
    """Heuristic: INNER/LEFT A forms may introduce 1:N duplication."""
    return a_form_tag in ("INNER", "LEFT")


def _parent_chain_text(axis_rows: Sequence[dict]) -> str:
    parts: List[str] = []
    for row in axis_rows:
        cte = row.get("selected_cte") or ""
        if row.get("selected_is_skip") or cte in (SKIP_CTE_MARKER, "<SKIP>"):
            parts.append(f"[{row.get('axis_id', '?')}] <SKIP>")
        else:
            preview = str(cte).replace("\n", " ")[:400]
            parts.append(f"[{row.get('axis_id', '?')}] {preview}")
    return "\n".join(parts) if parts else "(no prior CTEs)"


def select_c_forms_to_generate(
    question: str,
    evidence: str,
    *,
    a_form_tag: str = "",
) -> List[str]:
    """Pick which C forms to generate (1–4)."""
    q = f"{question} {evidence or ''}".lower()
    forms: List[str] = ["SKIP"]

    is_count_question = any(
        kw in q for kw in ("how many", "count", "number of", "total number")
    )
    is_pct_question = bool(
        re.search(
            r"\b(percentage|percent|ratio|proportion)\b",
            q,
            re.IGNORECASE,
        )
        or "what percent" in q
    )
    has_1n_join = parent_path_has_1n_join(a_form_tag)
    is_listing = any(
        kw in q for kw in ("list", "which", "what are", "name the", "find the")
    )

    if is_listing and not is_count_question and not is_pct_question:
        return ["SKIP"]

    if is_pct_question:
        return ["SKIP", "CONDITIONAL_AGG", "RAW_AGG"]

    if is_count_question:
        out = ["SKIP", "RAW_AGG"]
        if has_1n_join:
            out.append("DISTINCT_ENTITY")
        return out

    if any(kw in q for kw in ("average", "mean", "sum of", "total", "max", "min")):
        out = ["SKIP", "RAW_AGG"]
        if has_1n_join:
            out.append("DISTINCT_ENTITY")
        return out

    return ["SKIP", "RAW_AGG"]


def _build_c_axis_messages(
    *,
    question: str,
    evidence: str,
    schema_text: str,
    form_tag: str,
    parent_chain_text: str,
) -> List[dict]:
    spec = C_FORM_SPECS[form_tag]
    system = _C_AXIS_SYSTEM_TEMPLATE.format(
        form_tag=form_tag,
        form_instruction=spec["instruction"],
    )
    user = _C_AXIS_USER_TEMPLATE.format(
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


def _placeholder_c(form_tag: str, error: str) -> AxisCandidate:
    return AxisCandidate(
        axis_id="C",
        form_tag=form_tag,
        cte_sql="",
        is_valid=False,
        is_skip=False,
        error=error,
    )


def dedup_c_candidates_by_probe(
    candidates: List[AxisCandidate],
    *,
    k: int = 3,
) -> List[AxisCandidate]:
    """Keep best-first unique probe_hash candidates."""
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


def generate_axis_c_candidates(
    chat_llm,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    prior_axis_rows: List[dict],
    db_executor: Any,
    a_form_tag: str = "",
    max_tokens: int = 600,
) -> List[AxisCandidate]:
    """Generate 1–4 C-axis candidates for the requested forms."""
    form_tags = select_c_forms_to_generate(
        question, evidence, a_form_tag=a_form_tag
    )
    chain = _parent_chain_text(prior_axis_rows)
    out: List[AxisCandidate] = []

    for form_tag in form_tags:
        if form_tag not in C_FORM_SPECS:
            continue
        if form_tag == "SKIP":
            out.append(
                AxisCandidate(
                    axis_id="C",
                    form_tag="SKIP",
                    cte_sql=SKIP_CTE_MARKER,
                    is_valid=True,
                    is_skip=True,
                    error="skip",
                )
            )
            continue

        messages = _build_c_axis_messages(
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
            out.append(_placeholder_c(form_tag, f"llm_error: {e}"))
            continue

        if raw in (SKIP_CTE_MARKER, "<SKIP>"):
            out.append(
                AxisCandidate(
                    axis_id="C",
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
            out.append(_placeholder_c(form_tag, "llm_no_sql"))
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
                axis_id="C",
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


def c_form_index(form_tag: str) -> int:
    try:
        return C_FORM_ORDER.index(form_tag)
    except ValueError:
        return 99
