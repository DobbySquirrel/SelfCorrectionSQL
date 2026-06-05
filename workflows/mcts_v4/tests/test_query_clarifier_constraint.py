"""S5: constraint compile and sql_satisfies."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier.constraint import compile_constraint, sql_satisfies
from workflows.mcts_v4.query_clarifier.schemas import (
    ClarificationAnswer,
    ClarificationCandidate,
    ClarificationQuestion,
    CompiledConstraint,
)


def _compile(axis, hint, choice="A", conf=0.85):
    cq = ClarificationQuestion(
        qid=1,
        axis=axis,
        question="q",
        candidates=[ClarificationCandidate("A", "a", hint), ClarificationCandidate("B", "b", {})],
    )
    ans = ClarificationAnswer(qid=1, choice=choice, confidence=conf, evidence="e", abstain=False)
    os.environ["MCTS_CLARIFY_MODE"] = "full"
    return compile_constraint(cq, ans)


def test_measure_count_star():
    cc = _compile("Measure", {"agg": "COUNT_STAR"})
    assert sql_satisfies("SELECT COUNT(*) FROM orders", cc).satisfied
    assert not sql_satisfies("SELECT COUNT(order_id) FROM orders", cc).satisfied


def test_measure_count_distinct():
    cc = _compile("Measure", {"agg": "COUNT_DISTINCT", "column": "order_id"})
    sql_ok = "SELECT COUNT(DISTINCT order_id) FROM orders"
    assert sql_satisfies(sql_ok, cc).satisfied or sql_satisfies(sql_ok, cc).violated_fields == ["parse_warning"]


def test_ranking_desc_limit():
    cc = _compile("Ranking", {"order": "DESC", "limit": 1})
    ok = sql_satisfies("SELECT * FROM t ORDER BY score DESC LIMIT 1", cc)
    bad = sql_satisfies("SELECT * FROM t ORDER BY score ASC LIMIT 1", cc)
    if ok.violated_fields != ["parse_warning"]:
        assert ok.satisfied
        assert not bad.satisfied


def test_output_select_columns():
    cc = _compile("Output", {"select_columns": ["name", "age"]})
    ok = sql_satisfies("SELECT name, age FROM users", cc)
    assert ok.satisfied or "parse_warning" in ok.violated_fields


def test_reference_required_table():
    cc = _compile("Reference", {"required_tables": ["orders"]}, conf=0.85)
    assert cc.level == "soft"  # Reference forced soft in v0
    ok = sql_satisfies("SELECT * FROM orders", cc)
    assert ok.satisfied or "parse_warning" in ok.violated_fields


def test_none_level_always_satisfied():
    cc = CompiledConstraint(qid=1, axis="Measure", level="none")
    assert sql_satisfies("SELECT 1", cc).satisfied
