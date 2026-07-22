#!/usr/bin/env python3
"""P2: DeepEye FULL CTE plugin v2 — ICL + revision + reused linking/value-retrieval.

Model: Qwen3-Coder-30B-A3B (aligned with official DeepEye contested14).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Alpha-SQL-2.2.4"))

os.environ.setdefault(
    "DB_ROOT_DIR",
    "/hpc2hdd/home/sshen190/wtao565/datasets/dev_20240627/dev_databases",
)

from workflows.mcts_v4.actions.cte_layer_react import react_select_cte_cluster
from workflows.mcts_v4.actions.deepeye_cte_full_plugin import deepeye_cte_full_plugin
from workflows.mcts_v4.actions.deepeye_cte_plugin import (
    build_sql_from_chain,
    exec_rows,
    format_result_preview,
    result_sig,
)
from workflows.mcts_v4.actions.deepeye_snapshot_context import (
    filter_context_for_step,
    load_deepeye_context,
)
from workflows.mcts_v4.actions.plan_business_rewrite import (
    pick_final_among_prefixes,
    plan_business_oneshot,
    plan_business_vote,
    plan_business_vote_plugin,
    plan_from_circle_think,
    replan_from_rollout,
    replan_future_remaining,
    rewrite_business_plan,
    synthesize_sql_from_cte_chain,
)
from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils


def load_preset(name: str) -> dict:
    cfg = yaml.safe_load(
        (ROOT / "workflows/interactive_question/LLM/config.yaml").read_text(encoding="utf-8")
    )["llm_presets"][name]
    os.environ["VLLM_API_URL"] = cfg["base_url"]
    os.environ["VLLM_API_KEY"] = cfg["api_key"]
    os.environ["VLLM_MODEL"] = cfg["model"]
    if cfg.get("enable_thinking") is False:
        os.environ["VLLM_ENABLE_THINKING"] = "0"
    return cfg


def db_path(db_id: str) -> Path:
    return Path(os.environ["DB_ROOT_DIR"]) / db_id / f"{db_id}.sqlite"


def official_ex_hit(db_id: str, pred: str, gold: str) -> bool:
    pr, pe = exec_rows(db_path(db_id), pred)
    gr, ge = exec_rows(db_path(db_id), gold)
    if pe or ge or pr is None or gr is None:
        return False
    return set(pr) == set(gr)


def _pick_r4r8_sql(
    *,
    db_id: str,
    sqls: List[str],
    question: str,
    schema: str,
    llm_config: dict,
) -> Tuple[str, dict]:
    """Cluster SQLs by official-EX result, then gated R4 → R8 select."""
    from workflows.mcts_v4.test.test_mcts import build_db_connector
    from workflows.mcts_v4.utils.gated_selection import gated_r4_r8_select

    seen: set = set()
    deduped: List[str] = []
    for s in sqls:
        s = (s or "").strip()
        if s and s not in seen:
            seen.add(s)
            deduped.append(s)
    if not deduped:
        return "", {"ok": False, "reason": "empty", "pick_mode": "r4r8"}

    buckets: Counter = Counter()
    variants: List[dict] = []
    for sql in deduped:
        rows, err = exec_rows(db_path(db_id), sql)
        if err is not None or rows is None:
            key = f"invalid_{err or 'error'}"
            valid = False
            nrows = 0
        else:
            key = MCTSUtils.create_official_ex_cluster_key(rows)
            valid = True
            nrows = len(rows)
        buckets[key] += 1
        variants.append(
            {
                "sql": sql,
                "valid": valid,
                "result_signature": key,
                "result_signature_v2": key,
                "result_row_count": nrows,
            }
        )
    sel = next((v["sql"] for v in variants if v.get("valid")), variants[0]["sql"])
    rss = [
        {
            "rollout_id": 1,
            "reward": 0.0,
            "selected_sql": sel,
            "all_sql_variants": variants,
            "result_buckets": dict(buckets),
            "sql_bucket_count": sum(buckets.values()),
        }
    ]
    db = build_db_connector(db_id)
    picked, meta = gated_r4_r8_select(
        rss,
        question=question,
        schema_ddl=schema,
        db_connector=db,
        llm_config=llm_config,
    )
    out = {
        **(meta or {}),
        "ok": bool((picked or "").strip()),
        "n_variants": len(variants),
        "n_unique_sigs": len(buckets),
        "pick_mode": "r4r8",
    }
    return ((picked or sel).strip()), out


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def save_checkpoint(
    out: Path,
    *,
    preset: str,
    model: str,
    summary: dict,
    rows: list,
    step_schema_filter: bool,
    qids: List[str],
    status: str,
    current_qid: Optional[str] = None,
    run_config: Optional[dict] = None,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "preset": preset,
        "model": model,
        "status": status,
        "updated_at": _now(),
        "current_qid": current_qid,
        "summary": summary,
        "n_rows": len(rows),
        "n_qids_total": len(qids),
        "run_config": run_config or {},
        "notes": (
            "CTE full v2: reuse value-retrieval+schema-linking snapshot; "
            "optional per-step cheap schema/VR filter; "
            "PromptFactory DC+Sk+ICL; execution/common checker revision; "
            "consistency shortcut / BR; business plan + prefix final pick. "
            "JSON is checkpointed after each qid."
        ),
        "step_schema_filter": bool(step_schema_filter),
        "rows": rows,
    }
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out)

    prog = out.with_name(out.stem + ".progress.json")
    done_qids = [r.get("qid") for r in rows]
    prog.write_text(
        json.dumps(
            {
                "updated_at": _now(),
                "status": status,
                "current_qid": current_qid,
                "done": len(rows),
                "total": len(qids),
                "done_qids": done_qids,
                "summary": summary,
                "run_config": run_config or {},
                "last_row": (
                    {
                        "qid": rows[-1].get("qid"),
                        "pick_hit": (rows[-1].get("plugin") or {}).get("pick_hit"),
                        "last_hit": (rows[-1].get("plugin") or {}).get("last_hit"),
                        "error": rows[-1].get("error"),
                    }
                    if rows
                    else None
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def rollout(
    *,
    db_id: str,
    question: str,
    evidence: str,
    schema: str,
    plan_steps: List[str],
    gold: str,
    client,
    model: str,
    llm_config: dict,
    n_dc: int,
    n_skeleton: int,
    n_icl: int,
    filter_top_k: int,
    evaluator_votes: int,
    shortcut_threshold: float,
    checker_budget: int,
    few_shots: list,
    value_retrieval_hint: str,
    deepeye_ctx: dict | None = None,
    step_schema_filter: bool = False,
    qid: str = "",
    revise_max_unique: int = 0,
    max_plan_steps: int = 6,
    max_workers: int = 12,
    react_layer0: bool = False,
    react_budget: int = 4,
    pick_mode: str = "llm",
    beam_width: int = 1,
    chain_mode: str = "soft",
    chain_synth: bool = False,
    early_stop_same_sig: bool = False,
    require_refs: bool = False,
    inject_prior_exec: bool = False,
    prior_exec_agent: bool = False,
    cum_plan_prefix: bool = False,
    trim_cte: bool = False,
    parallel_slots: bool = False,
    replan_future: bool = False,
) -> dict:
    """Greedy (beam_width=1) or beam search over DeepEye CTE full plugin winners.

    beam_width>1: keep top-k partial chains; each step expands every beam once via
    the plugin, takes up to beam_width CTEs in DeepEye's own cluster order, then
    prunes by path consistency score (consistency ≡ size/n_exec; no extra size term).

    Final pick: candidates include prefixes of *all* surviving beams. pick_mode=r4r8
    selects among terminal beam finals via gated R4→R8 consistency.

    chain_synth (greedy only): after the CTE chain is built, ask the LLM to write a
    complete final SQL from the whole chain (summary rewrite), appended as a pick cand.

    early_stop_same_sig: if new step exec result_sig equals prior prefix, freeze chain
    (skip redundant later steps).
    require_refs: plugin aborts a layer when no CTE references preceding steps.
    inject_prior_exec: feed prior-prefix exec summary into the next step gen hint.
    prior_exec_agent: also run restricted Python analysis agent (implies inject).
    cum_plan_prefix: layer k solves plan steps 1..k together (cumulative), not only step k.
    trim_cte: LLM-trim each unique CTE to the current sub-step (drop full-question over-solve).
    parallel_slots: steps 1..N-1 are independent leaves (no preceding); last step assembles.
    replan_future: after each completed step, LLM revises remaining future plan steps.
    """
    from workflows.mcts_v4.actions.prior_exec_guidance import (
        analyze_prior_exec_with_agent,
        compact_prior_exec_summary,
    )

    def _cum_subq(plan: List[str], k: int) -> str:
        prefix = plan[: max(1, k + 1)]
        lines = [f"{i + 1}. {s}" for i, s in enumerate(prefix)]
        return (
            f"Solve this plan PREFIX (steps 1..{len(prefix)} together):\n"
            + "\n".join(lines)
        )

    steps = [s for s in plan_steps if (s or "").strip()][:max_plan_steps] or [question]
    beam_w = max(1, int(beam_width))
    # Each hyp: chain CTEs, score, step audits for that path tip.
    beams: List[dict] = [
        {"chain": [], "score": 0.0, "step_audits": [], "frozen": False, "prior_exec_guidance": ""}
    ]
    audits: List[dict] = []
    prefix_sqls: List[str] = []
    all_prefix_sqls: List[str] = []  # pool across beams for oracle
    t0 = time.time()
    react_layer0_audit: Optional[dict] = None
    n_plugin_calls = 0
    n_early_stop_same_sig = 0
    n_no_refs_stop = 0
    n_prior_exec_inject = 0
    n_prior_exec_agent_ok = 0
    n_replan_future = 0
    use_inject = bool(inject_prior_exec or prior_exec_agent)

    for si, subq_raw in enumerate(steps):
        t_step = time.time()
        subq = _cum_subq(steps, si) if cum_plan_prefix else subq_raw
        step_schema = schema
        step_vr = value_retrieval_hint
        filt_audit = None
        # Schema filter uses first beam's chain as proxy (shared plan context).
        if step_schema_filter and deepeye_ctx and deepeye_ctx.get("linked_schema"):
            filt = filter_context_for_step(
                deepeye_ctx,
                sub_question=subq,
                preceding_ctes=list(beams[0].get("chain") or []),
                evidence=evidence,
                question=question,
            )
            step_schema = filt.get("schema_profile") or schema
            step_vr = filt.get("value_retrieval_hint") or value_retrieval_hint
            filt_audit = filt.get("audit")
            if filt_audit:
                filt_audit = {
                    **filt_audit,
                    "keep_cols": filt.get("keep_cols"),
                }
                print(
                    f"  [step {si+1}/{len(steps)}] schema_filter "
                    f"cols={filt_audit.get('n_cols_kept')}/{filt_audit.get('n_cols_full')}",
                    flush=True,
                )
        print(
            f"  [step {si+1}/{len(steps)}] start beam={len(beams)} width={beam_w} "
            f"subq={subq[:80]!r}",
            flush=True,
        )
        expanded: List[dict] = []
        step_cluster_n = 0
        step_modes: List[str] = []
        for bi, hyp in enumerate(beams):
            if hyp.get("frozen"):
                expanded.append(hyp)
                continue
            chain = list(hyp.get("chain") or [])
            prior_hint = ""
            if use_inject and chain:
                prior_hint = str(hyp.get("prior_exec_guidance") or "").strip()
                if prior_hint:
                    n_prior_exec_inject += 1
            # Parallel slots: leaves ignore preceding; only assemble sees all slots.
            step_preceding = chain
            step_pm = ""
            step_chain_mode = chain_mode
            step_require_refs = bool(require_refs)
            if parallel_slots:
                if si < len(steps) - 1:
                    step_preceding = []
                    step_pm = "leaf"
                    step_chain_mode = "off"
                    step_require_refs = False
                else:
                    step_preceding = chain
                    step_pm = "assemble"
                    step_chain_mode = "soft"
                    # Prefer refs, but do not freeze the whole qid if assemble
                    # restarts from base tables (still may answer correctly).
                    step_require_refs = False
            out = deepeye_cte_full_plugin(
                db_path=db_path(db_id),
                schema=step_schema,
                question=question,
                evidence=evidence,
                sub_question=subq,
                preceding_ctes=step_preceding,
                llm_config=llm_config,
                few_shots=few_shots,
                value_retrieval_hint=step_vr,
                n_dc=n_dc,
                n_skeleton=n_skeleton,
                n_icl=n_icl,
                filter_top_k=filter_top_k,
                evaluator_votes=evaluator_votes,
                shortcut_threshold=shortcut_threshold,
                checker_budget=checker_budget,
                revise_max_unique=revise_max_unique,
                max_workers=max_workers,
                progress_prefix=f"[qid={qid} step={si+1}/{len(steps)} b={bi}]",
                chain_mode=step_chain_mode,
                require_refs=step_require_refs,
                prior_exec_hint=prior_hint,
                cum_plan_prefix=bool(cum_plan_prefix),
                trim_cte=bool(trim_cte),
                parallel_mode=step_pm,
            )
            n_plugin_calls += 1
            clusters = list(out.get("clusters") or [])
            step_cluster_n = max(step_cluster_n, len(clusters))
            audit = out.get("audit") or {}
            step_modes.append(str(audit.get("selection_mode") or ""))
            winner = out.get("winner_cte") or ""
            if str(audit.get("selection_mode") or "") == "no_refs_stop" or (
                require_refs and chain and not winner
            ):
                n_no_refs_stop += 1
                carried = {
                    "chain": chain,
                    "score": float(hyp.get("score") or 0),
                    "step_audits": list(hyp.get("step_audits") or [])
                    + [
                        {
                            "step": si,
                            "beam": bi,
                            "sub_question": subq,
                            "selection_mode": "no_refs_stop",
                            "early_stop": True,
                            "picked": False,
                        }
                    ],
                    "frozen": True,
                    "prior_exec_guidance": hyp.get("prior_exec_guidance") or "",
                }
                expanded.append(carried)
                print(
                    f"  [step {si+1}/{len(steps)}] b={bi} no_refs_stop → freeze",
                    flush=True,
                )
                continue
            step_react = None
            # ReAct only on CTE layer-0, beam0 (avoid k× react cost).
            if react_layer0 and si == 0 and bi == 0 and winner:
                print(
                    f"  [step {si+1}/{len(steps)}] react-layer0 "
                    f"clusters={len(clusters)} budget={react_budget}",
                    flush=True,
                )
                t_react = time.time()
                react_out = react_select_cte_cluster(
                    db_path=db_path(db_id),
                    schema=step_schema,
                    question=question,
                    evidence=evidence,
                    sub_question=subq,
                    clusters=clusters,
                    winner_cte=winner,
                    client=client,
                    model=model,
                    budget=react_budget,
                )
                step_react = react_out.get("audit") or {}
                step_react["elapsed_s"] = round(time.time() - t_react, 2)
                react_layer0_audit = step_react
                if react_out.get("switched") and react_out.get("winner_cte"):
                    # Prefer switched winner as first candidate.
                    winner = react_out["winner_cte"]
                    clusters = [
                        c for c in clusters if (c.get("cte") or "") == winner
                    ] + [c for c in clusters if (c.get("cte") or "") != winner]
                    print(
                        f"  [step {si+1}/{len(steps)}] react-layer0 SWITCH "
                        f"({step_react['elapsed_s']}s)",
                        flush=True,
                    )

            # Beam: keep DeepEye's own cluster ranking (already sorted:
            # soft/strict → refs_prev / near_dup / consistency; off → size).
            # Do NOT re-rank with consistency+size — consistency ≡ size/n_exec.
            if beam_w <= 1:
                ctes = [winner] if winner else []
            else:
                ctes = []
                seen = set()
                for c in clusters:
                    cte = (c.get("cte") or "").strip()
                    if cte and cte not in seen:
                        seen.add(cte)
                        ctes.append(cte)
                    if len(ctes) >= beam_w:
                        break
                if winner and winner not in seen:
                    ctes = [winner] + ctes
                    ctes = ctes[:beam_w]

            if not ctes:
                # No usable expansion — carry prior chain forward.
                expanded.append(
                    {
                        "chain": chain,
                        "score": float(hyp.get("score") or 0),
                        "step_audits": list(hyp.get("step_audits") or []),
                        "frozen": True,
                        "prior_exec_guidance": hyp.get("prior_exec_guidance") or "",
                    }
                )
                continue

            prev_sql = build_sql_from_chain(chain) if chain else ""
            prev_sig = None
            if early_stop_same_sig and prev_sql:
                rows_p, err_p = exec_rows(db_path(db_id), prev_sql)
                if err_p is None:
                    prev_sig = result_sig(rows_p)

            for cte in ctes:
                new_chain = chain + [cte]
                cons = 0.0
                size = 0
                refs_prev = None
                near_dup = None
                for c in clusters:
                    if (c.get("cte") or "") == cte:
                        cons = float(c.get("consistency") or 0)
                        size = int(c.get("size") or 0)
                        refs_prev = c.get("refs_prev")
                        near_dup = c.get("near_dup")
                        break
                add = cons
                sql = build_sql_from_chain(new_chain)
                all_prefix_sqls.append(sql)

                freeze = False
                if early_stop_same_sig and prev_sig is not None:
                    rows_n, err_n = exec_rows(db_path(db_id), sql)
                    if err_n is None and result_sig(rows_n) == prev_sig:
                        freeze = True
                        n_early_stop_same_sig += 1
                        print(
                            f"  [step {si+1}/{len(steps)}] b={bi} "
                            f"same_sig → freeze (skip redundant step)",
                            flush=True,
                        )

                tip_audit = {
                    "step": si,
                    "beam": bi,
                    "sub_question": subq,
                    "n_clusters": len(clusters),
                    "selection_mode": audit.get("selection_mode"),
                    "winner_consistency": cons,
                    "winner_size": size,
                    "refs_prev": refs_prev,
                    "near_dup": near_dup,
                    "early_stop_same_sig": freeze,
                    "picked": not freeze,
                    "step_schema_filter": filt_audit,
                    "react_layer0": step_react if bi == 0 else None,
                    "elapsed_s": round(time.time() - t_step, 2),
                    "audit": audit,
                    "clusters": [
                        {
                            "size": c.get("size"),
                            "consistency": c.get("consistency"),
                            "sources": c.get("sources"),
                            "refs_prev": c.get("refs_prev"),
                            "cte_preview": (c.get("cte") or "")[:100],
                        }
                        for c in clusters[:4]
                    ],
                }
                next_guidance = ""
                agent_audit = None
                if use_inject and not freeze and si + 1 < len(steps):
                    rows_g, err_g = exec_rows(db_path(db_id), sql)
                    next_subq = (
                        _cum_subq(steps, si + 1) if cum_plan_prefix else steps[si + 1]
                    )
                    if prior_exec_agent:
                        ag = analyze_prior_exec_with_agent(
                            rows=rows_g,
                            err=err_g,
                            next_subq=next_subq,
                            client=client,
                            model=model,
                        )
                        next_guidance = ag.get("guidance") or ""
                        agent_audit = ag.get("audit")
                        if agent_audit and agent_audit.get("agent_ok"):
                            n_prior_exec_agent_ok += 1
                    else:
                        next_guidance = compact_prior_exec_summary(rows_g, err_g, max_rows=5)
                        agent_audit = {"mode": "compact_only"}
                    tip_audit["prior_exec_for_next"] = {
                        "next_subq_preview": (next_subq or "")[:120],
                        "guidance_len": len(next_guidance),
                        "agent": agent_audit,
                    }
                    if next_guidance:
                        print(
                            f"  [step {si+1}/{len(steps)}] b={bi} prior-exec "
                            f"agent={bool(prior_exec_agent and agent_audit and agent_audit.get('agent_ok'))} "
                            f"hint_len={len(next_guidance)}",
                            flush=True,
                        )
                if freeze:
                    # Keep prior chain; do not append redundant CTE.
                    expanded.append(
                        {
                            "chain": chain,
                            "score": float(hyp.get("score") or 0),
                            "step_audits": list(hyp.get("step_audits") or []) + [tip_audit],
                            "frozen": True,
                            "prior_exec_guidance": hyp.get("prior_exec_guidance") or "",
                        }
                    )
                else:
                    expanded.append(
                        {
                            "chain": new_chain,
                            "score": float(hyp.get("score") or 0) + add,
                            "step_audits": list(hyp.get("step_audits") or []) + [tip_audit],
                            "frozen": False,
                            "prior_exec_guidance": next_guidance,
                        }
                    )

        if not expanded:
            print(f"  [step {si+1}/{len(steps)}] STOP no expansions", flush=True)
            break
        expanded.sort(key=lambda h: (-float(h.get("score") or 0), -len(h.get("chain") or [])))
        beams = expanded[:beam_w]
        audits = list(beams[0].get("step_audits") or [])
        print(
            f"  [step {si+1}/{len(steps)}] beam kept={len(beams)} "
            f"expanded={len(expanded)} clusters~={step_cluster_n} "
            f"modes={step_modes[:3]} ({time.time() - t_step:.1f}s)",
            flush=True,
        )

        # Mid-chain: revise remaining future plan steps using the latest CTE.
        if (
            replan_future
            and si < len(steps) - 1
            and beams
            and not beams[0].get("frozen")
        ):
            tip_chain = list(beams[0].get("chain") or [])
            latest = tip_chain[-1] if tip_chain else ""
            done_steps = list(steps[: si + 1])
            future_steps = list(steps[si + 1 :])
            if future_steps and latest:
                new_future, rf_audit = replan_future_remaining(
                    client=client,
                    model=model,
                    question=question,
                    evidence=evidence,
                    schema=schema,
                    done_steps=done_steps,
                    future_steps=future_steps,
                    latest_cte=latest,
                )
                n_replan_future += 1
                if rf_audit.get("ok") and new_future:
                    steps[si + 1 :] = new_future
                    print(
                        f"  [replan-future] after step {si+1} "
                        f"changed={rf_audit.get('changed')} "
                        f"n_future={len(new_future)} "
                        f"notes={(rf_audit.get('notes') or '')[:80]!r}",
                        flush=True,
                    )
                    for j, s in enumerate(new_future):
                        print(f"    future {j+1}. {s[:110]}", flush=True)
                else:
                    print(
                        f"  [replan-future] keep original future "
                        f"({rf_audit.get('notes') or 'no_change'})",
                        flush=True,
                    )

    # Surviving beams that reached a terminal chain (pruning keeps top-k each step).
    best = beams[0] if beams else {"chain": [], "score": 0.0, "step_audits": []}
    chain = list(best.get("chain") or [])
    beam_final_sqls: List[str] = []
    prefix_sqls: List[str] = []
    seen_pref: set = set()
    # Order: non-champion beams first, champion last so pick_mode last/keep_last
    # still defaults to the path-score champion's final SQL.
    ordered_beams = (list(beams[1:]) + [beams[0]]) if beams else []
    for hyp in ordered_beams:
        ch = list(hyp.get("chain") or [])
        if not ch:
            continue
        for i in range(len(ch)):
            sql = build_sql_from_chain(ch[: i + 1])
            if sql not in seen_pref:
                seen_pref.add(sql)
                prefix_sqls.append(sql)
        beam_final_sqls.append(build_sql_from_chain(ch))
    # Champion final first in R4R8 pool (weak prior via selected_sql).
    if beam_final_sqls and chain:
        champ = build_sql_from_chain(chain)
        beam_final_sqls = [champ] + [s for s in beam_final_sqls if s != champ]

    # Greedy-only: LLM summarizes the whole CTE chain into one complete SQL.
    synth_audit: Optional[dict] = None
    synth_sql = ""
    synth_hit = False
    if chain_synth and beam_w <= 1 and chain:
        assembled = build_sql_from_chain(chain)
        print(
            f"  [chain-synth] steps={len(chain)} assembled_len={len(assembled)}",
            flush=True,
        )
        t_syn = time.time()
        synth_sql, synth_audit = synthesize_sql_from_cte_chain(
            client=client,
            model=model,
            question=question,
            evidence=evidence,
            schema=schema,
            plan_steps=steps,
            cte_chain=chain,
            assembled_sql=assembled,
        )
        if synth_audit is not None:
            synth_audit = {**synth_audit, "elapsed_s": round(time.time() - t_syn, 2)}
        if synth_sql:
            rows_s, err_s = exec_rows(db_path(db_id), synth_sql)
            if err_s is None:
                if synth_sql not in seen_pref:
                    seen_pref.add(synth_sql)
                    prefix_sqls.append(synth_sql)
                synth_hit = official_ex_hit(db_id, synth_sql, gold)
                print(
                    f"  [chain-synth] ok exec rows={len(rows_s or [])} "
                    f"hit={synth_hit} ({(synth_audit or {}).get('elapsed_s')}s)",
                    flush=True,
                )
            else:
                if synth_audit is not None:
                    synth_audit["exec_error"] = err_s
                print(f"  [chain-synth] exec fail: {err_s}", flush=True)
                synth_sql = ""
        else:
            print(
                f"  [chain-synth] no sql "
                f"({(synth_audit or {}).get('reason') or (synth_audit or {}).get('error')})",
                flush=True,
            )
    elif chain_synth and beam_w > 1:
        print("  [chain-synth] skipped (beam_width>1)", flush=True)

    cands = []
    for i, sql in enumerate(prefix_sqls):
        rows, err = exec_rows(db_path(db_id), sql)
        if err is not None:
            continue
        cands.append(
            {
                "index": i,
                "sql": sql,
                "sql_preview": sql[:400],
                "result_preview": format_result_preview(rows, None),
                "result_sig": result_sig(rows),
                "n_rows": len(rows),
                "n_cols": (len(rows[0]) if rows else 0),
            }
        )
    last_sql = build_sql_from_chain(chain) if chain else ""
    # last_hit = EX on the full final chain prefix (even if that prefix failed exec and
    # is absent from `cands`). pick_mode last/keep_last default to last *executable*
    # cand, so pick_hit may differ from last_hit when the final step does not exec.
    last_hit = official_ex_hit(db_id, last_sql, gold) if last_sql else False
    print(
        f"  [pick-final] mode={pick_mode} candidates={len(cands)} "
        f"beam={beam_w} finals={len(beam_final_sqls)}",
        flush=True,
    )
    if pick_mode == "r4r8":
        # Consistency over all terminal-beam finals (not just path-score champion).
        pool = list(beam_final_sqls) if beam_final_sqls else list(prefix_sqls)
        picked_sql, pick_audit = _pick_r4r8_sql(
            db_id=db_id,
            sqls=pool,
            question=question,
            schema=schema,
            llm_config=llm_config,
        )
        pick_i = next(
            (c["index"] for c in cands if c["sql"] == picked_sql),
            -1,
        )
        if not picked_sql:
            picked_sql = last_sql
    else:
        pick_i, pick_audit = pick_final_among_prefixes(
            client=client,
            model=model,
            question=question,
            evidence=evidence,
            candidates=cands,
            pick_mode=pick_mode,
        )
        picked_sql = next((c["sql"] for c in cands if c["index"] == pick_i), last_sql)
    pick_hit = official_ex_hit(db_id, picked_sql, gold) if picked_sql else False
    # Oracle: any executable prefix on any surviving beam OR any expansion.
    prefix_oracle = any(official_ex_hit(db_id, c["sql"], gold) for c in cands)
    if not prefix_oracle:
        for sql in all_prefix_sqls:
            if official_ex_hit(db_id, sql, gold):
                prefix_oracle = True
                break
    # Also score each beam's last SQL for pool visibility.
    beam_last_hits = []
    for hyp in beams:
        sql = build_sql_from_chain(list(hyp.get("chain") or [])) if hyp.get("chain") else ""
        beam_last_hits.append(bool(sql and official_ex_hit(db_id, sql, gold)))
    pool_any_beam = any(beam_last_hits) or prefix_oracle
    # Deduped expansion pool for offline R4R8 (includes pruned mid-paths).
    saved_all_prefixes: List[str] = []
    seen_all: set = set()
    for sql in list(prefix_sqls) + list(all_prefix_sqls) + list(beam_final_sqls):
        s = (sql or "").strip()
        if s and s not in seen_all:
            seen_all.add(s)
            saved_all_prefixes.append(s)
    return {
        "n_steps_planned": len(steps),
        "n_steps_done": len(chain),
        "plan_steps": steps,
        "last_hit": last_hit,
        "pick_hit": pick_hit,
        "prefix_oracle_hit": prefix_oracle,
        "pool_oracle_hit": pool_any_beam,
        "picked_step_index": pick_i,
        "pick_audit": pick_audit,
        "pick_mode": pick_mode,
        "beam_width": beam_w,
        "n_beams_final": len(beams),
        "n_plugin_calls": n_plugin_calls,
        "beam_last_hits": beam_last_hits,
        "last_sql": last_sql,
        "picked_sql": picked_sql,
        "synth_sql": synth_sql,
        "synth_hit": synth_hit,
        "synth_audit": synth_audit,
        "chain_synth_enabled": bool(chain_synth and beam_w <= 1),
        "early_stop_same_sig": bool(early_stop_same_sig),
        "require_refs": bool(require_refs),
        "inject_prior_exec": bool(use_inject),
        "prior_exec_agent": bool(prior_exec_agent),
        "cum_plan_prefix": bool(cum_plan_prefix),
        "trim_cte": bool(trim_cte),
        "parallel_slots": bool(parallel_slots),
        "replan_future": bool(replan_future),
        "n_early_stop_same_sig": n_early_stop_same_sig,
        "n_no_refs_stop": n_no_refs_stop,
        "n_prior_exec_inject": n_prior_exec_inject,
        "n_prior_exec_agent_ok": n_prior_exec_agent_ok,
        "n_replan_future": n_replan_future,
        # Executable prefixes along all surviving beams (for keep_last/llm/offline).
        "prefix_sqls": [c["sql"] for c in cands],
        # Terminal SQLs of every surviving beam (R4R8 pool).
        "beam_final_sqls": beam_final_sqls,
        # Full expansion pool incl. pruned paths (offline reselect).
        "all_prefix_sqls": saved_all_prefixes,
        "steps": audits,
        "rollout1_hit": pick_hit,
        "step_schema_filter_enabled": step_schema_filter,
        "react_layer0_enabled": react_layer0,
        "react_layer0_audit": react_layer0_audit,
        "elapsed_s": round(time.time() - t0, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="yi_zhan_qwen3-30b-a3b-2507")
    ap.add_argument("--qids", default="35,40,43,189,286,436,653,656,972,1063,1454,1467,1511,1527")
    ap.add_argument("--n-dc", type=int, default=4)
    ap.add_argument("--n-skeleton", type=int, default=4)
    ap.add_argument("--n-icl", type=int, default=4)
    ap.add_argument("--filter-top-k", type=int, default=2)
    ap.add_argument("--evaluator-votes", type=int, default=5)
    ap.add_argument("--shortcut-threshold", type=float, default=0.6)
    ap.add_argument("--checker-budget", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=120.0, help="OpenAI client timeout seconds")
    ap.add_argument("--revise-max-unique", type=int, default=8)
    ap.add_argument("--max-plan-steps", type=int, default=4)
    ap.add_argument("--max-workers", type=int, default=12, help="Parallel LLM workers for gen/revise/BR")
    ap.add_argument(
        "--qid-workers",
        type=int,
        default=1,
        help="Parallel questions (wall-clock speedup; each qid still uses --max-workers internally).",
    )
    ap.add_argument(
        "--rollout-id",
        type=int,
        default=1,
        help="Label this run as rollout N (stored in JSON; for multi-rollout experiments).",
    )
    ap.add_argument(
        "--require-model-substr",
        default="2507",
        help="Refuse to run unless cfg.model contains this (set empty to disable).",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip qids already present in --out checkpoint.",
    )
    ap.add_argument(
        "--resume-force",
        action="store_true",
        help="With --resume: allow loading checkpoint even if pick_mode/chain_mode/plan_mode differ.",
    )
    ap.add_argument(
        "--step-schema-filter",
        action="store_true",
        help="Per CTE step: cheap rank/filter over already-linked schema + VR (no re-link).",
    )
    ap.add_argument(
        "--react-layer0",
        action="store_true",
        help="After CTE step-0 cluster selection, run ReAct to KEEP/SWITCH among top clusters only.",
    )
    ap.add_argument(
        "--react-budget",
        type=int,
        default=4,
        help="Max probe SQLs for --react-layer0 (default 4).",
    )
    ap.add_argument(
        "--no-rewrite",
        action="store_true",
        help="Alias for --plan-mode p1_tree (use p1 tree as-is).",
    )
    ap.add_argument(
        "--plan-mode",
        choices=[
            "oneshot",
            "p1_tree",
            "p1_rewrite",
            "deepeye_dc",
            "tree_business",
            "parallel_slots",
            "vote",
            "vote_plugin",
            "circle_think",
        ],
        default="",
        help=(
            "oneshot/deepeye_dc/tree_business=ONE prompt; "
            "parallel_slots=independent parallel retrievals + assemble last; "
            "vote=sample n plans then LLM vote; "
            "vote_plugin=sample n plans then DeepEye step0 consistency pick; "
            "circle_think=DeepEye circle first then plan from thinking; "
            "p1_tree / p1_rewrite use --p1-plans. "
            "Empty: --no-rewrite→p1_tree else p1_rewrite."
        ),
    )
    ap.add_argument(
        "--n-plans",
        type=int,
        default=4,
        help="For --plan-mode vote: sample 1..4 plans then vote (default 4).",
    )
    ap.add_argument(
        "--think-iters",
        type=int,
        default=1,
        help="For circle_think: if >1 and first rollout miss, replan from outcome and reroll once.",
    )
    ap.add_argument(
        "--pick-mode",
        choices=["llm", "last", "hybrid", "keep_last", "r4r8", "majority"],
        default="keep_last",
        help=(
            "Final SQL selection. keep_last=Round1 KEEP/SWITCH; "
            "majority=latest prefix in majority result_sig cluster; "
            "r4r8=gated R4→R8 over all terminal beam finals (recommended for --beam-width>1)."
        ),
    )
    ap.add_argument(
        "--chain-mode",
        choices=["off", "soft", "strict"],
        default="soft",
        help="CTE chaining anti-fake-restart (default soft). strict=harder; off=legacy.",
    )
    ap.add_argument(
        "--beam-width",
        type=int,
        default=1,
        help="CTE chain beam width (1=greedy). Cost ~ O(depth * beam_width) plugin calls.",
    )
    ap.add_argument(
        "--chain-synth",
        action="store_true",
        help=(
            "Greedy only: after CTE chain search, LLM-rewrite one complete final SQL "
            "from the whole chain (appended as last pick candidate). Ignored if beam>1."
        ),
    )
    ap.add_argument(
        "--early-stop-same-sig",
        action="store_true",
        help=(
            "If a new CTE step has the same exec result_sig as the prior prefix, "
            "freeze the chain (skip redundant later steps)."
        ),
    )
    ap.add_argument(
        "--require-refs",
        action="store_true",
        help=(
            "With soft/strict chain: if no candidate CTE references preceding steps, "
            "abort that layer and freeze (forces intermediate reuse)."
        ),
    )
    ap.add_argument(
        "--inject-prior-exec",
        action="store_true",
        help=(
            "After each CTE step, inject prior-prefix exec summary (+sample rows) "
            "into the next step generation hint."
        ),
    )
    ap.add_argument(
        "--prior-exec-agent",
        action="store_true",
        help=(
            "With prior-exec inject: LLM writes restricted Python to analyze intermediate "
            "rows conditioned on the *next* sub-question; printed findings go into the hint. "
            "Implies --inject-prior-exec."
        ),
    )
    ap.add_argument(
        "--cum-plan-prefix",
        action="store_true",
        help=(
            "Cumulative plan prefix: layer k generates a CTE answering plan steps 1..k "
            "together (not only step k). Gen target is the prefix text."
        ),
    )
    ap.add_argument(
        "--trim-cte",
        action="store_true",
        help=(
            "After CTE generation, LLM-trim each unique candidate to the current sub-step only "
            "(remove premature ORDER/LIMIT/final columns that over-solve the full question)."
        ),
    )
    ap.add_argument(
        "--parallel-slots",
        action="store_true",
        help=(
            "Execute plan as parallel slots: steps 1..N-1 generate independently (no preceding); "
            "last step assembles from all slot CTEs. Implied by --plan-mode parallel_slots."
        ),
    )
    ap.add_argument(
        "--replan-future",
        action="store_true",
        help=(
            "After each completed CTE step, LLM revises the remaining future plan steps "
            "(mid-chain plan correction)."
        ),
    )
    ap.add_argument(
        "--p1-plans",
        default=str(
            ROOT
            / "workflows/mcts_v4/test/out/deepeye_dev1534/layered_probe/results/p1_s8_vs_tree_contested14.json"
        ),
    )
    ap.add_argument(
        "--out",
        default=str(
            ROOT
            / "workflows/mcts_v4/test/out/deepeye_dev1534/layered_probe/results/p2_deepeye_cte_full_v2_2507_c14.json"
        ),
    )
    args = ap.parse_args()

    cfg = load_preset(args.preset)
    model = cfg["model"]
    if args.require_model_substr and args.require_model_substr not in str(model):
        raise SystemExit(
            f"[refuse] model={model!r} does not contain {args.require_model_substr!r}. "
            f"Use --preset yi_zhan_qwen3-30b-a3b-2507 (or clear --require-model-substr)."
        )
    print(f"[model-check] preset={args.preset} model={model} OK", flush=True)

    plan_mode = (args.plan_mode or "").strip()
    if not plan_mode:
        plan_mode = "p1_tree" if args.no_rewrite else "p1_rewrite"
    print(f"[plan-mode] {plan_mode}", flush=True)

    ppl = {
        str(x["question_id"]): x
        for x in json.loads(
            (ROOT / "data/ppl_dev_20240627_deepeye_schema_ddl.json").read_text(encoding="utf-8")
        )
    }
    gold_by = {
        str(row.get("question_id")): row.get("SQL") or row.get("query") or ""
        for row in json.loads(
            Path("/hpc2hdd/home/sshen190/wtao565/datasets/dev_20240627/dev.json").read_text(
                encoding="utf-8"
            )
        )
    }
    plans_by: Dict[str, Any] = {}
    if plan_mode in ("p1_tree", "p1_rewrite"):
        p1 = json.loads(Path(args.p1_plans).read_text())
        plans_by = {str(r["qid"]): r for r in p1.get("rows") or []}
        print(f"[p1-plans] loaded {len(plans_by)} from {args.p1_plans}", flush=True)
    else:
        print(f"[p1-plans] skipped ({plan_mode})", flush=True)

    llm_config = {
        "config_list": [
            {
                "model": cfg["model"],
                "base_url": cfg["base_url"],
                "api_key": cfg["api_key"],
                "api_type": "openai",
            }
        ],
        "timeout": float(args.timeout),
    }
    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=float(args.timeout))
    qids = [q.strip() for q in args.qids.split(",") if q.strip()]
    out = Path(args.out)

    rows: List[Dict[str, Any]] = []
    summary = {
        "pick_hit": 0,
        "last_hit": 0,
        "prefix_oracle": 0,
        "pool_oracle": 0,
        "rewrite_ok": 0,
        "used_linked_schema": 0,
        "used_value_retrieval_hint": 0,
        "used_few_shots": 0,
        "step_schema_filter": int(args.step_schema_filter),
        "beam_width": int(args.beam_width),
        "chain_synth": bool(args.chain_synth),
        "synth_hit": 0,
    }
    run_config = {
        "plan_mode": plan_mode,
        "pick_mode": str(args.pick_mode),
        "chain_mode": str(args.chain_mode),
        "beam_width": int(args.beam_width),
        "chain_synth": bool(args.chain_synth),
        "early_stop_same_sig": bool(args.early_stop_same_sig),
        "require_refs": bool(args.require_refs),
        "inject_prior_exec": bool(args.inject_prior_exec or args.prior_exec_agent),
        "prior_exec_agent": bool(args.prior_exec_agent),
        "cum_plan_prefix": bool(args.cum_plan_prefix),
        "trim_cte": bool(args.trim_cte),
        "rollout_id": int(args.rollout_id),
        "step_schema_filter": bool(args.step_schema_filter),
        "react_layer0": bool(args.react_layer0),
        "mcts_pick_structural": os.environ.get("MCTS_PICK_STRUCTURAL", "0"),
    }
    done_set = set()
    if args.resume and out.is_file():
        try:
            prev = json.loads(out.read_text(encoding="utf-8"))
            prev_cfg = prev.get("run_config") or {}
            # Compare critical knobs when prior checkpoint recorded them.
            critical = ("plan_mode", "pick_mode", "chain_mode")
            mismatches = []
            for k in critical:
                if k in prev_cfg and str(prev_cfg.get(k)) != str(run_config.get(k)):
                    mismatches.append(f"{k}: checkpoint={prev_cfg.get(k)!r} now={run_config.get(k)!r}")
            if mismatches and not args.resume_force:
                raise SystemExit(
                    "[resume] refuse: run_config mismatch (use --resume-force to override):\n  "
                    + "\n  ".join(mismatches)
                )
            if mismatches and args.resume_force:
                print(
                    "[resume] WARNING run_config mismatch; continuing due to --resume-force:\n  "
                    + "\n  ".join(mismatches),
                    flush=True,
                )
            rows = list(prev.get("rows") or [])
            # Drop error-only stubs so they can be retried.
            kept = []
            for r in rows:
                if r.get("error") and not (r.get("plugin") or {}).get("n_steps_done"):
                    print(f"[resume] retry error stub qid={r.get('qid')}", flush=True)
                    continue
                kept.append(r)
            rows = kept
            for r in rows:
                q = str(r.get("qid"))
                done_set.add(q)
                plug = r.get("plugin") or {}
                if plug.get("pick_hit"):
                    summary["pick_hit"] += 1
                if plug.get("last_hit"):
                    summary["last_hit"] += 1
                if plug.get("prefix_oracle_hit"):
                    summary["prefix_oracle"] += 1
                if plug.get("pool_oracle_hit"):
                    summary["pool_oracle"] += 1
                if plug.get("synth_hit"):
                    summary["synth_hit"] = summary.get("synth_hit", 0) + 1
                if (r.get("rewrite_audit") or {}).get("ok"):
                    summary["rewrite_ok"] += 1
                if r.get("deepeye_ctx_ok"):
                    summary["used_linked_schema"] += 1
            print(f"[resume] loaded {len(rows)} rows from {out}; skip={sorted(done_set)[:20]}{'...' if len(done_set)>20 else ''}", flush=True)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[resume] failed to load {out}: {e}; starting fresh", flush=True)
            rows = []
            done_set = set()

    print(
        f"[cte-full-v2] model={model} dc={args.n_dc} sk={args.n_skeleton} icl={args.n_icl} "
        f"checker={args.checker_budget} workers={args.max_workers} qid_workers={args.qid_workers} "
        f"rollout_id={args.rollout_id} step_schema_filter={args.step_schema_filter} "
        f"react_layer0={args.react_layer0} react_budget={args.react_budget} "
        f"pick_mode={args.pick_mode} chain_mode={args.chain_mode} "
        f"beam_width={args.beam_width} chain_synth={args.chain_synth} "
        f"timeout={args.timeout} qids={len(qids)}",
        flush=True,
    )
    save_checkpoint(
        out,
        preset=args.preset,
        model=model,
        summary=summary,
        rows=rows,
        step_schema_filter=args.step_schema_filter,
        qids=qids,
        status="running",
        current_qid=None,
        run_config=run_config,
    )

    pending = [q for q in qids if q not in done_set and ppl.get(q)]
    for q in qids:
        if q in done_set:
            print(f"[skip-done] {q}", flush=True)
        elif not ppl.get(q):
            print(f"[skip] {q} (not in ppl)", flush=True)

    lock = threading.Lock()
    in_flight: set = set()

    def _recompute_summary(cur_rows: List[dict]) -> dict:
        sm = {
            "pick_hit": 0,
            "last_hit": 0,
            "prefix_oracle": 0,
            "pool_oracle": 0,
            "rewrite_ok": 0,
            "used_linked_schema": 0,
            "used_value_retrieval_hint": 0,
            "used_few_shots": 0,
            "step_schema_filter": int(args.step_schema_filter),
            "beam_width": int(args.beam_width),
            "chain_synth": bool(args.chain_synth),
            "synth_hit": 0,
            "rollout_id": int(args.rollout_id),
            "qid_workers": int(args.qid_workers),
        }
        for r in cur_rows:
            plug = r.get("plugin") or {}
            if plug.get("pick_hit"):
                sm["pick_hit"] += 1
            if plug.get("last_hit"):
                sm["last_hit"] += 1
            if plug.get("prefix_oracle_hit"):
                sm["prefix_oracle"] += 1
            if plug.get("pool_oracle_hit"):
                sm["pool_oracle"] += 1
            if plug.get("synth_hit"):
                sm["synth_hit"] += 1
            if (r.get("rewrite_audit") or {}).get("ok"):
                sm["rewrite_ok"] += 1
            if r.get("deepeye_ctx_ok"):
                sm["used_linked_schema"] += 1
            if r.get("used_vr_hint"):
                sm["used_value_retrieval_hint"] += 1
            if r.get("used_few_shots"):
                sm["used_few_shots"] += 1
        return sm

    def run_one(qid: str) -> dict:
        t_q = time.time()
        item = ppl[qid]
        p1row = plans_by.get(qid) or {}
        # per-thread clients (OpenAI httpx is generally ok shared, but isolate for safety)
        local_client = OpenAI(
            base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=float(args.timeout)
        )
        question = item.get("question") or ""
        evidence = item.get("evidence") or ""
        ctx = load_deepeye_context(qid)
        schema = ctx.get("schema_profile") or ""
        used_linked = bool(schema)
        if not schema:
            schema = (
                item.get("schema_ddl")
                or item.get("ddl_data")
                or item.get("simplified_ddl")
                or item.get("schema")
                or ""
            )
            fk = item.get("foreign_key") or ""
            if fk and fk not in schema:
                schema = f"{schema}\n\nforeign_key:\n{fk}"
        vr_hint = ctx.get("value_retrieval_hint") or ""
        few = ctx.get("few_shots") or []
        if int(args.n_icl) <= 0:
            few = []
        db_id = item.get("db_id") or ""
        gold = gold_by.get(qid, "")
        old_steps: List[str] = []
        if plan_mode == "vote":
            print(
                f"\n======== qid={qid} db={db_id} linked={ctx.get('ok')} "
                f"fewshots={len(few)} vr_hint={bool(vr_hint)} "
                f"step_filt={args.step_schema_filter} rollout={args.rollout_id} "
                f"plan_mode=vote n_plans={args.n_plans} ========",
                flush=True,
            )
            print(f"  [plan] vote sample n={args.n_plans}...", flush=True)
            biz_steps, rw = plan_business_vote(
                client=local_client,
                model=model,
                question=question,
                evidence=evidence,
                schema=schema,
                n_plans=int(args.n_plans),
            )
            old_steps = list(biz_steps)
            print(
                f"  [plan] vote pick={rw.get('pick')} unique={rw.get('n_unique')}/{rw.get('n_sampled')} "
                f"why={(rw.get('reason') or '')[:80]}",
                flush=True,
            )
        elif plan_mode == "vote_plugin":
            print(
                f"\n======== qid={qid} db={db_id} linked={ctx.get('ok')} "
                f"fewshots={len(few)} vr_hint={bool(vr_hint)} "
                f"step_filt={args.step_schema_filter} rollout={args.rollout_id} "
                f"plan_mode=vote_plugin n_plans={args.n_plans} ========",
                flush=True,
            )
            print(
                f"  [plan] vote_plugin sample n={args.n_plans} + DeepEye step0 probe...",
                flush=True,
            )
            biz_steps, rw = plan_business_vote_plugin(
                client=local_client,
                model=model,
                question=question,
                evidence=evidence,
                schema=schema,
                db_path=db_path(db_id),
                llm_config=llm_config,
                n_plans=int(args.n_plans),
                few_shots=few,
                value_retrieval_hint=vr_hint,
                n_dc=max(2, min(4, int(args.n_dc))),
                n_skeleton=max(2, min(4, int(args.n_skeleton))),
                n_icl=0,
                max_workers=min(6, int(args.max_workers)),
                revise_max_unique=4,
                checker_budget=1,
                chain_mode=str(args.chain_mode),
            )
            old_steps = list(biz_steps)
            print(
                f"  [plan] vote_plugin pick={rw.get('pick')} "
                f"unique={rw.get('n_unique')}/{rw.get('n_sampled')} "
                f"why={(rw.get('reason') or '')[:100]}",
                flush=True,
            )
        elif plan_mode == "circle_think":
            print(
                f"\n======== qid={qid} db={db_id} linked={ctx.get('ok')} "
                f"fewshots={len(few)} vr_hint={bool(vr_hint)} "
                f"step_filt={args.step_schema_filter} rollout={args.rollout_id} "
                f"plan_mode=circle_think ========",
                flush=True,
            )
            print("  [plan] circle_think: DeepEye circle → plan...", flush=True)
            biz_steps, rw = plan_from_circle_think(
                client=local_client,
                model=model,
                question=question,
                evidence=evidence,
                schema=schema,
                db_path=db_path(db_id),
                llm_config=llm_config,
                few_shots=few,
                value_retrieval_hint=vr_hint,
                n_dc=int(args.n_dc),
                n_skeleton=int(args.n_skeleton),
                n_icl=min(2, int(args.n_icl)),
                max_workers=int(args.max_workers),
                revise_max_unique=max(4, int(args.revise_max_unique) or 6),
                checker_budget=int(args.checker_budget),
                evaluator_votes=int(args.evaluator_votes),
            )
            old_steps = list(biz_steps)
            th = rw.get("think") or {}
            print(
                f"  [plan] circle_think cons={th.get('winner_consistency')} "
                f"mode={th.get('selection_mode')} n_steps={rw.get('n_steps')}",
                flush=True,
            )
        elif plan_mode in ("oneshot", "deepeye_dc", "tree_business", "parallel_slots"):
            print(
                f"\n======== qid={qid} db={db_id} linked={ctx.get('ok')} "
                f"fewshots={len(few)} vr_hint={bool(vr_hint)} "
                f"step_filt={args.step_schema_filter} rollout={args.rollout_id} "
                f"plan_mode={plan_mode} ========",
                flush=True,
            )
            print(f"  [plan] {plan_mode} business steps...", flush=True)
            biz_steps, rw = plan_business_oneshot(
                client=local_client,
                model=model,
                question=question,
                evidence=evidence,
                schema=schema,
                variant=plan_mode,
            )
            old_steps = list(biz_steps)
        else:
            old_steps = (p1row.get("tree") or {}).get("winner_sub_questions") or []
            if not old_steps:
                raise RuntimeError(
                    f"qid={qid}: missing p1 tree.winner_sub_questions (refuse fallback)"
                )
            print(
                f"\n======== qid={qid} db={db_id} linked={ctx.get('ok')} "
                f"fewshots={len(few)} vr_hint={bool(vr_hint)} "
                f"step_filt={args.step_schema_filter} rollout={args.rollout_id} "
                f"plan_mode={plan_mode} ========",
                flush=True,
            )
            if plan_mode == "p1_tree":
                biz_steps = list(old_steps)
                rw = {"ok": True, "skipped": True, "mode": "p1_tree_direct"}
                print(f"  [plan] p1 tree direct n={len(biz_steps)}", flush=True)
            else:
                print("  [plan] p1 tree → business rewrite...", flush=True)
                biz_steps, rw = rewrite_business_plan(
                    client=local_client,
                    model=model,
                    question=question,
                    evidence=evidence,
                    old_steps=old_steps,
                )
        for i, s in enumerate(biz_steps):
            print(f"  {i+1}. {s[:110]}", flush=True)

        arm = rollout(
            db_id=db_id,
            question=question,
            evidence=evidence,
            schema=schema,
            plan_steps=biz_steps,
            gold=gold,
            client=local_client,
            model=model,
            llm_config=llm_config,
            n_dc=args.n_dc,
            n_skeleton=args.n_skeleton,
            n_icl=args.n_icl,
            filter_top_k=args.filter_top_k,
            evaluator_votes=args.evaluator_votes,
            shortcut_threshold=args.shortcut_threshold,
            checker_budget=args.checker_budget,
            few_shots=few,
            value_retrieval_hint=vr_hint,
            deepeye_ctx=ctx,
            step_schema_filter=args.step_schema_filter,
            qid=qid,
            revise_max_unique=args.revise_max_unique,
            max_plan_steps=args.max_plan_steps,
            max_workers=args.max_workers,
            react_layer0=bool(args.react_layer0),
            react_budget=int(args.react_budget),
            pick_mode=str(args.pick_mode),
            beam_width=int(args.beam_width),
            chain_mode=str(args.chain_mode),
            chain_synth=bool(args.chain_synth),
            early_stop_same_sig=bool(args.early_stop_same_sig),
            require_refs=bool(args.require_refs),
            inject_prior_exec=bool(args.inject_prior_exec or args.prior_exec_agent),
            prior_exec_agent=bool(args.prior_exec_agent),
            cum_plan_prefix=bool(args.cum_plan_prefix),
            trim_cte=bool(args.trim_cte),
            parallel_slots=bool(args.parallel_slots or plan_mode == "parallel_slots"),
            replan_future=bool(args.replan_future),
        )
        # Optional iterative reasoning: miss → replan from rollout → second rollout.
        replan_audit = None
        if (
            plan_mode == "circle_think"
            and int(args.think_iters) >= 2
            and not arm.get("pick_hit")
        ):
            print("  [plan] think-iter2: replan from rollout miss...", flush=True)
            th = (rw.get("think") or {}) if isinstance(rw, dict) else {}
            new_steps, replan_audit = replan_from_rollout(
                client=local_client,
                model=model,
                question=question,
                evidence=evidence,
                schema=schema,
                think_sql=str(th.get("winner_cte") or th.get("winner_cte_preview") or ""),
                prev_steps=biz_steps,
                last_sql=str(arm.get("last_sql") or ""),
                last_result="",
                pick_notes=str((arm.get("pick_audit") or {}).get("reason") or ""),
            )
            if replan_audit.get("ok") and new_steps and new_steps != biz_steps:
                for i, s in enumerate(new_steps):
                    print(f"  [replan] {i+1}. {s[:110]}", flush=True)
                arm2 = rollout(
                    db_id=db_id,
                    question=question,
                    evidence=evidence,
                    schema=schema,
                    plan_steps=new_steps,
                    gold=gold,
                    client=local_client,
                    model=model,
                    llm_config=llm_config,
                    n_dc=args.n_dc,
                    n_skeleton=args.n_skeleton,
                    n_icl=args.n_icl,
                    filter_top_k=args.filter_top_k,
                    evaluator_votes=args.evaluator_votes,
                    shortcut_threshold=args.shortcut_threshold,
                    checker_budget=args.checker_budget,
                    few_shots=few,
                    value_retrieval_hint=vr_hint,
                    deepeye_ctx=ctx,
                    step_schema_filter=args.step_schema_filter,
                    qid=qid,
                    revise_max_unique=args.revise_max_unique,
                    max_plan_steps=args.max_plan_steps,
                    max_workers=args.max_workers,
                    react_layer0=bool(args.react_layer0),
                    react_budget=int(args.react_budget),
                    pick_mode=str(args.pick_mode),
                    beam_width=int(args.beam_width),
                    chain_mode=str(args.chain_mode),
                    chain_synth=bool(args.chain_synth),
                    early_stop_same_sig=bool(args.early_stop_same_sig),
                    require_refs=bool(args.require_refs),
                    inject_prior_exec=bool(args.inject_prior_exec or args.prior_exec_agent),
                    prior_exec_agent=bool(args.prior_exec_agent),
                    cum_plan_prefix=bool(args.cum_plan_prefix),
                    trim_cte=bool(args.trim_cte),
                    parallel_slots=bool(args.parallel_slots or plan_mode == "parallel_slots"),
                    replan_future=bool(args.replan_future),
                )
                arm2["think_iter"] = 2
                arm2["first_rollout"] = {
                    "pick_hit": arm.get("pick_hit"),
                    "last_hit": arm.get("last_hit"),
                    "prefix_oracle_hit": arm.get("prefix_oracle_hit"),
                }
                arm2["replan_audit"] = replan_audit
                biz_steps = new_steps
                rw = dict(rw) if isinstance(rw, dict) else {"ok": True}
                rw["replan"] = replan_audit
                arm = arm2
                print(
                    f"  [think-iter2] pick={arm['pick_hit']} last={arm['last_hit']} "
                    f"(was first pick={arm2['first_rollout']['pick_hit']})",
                    flush=True,
                )
            else:
                print("  [plan] think-iter2 skipped (replan unchanged/failed)", flush=True)
        filt_cols = []
        for s in arm.get("steps") or []:
            fa = s.get("step_schema_filter") or {}
            if fa.get("n_cols_kept") is not None:
                filt_cols.append(f"{fa.get('n_cols_kept')}/{fa.get('n_cols_full')}")
        r0 = arm.get("react_layer0_audit") or {}
        react_tag = ""
        if args.react_layer0:
            react_tag = (
                f" react0_switched={r0.get('switched')} "
                f"skip={r0.get('skipped')} calls={r0.get('llm_calls')}"
            )
        print(
            f"  [cte-full-v2] pick={arm['pick_hit']} last={arm['last_hit']} "
            f"oracle={arm['prefix_oracle_hit']} pool={arm.get('pool_oracle_hit')} "
            f"beam={arm.get('beam_width')} plugins={arm.get('n_plugin_calls')} "
            f"done={arm['n_steps_done']}/{arm['n_steps_planned']} "
            f"modes={[s.get('selection_mode') for s in arm['steps']]}"
            + (f" cols={filt_cols}" if filt_cols else "")
            + react_tag
            + f" elapsed={arm.get('elapsed_s')}s qid_wall={time.time()-t_q:.1f}s",
            flush=True,
        )
        return {
            "qid": qid,
            "db_id": db_id,
            "question": question,
            "evidence": evidence,
            "gold_sql": gold,
            "old_plan_steps": old_steps,
            "biz_plan_steps": biz_steps,
            "plan_mode": plan_mode,
            "rewrite_audit": rw,
            "deepeye_ctx_ok": ctx.get("ok"),
            "used_vr_hint": bool(vr_hint),
            "used_few_shots": bool(few),
            "plugin": arm,
            "rollout_id": int(args.rollout_id),
            "finished_at": _now(),
            "_used_linked": used_linked,
        }

    def _error_row(qid: str, err: BaseException) -> dict:
        item = ppl.get(qid) or {}
        return {
            "qid": qid,
            "db_id": item.get("db_id") or "",
            "question": item.get("question") or "",
            "evidence": item.get("evidence") or "",
            "gold_sql": gold_by.get(qid) or "",
            "error": f"{type(err).__name__}: {err}"[:500],
            "plugin": {
                "pick_hit": False,
                "last_hit": False,
                "prefix_oracle_hit": False,
                "pool_oracle_hit": False,
                "n_steps_done": 0,
                "pick_mode": str(args.pick_mode),
            },
            "plan_mode": plan_mode,
            "rollout_id": int(args.rollout_id),
            "finished_at": _now(),
        }

    def _ckpt(cur_rows: List[dict], *, current_qid: Optional[str] = None, status: str = "running") -> dict:
        sm = _recompute_summary(cur_rows)
        save_checkpoint(
            out,
            preset=args.preset,
            model=model,
            summary=sm,
            rows=cur_rows,
            step_schema_filter=args.step_schema_filter,
            qids=qids,
            status=status,
            current_qid=current_qid,
            run_config=run_config,
        )
        return sm

    qid_workers = max(1, int(args.qid_workers))
    if qid_workers == 1:
        for qid in pending:
            with lock:
                in_flight.add(qid)
            try:
                row = run_one(qid)
            except Exception as e:
                print(f"[error] qid={qid} {type(e).__name__}: {e}", flush=True)
                row = _error_row(qid, e)
            with lock:
                in_flight.discard(qid)
                rows.append(row)
                summary = _ckpt(rows, current_qid=None)
            print(f"  [checkpoint] summary={summary}", flush=True)
    else:
        print(f"[parallel] launching {len(pending)} qids with qid_workers={qid_workers}", flush=True)
        with ThreadPoolExecutor(max_workers=qid_workers) as ex:
            futs = {ex.submit(run_one, qid): qid for qid in pending}
            with lock:
                in_flight.update(pending)
            for fut in as_completed(futs):
                qid = futs[fut]
                try:
                    row = fut.result()
                except Exception as e:
                    print(f"[error] qid={qid} {type(e).__name__}: {e}", flush=True)
                    row = _error_row(qid, e)
                with lock:
                    in_flight.discard(qid)
                    rows.append(row)
                    # keep rows ordered by original qid list when possible
                    order = {q: i for i, q in enumerate(qids)}
                    rows.sort(key=lambda r: order.get(str(r.get("qid")), 10**9))
                    summary = _ckpt(
                        rows,
                        current_qid=",".join(sorted(in_flight)) if in_flight else None,
                    )
                print(
                    f"  [checkpoint] finished qid={qid} in_flight={sorted(in_flight)} summary={summary}",
                    flush=True,
                )

    summary = _ckpt(rows, status="done")
    print(f"\n[cte-full-v2] DONE wrote {out}\nsummary={summary}", flush=True)


if __name__ == "__main__":
    main()
