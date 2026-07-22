#!/usr/bin/env python3
"""DeepEye-aligned FULL CTE plugin v2 (step-level).

Modules (aligned with official pipeline, CTE grain):
  1) Schema: reused linked schema (+ value-retrieval examples already in snapshot)
  2) Gen: PromptFactory DC×4 + Skeleton×4 + ICL×4
  3) Revise: execution_checker + common_checker (PromptFactory; official uses 8 specialized checkers)
  4) Select: consistency shortcut (≥0.6) else BR×5 on top-2
"""

from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _fix_revise_multicte() -> bool:
    """Bugfix #4: multi-CTE-aware _sql_to_cte (default OFF for ablation)."""
    return _env_flag("MCTS_BUGFIX_REVISE_MULTICTE", "0")


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
    checker_budget: int = 2,
) -> Tuple[str, Dict[str, Any]]:
    """Execution + common checker revision (PromptFactory)."""
    audit = {"rounds": [], "final_ok": False}
    cur = cte
    hint = f"{evidence or 'None'}\nCurrent sub-step: {sub_question}"

    for round_i in range(max(1, checker_budget)):
        sql = build_sql_from_chain(preceding_ctes, cur)
        rows, err = exec_rows(db_path, sql)
        if err is None:
            # common checker polish
            try:
                prompt = PF.format_common_checker_prompt(
                    schema[:8000],
                    question,
                    hint,
                    sql[:2500],
                    "Check JOIN/SELECT/NULL/ORDER issues for THIS sub-step only; keep one CTE/SQL.",
                )
                text = _llm(prompt, client=client, model=model, temperature=0.3)
                fixed = _extract_sql(text)
                if fixed:
                    new_cte = _sql_to_cte(
                        fixed, step_name="revised", preceding_ctes=preceding_ctes
                    )
                    if new_cte:
                        sql2 = build_sql_from_chain(preceding_ctes, new_cte)
                        rows2, err2 = exec_rows(db_path, sql2)
                        if err2 is None:
                            cur = new_cte
                            audit["rounds"].append({"round": round_i, "mode": "common_ok"})
                            audit["final_ok"] = True
                            return cur, audit
            except Exception as e:
                audit["rounds"].append({"round": round_i, "mode": "common_err", "error": str(e)[:120]})
            audit["final_ok"] = True
            audit["rounds"].append({"round": round_i, "mode": "exec_ok_keep"})
            return cur, audit

        # execution checker fix
        try:
            prompt = PF.format_execution_checker_prompt(
                schema[:8000],
                question,
                hint,
                sql[:2500],
                f"ERROR: {err}",
            )
            text = _llm(prompt, client=client, model=model, temperature=0.4)
            fixed = _extract_sql(text)
            if fixed:
                new_cte = _sql_to_cte(
                    fixed, step_name="fixed", preceding_ctes=preceding_ctes
                )
                if new_cte:
                    cur = new_cte
                    audit["rounds"].append({"round": round_i, "mode": "exec_fix", "err": (err or "")[:120]})
                    continue
        except Exception as e:
            audit["rounds"].append({"round": round_i, "mode": "exec_fix_err", "error": str(e)[:120]})
            break
        audit["rounds"].append({"round": round_i, "mode": "exec_fail", "err": (err or "")[:120]})
        break

    # last check
    sql = build_sql_from_chain(preceding_ctes, cur)
    _, err = exec_rows(db_path, sql)
    audit["final_ok"] = err is None
    return (cur if err is None else ""), audit


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
    checker_budget: int = 2,
    max_workers: int = 12,
    revise_max_unique: int = 0,
    progress_prefix: str = "",
    strict_chain: bool = False,
    chain_mode: str = "soft",
    require_refs: bool = False,
    prior_exec_hint: str = "",
    cum_plan_prefix: bool = False,
    trim_cte: bool = False,
    parallel_mode: str = "",
) -> Dict[str, Any]:
    """chain_mode: off | soft | strict

    soft (default): encourage true CTE chaining via prompt + prefer candidates that
    reference preceding CTEs / are not near-duplicates of prior steps.
    strict: stronger prompt (gen targets sub-question only); may hurt some qids.
    require_refs: if preceding CTEs exist and no candidate references them, abort
    this layer (selection_mode=no_refs_stop) instead of accepting a base-table restart.
    prior_exec_hint: optional text from prior-prefix exec summary / agent analysis.
    cum_plan_prefix: gen target is the cumulative plan prefix (steps 1..k), not the
    full original question — matches models that prefer emitting a complete SQL.
    trim_cte: after gen, LLM-trim each unique CTE to current sub-step only (drop over-solve).
    parallel_mode: '' | 'leaf' | 'assemble' — parallel-slot planning.
      leaf: independent slot (no full question; gen=sub_question; ignore preceding).
      assemble: final combine from preceding CTEs answering the full question.
    """
    t_step = time.time()
    client, model = _client_model(llm_config)
    PF = _load_deepeye_prompt_factory()
    schema_s = (schema or "")[:12000]
    evidence_s = evidence or "None"
    if value_retrieval_hint:
        evidence_s = f"{evidence_s}\n{value_retrieval_hint}"
    sub_q = (sub_question or question or "").strip()
    preceding = "\n\n".join(preceding_ctes) if preceding_ctes else "(none)"
    hint = f"{evidence_s}\n\nCurrent sub-step only: {sub_q}\nPreceding CTEs:\n{preceding}"
    if (prior_exec_hint or "").strip():
        from workflows.mcts_v4.actions.prior_exec_guidance import build_prior_exec_hint_block

        hint = f"{hint}{build_prior_exec_hint_block(prior_exec_hint)}"
    pref = progress_prefix or "[cte-plugin]"

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
            f"- Original full question (context only): {question}\n"
        )
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
                f"- Original question (context only, do not solve it now): {question}\n"
            )
        hint = f"{hint}{chain_rules}"
    elif preceding_ctes and mode == "soft":
        # Keep original question as gen target (PromptFactory), but steer chaining.
        chain_rules = (
            "\n\n# CTE CHAIN GUIDANCE (important)\n"
            "- Build on Preceding CTEs: prefer FROM/JOIN their CTE names.\n"
            "- Implement mainly the Current sub-step; avoid copying a full end-to-end solution\n"
            "  that ignores preceding CTEs and restarts from base tables.\n"
            "- Do not repeat the same filters/joins already computed in preceding CTEs.\n"
            "- Output one new CTE step (WITH ... AS (...)).\n"
        )
        hint = f"{hint}{chain_rules}"

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

    dc_prompt = PF.format_dc_sql_generation_prompt(schema_s, gen_question, hint) + chain_rules
    sk_prompt = PF.format_skeleton_sql_generation_prompt(schema_s, gen_question, hint) + chain_rules
    icl_prompt = None
    if n_icl > 0 and few_shots:
        icl_prompt = PF.format_icl_sql_generation_prompt(few_shots, schema_s, gen_question, hint) + chain_rules

    jobs: List[Tuple[str, str, float]] = []
    for i in range(max(0, n_dc)):
        jobs.append(("dc", dc_prompt, 0.2 + 0.2 * (i % 4)))
    for i in range(max(0, n_skeleton)):
        jobs.append(("skeleton", sk_prompt, 0.3 + 0.2 * (i % 4)))
    if icl_prompt:
        for i in range(max(0, n_icl)):
            jobs.append(("icl", icl_prompt, 0.25 + 0.2 * (i % 4)))

    print(
        f"  {pref} gen start chain_mode={mode} n_jobs={len(jobs)} "
        f"workers={min(max_workers, max(1, len(jobs)))}",
        flush=True,
    )
    raw_items: List[Dict[str, Any]] = []

    def _gen(kind: str, prompt: str, temp: float) -> Dict[str, Any]:
        try:
            text = _llm(prompt, client=client, model=model, temperature=temp)
            sql = _extract_sql(text)
            cte = _sql_to_cte(sql, step_name=f"{kind}_step")
            return {"kind": kind, "temp": temp, "sql": sql, "cte": cte, "ok": bool(cte)}
        except Exception as e:
            return {"kind": kind, "temp": temp, "sql": "", "cte": "", "ok": False, "error": str(e)}

    if jobs:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as ex:
            futs = [ex.submit(_gen, k, p, t) for k, p, t in jobs]
            done_n = 0
            for fut in as_completed(futs):
                raw_items.append(fut.result())
                done_n += 1
                if done_n == 1 or done_n == len(jobs) or done_n % 4 == 0:
                    print(f"  {pref} gen progress {done_n}/{len(jobs)}", flush=True)

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
            sql = build_sql_from_chain(preceding_ctes, it["cte"])
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
                question=question,
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
        fixed, raudit = _revise_candidate(
            cte=cte,
            preceding_ctes=preceding_ctes,
            db_path=db_path,
            schema=schema_s,
            question=question,
            evidence=evidence_s,
            sub_question=sub_q,
            client=client,
            model=model,
            PF=PF,
            checker_budget=checker_budget,
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
        sql = build_sql_from_chain(preceding_ctes, cte)
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
        # require_refs: also abort soft when nothing references priors.
        if (
            require_refs
            and pool
            and not any(c.get("refs_prev") for c in pool)
        ):
            pool = []
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
                            schema=schema_s[:6000],
                            question=question,
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
            "n_gen_jobs": len(jobs),
            "selection_mode": selection_mode,
            "prior_exec_hint_len": len((prior_exec_hint or "").strip()),
            "trim_cte": bool(trim_cte),
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
