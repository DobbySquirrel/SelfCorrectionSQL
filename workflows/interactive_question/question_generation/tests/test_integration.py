"""Integration smoke test: pool builder with mocked unit values."""

from __future__ import annotations

from unittest.mock import patch

from question_generation.data_structures import World
from question_generation.pool_builder import build_atomic_pool, fallback_render


@patch("question_generation.pool_builder.render_with_retry")
@patch("question_generation.pool_builder.aggregate_axes")
def test_build_atomic_pool_nl_path(mock_axes, mock_render):
    from question_generation.data_structures import DecisionAxis

    axis = DecisionAxis(
        axis_id="axis_test",
        unit_type="aggregate:GROUP",
        partition={"a": ["w1"], "b": ["w2"]},
    )
    mock_axes.return_value = [axis]
    mock_render.return_value = fallback_render(axis)

    worlds = [
        World("w1", "SELECT 1", "h1"),
        World("w2", "SELECT 2", "h2"),
    ]
    out = build_atomic_pool(
        worlds, [], "question?", object(),
        use_nl_rendering=True,
    )
    assert len(out) == 1
    mock_render.assert_called_once()
