"""S7: end-to-end integration dry run (mock LLM, log_only)."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier.config import ENV_ENABLE, ENV_MODE, ENV_TRACE_PATH
from workflows.mcts_v4.query_clarifier.integration import maybe_apply_clarify
from workflows.mcts_v4.query_clarifier.logging_utils import set_trace_path
from workflows.mcts_v4.query_clarifier.triggers import reset_run_clarify_count


def _trigger_rollouts():
    sig_a, sig_b = "sig_a", "sig_b"
    rss = []
    for i in range(8):
        rss.append(
            {
                "reward": 0.95,
                "leaf_visit_count": 3 if i < 4 else 2,
                "result_buckets": {sig_a: 2, sig_b: 1},
                "all_sql_variants": [
                    {"sql": "SELECT COUNT(*) FROM t", "valid": True, "result_signature": sig_a, "result_row_count": 1},
                    {"sql": "SELECT COUNT(DISTINCT id) FROM t", "valid": True, "result_signature": sig_b, "result_row_count": 1},
                ],
            }
        )
    return rss


def test_integration_log_only_trace(tmp_path):
    os.environ[ENV_ENABLE] = "1"
    os.environ[ENV_MODE] = "log_only"
    trace = tmp_path / "clarify_trace.jsonl"
    set_trace_path(trace)
    os.environ[ENV_TRACE_PATH] = str(trace)
    reset_run_clarify_count()

    mock_c = lambda: {
        "axis": "Measure",
        "question": "Count rows or distinct ids?",
        "candidates": [
            {"cid": "A", "summary": "all rows", "maps_to_cluster_rank": 1},
            {"cid": "B", "summary": "distinct ids", "maps_to_cluster_rank": 2},
        ],
        "rationale": "test",
    }
    mock_a = lambda: {"choice": "A", "confidence": 0.88, "evidence": "count all", "abstain": False}

    rss_out, rec = maybe_apply_clarify(
        _trigger_rollouts(),
        qid=1505,
        nl_question="how many records",
        schema_ddl="CREATE TABLE t(id INT)",
        mock_clarify_fn=mock_c,
        mock_answer_fn=mock_a,
    )
    assert rec is not None
    assert rec.trigger is True
    assert rec.clarify is not None
    assert rec.answer is not None
    assert rec.constraint is not None
    assert rec.enforcement is not None
    assert rec.enforcement["applied"] is False  # log_only
    assert trace.is_file()
    assert "known_limitations" in rec.to_dict()
