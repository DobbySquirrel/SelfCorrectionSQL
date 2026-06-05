"""S3/S4: clarify and answer agent parsing."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier import answer_agent, clarify_agent
from workflows.mcts_v4.query_clarifier.schemas import (
    AnswerInput,
    ClarifyInput,
    ClusterSummary,
    NodeStats,
)


def _clusters():
    return [
        ClusterSummary(1, 5, 0.95, "SELECT COUNT(*) FROM t", "sig_a"),
        ClusterSummary(2, 3, 0.92, "SELECT COUNT(DISTINCT id) FROM t", "sig_b"),
    ]


def test_clarify_mock_pipeline():
    mock = lambda: {
        "axis": "Measure",
        "question": "Count all rows or distinct ids?",
        "candidates": [
            {"cid": "A", "summary": "Count all rows", "maps_to_cluster_rank": 1},
            {"cid": "B", "summary": "Count distinct ids", "maps_to_cluster_rank": 2},
        ],
        "rationale": "clusters differ in aggregation",
    }
    cq = clarify_agent.ask(
        ClarifyInput(1, "how many", "CREATE TABLE t(id INT)", _clusters(), NodeStats()),
        mock_fn=mock,
    )
    assert cq.axis == "Measure"
    assert len(cq.candidates) == 2
    assert cq.candidates[0].constraint_hint.get("agg") == "COUNT_STAR"
    assert cq.candidates[1].constraint_hint.get("agg") == "COUNT_DISTINCT"


def test_answer_abstain_low_confidence():
    from workflows.mcts_v4.query_clarifier.clarify_agent import parse_clarify_response

    cq = parse_clarify_response(
        1,
        {
            "axis": "Measure",
            "question": "q?",
            "candidates": [
                {"cid": "A", "summary": "a", "maps_to_cluster_rank": 1},
                {"cid": "B", "summary": "b", "maps_to_cluster_rank": 2},
            ],
            "rationale": "",
        },
        2,
        top_clusters=_clusters(),
    )
    mock = lambda: {"choice": "A", "confidence": 0.4, "evidence": "guess", "abstain": False}
    ans = answer_agent.answer(
        AnswerInput(1, "how many", "ddl", cq),
        mock_fn=mock,
    )
    assert ans.abstain is True
    assert ans.choice is None


def test_answer_clear_choice():
    from workflows.mcts_v4.query_clarifier.clarify_agent import parse_clarify_response

    cq = parse_clarify_response(
        1,
        {
            "axis": "Ranking",
            "question": "highest or lowest?",
            "candidates": [
                {"cid": "A", "summary": "top 1 desc", "maps_to_cluster_rank": 1},
                {"cid": "B", "summary": "bottom asc", "maps_to_cluster_rank": 2},
            ],
            "rationale": "",
        },
        2,
        top_clusters=[
            ClusterSummary(1, 5, 0.95, "SELECT * FROM t ORDER BY score DESC LIMIT 1", "sig_a"),
            ClusterSummary(2, 3, 0.92, "SELECT * FROM t ORDER BY score ASC LIMIT 1", "sig_b"),
        ],
    )
    mock = lambda: {
        "choice": "A",
        "confidence": 0.9,
        "evidence": "phrase 'highest' in question",
        "abstain": False,
    }
    ans = answer_agent.answer(AnswerInput(1, "who has the highest score", "ddl", cq), mock_fn=mock)
    assert ans.abstain is False
    assert ans.choice == "A"
    assert ans.confidence >= 0.85
