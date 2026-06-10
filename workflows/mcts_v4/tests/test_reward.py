"""Unit tests for calibrated consistency reward (MCTS_REWARD_CALIBRATED)."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils


def _legacy(buckets, n):
    return MCTSUtils.calculate_consistency_reward_legacy(buckets, n)


def test_default_env_matches_legacy():
    os.environ.pop("MCTS_REWARD_CALIBRATED", None)
    cases = [
        ({}, 8),
        ({"a": 8}, 8),
        ({"a": 5, "b": 3}, 8),
        ({"a": 3, "b": 2, "c": 1}, 6),
    ]
    for buckets, n in cases:
        assert MCTSUtils.calculate_consistency_reward(buckets, n) == _legacy(buckets, n)


def test_calibrated_single_bucket_unchanged():
    os.environ["MCTS_REWARD_CALIBRATED"] = "1"
    assert MCTSUtils.calculate_consistency_reward({"sig": 8}, 8) == 1.0
    assert MCTSUtils.calculate_consistency_reward({"sig": 5}, 8) == 5 / 8.0
    os.environ.pop("MCTS_REWARD_CALIBRATED", None)


def test_calibrated_two_buckets_penalty():
    os.environ["MCTS_REWARD_CALIBRATED"] = "1"
    base = 6 / 8.0
    got = MCTSUtils.calculate_consistency_reward({"a": 6, "b": 2}, 8)
    assert abs(got - base * 0.85) < 1e-9
    os.environ.pop("MCTS_REWARD_CALIBRATED", None)


def test_calibrated_three_plus_buckets_penalty():
    os.environ["MCTS_REWARD_CALIBRATED"] = "1"
    base = 4 / 8.0
    got = MCTSUtils.calculate_consistency_reward({"a": 4, "b": 2, "c": 2}, 8)
    assert abs(got - base * 0.70) < 1e-9
    os.environ.pop("MCTS_REWARD_CALIBRATED", None)
