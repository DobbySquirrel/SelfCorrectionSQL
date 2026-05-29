"""Form-enumerated A-axis generator and top-K selector (beam-CTE stage 1)."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Sequence, Tuple

from workflows.mcts_v1.core.mcts_node import MCTSNode
from workflows.mcts_v1.utils.mcts_helpers import MCTSUtils
from workflows.mcts_v5.core.taxonomy_node import TaxonomyMCTSNode
from workflows.mcts_v5.taxonomy.axes import SKIP_CTE_MARKER
from workflows.mcts_v5.utils.baselines import extract_sql

from .types import AxisCandidate

A_FORM_ORDER: Tuple[str, ...] = ("INNER", "LEFT", "SUBQUERY")

A_FORM_SPECS = {
    "INNER": {
        "instruction": (
            "Use INNER JOIN to combine tables. Only rows present in all "
            "joined tables are kept. This is the default for queries that "
            "require all joined sides to have matching records."
        ),
        "example_hint": (
            "WITH a_inner_join_X_Y AS (SELECT ... FROM X INNER JOIN Y ON ...)"
        ),
    },
    "LEFT": {
        "instruction": (
            "Use LEFT JOIN to keep ALL rows from the primary table even "
            "when there is no matching row on the right side. Required for "
            "percentage / ratio / 'has or doesn't have' questions where the "
            "denominator must include unmatched rows."
        ),
        "example_hint": (
            "WITH a_left_join_X_Y AS (SELECT ... FROM X LEFT JOIN Y ON ...)"
        ),
    },
    "SUBQUERY": {
        "instruction": (
            "Avoid a join in the FROM clause. Instead select only from the "
            "primary entity table and express the relationship via a "
            "correlated/uncorrelated subquery (IN / EXISTS / NOT IN / EXCEPT). "
            "This is required when the question asks about entity-level set "
            "membership or set difference."
        ),
        "example_hint": (
            "WITH a_subquery_X AS (SELECT * FROM X WHERE id IN "
            "(SELECT id FROM Y WHERE ...))"
        ),
    },
}

_A_AXIS_SYSTEM_TEMPLATE = """You generate ONE SQLite CTE for the Reference Grounding axis (axis A).
You MUST follow the specified JOIN form: {form_tag}.
Hard rules:
- Output exactly one WITH clause, named with prefix "a_".
- Append a probe: SELECT * FROM <cte_name> LIMIT 15
- Form-specific instruction:
  {form_instruction}
- If the form is genuinely inapplicable to this question (e.g. SUBQUERY
  form requested but the question requires multi-table column projection),
  output exactly: <SKIP>
- Output format: ```sql\\n...\\n```
- SQLite syntax. INNER/LEFT JOIN must be explicit (do not use ',' joins).
- Use backticks for column/table names with whitespace or special chars.
- A-axis ONLY handles JOIN structure and entity reference resolution.
  DO NOT include: extremum filtering (MAX/MIN/youngest/oldest/highest/lowest),
  value filters, or LIMIT clauses — those belong to axes B/D respectively.
/no_think"""

_A_AXIS_USER_TEMPLATE = """Question: {question}
Evidence: {evidence}
Database schema:
{schema_text}

Form to use: {form_tag}
Hint: {example_hint}

Output the WITH ... + probe only."""

_SELECT_TOPK_SYSTEM = """You rank SQLite A-axis (reference grounding) CTE candidates for a question.
Output JSON only with this shape:
{"ranked": [{"idx": 1, "reason": "..."}, {"idx": 2, "reason": "..."}]}
idx is 1-based, matching the candidate list order shown to you.

Selection priority:
1. If the question asks for a percentage / ratio / "how many ... that have/don't have ...",
   LEFT JOIN is preferred over INNER JOIN.
2. If the question asks for entity-level set membership or set difference
   ("but not", "except", "doesn't have ... but has"), SUBQUERY (IN/EXCEPT) is preferred.
3. Otherwise, INNER JOIN is the safe default.
4. An invalid (non-executable) candidate must be ranked last.
/no_think"""

_SELECT_TOPK_SET_SEMANTICS = """
**Special case — set-semantics questions:**
If the evidence or question contains any of:
  - "but not" / "except" / "doesn't have ... but has" / "without ... but with"
  - "X without Y" / explicit set difference language
THEN SUBQUERY form is the STRONGLY preferred choice, regardless of probe rows.
The reason: set difference at the entity level (uuid IN ... EXCEPT ...) is
fundamentally a structure choice, and INNER/LEFT JOIN with NOT IN on join-
expanded rows is NOT semantically equivalent.

In this special case:
1. SUBQUERY first
2. LEFT second (only as fallback if SUBQUERY is invalid)
3. INNER last
"""


def _set_semantics_triggered(question: str, evidence: str) -> bool:
    q = f"{question} {evidence or ''}".lower()
    triggers = (
        "but not",
        "except",
        "doesn't have",
        "does not have",
        "without",
        "but has",
        "but with",
        "doesn't have",
    )
    if any(t in q for t in triggers):
        return True
    if re.search(r"\bwithout\b.+\bbut\b", q):
        return True
    return False


def _truncate_schema(schema_text: str, limit: int = 1500) -> str:
    s = (schema_text or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit] + "\n... (truncated)"


def _build_a_axis_messages(
    *,
    question: str,
    evidence: str,
    schema_text: str,
    form_tag: str,
) -> List[dict]:
    spec = A_FORM_SPECS[form_tag]
    system = _A_AXIS_SYSTEM_TEMPLATE.format(
        form_tag=form_tag,
        form_instruction=spec["instruction"],
    )
    user = _A_AXIS_USER_TEMPLATE.format(
        question=question,
        evidence=evidence or "(none)",
        schema_text=schema_text,
        form_tag=form_tag,
        example_hint=spec["example_hint"],
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _probe_root_node(question: str, schema_text: str, evidence: str) -> TaxonomyMCTSNode:
    MCTSNode._global_node_counter = 0
    root = TaxonomyMCTSNode(
        question=question,
        schema_info=schema_text,
        additional_context=evidence,
        parent=None,
    )
    root.bind_axis_from_depth()
    return root


def _extract_cte_name(sql_executor: Any, cte_sql: str) -> Optional[str]:
    if not cte_sql or cte_sql.strip() in (SKIP_CTE_MARKER, "<END>", "<SKIP>"):
        return None
    try:
        return sql_executor.extract_cte_name(cte_sql)
    except Exception:
        m = re.search(
            r"\bWITH\s+([`\"]?)([a-zA-Z_][\w]*)\1\s+AS\s*\(",
            cte_sql,
            re.IGNORECASE,
        )
        return m.group(2) if m else None


def _probe_candidate(
    db_executor: Any,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    cte_sql: str,
) -> Tuple[List[dict], List[str], Optional[str], bool, Optional[str]]:
    """Execute probe SQL; return rows, columns, hash, valid, error."""
    if cte_sql.strip() in (SKIP_CTE_MARKER, "<SKIP>"):
        return [], [], None, True, "skip"
    if cte_sql.strip() == "<END>":
        return [], [], None, False, "end_marker"

    node = _probe_root_node(question, schema_text, evidence)
    exec_res = db_executor.execute_queries(node, cte_sql).get("cte_result") or {}
    valid = bool(exec_res.get("valid"))
    rows = exec_res.get("query_result") or []
    try:
        rows = MCTSUtils.safe_to_dict(rows)
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []
    columns: List[str] = []
    if rows and isinstance(rows[0], dict):
        columns = list(rows[0].keys())
    err = exec_res.get("error")
    if valid:
        sig = MCTSUtils.create_result_signature(exec_res)
        return rows, columns, sig, True, None
    return rows, columns, None, False, f"exec_error: {err}"


def _placeholder_candidate(form_tag: str, error: str) -> AxisCandidate:
    return AxisCandidate(
        axis_id="A",
        form_tag=form_tag,
        cte_sql="",
        cte_name=None,
        probe_rows=None,
        probe_columns=None,
        probe_hash=None,
        is_valid=False,
        is_skip=False,
        error=error,
    )


def generate_axis_a_candidates(
    chat_llm,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    db_executor,
    max_tokens: int = 800,
) -> List[AxisCandidate]:
    """
    Generate exactly 3 A-axis candidates (INNER / LEFT / SUBQUERY), probe each,
    dedup by probe_hash. List length is always 3.
    """
    out: List[AxisCandidate] = []
    seen_hashes: dict[str, str] = {}

    for form_tag in A_FORM_ORDER:
        messages = _build_a_axis_messages(
            question=question,
            evidence=evidence,
            schema_text=schema_text,
            form_tag=form_tag,
        )
        try:
            resp = chat_llm.complete(
                messages,
                temperature=0.3,
                max_tokens=max_tokens,
            )
            raw = (resp.text or "").strip()
        except Exception as e:
            out.append(_placeholder_candidate(form_tag, f"llm_error: {e}"))
            continue

        if raw == SKIP_CTE_MARKER or raw == "<SKIP>":
            cand = AxisCandidate(
                axis_id="A",
                form_tag=form_tag,
                cte_sql=SKIP_CTE_MARKER,
                cte_name=None,
                probe_rows=[],
                probe_columns=[],
                probe_hash=None,
                is_valid=True,
                is_skip=True,
                error="skip",
            )
            out.append(cand)
            continue

        cte_sql = extract_sql(raw)
        if not cte_sql:
            out.append(_placeholder_candidate(form_tag, "llm_no_sql"))
            continue

        cte_name = _extract_cte_name(db_executor, cte_sql)
        rows, cols, phash, valid, perr = _probe_candidate(
            db_executor,
            question=question,
            evidence=evidence,
            schema_text=schema_text,
            cte_sql=cte_sql,
        )

        cand = AxisCandidate(
            axis_id="A",
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

        if phash and phash in seen_hashes:
            dup_of = seen_hashes[phash]
            cand.is_valid = False
            cand.error = f"duplicate_of_{dup_of}"
        elif phash:
            seen_hashes[phash] = form_tag

        out.append(cand)

    while len(out) < 3:
        out.append(_placeholder_candidate("UNKNOWN", "missing_form"))

    return out[:3]


def _parse_ranked_indices(text: str, n: int) -> List[int]:
    """Return 0-based indices best-first; fallback INNER, LEFT, SUBQUERY order."""
    fallback = list(range(min(n, 3)))
    if not (text or "").strip():
        return fallback[:n]

    blob = text.strip()
    m = re.search(r"\{[\s\S]*\}", blob)
    if m:
        blob = m.group(0)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return fallback[:n]

    ranked = data.get("ranked") if isinstance(data, dict) else data
    if not isinstance(ranked, list):
        return fallback[:n]

    out: List[int] = []
    for item in ranked:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        if idx is None:
            continue
        try:
            i = int(idx) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in out:
            out.append(i)
    for i in fallback:
        if i not in out and i < n:
            out.append(i)
    return out[:n]


def select_topk_axis_a(
    chat_llm,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    candidates: List[AxisCandidate],
    k: int = 3,
) -> List[AxisCandidate]:
    """Rank A-axis candidates; return up to k, best-first."""
    n = len(candidates)
    if n == 0:
        return []
    if n == 1:
        return candidates[:1]

    lines: List[str] = []
    for i, c in enumerate(candidates, 1):
        preview = (c.cte_sql or "").replace("\n", " ")[:220]
        sample = (c.probe_rows or [])[:3]
        lines.append(
            f"[{i}] form_tag={c.form_tag} valid={c.is_valid} error={c.error or ''}\n"
            f"SQL: {preview}\n"
            f"probe_columns: {c.probe_columns or []}\n"
            f"probe_rows[:3]: {sample}\n"
        )

    user = (
        f"Question: {question}\n"
        f"Evidence: {evidence or '(none)'}\n"
        f"Schema (truncated):\n{_truncate_schema(schema_text)}\n\n"
        f"Candidates:\n" + "\n".join(lines)
    )
    system = _SELECT_TOPK_SYSTEM
    if _set_semantics_triggered(question, evidence):
        system = _SELECT_TOPK_SET_SEMANTICS + "\n" + system

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    try:
        resp = chat_llm.complete(messages, temperature=0.0, max_tokens=400)
        order = _parse_ranked_indices(resp.text or "", n)
    except Exception:
        order = list(range(min(n, 3)))

    ranked = [candidates[i] for i in order if i < n]
    for c in candidates:
        if c not in ranked:
            ranked.append(c)

    def _is_demoted(c: AxisCandidate) -> bool:
        if not c.is_valid:
            return True
        err = (c.error or "").strip()
        return err.startswith("duplicate_of_")

    valid_part = [c for c in ranked if not _is_demoted(c)]
    invalid_part = [c for c in ranked if _is_demoted(c)]
    return (valid_part + invalid_part)[:k]
