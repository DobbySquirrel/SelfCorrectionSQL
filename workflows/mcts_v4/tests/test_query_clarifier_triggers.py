"""S2: trigger logic tests."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.query_clarifier.config import ENV_ENABLE
from workflows.mcts_v4.query_clarifier.schemas import NodeStats
from workflows.mcts_v4.query_clarifier.triggers import (
    build_node_stats,
    reset_run_clarify_count,
    should_clarify,
)


def _enable():
    os.environ[ENV_ENABLE] = "1"
    reset_run_clarify_count()


def _disable():
    os.environ.pop(ENV_ENABLE, None)
    reset_run_clarify_count()


def test_disabled_never_triggers():
    _disable()
    ok, reason = should_clarify(NodeStats(high_reward_rollouts=8, n_nonempty_result_buckets=3))
    assert ok is False
    assert reason == "disabled"


def test_s7_like_trigger():
    _enable()
    ok, reason = should_clarify(NodeStats(high_reward_rollouts=6, n_nonempty_result_buckets=2))
    assert ok is True
    assert "high_reward" in reason


def test_high_reward_but_single_cluster():
    _enable()
    ok, _ = should_clarify(NodeStats(high_reward_rollouts=8, n_nonempty_result_buckets=1))
    assert ok is False


def test_exhaustion_trigger():
    _enable()
    ok, reason = should_clarify(
        NodeStats(high_reward_rollouts=2, n_nonempty_result_buckets=2, exhaustion_triggered=True)
    )
    assert ok is True
    assert "exhaustion" in reason


def test_build_node_stats_from_rollouts():
    rss = [
        {"reward": 0.95, "result_buckets": {"sig_a": 2}, "leaf_visit_count": 3, "all_sql_variants": []},
        {"reward": 0.92, "result_buckets": {"sig_b": 1}, "leaf_visit_count": 2, "all_sql_variants": []},
        {"reward": 0.5, "result_buckets": {"sig_a": 1}, "leaf_visit_count": 1, "all_sql_variants": []},
    ]
    ns = build_node_stats(rss)
    assert ns.high_reward_rollouts == 2
    assert ns.n_nonempty_result_buckets == 2
