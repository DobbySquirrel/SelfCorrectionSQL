#!/usr/bin/env python3
"""Offline unit checks for beam-CTE parsers (no LLM / DB)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

from workflows.mcts_v5.test.beam_cte.a_axis_generator import _parse_ranked_indices
from workflows.mcts_v5.test.beam_cte.llm_judge import _parse_judge_lines


def test_parse_ranked_indices() -> None:
    text = '{"ranked": [{"idx": 2, "reason": "left"}, {"idx": 1, "reason": "inner"}]}'
    assert _parse_ranked_indices(text, 3) == [1, 0, 2]


def test_parse_judge_lines() -> None:
    text = (
        '{"idx": 1, "score": 9.0, "reason": "good"}\n'
        '{"idx": 2, "score": 4.0, "reason": "bad"}\n'
    )
    scores = _parse_judge_lines(text, 2)
    assert scores[1][0] == 9.0
    assert scores[2][0] == 4.0


if __name__ == "__main__":
    test_parse_ranked_indices()
    test_parse_judge_lines()
    print("beam_cte unit checks passed")
