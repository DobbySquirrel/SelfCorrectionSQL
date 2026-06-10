"""Unit tests for SQLSelector strategies."""

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.utils.sql_selector import SQLSelector, STRATEGY_ENV


def _mock_rollouts():
    sig_a, sig_b = "sig_a_111", "sig_b_222"
    return [
        {
            "rollout_id": 1,
            "reward": 1.0,
            "leaf_visit_count": 3,
            "result_buckets": {sig_a: 2, sig_b: 1},
            "all_sql_variants": [
                {
                    "sql": "SELECT 1 AS x",
                    "valid": True,
                    "result_signature": sig_a,
                    "result_row_count": 1,
                },
                {
                    "sql": "SELECT 2 AS y",
                    "valid": True,
                    "result_signature": sig_b,
                    "result_row_count": 1,
                },
            ],
        },
        {
            "rollout_id": 2,
            "reward": 0.5,
            "leaf_visit_count": 1,
            "result_buckets": {sig_b: 1},
            "all_sql_variants": [
                {
                    "sql": "SELECT 2 AS y",
                    "valid": True,
                    "result_signature": sig_b,
                    "result_row_count": 1,
                },
            ],
        },
    ]


def test_r0_default_matches_highest_reward():
    os.environ.pop(STRATEGY_ENV, None)
    rss = _mock_rollouts()
    with redirect_stdout(io.StringIO()):
        a = SQLSelector.select(rss)
        b = SQLSelector.select_by_highest_reward(rss)
    assert a == b


def test_r2_picks_max_visit_cluster():
    os.environ[STRATEGY_ENV] = "R2"
    sig_a, sig_b = "sig_a_111", "sig_b_222"
    rss = [
        {
            "rollout_id": 1,
            "reward": 1.0,
            "leaf_visit_count": 10,
            "result_buckets": {sig_a: 8},
            "all_sql_variants": [
                {
                    "sql": "SELECT 1 AS x",
                    "valid": True,
                    "result_signature": sig_a,
                    "result_row_count": 1,
                },
            ],
        },
        {
            "rollout_id": 2,
            "reward": 0.5,
            "leaf_visit_count": 1,
            "result_buckets": {sig_a: 2, sig_b: 6},
            "all_sql_variants": [
                {
                    "sql": "SELECT 2 AS y",
                    "valid": True,
                    "result_signature": sig_b,
                    "result_row_count": 1,
                },
            ],
        },
    ]
    with redirect_stdout(io.StringIO()):
        sql = SQLSelector.select(rss)
    os.environ.pop(STRATEGY_ENV, None)
    assert "SELECT 1" in sql
