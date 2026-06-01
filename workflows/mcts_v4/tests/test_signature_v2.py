"""Unit tests for create_result_signature_v2 vs legacy top-5 hash."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils


def _legacy(res):
    return MCTSUtils.create_result_signature(res)


def _v2(res):
    rows, cols = MCTSUtils.execution_result_to_rows_columns(res)
    return MCTSUtils.create_result_signature_v2(rows, cols)


def test_row_order_invariant_v2_may_differ_legacy():
    base = [{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 5, "b": 6}]
    rev = list(reversed(base))
    r1 = {"valid": True, "query_result": base}
    r2 = {"valid": True, "query_result": rev}
    assert _v2(r1) == _v2(r2)
    # legacy uses top-5 sorted rows — also order-invariant within sample
    assert _legacy(r1) == _legacy(r2)


def test_top5_same_tail_differs_v2_splits_legacy_may_merge():
    shared = [{"x": i, "y": i * 10} for i in range(5)]
    extra = [{"x": 99, "y": 990}]
    r_short = {"valid": True, "query_result": shared}
    r_long = {"valid": True, "query_result": shared + extra}
    assert _v2(r_short) != _v2(r_long)
    # legacy only hashes first 5 rows
    assert _legacy(r_short) == _legacy(r_long)


def test_null_and_float_normalization_stable():
    r1 = {
        "valid": True,
        "query_result": [{"v": None, "n": 1.2345671}],
    }
    r2 = {
        "valid": True,
        "query_result": [{"v": None, "n": 1.2345674}],
    }
    assert _v2(r1) == _v2(r2)
    # legacy keeps full float precision in value sort — only v2 normalizes to 6 decimals
