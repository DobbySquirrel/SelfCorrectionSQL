"""Tests for axis aggregation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from question_generation.axis_aggregation import ABSENT_VALUE, aggregate_axes
from question_generation.data_structures import AtomicDiff, World


def _world(wid: str, sql: str = "SELECT 1") -> World:
    return World(
        world_id=wid,
        representative_sql=sql,
        exec_hash=wid,
        member_sqls=[sql],
    )


@patch("question_generation.axis_aggregation.world_unit_value")
def test_aggregate_single_unit(mock_value):
    """Three worlds differ on one unit → one axis."""
    worlds = [_world("w_1"), _world("w_2"), _world("w_3")]
    mock_value.side_effect = lambda w, ut, db: {
        "w_1": "customer",
        "w_2": "order",
        "w_3": "product",
    }[w.world_id]

    diffs = [
        AtomicDiff(
            world_pair=("w_1", "w_2"),
            differing_units=["aggregate:GROUP"],
            unit_values={},
        ),
    ]
    axes = aggregate_axes(worlds, diffs, db_path=None)

    assert len(axes) == 1
    assert axes[0].unit_type == "aggregate:GROUP"
    assert axes[0].partition == {
        "customer": ["w_1"],
        "order": ["w_2"],
        "product": ["w_3"],
    }


@patch("question_generation.axis_aggregation.world_unit_value")
def test_aggregate_multiple_units(mock_value):
    """Two differing units → two axes."""
    worlds = [_world("w_1"), _world("w_2"), _world("w_3")]

    def _val(w, ut, db):
        table = {
            ("w_1", "aggregate:GROUP"): "customer",
            ("w_2", "aggregate:GROUP"): "order",
            ("w_3", "aggregate:GROUP"): "product",
            ("w_1", "aggregate:SELECT"): "sum(amount)",
            ("w_2", "aggregate:SELECT"): "sum(amount)",
            ("w_3", "aggregate:SELECT"): "avg(amount)",
        }
        return table[(w.world_id, ut)]

    mock_value.side_effect = _val
    diffs = [
        AtomicDiff(
            world_pair=("w_1", "w_3"),
            differing_units=["aggregate:GROUP", "aggregate:SELECT"],
            unit_values={},
        ),
    ]
    axes = aggregate_axes(worlds, diffs, db_path=None)
    unit_types = {a.unit_type for a in axes}
    assert unit_types == {"aggregate:GROUP", "aggregate:SELECT"}


@patch("question_generation.axis_aggregation.world_unit_value")
def test_aggregate_absent_unit(mock_value):
    """Missing unit on a world → '<absent>' branch."""
    worlds = [_world("w_1"), _world("w_2")]

    def _val(w, ut, db):
        if w.world_id == "w_1":
            return "customer"
        return ABSENT_VALUE

    mock_value.side_effect = _val
    diffs = [
        AtomicDiff(
            world_pair=("w_1", "w_2"),
            differing_units=["aggregate:GROUP"],
            unit_values={},
        ),
    ]
    axes = aggregate_axes(worlds, diffs, db_path=None)
    assert len(axes) == 1
    assert ABSENT_VALUE in axes[0].partition
    assert axes[0].partition[ABSENT_VALUE] == ["w_2"]


@patch("question_generation.axis_aggregation.world_unit_value")
def test_aggregate_skip_uniform(mock_value):
    """Uniform unit values → no axis."""
    worlds = [_world("w_1"), _world("w_2")]
    mock_value.return_value = "same_value"
    diffs = [
        AtomicDiff(
            world_pair=("w_1", "w_2"),
            differing_units=["filter:WHERE"],
            unit_values={},
        ),
    ]
    axes = aggregate_axes(worlds, diffs, db_path=None)
    assert axes == []
