"""LLM judge rerank for final BeamPath SQL candidates (v1 + pairwise v2)."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Tuple

from .types import BeamPath

_MIN_SCORE_GAP = 1.5

_STAGE_A_SYSTEM = """You compare K SQLite candidates for the same natural-language question.
Do NOT score yet. List concrete semantic differences between candidates.

Each difference must be specific, e.g.:
- "Cand 1 uses INNER JOIN; Cand 2 uses LEFT JOIN — affects rows without matches on the right."
- "Cand 1 filters language='French' in WHERE; Cand 2 aggregates with CASE — changes the denominator."
- "Cand 1 uses COUNT(*); Cand 2 uses COUNT(DISTINCT id) — duplicate join rows counted differently."

Tag each difference with the axis it likely lives on (A/B/C/D/E).
If candidates are nearly identical, say so explicitly.

Output JSON only:
{"differences": [{"summary": "...", "candidates": [1,2], "axis": "A"}, ...]}
/no_think"""

_STAGE_B_SYSTEM = """You score SQLite candidates for a natural-language question.
You already have a list of semantic differences between candidates.

Rules:
1. Score each candidate 0-10 for semantic correctness vs the question + evidence.
2. Penalize wrong JOIN type, wrong aggregation grain, wrong filter placement, wrong projection.
3. Use execution samples (row count + first rows) — empty or clearly wrong shape → lower score.
4. Do NOT give all candidates similar scores. Top-1 must be at least 1.5 points above top-2
   unless they are truly equivalent (then say equivalent in reasons).
5. If you cannot find a meaningful difference, note that in the top candidate's reason.

When candidates differ along axis C (aggregation form):
- For "how many <entity>" with 1:N JOIN, prefer DISTINCT_ENTITY over RAW_AGG.
- For "percentage / ratio" questions, prefer CONDITIONAL_AGG over RAW_AGG with WHERE filter.
- For listing/filter-only questions, prefer SKIP.

When candidates differ along axis D (ranking form):
- For "youngest/oldest/highest/lowest" without explicit "top 1", prefer
  EXTREMUM_SUBQUERY over ORDER_LIMIT (LIMIT 1 may miss ties).
- For listing without ranking words, prefer SKIP.

Output one JSON line per candidate:
{"idx": 1, "score": 8.5, "reason": "..."}
/no_think"""

_JUDGE_V1_SYSTEM = """You are evaluating SQL candidates for a natural-language question.
For each candidate, output a score 0-10 and a one-sentence reason.

Scoring criteria (in order of importance):
1. Does the SQL faithfully implement the question's intent?
2. Does the SQL respect the evidence literally?
3. Does the projection match what the question asks?
4. Is the SQL executable and result non-degenerate?

Output format (one JSON per line):
{"idx": 1, "score": 7.5, "reason": "..."}
/no_think"""


def _truncate_schema(schema_text: str, limit: int = 1500) -> str:
    s = (schema_text or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit] + "\n... (truncated)"


def _path_exec_sample(p: BeamPath) -> Tuple[str, int]:
    """Final-SQL execution sample: prefer stored full-exec rows."""
    rows = getattr(p, "exec_sample_rows", None)
    n = getattr(p, "exec_row_count", None)
    if rows is not None:
        count = n if n is not None else len(rows)
        return str(rows[:3]), int(count)
    if p.executable and p.axis_rows:
        for row in reversed(p.axis_rows):
            ex = row.get("selected_execution") or {}
            qr = ex.get("query_result") or []
            if qr:
                return str(qr[:3]), len(qr)
    return "(empty)", 0


def _candidate_block(i: int, p: BeamPath) -> str:
    sample, nrows = _path_exec_sample(p)
    fc = getattr(p, "form_c", "") or ""
    fd = getattr(p, "form_d", "") or ""
    return (
        f"[{i}] form_a={p.form_a}, form_c={fc or 'n/a'}, form_d={fd or 'n/a'}, "
        f"executable={p.executable}\n"
        f"SQL:\n{p.final_sql or '(none)'}\n"
        f"Exec row_count: {nrows}\n"
        f"Exec sample rows (first 3): {sample}\n"
    )


def _parse_json_blob(text: str) -> Any:
    blob = (text or "").strip()
    m = re.search(r"\{[\s\S]*\}", blob)
    if m:
        blob = m.group(0)
    return json.loads(blob)


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


def _apply_scores(paths: List[BeamPath], parsed: dict[int, tuple[float, str]]) -> None:
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


def _score_gap_ok(paths: List[BeamPath]) -> bool:
    exec_scores = sorted(
        [(p.judge_score or 0.0) for p in paths if p.executable],
        reverse=True,
    )
    if len(exec_scores) < 2:
        return True
    return exec_scores[0] - exec_scores[1] >= _MIN_SCORE_GAP


def _llm_judge_rerank_v1(
    chat_llm,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    paths: List[BeamPath],
) -> List[BeamPath]:
    blocks = [_candidate_block(i, p) for i, p in enumerate(paths, 1)]
    user = (
        f"Question: {question}\n"
        f"Evidence: {evidence or '(none)'}\n"
        f"Schema (truncated):\n{_truncate_schema(schema_text)}\n\n"
        f"Candidates:\n" + "\n\n".join(blocks)
        + f"\n\nOutput {len(paths)} JSON lines, one per candidate."
    )
    messages = [
        {"role": "system", "content": _JUDGE_V1_SYSTEM},
        {"role": "user", "content": user},
    ]
    parsed: dict[int, tuple[float, str]] = {}
    try:
        resp = chat_llm.complete(messages, temperature=0.0, max_tokens=600)
        parsed = _parse_judge_lines(resp.text or "", len(paths))
    except Exception:
        parsed = {}
    _apply_scores(paths, parsed)
    return _sort_paths(paths)


def _llm_judge_rerank_v2(
    chat_llm,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    paths: List[BeamPath],
) -> List[BeamPath]:
    n = len(paths)
    blocks = [_candidate_block(i, p) for i, p in enumerate(paths, 1)]
    cand_text = (
        f"Question: {question}\n"
        f"Evidence: {evidence or '(none)'}\n"
        f"Schema (truncated):\n{_truncate_schema(schema_text)}\n\n"
        + "\n\n".join(blocks)
    )

    differences_text = ""
    try:
        resp_a = chat_llm.complete(
            [
                {"role": "system", "content": _STAGE_A_SYSTEM},
                {"role": "user", "content": cand_text},
            ],
            temperature=0.0,
            max_tokens=700,
        )
        data = _parse_json_blob(resp_a.text or "")
        if isinstance(data, dict) and data.get("differences"):
            differences_text = json.dumps(data["differences"], indent=2)
        else:
            differences_text = resp_a.text or ""
    except Exception:
        differences_text = "(stage A failed)"

    def _run_stage_b(strict: bool) -> dict[int, tuple[float, str]]:
        extra = ""
        if strict:
            extra = (
                "\n\nSTRICT: Your previous scores were too close. "
                f"Top-1 must beat top-2 by at least {_MIN_SCORE_GAP} points. "
                "Spread scores using the listed differences."
            )
        user_b = (
            f"{cand_text}\n\n"
            f"Identified differences:\n{differences_text}\n"
            f"{extra}\n\n"
            f"Score all {n} candidates. Output {n} JSON lines."
        )
        resp_b = chat_llm.complete(
            [
                {"role": "system", "content": _STAGE_B_SYSTEM},
                {"role": "user", "content": user_b},
            ],
            temperature=0.0,
            max_tokens=700,
        )
        return _parse_judge_lines(resp_b.text or "", n)

    parsed = _run_stage_b(strict=False)
    _apply_scores(paths, parsed)
    if not _score_gap_ok(paths):
        parsed_retry = _run_stage_b(strict=True)
        if parsed_retry:
            _apply_scores(paths, parsed_retry)

    for p in paths:
        if p.executable and p.judge_reason and p.judge_reason != "non-executable":
            p.judge_reason = f"[v2] {p.judge_reason}"

    return _sort_paths(paths)


def _sort_paths(paths: List[BeamPath]) -> List[BeamPath]:
    return sorted(
        paths,
        key=lambda p: (
            -(p.judge_score or 0.0),
            0 if p.executable else 1,
        ),
    )


def llm_judge_rerank(
    chat_llm,
    *,
    question: str,
    evidence: str,
    schema_text: str,
    paths: List[BeamPath],
    mode: str = "v2",
) -> List[BeamPath]:
    """
    Score paths and return sorted best-first.
    mode: 'v2' (pairwise differences + spread enforcement) or 'v1'.
    """
    if not paths:
        return []
    if (mode or "v2").lower() == "v1":
        return _llm_judge_rerank_v1(
            chat_llm,
            question=question,
            evidence=evidence,
            schema_text=schema_text,
            paths=paths,
        )
    return _llm_judge_rerank_v2(
        chat_llm,
        question=question,
        evidence=evidence,
        schema_text=schema_text,
        paths=paths,
    )
