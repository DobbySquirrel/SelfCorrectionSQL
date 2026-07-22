#!/usr/bin/env python3
"""Arm B: full-SQL gen/revise/select matched to CTE full plugin stack.

Same modules as CTE v2, but one complete SQL (no step CTE):
  DC + Skeleton + ICL → exec/common checker → consistency / BR.
Fair budget: n_rounds × (n_dc+n_skeleton+n_icl) generation calls.
"""

from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from workflows.mcts_v4.actions.deepeye_cte_plugin import (
    exec_rows,
    format_result_preview,
    result_sig,
    _llm,
    _parse_ab,
)


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
    raw = raw.strip().rstrip(";")
    if re.search(r"\bSELECT\b", raw, re.I):
        return raw
    return ""


def _revise_full_sql(
    *,
    sql: str,
    db_path: Path,
    schema: str,
    question: str,
    evidence: str,
    client,
    model: str,
    PF,
    checker_budget: int = 2,
) -> Tuple[str, Dict[str, Any]]:
    audit = {"rounds": [], "final_ok": False}
    cur = (sql or "").strip().rstrip(";")
    hint = evidence or "None"

    for round_i in range(max(1, checker_budget)):
        rows, err = exec_rows(db_path, cur)
        if err is None:
            try:
                prompt = PF.format_common_checker_prompt(
                    schema[:8000],
                    question,
                    hint,
                    cur[:2500],
                    "Check JOIN/SELECT/NULL/ORDER issues; return one complete SQLite SQL.",
                )
                text = _llm(prompt, client=client, model=model, temperature=0.3)
                fixed = _extract_sql(text)
                if fixed:
                    rows2, err2 = exec_rows(db_path, fixed)
                    if err2 is None:
                        cur = fixed
                        audit["rounds"].append({"round": round_i, "mode": "common_ok"})
                        audit["final_ok"] = True
                        return cur, audit
            except Exception as e:
                audit["rounds"].append({"round": round_i, "mode": "common_err", "error": str(e)[:120]})
            audit["final_ok"] = True
            audit["rounds"].append({"round": round_i, "mode": "exec_ok_keep"})
            return cur, audit

        try:
            prompt = PF.format_execution_checker_prompt(
                schema[:8000],
                question,
                hint,
                cur[:2500],
                f"ERROR: {err}",
            )
            text = _llm(prompt, client=client, model=model, temperature=0.4)
            fixed = _extract_sql(text)
            if fixed:
                cur = fixed
                audit["rounds"].append({"round": round_i, "mode": "exec_fix", "err": (err or "")[:120]})
                continue
        except Exception as e:
            audit["rounds"].append({"round": round_i, "mode": "exec_fix_err", "error": str(e)[:120]})
            break
        audit["rounds"].append({"round": round_i, "mode": "exec_fail", "err": (err or "")[:120]})
        break

    _, err = exec_rows(db_path, cur)
    audit["final_ok"] = err is None
    return (cur if err is None else ""), audit


def deepeye_fullsql_fair_plugin(
    *,
    db_path: Path,
    schema: str,
    question: str,
    evidence: str,
    llm_config: Optional[dict] = None,
    few_shots: Optional[List[Dict[str, str]]] = None,
    value_retrieval_hint: str = "",
    n_dc: int = 4,
    n_skeleton: int = 4,
    n_icl: int = 4,
    n_rounds: int = 1,
    filter_top_k: int = 2,
    evaluator_votes: int = 5,
    shortcut_threshold: float = 0.6,
    checker_budget: int = 2,
    max_workers: int = 6,
    revise_max_unique: int = 0,
    progress_prefix: str = "",
) -> Dict[str, Any]:
    """Full-SQL arm with n_rounds matched to CTE step count."""
    t0 = time.time()
    client, model = _client_model(llm_config)
    PF = _load_deepeye_prompt_factory()
    schema_s = (schema or "")[:12000]
    evidence_s = evidence or "None"
    if value_retrieval_hint:
        evidence_s = f"{evidence_s}\n{value_retrieval_hint}"
    pref = progress_prefix or "[fullsql-B]"
    n_rounds = max(1, int(n_rounds))

    dc_prompt = PF.format_dc_sql_generation_prompt(schema_s, question, evidence_s)
    sk_prompt = PF.format_skeleton_sql_generation_prompt(schema_s, question, evidence_s)
    icl_prompt = None
    if n_icl > 0 and few_shots:
        icl_prompt = PF.format_icl_sql_generation_prompt(few_shots, schema_s, question, evidence_s)

    jobs: List[Tuple[str, str, float]] = []
    for r in range(n_rounds):
        for i in range(max(0, n_dc)):
            jobs.append(("dc", dc_prompt, 0.2 + 0.2 * (i % 4) + 0.05 * r))
        for i in range(max(0, n_skeleton)):
            jobs.append(("skeleton", sk_prompt, 0.3 + 0.2 * (i % 4) + 0.05 * r))
        if icl_prompt:
            for i in range(max(0, n_icl)):
                jobs.append(("icl", icl_prompt, 0.25 + 0.2 * (i % 4) + 0.05 * r))

    print(
        f"  {pref} gen start n_jobs={len(jobs)} rounds={n_rounds} "
        f"workers={min(max_workers, max(1, len(jobs)))}",
        flush=True,
    )
    raw_items: List[Dict[str, Any]] = []

    def _gen(kind: str, prompt: str, temp: float) -> Dict[str, Any]:
        try:
            text = _llm(prompt, client=client, model=model, temperature=temp)
            sql = _extract_sql(text)
            return {"kind": kind, "temp": temp, "sql": sql, "ok": bool(sql)}
        except Exception as e:
            return {"kind": kind, "temp": temp, "sql": "", "ok": False, "error": str(e)}

    if jobs:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as ex:
            futs = [ex.submit(_gen, k, p, t) for k, p, t in jobs]
            done_n = 0
            for fut in as_completed(futs):
                raw_items.append(fut.result())
                done_n += 1
                if done_n == 1 or done_n == len(jobs) or done_n % 8 == 0:
                    print(f"  {pref} gen progress {done_n}/{len(jobs)}", flush=True)

    n_raw_ok = sum(1 for x in raw_items if x.get("ok"))
    n_raw_err = sum(1 for x in raw_items if x.get("error"))
    err_sample = next((x.get("error") for x in raw_items if x.get("error")), None)
    print(
        f"  {pref} gen done ok={n_raw_ok}/{len(raw_items)} err={n_raw_err}"
        + (f" sample_err={str(err_sample)[:120]}" if err_sample else "")
        + f" ({time.time() - t0:.1f}s)",
        flush=True,
    )

    uniq: List[Dict[str, Any]] = []
    seen = set()
    for it in raw_items:
        sql = (it.get("sql") or "").strip()
        if not sql:
            continue
        key = re.sub(r"\s+", " ", sql.lower())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    if revise_max_unique and revise_max_unique > 0 and len(uniq) > revise_max_unique:
        # prefer ones that already exec-ok
        scored = []
        for it in uniq:
            rows, err = exec_rows(db_path, it["sql"])
            scored.append((0 if err is None else 1, it))
        scored.sort(key=lambda x: x[0])
        uniq = [it for _, it in scored[:revise_max_unique]]

    print(f"  {pref} revise start unique={len(uniq)}", flush=True)
    revised: List[Dict[str, Any]] = []
    for ui, it in enumerate(uniq):
        fixed, raudit = _revise_full_sql(
            sql=it["sql"],
            db_path=db_path,
            schema=schema_s,
            question=question,
            evidence=evidence_s,
            client=client,
            model=model,
            PF=PF,
            checker_budget=checker_budget,
        )
        if not fixed:
            it["exec_ok"] = False
            it["revise"] = raudit
            continue
        it["sql"] = fixed
        it["exec_ok"] = True
        it["revise"] = raudit
        revised.append(it)
        if (ui + 1) == len(uniq) or (ui + 1) % 4 == 0:
            print(f"  {pref} revise progress {ui+1}/{len(uniq)} ok={len(revised)}", flush=True)

    clusters_map: Dict[str, Dict[str, Any]] = {}
    for it in revised:
        sql = (it.get("sql") or "").strip()
        rows, err = exec_rows(db_path, sql)
        if err is not None:
            continue
        sig = result_sig(rows)
        if sig not in clusters_map:
            clusters_map[sig] = {
                "sig": sig,
                "sql": sql,
                "size": 0,
                "sources": [],
                "rows_preview": format_result_preview(rows, None),
            }
        clusters_map[sig]["size"] += 1
        clusters_map[sig]["sources"].append(it.get("kind") or "?")

    clusters = sorted(clusters_map.values(), key=lambda c: (-c["size"], c["sig"]))
    n_exec = sum(c["size"] for c in clusters) or 1
    for c in clusters:
        c["consistency"] = c["size"] / n_exec

    selection_mode = "empty"
    winner = None
    br_audit: List[dict] = []

    if not clusters:
        selection_mode = "empty"
    elif len(clusters) == 1:
        winner = clusters[0]
        selection_mode = "only_one"
    else:
        top = clusters[: max(1, filter_top_k)]
        if top[0]["consistency"] >= shortcut_threshold:
            winner = top[0]
            selection_mode = "shortcut_consistency"
        elif len(top) == 1:
            winner = top[0]
            selection_mode = "top1_only"
        else:
            a, b = top[0], top[1]
            wins = {a["sig"]: 0.0, b["sig"]: 0.0}
            votes = []
            for vi in range(max(1, evaluator_votes)):
                try:
                    prompt = PF.format_br_pair_selection_prompt(
                        schema_s[:6000],
                        question,
                        evidence_s,
                        a["sql"][:1800],
                        a.get("rows_preview") or "",
                        b["sql"][:1800],
                        b.get("rows_preview") or "",
                    )
                    text = _llm(
                        prompt,
                        client=client,
                        model=model,
                        temperature=0.2 + 0.1 * (vi % 3),
                    )
                    choice = _parse_ab(text)
                except Exception:
                    choice = "A"
                votes.append(choice)
                wins[a["sig"] if choice == "A" else b["sig"]] += 1.0
            br_audit.append({"votes": votes})
            winner = a if wins[a["sig"]] >= wins[b["sig"]] else b
            selection_mode = "br_pairwise"

    elapsed = time.time() - t0
    print(
        f"  {pref} select mode={selection_mode} clusters={len(clusters)} "
        f"revised={len(revised)} picked={bool(winner)} "
        f"({elapsed:.1f}s)",
        flush=True,
    )

    pool_hit_sqls = [c["sql"] for c in clusters]
    return {
        "winner_sql": (winner["sql"] if winner else ""),
        "winner_consistency": (round(float(winner["consistency"]), 4) if winner else 0.0),
        "clusters": [
            {
                "sig": c["sig"][:120],
                "sql_preview": (c["sql"] or "")[:200],
                "size": c["size"],
                "consistency": round(float(c["consistency"]), 4),
                "sources": c.get("sources") or [],
            }
            for c in clusters[:8]
        ],
        "pool_sqls": pool_hit_sqls,
        "audit": {
            "arm": "B_fullsql",
            "n_rounds": n_rounds,
            "n_dc": n_dc,
            "n_skeleton": n_skeleton,
            "n_icl": n_icl if icl_prompt else 0,
            "n_gen_jobs": len(jobs),
            "n_raw_ok": n_raw_ok,
            "n_raw_err": n_raw_err,
            "n_unique": len(uniq),
            "n_revised_ok": len(revised),
            "n_clusters": len(clusters),
            "selection_mode": selection_mode,
            "filter_top_k": filter_top_k,
            "evaluator_votes": evaluator_votes,
            "shortcut_threshold": shortcut_threshold,
            "checker_budget": checker_budget,
            "revise_max_unique": revise_max_unique,
            "br_pairs": br_audit,
            "elapsed_s": round(elapsed, 2),
            "sample_gen_error": (str(err_sample)[:200] if err_sample else None),
        },
    }
