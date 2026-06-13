"""Tests for MCTS_DECOMPOSE_MIN_SUBQUESTIONS prompt + retry wiring."""

import os
from unittest.mock import MagicMock, patch

from workflows.mcts_v4.agents.question_decomposer import (
    QuestionDecomposer,
    decompose_min_subquestions,
    _subq_count_requirement,
)


def test_min_subquestions_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MCTS_DECOMPOSE_MIN_SUBQUESTIONS", None)
        assert decompose_min_subquestions() == 1
        assert "only 1 sub-question" in _subq_count_requirement()


def test_min_subquestions_env_2():
    with patch.dict(os.environ, {"MCTS_DECOMPOSE_MIN_SUBQUESTIONS": "2"}):
        assert decompose_min_subquestions() == 2
        req = _subq_count_requirement()
        assert "at least 2" in req


def test_s2_system_message_reflects_min2():
    with patch.dict(os.environ, {"MCTS_DECOMPOSE_MIN_SUBQUESTIONS": "2"}):
        msg = QuestionDecomposer._msg_s2(None)
        assert "at least 2" in msg


def test_decompose_retries_when_too_few():
    llm = {"config_list": [{"model": "m", "base_url": "http://x/v1", "api_key": "k"}]}
    proxy = MagicMock()
    proxy.initiate_chat.side_effect = [
        MagicMock(chat_history=[MagicMock(content='["only one step"]')]),
        MagicMock(chat_history=[MagicMock(content='["step one", "step two"]')]),
    ]

    with patch.dict(os.environ, {"MCTS_DECOMPOSE_MIN_SUBQUESTIONS": "2"}):
        d = QuestionDecomposer(llm, strategy="S2")
        d._user_proxy = proxy
        out = d.decompose("Q?", "schema", "")
    assert out == ["step one", "step two"]
    assert proxy.initiate_chat.call_count == 2
