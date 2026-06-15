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
    assert _legacy(r1) == _legacy(r2)


def test_top5_same_tail_differs_v2_splits_legacy_may_merge():
    shared = [{"x": i, "y": i * 10} for i in range(5)]
    extra = [{"x": 99, "y": 990}]
    r_short = {"valid": True, "query_result": shared}
    r_long = {"valid": True, "query_result": shared + extra}
    assert _v2(r_short) != _v2(r_long)
    assert _legacy(r_short) == _legacy(r_long)


def test_column_names_ignored_v2():
    r1 = {"valid": True, "query_result": [{"answer": 5}]}
    r2 = {"valid": True, "query_result": [{"count": 5}]}
    assert _v2(r1) == _v2(r2)


def test_float_keeps_original_precision_v2():
    r1 = {"valid": True, "query_result": [{"v": None, "n": 1.2345671}]}
    r2 = {"valid": True, "query_result": [{"v": None, "n": 1.2345674}]}
    assert _v2(r1) != _v2(r2)


def test_scheme_a_dual_search_legacy_final_v2(monkeypatch):
    import workflows.mcts_v4.utils.mcts_helpers as mh

    shared = [{"x": i} for i in range(5)]
    r_short = {"valid": True, "query_result": shared}
    r_long = {"valid": True, "query_result": shared + [{"x": 99}]}

    monkeypatch.setenv("MCTS_USE_SIGNATURE_V2", "0")
    monkeypatch.setenv("MCTS_FINAL_SIGNATURE_V2", "1")
    mh.USE_SIGNATURE_V2_FOR_SEARCH = False
    mh.USE_FINAL_SIGNATURE_V2 = True

    assert mh.MCTSUtils.bucket_key_for_search(r_short) == mh.MCTSUtils.bucket_key_for_search(r_long)
    assert mh.MCTSUtils.bucket_key_for_final(r_short) != mh.MCTSUtils.bucket_key_for_final(r_long)

    buckets, _ = mh.MCTSUtils.bucketize_valid_nonempty([r_short, r_long])
    assert len(buckets) == 2
