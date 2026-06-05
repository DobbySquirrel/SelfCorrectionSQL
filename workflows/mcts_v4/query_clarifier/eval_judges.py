"""Hit@1 judges for QueryClarifier replay / audit (evaluator layer only)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable, List, Optional, Set

from workflows.mcts_v4.query_clarifier.constraint import sql_satisfies
from workflows.mcts_v4.query_clarifier.extractor import extract_constraint_hint
from workflows.mcts_v4.query_clarifier.schemas import ClarificationAnswer, ClarificationCandidate, ClarificationQuestion, CompiledConstraint
from workflows.mcts_v4.utils.sql_exec_helpers import normalize_sql

JUDGE_NORMALIZE = "normalize"
JUDGE_EXEC_EQUIV = "exec_equiv"
JUDGE_AST = "ast"
CANONICAL_JUDGE = JUDGE_EXEC_EQUIV
ENV_HIT_JUDGE = "MCTS_CLARIFY_HIT_JUDGE"


def resolve_judge(name: Optional[str] = None) -> str:
    j = (name or os.environ.get(ENV_HIT_JUDGE) or CANONICAL_JUDGE).strip().lower()
    if j in (JUDGE_NORMALIZE, JUDGE_EXEC_EQUIV, JUDGE_AST):
        return j
    return CANONICAL_JUDGE


def _compile_hint(axis: str, hint: dict) -> CompiledConstraint:
    cq = ClarificationQuestion(0, axis, "", [ClarificationCandidate("A", "", hint)])  # type: ignore[arg-type]
    ans = ClarificationAnswer(0, "A", 0.9, "", False)
    cc = CompiledConstraint(qid=0, axis=axis, level="hard", source_answer=ans)  # type: ignore[arg-type]
    from workflows.mcts_v4.query_clarifier.constraint import _compile_constraint_fields

    chosen = cq.candidates[0]
    return _compile_constraint_fields(cq, ans, chosen)


def ast_satisfies_gold(pick_sql: str, gold_sql: str) -> bool:
    """J3: pick satisfies all extractable AST constraints from gold SQL."""
    if not (pick_sql or "").strip() or not (gold_sql or "").strip():
        return False
    matched = 0
    for axis in ("Measure", "Ranking", "Output", "Reference", "Value"):
        hint = extract_constraint_hint(axis, gold_sql)
        if not hint:
            continue
        cc = _compile_hint(axis, hint)
        if cc.level == "none":
            continue
        sat = sql_satisfies(pick_sql, cc)
        if not sat.satisfied:
            return False
        matched += 1
    return matched > 0


@lru_cache(maxsize=512)
def _exec_equiv_cached(pick_sql: str, gold_sql: str, db_id: str) -> bool:
    if not pick_sql or not gold_sql or not db_id:
        return False
    try:
        from workflows.mcts_v1.test.test_mcts import build_db_connector, compare_with_gold

        conn = build_db_connector(db_id)
        try:
            return bool(compare_with_gold(pick_sql, gold_sql, conn))
        finally:
            conn.disconnect()
    except Exception:
        return False


def hits_gold(
    pick_sql: str,
    gold_sql: str,
    *,
    judge: Optional[str] = None,
    db_id: str = "",
) -> bool:
    j = resolve_judge(judge)
    if not gold_sql:
        return False
    if not (pick_sql or "").strip():
        return False
    if j == JUDGE_NORMALIZE:
        return normalize_sql(pick_sql) == normalize_sql(gold_sql)
    if j == JUDGE_EXEC_EQUIV:
        return _exec_equiv_cached(pick_sql.strip(), gold_sql.strip(), db_id)
    return ast_satisfies_gold(pick_sql, gold_sql)


def pool_contains_gold(
    pool_sqls: Iterable[str],
    gold_sql: str,
    *,
    judge: Optional[str] = None,
    db_id: str = "",
) -> bool:
    seen: Set[str] = set()
    for sql in pool_sqls:
        s = (sql or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        if hits_gold(s, gold_sql, judge=judge, db_id=db_id):
            return True
    return False


def simulate_gold_prune(
    pool_sqls: List[str],
    pruned_sqls: List[str],
    gold_sql: str,
    *,
    judge: Optional[str] = None,
    db_id: str = "",
) -> tuple[bool, bool]:
    """Return (gold_in_pruned, gold_pruned_from_pool)."""
    in_pool = pool_contains_gold(pool_sqls, gold_sql, judge=judge, db_id=db_id)
    in_pruned = pool_contains_gold(pruned_sqls, gold_sql, judge=judge, db_id=db_id)
    gold_pruned = in_pool and not in_pruned
    return in_pruned, gold_pruned
