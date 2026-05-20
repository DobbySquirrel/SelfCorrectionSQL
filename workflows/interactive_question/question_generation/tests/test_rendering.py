"""Tests for LLM rendering and fidelity."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from question_generation.data_structures import DecisionAxis
from question_generation.fidelity_validator import validate_fidelity
from question_generation.llm_rendering import render_with_retry


@dataclass
class MockLLMResponse:
    text: str


class MockLLM:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, messages, temperature=0.3, max_tokens=1024, stop=None):
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return MockLLMResponse(text=self._responses[idx])


def _axis() -> DecisionAxis:
    return DecisionAxis(
        axis_id="axis_aggregate_group",
        unit_type="aggregate:GROUP",
        partition={
            "customer": ["w_1"],
            "order": ["w_2"],
            "product": ["w_3"],
        },
    )


def test_render_fidelity_pass():
    """Valid LLM JSON → fidelity_passed=True."""
    llm_json = """{
      "semantic_focus": "highest monthly",
      "options": [
        {"branch_key": "customer", "nl_text": "by customer"},
        {"branch_key": "order", "nl_text": "by order"},
        {"branch_key": "product", "nl_text": "by product"}
      ]
    }"""
    llm = MockLLM([llm_json])
    rendered = render_with_retry(_axis(), "Who had the highest monthly sales?", llm)
    assert rendered.fidelity_passed is True
    assert rendered.semantic_focus == "highest monthly"
    assert len(rendered.options) == 3


def test_render_fidelity_fail_wrong_count():
    """Wrong option count → retry → fallback."""
    bad = """{"semantic_focus": "sales", "options": [
      {"branch_key": "customer", "nl_text": "a"},
      {"branch_key": "order", "nl_text": "b"}
    ]}"""
    llm = MockLLM([bad, bad])
    rendered = render_with_retry(
        _axis(), "monthly sales", llm, max_retries=2,
    )
    assert rendered.fidelity_passed is False
    assert llm.calls == 2
    assert rendered.options[0]["nl_text"] == "customer"


def test_render_fidelity_fail_invalid_branch_key():
    """Invalid branch_key → retry → fallback."""
    bad = """{"semantic_focus": "sales", "options": [
      {"branch_key": "customer", "nl_text": "a"},
      {"branch_key": "bogus", "nl_text": "b"},
      {"branch_key": "product", "nl_text": "c"}
    ]}"""
    llm = MockLLM([bad, bad])
    rendered = render_with_retry(_axis(), "q", llm, max_retries=2)
    assert rendered.fidelity_passed is False
    assert "bogus" not in [o["branch_key"] for o in rendered.options]


def test_render_fallback():
    """LLM always fails → DSL fallback, pipeline-safe output."""
    llm = MockLLM(["not json at all", "still bad"])
    rendered = render_with_retry(_axis(), "q", llm, max_retries=2)
    assert rendered.fidelity_passed is False
    assert len(rendered.options) == 3
    keys = {o["branch_key"] for o in rendered.options}
    assert keys == {"customer", "order", "product"}


def test_validate_fidelity_empty_focus():
    ok, reason = validate_fidelity(
        {"semantic_focus": "", "options": []},
        _axis(),
    )
    assert ok is False
    assert "semantic_focus" in reason
