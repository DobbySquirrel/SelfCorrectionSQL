"""Axis aggregation + LLM rendering for NL clarification questions."""

from .axis_aggregation import ABSENT_VALUE, aggregate_axes
from .data_structures import (
    AtomicDiff,
    DecisionAxis,
    RenderedQuestion,
    World,
)
from .fidelity_validator import validate_fidelity
from .llm_rendering import render_question, render_with_retry
from .pool_builder import (
    build_atomic_pool,
    fallback_render,
    rendered_to_questions,
)

__all__ = [
    "ABSENT_VALUE",
    "AtomicDiff",
    "DecisionAxis",
    "RenderedQuestion",
    "World",
    "aggregate_axes",
    "build_atomic_pool",
    "fallback_render",
    "render_question",
    "render_with_retry",
    "rendered_to_questions",
    "validate_fidelity",
]
