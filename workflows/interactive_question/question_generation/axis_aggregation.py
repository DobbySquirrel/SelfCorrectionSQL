"""Step A: aggregate pairwise atomic diffs into k-way decision axes."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from experiment.pipeline.ast.dsl_slots import (
    FAMILY_PARAMETERS,
    sql_to_dsl_atomic_units,
)

from .data_structures import AtomicDiff, DecisionAxis, World

ABSENT_VALUE = "<absent>"


def unit_type_key(family: str, parameter: str) -> str:
    """Canonical axis key aligned with DSL (family:parameter)."""
    return f"{family}:{parameter}"


def parse_unit_type(unit_type: str) -> tuple[str, str]:
    if ":" not in unit_type:
        raise ValueError(f"invalid unit_type {unit_type!r}; expected 'family:parameter'")
    family, parameter = unit_type.split(":", 1)
    return family, parameter


def normalize_dsl_value(value: str) -> str:
    """Reuse DSL slot normalization (lowercase, collapse whitespace)."""
    if value == ABSENT_VALUE:
        return ABSENT_VALUE
    return " ".join(str(value).split()).lower()


def world_unit_value(
    world: World,
    unit_type: str,
    db_path: str | Path | None,
) -> str:
    """Normalized DSL value for ``unit_type`` on one world; absent if missing."""
    family, parameter = parse_unit_type(unit_type)
    units, _lines, err = sql_to_dsl_atomic_units(
        world.representative_sql, db_path,
    )
    if err:
        return ABSENT_VALUE
    for u in units:
        if u.family == family and u.parameter == parameter:
            return normalize_dsl_value(u.normalized_value)
    return ABSENT_VALUE


def _collect_unit_types(
    pairwise_diffs: list[AtomicDiff],
    worlds: list[World],
    db_path: str | Path | None,
) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(t: str) -> None:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    for diff in pairwise_diffs:
        for u in diff.differing_units:
            _add(u)

    if ordered:
        return ordered

    for family, params in FAMILY_PARAMETERS.items():
        for parameter in params:
            _add(unit_type_key(family, parameter))

    # Drop types that never appear on any world (optional optimization).
    if not worlds:
        return ordered

    present: list[str] = []
    for ut in ordered:
        vals = {world_unit_value(w, ut, db_path) for w in worlds}
        if len(vals) >= 1:
            present.append(ut)
    return present


def aggregate_axes(
    worlds: list[World],
    pairwise_diffs: list[AtomicDiff],
    *,
    db_path: str | Path | None = None,
) -> list[DecisionAxis]:
    """
    Aggregate pairwise diffs into axes: one k-way partition per atomic unit.

    Each axis groups worlds by normalized DSL value on that unit; uniform
    partitions are skipped.
    """
    if len(worlds) < 2:
        return []

    unit_types = _collect_unit_types(pairwise_diffs, worlds, db_path)
    axes: list[DecisionAxis] = []

    for unit_type in unit_types:
        value_to_worlds: dict[str, list[str]] = defaultdict(list)
        for world in worlds:
            val = world_unit_value(world, unit_type, db_path)
            value_to_worlds[val].append(world.world_id)

        if len(value_to_worlds) < 2:
            continue

        family, parameter = parse_unit_type(unit_type)
        partition = {
            val: sorted(wids)
            for val, wids in sorted(value_to_worlds.items())
        }
        axes.append(DecisionAxis(
            axis_id=f"axis_{family}_{parameter}".lower(),
            unit_type=unit_type,
            partition=partition,
        ))

    return axes


def build_pairwise_diffs(
    worlds: list[World],
    *,
    db_path: str | Path | None = None,
) -> list[AtomicDiff]:
    """
    Build pairwise atomic diffs from worlds (for axis unit-type discovery).

    TODO: design decision — full cross-product vs. only differing pairs;
    currently all pairs for compatibility with probing output.
    """
    diffs: list[AtomicDiff] = []
    unit_types = [
        unit_type_key(family, parameter)
        for family, params in FAMILY_PARAMETERS.items()
        for parameter in params
    ]

    for i, wi in enumerate(worlds):
        for wj in worlds[i + 1:]:
            differing: list[str] = []
            unit_values: dict[str, dict[str, str]] = {}
            for ut in unit_types:
                vi = world_unit_value(wi, ut, db_path)
                vj = world_unit_value(wj, ut, db_path)
                if vi != vj:
                    differing.append(ut)
                    unit_values[ut] = {wi.world_id: vi, wj.world_id: vj}
            if differing:
                diffs.append(AtomicDiff(
                    world_pair=(wi.world_id, wj.world_id),
                    differing_units=differing,
                    unit_values=unit_values,
                ))
    return diffs
