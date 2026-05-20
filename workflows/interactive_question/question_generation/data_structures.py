"""Data structures for question generation (axis aggregation + NL rendering)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class World:
    """Execution-equivalence class (one branch of W)."""

    world_id: str
    representative_sql: str
    exec_hash: str
    member_sqls: list[str] = field(default_factory=list)
    ast: dict[str, Any] = field(default_factory=dict)


@dataclass
class AtomicDiff:
    """Atomic-unit diff between two worlds."""

    world_pair: tuple[str, str]
    differing_units: list[str]
    unit_values: dict[str, dict[str, str]]


@dataclass
class DecisionAxis:
    """A k-way partition of W along one atomic unit dimension."""

    axis_id: str
    unit_type: str
    partition: dict[str, list[str]]

    @property
    def num_branches(self) -> int:
        return len(self.partition)

    @property
    def covered_worlds(self) -> set[str]:
        return {w for ws in self.partition.values() for w in ws}


@dataclass
class RenderedQuestion:
    """LLM-rendered natural-language clarification question."""

    axis_id: str
    semantic_focus: str
    options: list[dict[str, Any]]
    none_of_the_above_label: str = "None of the above"
    fidelity_passed: bool = False
    raw_llm_response: str = ""
    unit_type: str = ""
    family: str = ""
    parameter: str = ""
