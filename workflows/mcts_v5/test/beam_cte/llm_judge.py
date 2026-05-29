"""LLM judge rerank for final BeamPath SQL candidates."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from .types import BeamPath

_JUDGE_SYSTEM = """You are evaluating SQL candidates for a natural-language question.
For each candidate, output a score 0-10 and a one-sentence reason.

Scoring criteria (in order of importance):
1. Does the SQL faithfully implement the question's intent?
   - Aggregation granularity correct (COUNT(*) vs COUNT(DISTINCT))
   - JOIN type correct (INNER vs LEFT for percentage / has-or-not questions)
   - Set semantics correct (entity-level vs row-level for "but not" questions)
2. Does the SQL respect the evidence literally?
   - LIKE / NOT LIKE forms preserved
   - Literal values preserved
3. Does the projection match what the question asks?
   - Right columns, no extra, no missing
   - DISTINCT used when listing names/categories
4. Is the SQL executable and result non-degenerate (not empty when the
   question expects rows)?

Output format (one JSON per line):
{"idx": 1, "score": 7.5, "reason": "..."}
{"idx": 2, "score": 9.0, "reason": "..."}
/no_think"""


def _truncate_schema(schema_text: str, limit: int = 1500) -> str:
    s = (schema_text or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit] + "\n... (truncated)"


def _parse_judge_lines(text: str, n: int) -> dict[int, tuple[float, str]]:
    scores: dict[int, tuple[float, str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        if not line.startswith("{"):
            m = re.search(r"\{[\s\S]*?\}", line)
            if m:
                line = m.group(0)
            else:
                continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        try:
            idx = int(obj.get("idx", 0))
        except (TypeError, ValueError):
            continue
        if idx < 1 or idx > n:
            continue
        try:
            score = float(obj.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        reason = str(obj.get("reason") or "").strip()
        scores[idx] = (max(0.0, min(10.0, score)), reason)
    return scores


def llm_judge_rerank(
    chat_llm,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    paths: List[BeamPath],
) -> List[BeamPath]:
    """
    Score executable paths with an LLM judge (0-10). Non-executable → 0.
    Returns paths sorted by judge_score desc (in-place fields filled).
    """
    if not paths:
        return []

    blocks: List[str] = []
    for i, p in enumerate(paths, 1):
        sample_rows = "(empty)"
        if p.executable and p.axis_rows:
            last_exec = None
            for row in reversed(p.axis_rows):
                ex = row.get("selected_execution") or {}
                qr = ex.get("query_result") or []
                if qr:
                    last_exec = qr[:3]
                    break
            if last_exec is not None:
                sample_rows = str(last_exec)
        blocks.append(
            f"[{i}] form_tag={p.form_a}, executable={p.executable}\n"
            f"SQL:\n{p.final_sql or '(none)'}\n"
            f"Sample exec rows: {sample_rows}\n"
        )

    user = (
        f"Question: {question}\n"
        f"Evidence: {evidence or '(none)'}\n"
        f"Schema (truncated):\n{_truncate_schema(schema_text)}\n\n"
        f"Candidates:\n" + "\n\n".join(blocks)
        + f"\n\nOutput {len(paths)} JSON lines, one per candidate."
    )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]

    parsed: dict[int, tuple[float, str]] = {}
    try:
        resp = chat_llm.complete(messages, temperature=0.0, max_tokens=600)
        parsed = _parse_judge_lines(resp.text or "", len(paths))
    except Exception:
        parsed = {}

    for i, p in enumerate(paths, 1):
        if not p.executable:
            p.judge_score = 0.0
            p.judge_reason = "non-executable"
            continue
        if i in parsed:
            p.judge_score, p.judge_reason = parsed[i]
        else:
            p.judge_score = 0.0
            p.judge_reason = "judge_parse_miss"

    return sorted(
        paths,
        key=lambda p: (
            -(p.judge_score or 0.0),
            0 if p.executable else 1,
        ),
    )
