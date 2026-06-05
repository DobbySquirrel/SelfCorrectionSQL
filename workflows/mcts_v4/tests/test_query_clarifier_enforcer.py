"""S6: enforcer hard/soft and safety fallback."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier.constraint import compile_constraint
from workflows.mcts_v4.query_clarifier.enforcer import enforce, hard_prune
from workflows.mcts_v4.query_clarifier.schemas import (
    ClarificationAnswer,
    ClarificationCandidate,
    ClarificationQuestion,
    PoolEntry,
)


def _hard_measure_cc():
    cq = ClarificationQuestion(
        qid=1,
        axis="Measure",
        question="q",
        candidates=[
            ClarificationCandidate("A", "count star", {"agg": "COUNT_STAR"}),
            ClarificationCandidate("B", "count col", {"agg": "COUNT_DISTINCT", "column": "id"}),
        ],
    )
    ans = ClarificationAnswer(qid=1, choice="A", confidence=0.9, evidence="e", abstain=False)
    os.environ["MCTS_CLARIFY_MODE"] = "full"
    return compile_constraint(cq, ans)


def test_hard_prune_keeps_matching():
    cc = _hard_measure_cc()
    pool = [
        PoolEntry("SELECT COUNT(*) FROM t", reward=0.9),
        PoolEntry("SELECT COUNT(id) FROM t", reward=0.9),
    ]
    pruned = hard_prune(pool, cc)
    assert len(pruned) >= 1
    assert any("COUNT(*)" in e.sql.upper().replace(" ", "") or "count(*)" in e.sql.lower() for e in pruned)


def test_safety_fallback_empty_pool():
    cc = _hard_measure_cc()
    pool = [PoolEntry("SELECT COUNT(id) FROM t", reward=0.9)]
    os.environ["MCTS_CLARIFY_MODE"] = "full"
    out, er = enforce(pool, cc, regen_budget=0)
    assert er.safety_fallback is True
    assert len(out) == len(pool)


def test_log_only_does_not_apply():
    cc = _hard_measure_cc()
    pool = [
        PoolEntry("SELECT COUNT(*) FROM t", reward=0.9),
        PoolEntry("SELECT COUNT(id) FROM t", reward=0.9),
    ]
    os.environ["MCTS_CLARIFY_MODE"] = "log_only"
    out, er = enforce(pool, cc)
    assert er.applied is False
    assert len(out) == 2
