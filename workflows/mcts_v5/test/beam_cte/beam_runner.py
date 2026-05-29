"""Beam expansion: form-enumerated A-axis + oneshot v2 for B–E."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, List, Optional

from workflows.mcts_v1.core.mcts_node import MCTSNode
from workflows.mcts_v1.utils.mcts_helpers import MCTSUtils
from workflows.mcts_v5.taxonomy.axes import NUM_TAXONOMY_AXES, SKIP_CTE_MARKER
from workflows.mcts_v5.utils.eval_ex import evaluate_ex

from .a_axis_generator import (
    A_FORM_ORDER,
    generate_axis_a_candidates,
    select_topk_axis_a,
)
from .c_axis_generator import (
    c_form_index,
    dedup_c_candidates_by_probe,
    generate_axis_c_candidates,
    select_c_forms_to_generate,
)
from .d_axis_generator import (
    d_form_index,
    dedup_d_candidates_by_probe,
    generate_axis_d_candidates,
    select_d_forms_to_generate,
)
from .types import AxisCandidate, BeamPath

MAX_PATHS_PER_QUERY = 12

# Prompt lab helpers (oneshot B–E unchanged)
from workflows.mcts_v5.test.test_taxonomy_prompt_lab import (  # noqa: E402
    LabCase,
    _rebuild_node_after_axes,
    attach_selected_child,
    evaluate_qid_ex,
    full_sql_from_axis_rows,
    run_axis_on_node,
)

PROMPT_MODE = "oneshot"
SELECT_STRATEGY = "llm_select"


def _form_index(form_tag: str) -> int:
    try:
        return A_FORM_ORDER.index(form_tag)
    except ValueError:
        return 99


def _candidate_to_layer(cand: AxisCandidate) -> dict:
    if cand.is_skip or cand.cte_sql in (SKIP_CTE_MARKER, "<SKIP>"):
        return {
            "cte": SKIP_CTE_MARKER,
            "is_skip": True,
            "inherit_parent_probe": False,
        }
    return {
        "cte": cand.cte_sql,
        "is_skip": False,
        "probe_rows": cand.probe_rows or [],
        "probe_valid": cand.is_valid,
    }


def _candidate_to_axis_row(
    cand: AxisCandidate,
    *,
    question_id: Optional[str],
    depth: int = 0,
) -> dict:
    is_skip = cand.is_skip or cand.cte_sql in (SKIP_CTE_MARKER, "<SKIP>")
    exec_res = None
    if not is_skip:
        exec_res = {
            "valid": cand.is_valid,
            "query_result": cand.probe_rows or [],
            "error": cand.error,
        }
    axis_names = {
        "A": "Reference Grounding (beam-A)",
        "C": "Measure Construction (beam-C)",
        "D": "Ranking Target (beam-D)",
    }
    return {
        "case_id": f"qid_{question_id}_axis_{cand.axis_id}_beam",
        "expand_at_depth": depth,
        "axis_id": cand.axis_id,
        "axis_name": axis_names.get(cand.axis_id, f"beam-{cand.axis_id}"),
        "prompt_mode": PROMPT_MODE,
        "select_strategy": "beam_form",
        "n_variants_raw": 1,
        "n_buckets": 1,
        "selected_cte": cand.cte_sql if not is_skip else SKIP_CTE_MARKER,
        "selected_is_skip": is_skip,
        "selected_signature": (cand.probe_hash or "")[:48],
        "selected_bucket_count": 1,
        "question_id": question_id,
        "selected_execution": exec_res,
        "error": cand.error or "",
        "beam_form_tag": cand.form_tag,
    }


def _axis_result_to_row(result: Any, question_id: Optional[str]) -> dict:
    row = asdict(result)
    row["question_id"] = question_id
    return row


def _run_axes_from(
    wf: Any,
    base: LabCase,
    *,
    chat_llm,
    prior_axis_rows: List[dict],
    start_depth: int,
    end_depth: Optional[int] = None,
) -> List[dict]:
    """
    Run axes [start_depth .. end_depth) on top of fixed prior_axis_rows.
    end_depth defaults to NUM_TAXONOMY_AXES (all remaining axes).
    """
    out_rows: List[dict] = list(prior_axis_rows)
    stop = end_depth if end_depth is not None else NUM_TAXONOMY_AXES
    if start_depth >= stop:
        return out_rows

    if start_depth > 0:
        node = _rebuild_node_after_axes(base, prior_axis_rows)
    else:
        MCTSNode._global_node_counter = 0
        from workflows.mcts_v5.core.taxonomy_node import TaxonomyMCTSNode

        node = TaxonomyMCTSNode(
            question=base.question,
            schema_info=base.schema_prompt,
            additional_context=base.evidence,
            parent=None,
        )
        node.bind_axis_from_depth()

    for depth in range(start_depth, stop):
        step_case = LabCase(
            case_id=base.case_id,
            db=base.db,
            question=base.question,
            schema_prompt=base.schema_prompt,
            evidence=base.evidence,
            expand_at_depth=depth,
            path=[],
            expected=base.expected,
            notes=base.notes,
            question_id=base.question_id,
        )
        try:
            result, chosen, _ = run_axis_on_node(
                wf,
                node,
                step_case,
                chat_llm=chat_llm,
                prompt_mode=PROMPT_MODE,
                select_strategy=SELECT_STRATEGY,
            )
            out_rows.append(_axis_result_to_row(result, base.question_id))
            if result.error:
                break
            node = attach_selected_child(wf, node, chosen)
        except Exception as e:
            from workflows.mcts_v5.taxonomy.axes import axis_for_expansion

            ax = axis_for_expansion(depth)
            out_rows.append(
                {
                    "case_id": f"{base.case_id}_axis_{ax.axis_id.value}",
                    "expand_at_depth": depth,
                    "axis_id": ax.axis_id.value,
                    "error": str(e),
                    "question_id": base.question_id,
                }
            )
            break

    return out_rows


def _execute_full_sql(
    wf: Any,
    base: LabCase,
    sql: str,
) -> tuple[bool, Optional[str], Optional[List[dict]], int]:
    if not sql or not wf.db_connector:
        return False, None, None, 0
    from workflows.mcts_v5.core.taxonomy_node import TaxonomyMCTSNode

    node = TaxonomyMCTSNode(
        question=base.question,
        schema_info=base.schema_prompt,
        additional_context=base.evidence,
        parent=None,
    )
    exec_res = wf.sql_executor.execute_queries(node, sql).get("final_sql_result") or {}
    if not exec_res.get("valid"):
        return False, None, None, 0
    rows = exec_res.get("query_result") or []
    try:
        rows = MCTSUtils.safe_to_dict(rows)
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []
    return (
        True,
        MCTSUtils.create_result_signature(exec_res),
        rows,
        len(rows),
    )


def _path_id_for_forms(
    a_tag: str,
    c_tag: str = "0",
    d_tag: str = "0",
) -> str:
    return f"A{_form_index(a_tag)}.B0.C{c_form_index(c_tag)}.D{d_form_index(d_tag)}.E0"


def _path_id_for_form(form_tag: str) -> str:
    return _path_id_for_forms(form_tag)


def _finalize_beam_path(
    wf: Any,
    case: LabCase,
    *,
    axis_candidates: List[AxisCandidate],
    axis_rows: List[dict],
    path_id: str,
    gold_sql: Optional[str],
) -> BeamPath:
    final_sql = full_sql_from_axis_rows(wf, axis_rows)
    executable, exec_hash, exec_rows, exec_n = _execute_full_sql(wf, case, final_sql)
    path = BeamPath(
        path_id=path_id,
        axis_candidates=axis_candidates,
        axis_rows=axis_rows,
        final_sql=final_sql,
        executable=executable,
        exec_result_hash=exec_hash,
        exec_row_count=exec_n,
        exec_sample_rows=exec_rows[:3] if exec_rows else None,
    )
    if gold_sql and final_sql and wf.db_connector:
        ex = evaluate_ex(wf.db_connector, final_sql, gold_sql, [])
        path.exec_match_gold = bool(ex.get("ex_optimal"))
    return path


def dedup_paths_for_judge(paths: List[BeamPath]) -> List[BeamPath]:
    """
    Collapse paths with identical full-SQL execution hashes before judge rerank.
    EX metrics (hit1/any_path) must use the full path list, not this output.
    """
    by_hash: dict[str, BeamPath] = {}
    extras: List[BeamPath] = []
    for p in paths:
        if not p.executable or not p.exec_result_hash:
            extras.append(p)
            continue
        h = p.exec_result_hash
        if h not in by_hash:
            by_hash[h] = p
            continue
        kept = by_hash[h]
        if _form_index(p.form_a) < _form_index(kept.form_a):
            kept.error = "exec_dup"
            extras.append(kept)
            by_hash[h] = p
        else:
            p.error = "exec_dup"
            extras.append(p)

    deduped = list(by_hash.values()) + [p for p in extras if p.error != "exec_dup"]
    return deduped if deduped else list(paths)


def run_beam_a_oneshot_rest(
    chat_llm,
    case: LabCase,
    db_executor,
    *,
    k_a: int = 3,
    gold_sql: Optional[str] = None,
) -> List[BeamPath]:
    """
    1. Form-enumerate 3 A candidates.
    2. Rank, keep top-k_a.
    3. For each: run oneshot B–E from fixed A.
    4. Return all paths (dedup only at judge input via dedup_paths_for_judge).
    """
    wf = db_executor
    raw_a = generate_axis_a_candidates(
        chat_llm,
        question=case.question,
        evidence=case.evidence or "",
        schema_text=case.schema_prompt,
        db_executor=wf.sql_executor,
    )
    ranked_a = select_topk_axis_a(
        chat_llm,
        question=case.question,
        evidence=case.evidence or "",
        schema_text=case.schema_prompt,
        candidates=raw_a,
        k=k_a,
    )

    paths: List[BeamPath] = []

    for a_cand in ranked_a:
        pid = _path_id_for_form(a_cand.form_tag)
        if a_cand.is_skip or not a_cand.is_valid:
            paths.append(
                BeamPath(
                    path_id=pid,
                    axis_candidates=[a_cand],
                    error=a_cand.error or "invalid_a",
                )
            )
            continue

        base = LabCase(
            case_id=case.case_id,
            db=case.db,
            question=case.question,
            schema_prompt=case.schema_prompt,
            evidence=case.evidence,
            expand_at_depth=1,
            path=[_candidate_to_layer(a_cand)],
            question_id=case.question_id,
        )

        a_row = _candidate_to_axis_row(a_cand, question_id=case.question_id, depth=0)
        axis_rows = _run_axes_from(
            wf,
            base,
            chat_llm=chat_llm,
            prior_axis_rows=[a_row],
            start_depth=1,
        )

        path = _finalize_beam_path(
            wf,
            case,
            axis_candidates=[a_cand],
            axis_rows=axis_rows,
            path_id=pid,
            gold_sql=gold_sql,
        )
        paths.append(path)

    return paths


def run_beam_acd_oneshot_rest(
    chat_llm,
    case: LabCase,
    db_executor,
    *,
    k_a: int = 3,
    k_c: int = 3,
    k_d: int = 3,
    max_paths: int = MAX_PATHS_PER_QUERY,
    gold_sql: Optional[str] = None,
) -> tuple[List[BeamPath], dict]:
    """
    Beam over A, C, D; B and E stay oneshot v2 single-path.
    Returns (all_paths, meta) with triggered form lists.
    """
    wf = db_executor
    q = case.question
    ev = case.evidence or ""
    schema = case.schema_prompt

    raw_a = generate_axis_a_candidates(
        chat_llm,
        question=q,
        evidence=ev,
        schema_text=schema,
        db_executor=wf.sql_executor,
    )
    ranked_a = select_topk_axis_a(
        chat_llm,
        question=q,
        evidence=ev,
        schema_text=schema,
        candidates=raw_a,
        k=k_a,
    )

    triggered_c_global = select_c_forms_to_generate(q, ev, a_form_tag="")
    triggered_d_global = select_d_forms_to_generate(q, ev)

    paths: List[BeamPath] = []

    for a_cand in ranked_a:
        if len(paths) >= max_paths:
            break

        pid_base = _path_id_for_form(a_cand.form_tag)
        if a_cand.is_skip or not a_cand.is_valid:
            paths.append(
                BeamPath(
                    path_id=pid_base,
                    axis_candidates=[a_cand],
                    error=a_cand.error or "invalid_a",
                )
            )
            continue

        base = LabCase(
            case_id=case.case_id,
            db=case.db,
            question=case.question,
            schema_prompt=case.schema_prompt,
            evidence=case.evidence,
            expand_at_depth=1,
            path=[_candidate_to_layer(a_cand)],
            question_id=case.question_id,
        )

        a_row = _candidate_to_axis_row(a_cand, question_id=case.question_id, depth=0)
        rows_ab = _run_axes_from(
            wf,
            base,
            chat_llm=chat_llm,
            prior_axis_rows=[a_row],
            start_depth=1,
            end_depth=2,
        )

        c_forms = select_c_forms_to_generate(q, ev, a_form_tag=a_cand.form_tag)
        raw_c = generate_axis_c_candidates(
            chat_llm,
            question=q,
            evidence=ev,
            schema_text=schema,
            prior_axis_rows=rows_ab,
            db_executor=wf.sql_executor,
            a_form_tag=a_cand.form_tag,
        )
        c_list = dedup_c_candidates_by_probe(raw_c, k=k_c)

        for c_cand in c_list:
            if len(paths) >= max_paths:
                break

            rows_abc = list(rows_ab) + [
                _candidate_to_axis_row(
                    c_cand, question_id=case.question_id, depth=2
                )
            ]

            d_forms = select_d_forms_to_generate(q, ev)
            raw_d = generate_axis_d_candidates(
                chat_llm,
                question=q,
                evidence=ev,
                schema_text=schema,
                prior_axis_rows=rows_abc,
                db_executor=wf.sql_executor,
            )
            d_list = dedup_d_candidates_by_probe(raw_d, k=k_d)

            for d_cand in d_list:
                if len(paths) >= max_paths:
                    break

                rows_abcd = list(rows_abc) + [
                    _candidate_to_axis_row(
                        d_cand, question_id=case.question_id, depth=3
                    )
                ]
                rows_full = _run_axes_from(
                    wf,
                    base,
                    chat_llm=chat_llm,
                    prior_axis_rows=rows_abcd,
                    start_depth=4,
                    end_depth=NUM_TAXONOMY_AXES,
                )

                pid = _path_id_for_forms(
                    a_cand.form_tag,
                    c_cand.form_tag,
                    d_cand.form_tag,
                )
                path = _finalize_beam_path(
                    wf,
                    case,
                    axis_candidates=[a_cand, c_cand, d_cand],
                    axis_rows=rows_full,
                    path_id=pid,
                    gold_sql=gold_sql,
                )
                paths.append(path)

    meta = {
        "triggered_c_forms": triggered_c_global,
        "triggered_d_forms": triggered_d_global,
        "max_paths": max_paths,
    }
    return paths, meta
