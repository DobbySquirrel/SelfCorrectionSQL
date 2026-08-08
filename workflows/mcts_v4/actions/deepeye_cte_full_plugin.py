#!/usr/bin/env python3
"""DeepEye-aligned FULL CTE plugin v2 (step-level).

Modules (aligned with official pipeline, CTE grain):
  1) Schema: linked schema; VR → schema footer (same as fullsql fair)
  2) Gen: PromptFactory DC/Skeleton/ICL with evidence-only HINT + step context postfix;
     progressive schema fit; fixed temp 0.7 × n completions (not temp ladder)
  3) Revise: execution_checker + common_checker (PromptFactory)
  4) Select: consistency shortcut (≥0.6) else BR×5 on top-2
"""

from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import OpenAI

from workflows.mcts_v4.actions.deepeye_cte_plugin import (
    _apply_cte_rename,
    build_sql_from_chain,
    exec_rows,
    extract_cte_defs,
    format_result_preview,
    result_sig,
    _llm,
    _parse_ab,
)
from workflows.mcts_v4.actions.deepeye_fullsql_fair_plugin import (
    _fit_prompt_with_schema,
    _llm_max_model_len,
    _llm_n,
    _max_prompt_chars,
)


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _fix_revise_multicte() -> bool:
    """Bugfix #4: multi-CTE-aware _sql_to_cte.

    Default ON: models often emit full WITH chains for a step; keep leaf only when
    preceding CTEs exist (matches progressive DAG / chain ingest expectations).
    Set MCTS_BUGFIX_REVISE_MULTICTE=0 to restore legacy pass-through.
    """
    return _env_flag("MCTS_BUGFIX_REVISE_MULTICTE", "1")


def _fix_cte_names_multi() -> bool:
    """Bugfix #6: extract all preceding CTE names (default OFF for ablation)."""
    return _env_flag("MCTS_BUGFIX_CTE_NAMES_MULTI", "0")

PROMPT_BR = """# Task
Given the DB info and the CURRENT sub-question, compare two candidate intermediate CTEs / SQL steps. Choose the correct one for THIS step.

# Context
- Candidate A currently has higher consistency prior than B.
- Prefer A unless B is clearly superior or A has obvious errors.
- Prefer the candidate that continues the CTE chain (references preceding CTEs) over one that re-solves the full question from base tables.

# Output Format
<result>
A or B
</result>

# Input
## Database Schema:
{schema}

## Original Question:
{question}

## Evidence:
{evidence}

## Current Sub-question:
{sub_question}

## Preceding CTEs:
{preceding}

Candidate A:
{query_a}

## Execution Result A:
{result_a}

Candidate B:
{query_b}

## Execution Result B:
{result_b}

# Output:
"""


def _cte_names(preceding_ctes: List[str]) -> List[str]:
    """Preceding CTE names for refs_prev. Multi-CTE expand behind MCTS_BUGFIX_CTE_NAMES_MULTI."""
    names: List[str] = []
    seen = set()
    for c in preceding_ctes or []:
        if _fix_cte_names_multi():
            defs = extract_cte_defs(c or "")
            if defs:
                for n, _inner in defs:
                    key = n.lower()
                    if key not in seen:
                        seen.add(key)
                        names.append(n)
                continue
        m = re.search(r"\bWITH\s+(\w+)\s+AS\b", c or "", re.I)
        if m:
            n = m.group(1)
            key = n.lower()
            if key not in seen:
                seen.add(key)
                names.append(n)
    return names


def _sql_body_norm(cte: str) -> str:
    """Normalize CTE/SQL body for near-duplicate detection."""
    s = (cte or "").strip()
    s = re.sub(r"(?is)\bWITH\s+\w+\s+AS\s*\(", "(", s)
    s = re.sub(r"(?is)\)\s*SELECT\s+\*\s+FROM\s+\w+\s*;?\s*$", ")", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _references_preceding(cte: str, names: List[str]) -> bool:
    if not names:
        return False
    text = cte or ""
    return any(re.search(rf"\b{re.escape(n)}\b", text, re.I) for n in names)


def _is_near_duplicate_of_preceding(cte: str, preceding_ctes: List[str]) -> bool:
    body = _sql_body_norm(cte)
    if len(body) < 20:
        return False
    for p in preceding_ctes or []:
        pb = _sql_body_norm(p)
        if len(pb) < 20:
            continue
        if body == pb:
            return True
        # high overlap on token bags
        bt, pt = set(body.split()), set(pb.split())
        if not bt or not pt:
            continue
        inter = len(bt & pt)
        union = len(bt | pt)
        if union and inter / union >= 0.92:
            return True
    return False


def _load_deepeye_prompt_factory():
    root = "/hpc2hdd/home/sshen190/wtao565/related_project/DeepEye-SQL"
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.prompt.factory import PromptFactory  # type: ignore

    return PromptFactory


def _client_model(llm_config: Optional[dict]):
    if llm_config and llm_config.get("config_list"):
        c0 = llm_config["config_list"][0]
        timeout = float((llm_config or {}).get("timeout") or 120)
        client = OpenAI(
            base_url=c0.get("base_url"),
            api_key=c0.get("api_key") or "EMPTY",
            timeout=timeout,
        )
        return client, c0.get("model") or ""
    raise RuntimeError("llm_config.config_list required")


def _extract_sql(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    m = re.search(r"<result>\s*([\s\S]*?)\s*</result>", raw, re.I)
    if m:
        raw = m.group(1).strip()
    m = re.search(r"<sql>\s*([\s\S]*?)\s*</sql>", raw, re.I)
    if m:
        raw = m.group(1).strip()
    m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", raw, re.I)
    if m:
        raw = m.group(1).strip()
    return raw.strip().rstrip(";")


def _sql_to_cte(
    sql: str,
    *,
    step_name: str = "step",
    preceding_ctes: Optional[List[str]] = None,
) -> str:
    """Normalize model SQL into a chain step.

    Legacy (default): pass WITH through / wrap SELECT (may keep table-named CTEs).
    Bugfix #4 (MCTS_BUGFIX_REVISE_MULTICTE=1): expand multi-CTE, synthetic names,
    and if preceding exists keep only the last CTE from a full-chain rewrite.
    """
    raw = (sql or "").strip().rstrip(";")
    if not raw:
        return ""
    if not _fix_revise_multicte():
        if re.search(r"\bWITH\b", raw, re.I):
            return raw if raw.upper().lstrip().startswith("WITH") else f"WITH {raw}"
        if re.search(r"\bSELECT\b", raw, re.I):
            return f"WITH {step_name} AS (\n{raw}\n)\nSELECT * FROM {step_name}"
        return ""

    defs = extract_cte_defs(raw) if re.search(r"\bWITH\b", raw, re.I) else []
    if not defs:
        if re.search(r"\bSELECT\b", raw, re.I):
            return f"WITH {step_name} AS (\n{raw}\n)\nSELECT * FROM {step_name}"
        return ""
    if preceding_ctes and len(defs) > 1:
        # Common checker often rewrites the whole WITH chain; don't re-append priors.
        defs = [defs[-1]]
    parts: List[str] = []
    rename: Dict[str, str] = {}
    for j, (old_name, inner) in enumerate(defs):
        new_name = step_name if len(defs) == 1 else f"{step_name}_{j}"
        body = _apply_cte_rename(inner, rename)
        parts.append(f"{new_name} AS (\n{body}\n)")
        rename[old_name] = new_name
    last = step_name if len(defs) == 1 else f"{step_name}_{len(defs) - 1}"
    return f"WITH {', '.join(parts)}\nSELECT * FROM {last}"


def _revise_candidate(
    *,
    cte: str,
    preceding_ctes: List[str],
    db_path: Path,
    schema: str,
    question: str,
    evidence: str,
    sub_question: str,
    client,
    model: str,
    PF,
    checker_budget: int = 3,
    sql_compose_fn: Optional[Callable[[str], str]] = None,
    exec_timeout_s: float = 600.0,
    linked_schema: Optional[Dict[str, Any]] = None,
    extra_evidence: str = "",
    max_model_len: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Revise composed step SQL; default = official DeepEye checkers (same as full SQL).

    Flow: compose(CTE) → official_revise_full_sql → wrap leaf back to CTE.
    Falls back to legacy exec/common loop only if official revise is disabled/errors.
    """
    audit: Dict[str, Any] = {"rounds": [], "final_ok": False, "mode": "legacy"}
    cur = (cte or "").strip()
    evidence_s = (evidence or "").strip() or "None"
    # Official HINT = evidence only (do not stuff sub-step into HINT).
    q = (question or sub_question or "").strip()

    def _compose(blob: str) -> str:
        if sql_compose_fn is not None:
            try:
                out = (sql_compose_fn(blob) or "").strip()
                if out:
                    return out
            except Exception:
                pass
        return build_sql_from_chain(preceding_ctes, blob)

    def _step_name(blob: str) -> str:
        m = re.search(r"\bWITH\s+(\w+)\s+AS\b", blob or "", re.I)
        return m.group(1) if m else "revised"

    # --- preferred: official revise import (Syntax→rules→Result empty-trigger) ---
    try:
        from workflows.mcts_v4.actions.deepeye_plugin import (
            official_revise_enabled,
            revise_full_sql_official,
        )

        if official_revise_enabled() and cur:
            sql0 = _compose(cur)
            if not sql0.strip():
                audit["rounds"].append({"mode": "official_skip_empty_compose"})
            else:
                def _exec_fn(p: Path, s: str):
                    return exec_rows(p, s, timeout_s=float(exec_timeout_s))

                fixed_sql, raudit = revise_full_sql_official(
                    sql=sql0,
                    question=q,
                    evidence=evidence_s,
                    schema=schema,
                    db_path=db_path,
                    client=client,
                    model=model,
                    exec_fn=_exec_fn,
                    sampling_budget=max(1, int(checker_budget)),
                    linked_schema=linked_schema,
                    max_model_len=max_model_len,
                    exec_timeout_s=float(exec_timeout_s),
                    extra_evidence=extra_evidence,
                )
                fixed_sql = (fixed_sql or sql0).strip()
                new_cte = _sql_to_cte(
                    fixed_sql,
                    step_name=_step_name(cur),
                    preceding_ctes=list(preceding_ctes or []),
                )
                if new_cte:
                    cur = new_cte
                audit["mode"] = getattr(raudit, "mode", "") or "official_revise_import"
                audit["n_llm_calls"] = getattr(raudit, "n_llm_calls", 0)
                audit["fallback"] = getattr(raudit, "fallback", "") or ""
                audit["events"] = [
                    {
                        "checker": getattr(e, "checker", ""),
                        "action": getattr(e, "action", ""),
                        "note": (getattr(e, "note", "") or "")[:160],
                    }
                    for e in (getattr(raudit, "events", None) or [])
                ]
                sql_f = _compose(cur)
                _, err_f = exec_rows(db_path, sql_f, timeout_s=float(exec_timeout_s))
                audit["final_ok"] = err_f is None
                # Keep candidate even if still failing (official keep-in-pool).
                return cur, audit
    except Exception as e:
        audit["rounds"].append({"mode": "official_revise_fallback", "error": str(e)[:160]})

    # --- legacy fallback ---
    audit["mode"] = "legacy_exec_common"
    hint = f"{evidence_s}\nCurrent sub-step: {sub_question}"
    for round_i in range(max(1, checker_budget)):
        sql = _compose(cur)
        rows, err = exec_rows(db_path, sql, timeout_s=float(exec_timeout_s))
        if err is None:
            try:
                prompt = PF.format_common_checker_prompt(
                    schema[:8000],
                    q,
                    hint,
                    sql[:2500],
                    "Check JOIN/SELECT/NULL/ORDER issues for THIS sub-step only; keep one CTE/SQL.",
                )
                text = _llm(prompt, client=client, model=model, temperature=0.7)
                fixed = _extract_sql(text)
                if fixed:
                    new_cte = _sql_to_cte(
                        fixed,
                        step_name=_step_name(cur),
                        preceding_ctes=preceding_ctes,
                    )
                    if new_cte:
                        sql2 = _compose(new_cte)
                        rows2, err2 = exec_rows(
                            db_path, sql2, timeout_s=float(exec_timeout_s)
                        )
                        if err2 is None:
                            cur = new_cte
                            audit["rounds"].append({"round": round_i, "mode": "common_ok"})
                            audit["final_ok"] = True
                            return cur, audit
            except Exception as e:
                audit["rounds"].append(
                    {"round": round_i, "mode": "common_err", "error": str(e)[:120]}
                )
            audit["final_ok"] = True
            audit["rounds"].append({"round": round_i, "mode": "exec_ok_keep"})
            return cur, audit

        try:
            prompt = PF.format_execution_checker_prompt(
                schema[:8000],
                q,
                hint,
                sql[:2500],
                f"ERROR: {err}",
            )
            text = _llm(prompt, client=client, model=model, temperature=0.7)
            fixed = _extract_sql(text)
            if fixed:
                new_cte = _sql_to_cte(
                    fixed,
                    step_name=_step_name(cur),
                    preceding_ctes=preceding_ctes,
                )
                if new_cte:
                    cur = new_cte
                    audit["rounds"].append(
                        {"round": round_i, "mode": "exec_fix", "err": (err or "")[:120]}
                    )
                    continue
        except Exception as e:
            audit["rounds"].append(
                {"round": round_i, "mode": "exec_fix_err", "error": str(e)[:120]}
            )
            break
        audit["rounds"].append({"round": round_i, "mode": "exec_fail", "err": (err or "")[:120]})
        break

    sql = _compose(cur)
    _, err = exec_rows(db_path, sql, timeout_s=float(exec_timeout_s))
    audit["final_ok"] = err is None
    # Keep last CTE (do not drop from pool).
    return cur, audit


def deepeye_cte_full_plugin(
    *,
    db_path: Path,
    schema: str,
    question: str,
    evidence: str,
    sub_question: str,
    preceding_ctes: List[str],
    llm_config: Optional[dict] = None,
    few_shots: Optional[List[Dict[str, str]]] = None,
    value_retrieval_hint: str = "",
    n_dc: int = 4,
    n_skeleton: int = 4,
    n_icl: int = 4,
    filter_top_k: int = 2,
    evaluator_votes: int = 5,
    shortcut_threshold: float = 0.6,
    checker_budget: int = 3,
    max_workers: int = 12,
    revise_max_unique: int = 0,
    progress_prefix: str = "",
    strict_chain: bool = False,
    chain_mode: str = "soft",
    require_refs: bool = False,
    prior_exec_hint: str = "",
    prompt_preceding_ctes: Optional[List[str]] = None,
    sql_compose_fn: Optional[Callable[[str], str]] = None,
    cum_plan_prefix: bool = False,
    trim_cte: bool = False,
    parallel_mode: str = "",
    subq_as_gen: bool = False,
    hide_full_q: bool = False,
    linked_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """chain_mode: off | soft | strict

    soft (default): encourage true CTE chaining via prompt + prefer candidates that
    reference preceding CTEs / are not near-duplicates of prior steps.
    strict: stronger prompt (gen targets sub-question only); may hurt some qids.
    require_refs: if preceding CTEs exist and no candidate references them, abort
    this layer (selection_mode=no_refs_stop) instead of accepting a base-table restart.
    prior_exec_hint: optional text from prior-prefix exec summary / agent analysis.
    prompt_preceding_ctes: if set, only this list is shown in the LLM hint as
      "Preceding CTEs" (exec/compose still uses ``preceding_ctes`` unless
      ``sql_compose_fn`` is set). Pass ``[]`` when a CTE DAG table already
      carries full brick SQL (avoid duplicate dump).
    sql_compose_fn: optional ``cte_blob -> executable SQL``. When set (e.g.
      ``CteDAG.compose_candidate_sql``), replaces flat ``build_sql_from_chain``.
    cum_plan_prefix: gen target is the cumulative plan prefix (steps 1..k), not the
    full original question — matches models that prefer emitting a complete SQL.
    trim_cte: after gen, LLM-trim each unique CTE to current sub-step only (drop over-solve).
    parallel_mode: '' | 'leaf' | 'assemble' — parallel-slot planning.
      leaf: independent slot (no full question; gen=sub_question; ignore preceding).
      assemble: final combine from preceding CTEs answering the full question.
    subq_as_gen: force PromptFactory gen target = Current sub-step (even under soft).
      Intended for question-form plan steps (plan_mode=subquestion).
    hide_full_q: never put the original end-user question into gen/revise/BR prompts
      (only sub-question + evidence + schema + preceding CTEs).
    """
    t_step = time.time()
    client, model = _client_model(llm_config)
    PF = _load_deepeye_prompt_factory()
    # Align with fair_plugin / official DeepEye:
    #   PF HINT = evidence only; VR footer only when caller still passes a hint
    #   (official SchemaService profile already embeds value_examples).
    schema_profile = (schema or "").strip()
    evidence_s = (evidence or "").strip() or "None"
    vr = (value_retrieval_hint or "").strip()
    if vr:
        schema_s = (
            f"{schema_profile}\n\n-- Value retrieval (column values / keywords):\n{vr}"
            if schema_profile
            else f"-- Value retrieval:\n{vr}"
        )
    else:
        schema_s = schema_profile
    sub_q = (sub_question or question or "").strip()
    # What revise / BR see as "question" — hide when requested.
    q_for_aux = "" if hide_full_q else question
    prec_for_prompt = (
        list(preceding_ctes)
        if prompt_preceding_ctes is None
        else list(prompt_preceding_ctes)
    )
    if prec_for_prompt:
        preceding = "\n\n".join(prec_for_prompt)
    elif prompt_preceding_ctes is not None:
        preceding = (
            "(omitted here — reuse bricks from the CTE DAG table in "
            "PRIOR INTERMEDIATE EXEC below; reference StepK_k by name)"
        )
    else:
        preceding = "(none)"
    # Step context (NOT stuffed into PF HINT — official HINT is evidence-only).
    step_ctx = f"Current sub-step only: {sub_q}\nPreceding CTEs:\n{preceding}"
    if (prior_exec_hint or "").strip():
        from workflows.mcts_v4.actions.prior_exec_guidance import build_prior_exec_hint_block

        step_ctx = f"{step_ctx}{build_prior_exec_hint_block(prior_exec_hint)}"
    # Legacy combined hint string kept for hide_full_q replacements below.
    hint = f"{evidence_s}\n\n{step_ctx}"
    pref = progress_prefix or "[cte-plugin]"

    def _compose(cte_blob: str) -> str:
        """Executable SQL for a candidate CTE blob (DAG-aware when provided)."""
        if sql_compose_fn is not None:
            try:
                out = (sql_compose_fn(cte_blob) or "").strip()
                if out:
                    return out
            except Exception:
                pass
        return build_sql_from_chain(preceding_ctes, cte_blob)

    mode = (chain_mode or "soft").strip().lower()
    if strict_chain:
        mode = "strict"
    if mode not in ("off", "soft", "strict"):
        mode = "soft"

    chain_rules = ""
    gen_question = question
    pm = (parallel_mode or "").strip().lower()
    if pm == "leaf":
        # Independent parallel slot: only the slot text; no full-question leak.
        gen_question = sub_q
        chain_rules = (
            "\n\n# PARALLEL SLOT RULES (MANDATORY)\n"
            f"- Implement ONLY this independent slot:\n  {sub_q}\n"
            "- Build from base tables. Do NOT depend on other slots.\n"
            "- Do NOT invent ranking/LIMIT-1 final-answer logic unless this slot text asks for it.\n"
            "- Output ONE intermediate CTE: WITH slot_k AS ( ... ) SELECT * FROM slot_k;\n"
        )
        hint = (
            f"{evidence_s}\n\nIndependent parallel slot only:\n{sub_q}\n"
            f"(No preceding CTEs — this slot stands alone.)"
        )
        if (prior_exec_hint or "").strip():
            from workflows.mcts_v4.actions.prior_exec_guidance import build_prior_exec_hint_block

            hint = f"{hint}{build_prior_exec_hint_block(prior_exec_hint)}"
        hint = f"{hint}{chain_rules}"
    elif pm == "assemble":
        gen_question = question
        chain_rules = (
            "\n\n# PARALLEL ASSEMBLE RULES (MANDATORY)\n"
            "- Preceding CTEs are independent parallel slots already computed.\n"
            "- You MUST FROM/JOIN those CTE names to assemble the final answer.\n"
            "- Prefer combining slot CTEs over restarting from base tables when possible.\n"
            "- Output ONE final CTE that answers the original question.\n"
        )
        hint = (
            f"{evidence_s}\n\nAssemble the final answer using these parallel slot CTEs:\n{preceding}\n"
            f"Assemble instruction: {sub_q}"
        )
        if (prior_exec_hint or "").strip():
            from workflows.mcts_v4.actions.prior_exec_guidance import build_prior_exec_hint_block

            hint = f"{hint}{build_prior_exec_hint_block(prior_exec_hint)}"
        hint = f"{hint}{chain_rules}"
    elif cum_plan_prefix:
        # Align with DeepEye-like habit: emit a complete SQL for a growing subproblem.
        gen_question = sub_q
        hint = (
            f"{evidence_s}\n\n"
            f"# Plan PREFIX to solve now (cumulative — all listed steps together):\n{sub_q}\n\n"
            f"Preceding CTEs:\n{preceding}"
        )
        if (prior_exec_hint or "").strip():
            from workflows.mcts_v4.actions.prior_exec_guidance import build_prior_exec_hint_block

            hint = f"{hint}{build_prior_exec_hint_block(prior_exec_hint)}"
        chain_rules = (
            "\n\n# CUMULATIVE PREFIX RULES (important)\n"
            "- Produce ONE CTE whose result answers the plan PREFIX above "
            "(steps 1..k together), not only the last bullet.\n"
            "- Do NOT solve beyond this prefix / the full original question yet.\n"
            "- If Preceding CTEs exist, prefer FROM/JOIN their names and extend them; "
            "avoid ignoring them and restarting from base tables when possible.\n"
            "- Output one new CTE: WITH step_k AS ( ... ) SELECT * FROM step_k;\n"
        )
        if not hide_full_q:
            chain_rules += f"- Original full question (context only): {question}\n"
        hint = f"{hint}{chain_rules}"
    elif mode == "strict":
        # Intent: each DeepEye layer solves ONLY the sub-task (primary prompt target).
        gen_question = sub_q
        if preceding_ctes:
            chain_rules = (
                "\n\n# CTE CHAIN RULES (MANDATORY)\n"
                "- Implement ONLY the Current sub-step above. Do NOT re-answer the full original question.\n"
                "- You MUST reference preceding CTE name(s) in FROM/JOIN.\n"
                "- Do NOT recompute filters/joins already done in preceding CTEs.\n"
                "- Output one intermediate CTE for THIS step only "
                "(WITH step_k AS ( ... ) SELECT * FROM step_k;).\n"
                "- Do NOT emit the final answer columns unless the Current sub-step explicitly asks for them.\n"
            )
            if not hide_full_q:
                chain_rules += (
                    f"- Original question (context only, do not solve it now): {question}\n"
                )
        else:
            chain_rules = (
                "\n\n# SUB-STEP RULES (MANDATORY)\n"
                "- Implement ONLY the Current sub-step above as an intermediate result.\n"
                "- Do NOT re-answer / fully solve the original question in this step.\n"
                "- Prefer a narrow projection needed for THIS sub-step "
                "(e.g. filtered keys/rows), not the final answer.\n"
                "- Output one CTE: WITH step_0 AS ( ... ) SELECT * FROM step_0;\n"
            )
            if not hide_full_q:
                chain_rules += (
                    f"- Original question (context only, do not solve it now): {question}\n"
                )
        hint = f"{hint}{chain_rules}"
    elif preceding_ctes and mode == "soft":
        # Keep original question as gen target (PromptFactory), but steer chaining.
        # (Overridden below when subq_as_gen / trim_cte / hide_full_q.)
        chain_rules = (
            "\n\n# CTE CHAIN GUIDANCE (important)\n"
            "- Build on Preceding CTEs: prefer FROM/JOIN their CTE names.\n"
            "- Implement mainly the Current sub-step; avoid copying a full end-to-end solution\n"
            "  that ignores preceding CTEs and restarts from base tables.\n"
            "- Do not repeat the same filters/joins already computed in preceding CTEs.\n"
            "- Output one new CTE step (WITH ... AS (...)).\n"
        )
        hint = f"{hint}{chain_rules}"

    # subq_as_gen: put Current sub-step into PromptFactory gen_question even under soft.
    if (
        subq_as_gen
        and not cum_plan_prefix
        and pm not in ("leaf", "assemble")
        and not trim_cte
    ):
        gen_question = sub_q
        subq_gen_rules = (
            "\n\n# SUB-QUESTION AS GENERATION TARGET\n"
            f"- Answer THIS sub-question as SQL (intermediate CTE):\n  {sub_q}\n"
            "- Prefer a result that answers the sub-question only.\n"
            "- Do NOT invent later filters, ORDER BY/LIMIT ranking, or final-answer-only "
            "columns unless this sub-question text explicitly requires them.\n"
            "- If Preceding CTEs exist, prefer FROM/JOIN their names.\n"
            "- Output one CTE: WITH step_k AS ( ... ) SELECT * FROM step_k;\n"
        )
        if hide_full_q:
            subq_gen_rules += (
                "- You are NOT given the full end-user question; do not guess a larger task.\n"
            )
        else:
            subq_gen_rules += (
                f"- Original full question (context only): {question}\n"
            )
        chain_rules = (chain_rules or "") + subq_gen_rules
        hint = f"{hint}{subq_gen_rules}"

    # hide_full_q alone (without trim): force gen=sub_q and strip any full-Q leaks.
    if hide_full_q and not cum_plan_prefix and pm not in ("leaf", "assemble"):
        gen_question = sub_q
        if chain_rules:
            chain_rules = re.sub(
                r"(?m)^- Original (?:full )?question \(context only[^)]*\): .*\n?",
                "",
                chain_rules,
            )
            chain_rules = re.sub(
                r"(?m)^- Original question \(context only[^)]*\): .*\n?",
                "",
                chain_rules,
            )
        if question and question.strip() and question.strip() in hint:
            hint = hint.replace(question.strip(), "[hidden full question]")
        hide_rules = (
            "\n\n# NO FULL QUESTION (MANDATORY)\n"
            "- The full end-user question is hidden. Solve ONLY the Current sub-step.\n"
            "- Do not invent phone/email/LIMIT-1 final answers unless the sub-step asks for them.\n"
        )
        chain_rules = (chain_rules or "") + hide_rules
        hint = f"{hint}{hide_rules}"

    # When trim_cte is on: generation itself must target ONLY the current sub-step
    # (not the full original question), then trim agent further strips leftovers.
    # Critically: do NOT leak the full original question into the prompt — models
    # treat "context only" as a soft invitation to over-solve end-to-end.
    # Skip when parallel_mode already set leaf/assemble prompts.
    if trim_cte and not cum_plan_prefix and pm not in ("leaf", "assemble"):
        gen_question = sub_q
        if chain_rules:
            chain_rules = re.sub(
                r"(?m)^- Original (?:full )?question \(context only[^)]*\): .*\n?",
                "",
                chain_rules,
            )
        only_step_rules = (
            "\n\n# ONLY-THIS-STEP GENERATION (MANDATORY)\n"
            f"- Your ENTIRE job is to implement ONLY this sub-step:\n  {sub_q}\n"
            "- Do NOT generate anything beyond this one sub-step.\n"
            "- Do NOT invent later filters, ORDER BY/LIMIT ranking, or final-answer-only columns "
            "unless this sub-step text explicitly requires them.\n"
            "- Output ONE intermediate CTE for this sub-step only "
            "(WITH step_k AS ( ... ) SELECT * FROM step_k;).\n"
            "- You are NOT given the full end-user question; ignore any urge to guess it.\n"
        )
        chain_rules = (chain_rules or "") + only_step_rules
        hint = f"{hint}{only_step_rules}"

    # Hard dependency: this step is marked depends_on prior result tables.
    if require_refs and preceding_ctes and pm not in ("leaf",):
        must_ref = (
            "\n\n# MUST REUSE PRECEDING CTEs (MANDATORY)\n"
            "- This sub-step DEPENDS on preceding CTE result(s).\n"
            "- You MUST FROM/JOIN at least one preceding CTE name in the new CTE body.\n"
            "- Do NOT restart the whole query from base tables while ignoring preceding CTEs.\n"
        )
        chain_rules = (chain_rules or "") + must_ref
        hint = f"{hint}{must_ref}"

    # --- PromptFactory gen (aligned with fullsql fair) ---
    # PF HINT = evidence only; step/chain/VR already handled via schema footer + postfix.
    if (hint or "").startswith(evidence_s):
        step_postfix = (hint[len(evidence_s) :] or "").lstrip("\n")
    else:
        step_postfix = (hint or "").strip()
    # chain_rules already folded into hint in the branches above — do not append twice.
    max_prompt_chars = _max_prompt_chars(llm_config)
    strip_levels: Dict[str, int] = {}
    gen_temperature = 0.7

    def _with_step_ctx(base_prompt: str) -> str:
        if not step_postfix and not chain_rules:
            return base_prompt
        # Prefer step_postfix (already includes chain_rules when branches appended).
        extra = step_postfix if step_postfix else chain_rules
        return f"{base_prompt}\n\n# STEP / CHAIN CONTEXT\n{extra}"

    def _fmt_dc(sch: str) -> str:
        return _with_step_ctx(
            PF.format_dc_sql_generation_prompt(sch, gen_question, evidence_s)
        )

    def _fmt_sk(sch: str) -> str:
        return _with_step_ctx(
            PF.format_skeleton_sql_generation_prompt(sch, gen_question, evidence_s)
        )

    def _fmt_icl(sch: str) -> str:
        return _with_step_ctx(
            PF.format_icl_sql_generation_prompt(few_shots, sch, gen_question, evidence_s)
        )

    fit_schema = schema_profile if linked_schema else schema_s
    _fit_kw = dict(
        max_prompt_chars=max_prompt_chars,
        linked_schema=linked_schema,
        encoding_model_name=model,
    )
    dc_prompt, strip_levels["dc"] = _fit_prompt_with_schema(
        _fmt_dc, fit_schema, **_fit_kw
    )
    sk_prompt, strip_levels["skeleton"] = _fit_prompt_with_schema(
        _fmt_sk, fit_schema, **_fit_kw
    )
    icl_prompt = None
    if n_icl > 0 and few_shots:
        icl_prompt, strip_levels["icl"] = _fit_prompt_with_schema(
            _fmt_icl, fit_schema, **_fit_kw
        )

    arm_jobs: List[Tuple[str, str, int]] = []
    if n_dc > 0:
        arm_jobs.append(("dc", dc_prompt, int(n_dc)))
    if n_skeleton > 0:
        arm_jobs.append(("skeleton", sk_prompt, int(n_skeleton)))
    if icl_prompt and n_icl > 0:
        arm_jobs.append(("icl", icl_prompt, int(n_icl)))

    n_gen_calls = sum(n for _, _, n in arm_jobs)
    print(
        f"  {pref} gen start chain_mode={mode} arms={len(arm_jobs)} "
        f"n_samples={n_gen_calls} temp={gen_temperature} "
        f"strip={strip_levels} workers={min(max_workers, max(1, len(arm_jobs)))}",
        flush=True,
    )
    raw_items: List[Dict[str, Any]] = []

    def _gen_arm(kind: str, prompt: str, n_samp: int) -> List[Dict[str, Any]]:
        outs: List[Dict[str, Any]] = []
        try:
            texts = _llm_n(
                prompt,
                client=client,
                model=model,
                temperature=gen_temperature,
                n=max(1, int(n_samp)),
            )
        except Exception as e:
            return [
                {
                    "kind": kind,
                    "temp": gen_temperature,
                    "sql": "",
                    "cte": "",
                    "ok": False,
                    "error": str(e)[:200],
                }
            ]
        for text in texts:
            try:
                sql = _extract_sql(text)
                cte = _sql_to_cte(
                    sql,
                    step_name=f"{kind}_step",
                    preceding_ctes=list(preceding_ctes or []),
                )
                outs.append(
                    {
                        "kind": kind,
                        "temp": gen_temperature,
                        "sql": sql,
                        "cte": cte,
                        "ok": bool(cte),
                    }
                )
            except Exception as e:
                outs.append(
                    {
                        "kind": kind,
                        "temp": gen_temperature,
                        "sql": "",
                        "cte": "",
                        "ok": False,
                        "error": str(e)[:200],
                    }
                )
        return outs

    if arm_jobs:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(arm_jobs))) as ex:
            futs = [ex.submit(_gen_arm, k, p, n) for k, p, n in arm_jobs]
            done_n = 0
            for fut in as_completed(futs):
                raw_items.extend(fut.result())
                done_n += 1
                if done_n == 1 or done_n == len(arm_jobs):
                    print(
                        f"  {pref} gen progress arms {done_n}/{len(arm_jobs)} "
                        f"cands={len(raw_items)}",
                        flush=True,
                    )

    n_raw_ok = sum(1 for x in raw_items if x.get("ok"))
    n_raw_err = sum(1 for x in raw_items if x.get("error"))
    err_sample = next((x.get("error") for x in raw_items if x.get("error")), None)
    print(
        f"  {pref} gen done ok={n_raw_ok}/{len(raw_items)} err={n_raw_err}"
        + (f" sample_err={str(err_sample)[:120]}" if err_sample else "")
        + f" ({time.time() - t_step:.1f}s)",
        flush=True,
    )

    # revise each unique CTE
    revised: List[Dict[str, Any]] = []
    seen = set()
    uniq = []
    for it in raw_items:
        cte = (it.get("cte") or "").strip()
        if not cte:
            continue
        key = re.sub(r"\s+", " ", cte.lower())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    # Soft/strict: drop near-duplicate restarts of prior steps when alternatives exist.
    prev_names = _cte_names(preceding_ctes)
    chain_filter_audit: Dict[str, Any] = {"mode": mode, "n_before": len(uniq)}
    if mode in ("soft", "strict") and preceding_ctes and uniq:
        non_dup = [
            it
            for it in uniq
            if not _is_near_duplicate_of_preceding(it.get("cte") or "", preceding_ctes)
        ]
        if non_dup:
            uniq = non_dup
        if mode == "strict":
            with_ref = [
                it
                for it in uniq
                if _references_preceding(it.get("cte") or "", prev_names)
            ]
            if with_ref:
                uniq = with_ref
        elif mode == "soft":
            # Prefer revise order: refs first (keeps non-refs as fallback).
            uniq = sorted(
                uniq,
                key=lambda it: (
                    0 if _references_preceding(it.get("cte") or "", prev_names) else 1,
                    re.sub(r"\s+", " ", (it.get("cte") or "").lower()),
                ),
            )
        chain_filter_audit["n_after_dup_ref"] = len(uniq)
        chain_filter_audit["n_refs"] = sum(
            1
            for it in uniq
            if _references_preceding(it.get("cte") or "", prev_names)
        )

    if revise_max_unique and revise_max_unique > 0 and len(uniq) > revise_max_unique:
        scored = []
        for it in uniq:
            sql = _compose(it["cte"])
            rows, err = exec_rows(db_path, sql)
            # Prefer exec-ok, then refs_prev when chaining.
            refs = (
                1
                if (mode in ("soft", "strict") and preceding_ctes
                    and _references_preceding(it.get("cte") or "", prev_names))
                else 0
            )
            scored.append((0 if err is None else 1, 0 if refs else 1, it))
        scored.sort(key=lambda x: (x[0], x[1]))
        uniq = [it for _, _, it in scored[:revise_max_unique]]

    # Optional: trim over-solving CTEs down to the current sub-step only.
    trim_audit: Dict[str, Any] = {"enabled": bool(trim_cte), "n_in": 0, "n_changed": 0, "n_ok": 0}
    if trim_cte and uniq:
        from workflows.mcts_v4.actions.cte_trim_agent import trim_cte_to_substep

        trim_audit["n_in"] = len(uniq)
        print(
            f"  {pref} trim-cte start n={len(uniq)} workers={min(max_workers, len(uniq))}",
            flush=True,
        )

        def _trim_one(it: Dict[str, Any]) -> Dict[str, Any]:
            out = trim_cte_to_substep(
                cte=it.get("cte") or "",
                sub_question=sub_q,
                question=(q_for_aux or sub_q),
                preceding_ctes=preceding_ctes,
                client=client,
                model=model,
                sql_to_cte_fn=_sql_to_cte,
            )
            new_it = dict(it)
            if out.get("ok") and out.get("cte"):
                new_it["cte"] = out["cte"]
                new_it["sql"] = out["cte"]
            new_it["trim"] = out.get("audit")
            new_it["_trim_changed"] = bool(out.get("changed"))
            new_it["_trim_ok"] = bool(out.get("ok"))
            return new_it

        trimmed: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(uniq)))) as ex:
            futs = [ex.submit(_trim_one, it) for it in uniq]
            for fut in as_completed(futs):
                trimmed.append(fut.result())
        seen2: set = set()
        uniq2: List[Dict[str, Any]] = []
        for it in trimmed:
            cte = (it.get("cte") or "").strip()
            if not cte:
                continue
            key = re.sub(r"\s+", " ", cte.lower())
            if key in seen2:
                continue
            seen2.add(key)
            uniq2.append(it)
            if it.get("_trim_ok"):
                trim_audit["n_ok"] += 1
            if it.get("_trim_changed"):
                trim_audit["n_changed"] += 1
        uniq = uniq2
        trim_audit["n_after"] = len(uniq)
        print(
            f"  {pref} trim-cte done changed={trim_audit['n_changed']}/{trim_audit['n_in']} "
            f"uniq={len(uniq)} ({time.time() - t_step:.1f}s)",
            flush=True,
        )

    print(f"  {pref} revise start unique={len(uniq)} workers={min(max_workers, max(1, len(uniq)))}", flush=True)

    def _revise_one(it: Dict[str, Any]) -> Dict[str, Any]:
        cte = (it.get("cte") or "").strip()
        revise_extra = ""
        # Keep step postfix out of official schema strip; fold into evidence if needed.
        fixed, raudit = _revise_candidate(
            cte=cte,
            preceding_ctes=preceding_ctes,
            db_path=db_path,
            schema=schema_s,
            question=(q_for_aux or sub_q),
            evidence=evidence_s,
            sub_question=sub_q,
            client=client,
            model=model,
            PF=PF,
            checker_budget=checker_budget,
            sql_compose_fn=sql_compose_fn,
            linked_schema=linked_schema,
            extra_evidence=revise_extra,
            max_model_len=_llm_max_model_len(llm_config),
        )
        out = dict(it)
        if not fixed:
            out["exec_ok"] = False
            out["revise"] = raudit
            return out
        out["cte"] = fixed
        out["exec_ok"] = True
        out["revise"] = raudit
        return out

    if uniq:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(uniq))) as ex:
            futs = [ex.submit(_revise_one, it) for it in uniq]
            done_n = 0
            for fut in as_completed(futs):
                it = fut.result()
                done_n += 1
                if it.get("exec_ok"):
                    revised.append(it)
                if done_n == 1 or done_n == len(uniq) or done_n % 3 == 0:
                    print(
                        f"  {pref} revise progress {done_n}/{len(uniq)} ok={len(revised)}",
                        flush=True,
                    )

    clusters_map: Dict[str, Dict[str, Any]] = {}
    for it in revised:
        cte = (it.get("cte") or "").strip()
        sql = _compose(cte)
        rows, err = exec_rows(db_path, sql)
        if err is not None:
            continue
        sig = result_sig(rows)
        if sig not in clusters_map:
            clusters_map[sig] = {
                "sig": sig,
                "cte": cte,
                "sql": sql,
                "size": 0,
                "sources": [],
                "rows_preview": format_result_preview(rows, None),
                "refs_prev": bool(
                    preceding_ctes and _references_preceding(cte, prev_names)
                ),
                "near_dup": bool(
                    preceding_ctes
                    and _is_near_duplicate_of_preceding(cte, preceding_ctes)
                ),
            }
        clusters_map[sig]["size"] += 1
        clusters_map[sig]["sources"].append(it.get("kind") or "?")

    raw_clusters = list(clusters_map.values())
    n_exec = sum(c["size"] for c in raw_clusters) or 1
    for c in raw_clusters:
        c["consistency"] = c["size"] / n_exec

    # Soft/strict select ranking: prefer refs_prev, downrank near_dup, then consistency.
    selection_mode = "empty"
    winner = None
    br_audit: List[dict] = []
    if mode in ("soft", "strict") and preceding_ctes and raw_clusters:
        non_dup = [c for c in raw_clusters if not c.get("near_dup")]
        pool = non_dup if non_dup else raw_clusters
        if mode == "strict":
            with_ref = [c for c in pool if c.get("refs_prev")]
            if with_ref:
                pool = with_ref
            else:
                # Hard require under strict: no reuse of priors → abort this layer.
                pool = []
        # require_refs: prefer refs; if none reference priors, keep pool but mark
        # unsatisfied (caller may continue with best no-ref instead of freezing).
        if (
            require_refs
            and pool
            and not any(c.get("refs_prev") for c in pool)
        ):
            chain_filter_audit["require_refs_unsatisfied"] = True
        clusters = sorted(
            pool,
            key=lambda c: (
                -int(bool(c.get("refs_prev"))),
                int(bool(c.get("near_dup"))),
                -float(c.get("consistency") or 0),
                c["sig"],
            ),
        )
        chain_filter_audit["n_clusters_raw"] = len(raw_clusters)
        chain_filter_audit["n_clusters_pool"] = len(clusters)
        chain_filter_audit["n_pool_refs"] = sum(1 for c in clusters if c.get("refs_prev"))
        chain_filter_audit["require_refs"] = bool(require_refs)
        if not clusters:
            selection_mode = "no_refs_stop"
        elif chain_filter_audit.get("require_refs_unsatisfied"):
            # Keep going with best non-ref; do not abort the layer.
            selection_mode = ""  # filled below by normal BR/shortcut path
            chain_filter_audit["selection_note"] = "no_refs_fallback"
    else:
        clusters = sorted(raw_clusters, key=lambda c: (-c["size"], c["sig"]))

    if not clusters:
        if selection_mode != "no_refs_stop":
            selection_mode = "empty"
    elif len(clusters) == 1:
        winner = clusters[0]
        selection_mode = "only_one"
    else:
        top = clusters[: max(1, filter_top_k)]
        # Soft: if top-1 refs_prev and consistency close, allow chain preference over
        # pure consistency shortcut when top-1 is a near_dup restart.
        if top[0]["consistency"] >= shortcut_threshold:
            winner = top[0]
            selection_mode = "shortcut_consistency"
            # Soft override: if shortcut winner is near_dup but another top refs_prev.
            if (
                mode == "soft"
                and preceding_ctes
                and top[0].get("near_dup")
            ):
                alt = next(
                    (
                        c
                        for c in clusters
                        if c.get("refs_prev") and not c.get("near_dup")
                    ),
                    None,
                )
                if alt is not None:
                    winner = alt
                    selection_mode = "soft_prefer_chain_over_dup"
        elif len(top) == 1:
            winner = top[0]
            selection_mode = "top1_only"
        else:
            a, b = top[0], top[1]
            wins = {a["sig"]: 0.0, b["sig"]: 0.0}
            votes = []

            def _br_vote(vi: int) -> str:
                try:
                    text = _llm(
                        PROMPT_BR.format(
                            schema=schema_s,
                            question=(q_for_aux or "(hidden — use Current Sub-question only)"),
                            evidence=evidence_s,
                            sub_question=sub_q,
                            preceding=preceding,
                            query_a=a["cte"][:1800],
                            result_a=a.get("rows_preview") or "",
                            query_b=b["cte"][:1800],
                            result_b=b.get("rows_preview") or "",
                        ),
                        client=client,
                        model=model,
                        temperature=0.2 + 0.1 * (vi % 3),
                    )
                    return _parse_ab(text)
                except Exception:
                    return "A"

            n_votes = max(1, evaluator_votes)
            with ThreadPoolExecutor(max_workers=min(max_workers, n_votes)) as ex:
                futs = [ex.submit(_br_vote, vi) for vi in range(n_votes)]
                for fut in as_completed(futs):
                    choice = fut.result()
                    votes.append(choice)
                    wins[a["sig"] if choice == "A" else b["sig"]] += 1.0
            # Soft tie-break: prefer refs_prev / non-dup when BR ties.
            if wins[a["sig"]] == wins[b["sig"]] and mode in ("soft", "strict"):
                def _chain_key(c: Dict[str, Any]) -> Tuple[int, int]:
                    return (int(bool(c.get("refs_prev"))), 0 if c.get("near_dup") else 1)

                winner = a if _chain_key(a) >= _chain_key(b) else b
                selection_mode = "br_pairwise_chain_tiebreak"
            else:
                winner = a if wins[a["sig"]] >= wins[b["sig"]] else b
                selection_mode = "br_pairwise"
            br_audit.append({"votes": votes})

    elapsed = time.time() - t_step
    if chain_filter_audit.get("require_refs_unsatisfied") and winner:
        if not winner.get("refs_prev"):
            selection_mode = f"{selection_mode}+no_refs_fallback" if selection_mode else "no_refs_fallback"
    print(
        f"  {pref} select mode={selection_mode} clusters={len(clusters)} "
        f"revised={len(revised)} picked={bool(winner)} cons="
        f"{(round(float(winner['consistency']), 3) if winner else 0)} "
        f"({elapsed:.1f}s)",
        flush=True,
    )

    return {
        "clusters": [
            {
                "sig": c["sig"][:120],
                "cte": c["cte"],
                "size": c["size"],
                "consistency": round(float(c["consistency"]), 4),
                "sources": c.get("sources") or [],
                "rows_preview": (c.get("rows_preview") or "")[:300],
                "refs_prev": bool(c.get("refs_prev")),
                "near_dup": bool(c.get("near_dup")),
            }
            for c in clusters
        ],
        "winner_cte": (winner["cte"] if winner else ""),
        "winner_sig": (winner["sig"][:120] if winner else ""),
        "winner_consistency": (round(float(winner["consistency"]), 4) if winner else 0.0),
        "audit": {
            "mode": "deepeye_cte_full_plugin_v2",
            "chain_mode": mode,
            "cum_plan_prefix": bool(cum_plan_prefix),
            "parallel_mode": pm or "",
            "strict_chain": bool(strict_chain or mode == "strict"),
            "n_dc": n_dc,
            "n_skeleton": n_skeleton,
            "n_icl": n_icl if icl_prompt else 0,
            "n_few_shots": len(few_shots or []),
            "n_raw_ok": n_raw_ok,
            "n_raw_err": n_raw_err,
            "n_unique": len(uniq),
            "n_revised_ok": len(revised),
            "n_clusters": len(clusters),
            "n_gen_jobs": n_gen_calls,
            "schema_strip_levels": strip_levels,
            "gen_temperature": gen_temperature,
            "selection_mode": selection_mode,
            "prior_exec_hint_len": len((prior_exec_hint or "").strip()),
            "trim_cte": bool(trim_cte),
            "subq_as_gen": bool(subq_as_gen),
            "hide_full_q": bool(hide_full_q),
            "gen_question_preview": (gen_question or "")[:160],
            "trim_audit": trim_audit,
            "filter_top_k": filter_top_k,
            "evaluator_votes": evaluator_votes,
            "shortcut_threshold": shortcut_threshold,
            "checker_budget": checker_budget,
            "revise_max_unique": revise_max_unique,
            "br_pairs": br_audit,
            "chain_filter": chain_filter_audit,
            "winner_refs_prev": bool(winner.get("refs_prev")) if winner else False,
            "winner_near_dup": bool(winner.get("near_dup")) if winner else False,
            "has_value_retrieval_hint": bool(value_retrieval_hint),
            "elapsed_s": round(elapsed, 2),
            "sample_gen_error": (str(err_sample)[:200] if err_sample else None),
        },
    }
