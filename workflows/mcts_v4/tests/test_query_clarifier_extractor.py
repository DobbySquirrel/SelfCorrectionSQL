"""AST constraint_hint extractor unit tests."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier.constraint import candidate_passes_self_check, compile_constraint, sql_satisfies
from workflows.mcts_v4.query_clarifier.extractor import (
    extract_constraint_hint,
    extract_measure,
    extract_output,
    extract_ranking,
    extract_reference,
    extract_value,
)
from workflows.mcts_v4.query_clarifier.schemas import (
    ClarificationAnswer,
    ClarificationCandidate,
    ClarificationQuestion,
    ClusterSummary,
)

MEASURE_FIXTURES = [
    ("SELECT COUNT(*) FROM t", {"agg": "COUNT_STAR"}),
    ("SELECT COUNT(DISTINCT id) FROM t", {"agg": "COUNT_DISTINCT", "column": "id"}),
    ("SELECT COUNT(id) FROM t", {"agg": "COUNT", "column": "id"}),
    ("SELECT SUM(amount) FROM orders", {"agg": "SUM", "column": "amount"}),
    ("SELECT AVG(score) AS avg_score FROM students", {"agg": "AVG", "column": "score"}),
    ("SELECT MIN(dob) FROM drivers", {"agg": "MIN", "column": "dob"}),
    ("SELECT MAX(Consumption) FROM yearmonth", {"agg": "MAX", "column": "consumption"}),
    ("SELECT COUNT(*) AS n FROM schools WHERE County = 'Orange'", {"agg": "COUNT_STAR"}),
]

RANKING_FIXTURES = [
    ("SELECT * FROM t ORDER BY score DESC LIMIT 1", {"order": "DESC", "limit": 1, "key": "score"}),
    ("SELECT * FROM t ORDER BY name ASC LIMIT 5", {"order": "ASC", "limit": 5, "key": "name"}),
    ("SELECT * FROM t ORDER BY AvgScrMath DESC LIMIT 1", {"order": "DESC", "limit": 1, "key": "avgscrmath"}),
    ("SELECT * FROM drivers ORDER BY dob ASC LIMIT 1", {"order": "ASC", "limit": 1, "key": "dob"}),
    ("SELECT * FROM t ORDER BY x DESC", {"order": "DESC", "key": "x"}),
    ("SELECT * FROM t LIMIT 10", {"limit": 10}),
]

OUTPUT_FIXTURES = [
    ("SELECT name, age FROM users", {"select_columns": ["name", "age"]}),
    ("SELECT id, artist, name FROM cards", {"select_columns": ["id", "artist", "name"]}),
    ("SELECT dept, COUNT(*) AS c FROM emp GROUP BY dept", {"select_columns": ["c"], "group_by": ["dept"]}),
    ("SELECT a, b FROM t GROUP BY a, b", {"select_columns": ["a", "b"], "group_by": ["a", "b"]}),
    ("SELECT t.name AS n FROM t", {"select_columns": ["n"]}),
]

REFERENCE_FIXTURES = [
    ("SELECT * FROM orders o JOIN customers c ON o.cid = c.id", {"required_tables": ["customers", "orders"]}),
    ("SELECT eye_colour_id FROM superhero", {"required_tables": ["superhero"]}),
    ("SELECT a.x, b.y FROM a JOIN b ON a.id = b.aid", {"required_tables": ["a", "b"]}),
]

VALUE_FIXTURES = [
    ("SELECT * FROM t WHERE County = 'Orange'", {"column": "county", "op": "=", "value": "Orange"}),
    ("SELECT * FROM t WHERE last_name LIKE 'L%'", {"column": "last_name", "op": "LIKE", "value": "L%"}),
    ("SELECT * FROM t WHERE age > 65", {"column": "age", "op": ">", "value": "65"}),
    ("SELECT * FROM t WHERE status != 'active'", {"column": "status", "op": "!=", "value": "active"}),
    (
        "SELECT * FROM t WHERE a = 1 AND b = 2",
        {"column": "a", "op": "=", "value": "1"},
    ),
]

INVALID = [
    "",
    "not valid sql {{{",
    "SELECT 1",
]


def _assert_subset(actual: dict, expected: dict) -> None:
    for k, v in expected.items():
        assert k in actual, f"missing key {k} in {actual}"
        if isinstance(v, list):
            for item in v:
                assert item in actual[k], f"{item} not in {actual[k]}"
        else:
            assert actual[k] == v, f"{k}: {actual[k]!r} != {v!r}"


def test_measure_fixtures():
    for sql, expected in MEASURE_FIXTURES:
        got = extract_measure(sql)
        assert got is not None, sql
        _assert_subset(got, expected)


def test_ranking_fixtures():
    for sql, expected in RANKING_FIXTURES:
        got = extract_ranking(sql)
        assert got is not None, sql
        _assert_subset(got, expected)


def test_output_fixtures():
    for sql, expected in OUTPUT_FIXTURES:
        got = extract_output(sql)
        assert got is not None, sql
        _assert_subset(got, expected)


def test_reference_fixtures():
    for sql, expected in REFERENCE_FIXTURES:
        got = extract_reference(sql)
        assert got is not None, sql
        assert set(expected["required_tables"]).issubset(set(got["required_tables"]))


def test_value_fixtures():
    for sql, expected in VALUE_FIXTURES:
        got = extract_value(sql)
        assert got is not None, sql
        _assert_subset(got, expected)


def test_invalid_returns_none():
    for sql in INVALID:
        assert extract_measure(sql) is None or sql == "SELECT 1"
        assert extract_ranking(sql) is None
        assert extract_output(sql) is None or sql == "SELECT 1"
        assert extract_reference(sql) is None or sql == "SELECT 1"
        assert extract_value(sql) is None


def test_extract_self_check_roundtrip():
    cases = [
        ("Measure", "SELECT COUNT(*) FROM t"),
        ("Measure", "SELECT COUNT(DISTINCT id) FROM t"),
        ("Ranking", "SELECT * FROM t ORDER BY score DESC LIMIT 1"),
        ("Output", "SELECT name, age FROM users"),
        ("Reference", "SELECT * FROM orders"),
        ("Value", "SELECT * FROM t WHERE x = 1"),
    ]
    for axis, sql in cases:
        hint = extract_constraint_hint(axis, sql)
        assert hint is not None
        clusters = [
            ClusterSummary(1, 5, 0.9, sql, "sig_a"),
            ClusterSummary(2, 3, 0.8, "SELECT 1", "sig_b"),
        ]
        cand = ClarificationCandidate("A", "a", hint, maps_to_cluster_rank=1)
        assert candidate_passes_self_check(cand, axis, clusters)
        cq = ClarificationQuestion(1, axis, "q", [cand, ClarificationCandidate("B", "b", {}, maps_to_cluster_rank=2)])
        ans = ClarificationAnswer(1, "A", 0.9, "e", abstain=False)
        cc = compile_constraint(cq, ans, top_clusters=clusters)
        assert cc.self_check_failed is False
        assert sql_satisfies(sql, cc).satisfied
