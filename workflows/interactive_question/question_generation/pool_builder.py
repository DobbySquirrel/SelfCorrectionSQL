"""Step C: build atomic question pool from axes + NL rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from experiment.pipeline.selectors import Question
from experiment.pipeline.user_simulator import OptionSpec

from .axis_aggregation import aggregate_axes, build_pairwise_diffs
from .data_structures import DecisionAxis, RenderedQuestion, World
from .llm_rendering import fallback_render, render_with_retry


def worlds_from_items(items) -> list[World]:
    """Map ``InteractionItem`` list to question-generation ``World`` objects."""
    return [
        World(
            world_id=it.key,
            representative_sql=it.representative_sql,
            exec_hash=it.exec_hash,
            member_sqls=[it.representative_sql],
        )
        for it in items
    ]


def append_nota_option(rendered: RenderedQuestion) -> None:
    """Append system 'None of the above' branch (empty world set)."""
    rendered.options.append({
        "label": "NOTA",
        "branch_key": "__nota__",
        "nl_text": rendered.none_of_the_above_label,
        "world_ids": [],
    })


def build_atomic_pool(
    worlds: list[World],
    pairwise_diffs: list,
    question: str,
    llm_client: Any | None,
    *,
    use_nl_rendering: bool = True,
    db_path: str | Path | None = None,
    append_nota: bool = False,
    max_render_retries: int = 2,
) -> list[RenderedQuestion]:
    """
    Aggregate axes and render each into a ``RenderedQuestion``.

    When ``use_nl_rendering`` is False, uses DSL fallback labels only.
    """
    axes = aggregate_axes(worlds, pairwise_diffs, db_path=db_path)
    questions: list[RenderedQuestion] = []

    for axis in axes:
        if use_nl_rendering and llm_client is not None:
            rendered = render_with_retry(
                axis, question, llm_client,
                max_retries=max_render_retries,
            )
        else:
            rendered = fallback_render(axis)

        if append_nota:
            append_nota_option(rendered)
        questions.append(rendered)

    return questions


def rendered_to_questions(
    rendered_list: list[RenderedQuestion],
    items,
    *,
    db_path: str | Path | None = None,
) -> list[Question]:
    """Convert rendered questions to pipeline ``Question`` objects for EIG."""
    from experiment.pipeline.openworld.oracle_hint import scope_from_atomic

    key_set = {it.key for it in items}
    by_key = {it.key: it for it in items}
    out: list[Question] = []

    for rq in rendered_list:
        options: list[OptionSpec] = []
        covered: set[str] = set()

        for opt in rq.options:
            world_ids = set(opt.get("world_ids") or [])
            if opt.get("branch_key") == "__nota__":
                world_ids = set()
            else:
                world_ids &= key_set
            if world_ids:
                rep = next(
                    by_key[h].representative_sql
                    for h in world_ids if h in by_key
                )
            else:
                rep = ""
            options.append(OptionSpec(
                label=opt.get("nl_text", opt.get("branch_key", ""))[:80],
                world_hashes=world_ids,
                representative_sql=rep,
            ))
            covered |= world_ids

        if len(options) < 2:
            continue
        if covered and covered != key_set:
            continue

        family = rq.family or (rq.unit_type.split(":")[0] if ":" in rq.unit_type else "")
        parameter = rq.parameter or (
            rq.unit_type.split(":", 1)[1] if ":" in rq.unit_type else ""
        )
        qid = f"atomic:{family}:{parameter}"
        out.append(Question(
            options=options,
            source=qid,
            metadata={
                "family": family,
                "parameter": parameter,
                "semantic_focus": rq.semantic_focus,
                "fidelity_passed": rq.fidelity_passed,
                "nl_rendering": True,
            },
            scope=scope_from_atomic(family, parameter) if family else (),
            qid=qid,
        ))

    return out


def build_pool_from_items(
    items,
    question: str,
    llm_client: Any | None,
    *,
    use_nl_rendering: bool = False,
    db_path: str | Path | None = None,
    append_nota: bool = False,
) -> list[Question]:
    """End-to-end pool build for ``AtomicPool`` integration."""
    if len(items) < 2:
        return []

    worlds = worlds_from_items(items)
    diffs = build_pairwise_diffs(worlds, db_path=db_path)
    rendered = build_atomic_pool(
        worlds,
        diffs,
        question,
        llm_client,
        use_nl_rendering=use_nl_rendering,
        db_path=db_path,
        append_nota=append_nota,
    )
    if use_nl_rendering:
        return rendered_to_questions(rendered, items, db_path=db_path)

    # Legacy path: same questions as original AtomicPool (DSL labels).
    from experiment.pipeline.ast import (
        dsl_available,
        extract_dsl_variables_for_candidates,
    )

    if not dsl_available():
        return []

    pairs = [(it.key, it.representative_sql) for it in items]
    dvars, _errs = extract_dsl_variables_for_candidates(pairs, db_path=db_path)
    key_set = {it.key for it in items}
    by_key = {it.key: it for it in items}
    questions: list[Question] = []

    for v in dvars:
        value_to_keys: dict[str, set[str]] = {}
        for cid, val in v.candidate_to_value.items():
            if cid in key_set:
                value_to_keys.setdefault(val, set()).add(cid)

        if len(value_to_keys) < 2:
            continue

        options: list[OptionSpec] = []
        for value in sorted(value_to_keys.keys()):
            hashes = value_to_keys[value]
            rep = next(by_key[h].representative_sql for h in hashes if h in by_key)
            label = f"{v.family}:{v.parameter}={value}"
            options.append(OptionSpec(
                label=label[:80],
                world_hashes=hashes,
                representative_sql=rep,
            ))

        covered: set[str] = set()
        for o in options:
            covered |= o.world_hashes

        if len(options) >= 2 and covered == key_set:
            from experiment.pipeline.openworld.oracle_hint import scope_from_atomic
            qid = f"atomic:{v.family}:{v.parameter}"
            questions.append(Question(
                options=options,
                source=qid,
                metadata={"family": v.family, "parameter": v.parameter},
                scope=scope_from_atomic(v.family, v.parameter),
                qid=qid,
            ))

    return questions
