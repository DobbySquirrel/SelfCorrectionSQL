#!/usr/bin/env python3
"""Arm B: full-SQL gen/revise/select matched to CTE full plugin stack.

Same modules as CTE v2, but one complete SQL (no step CTE):
  DC + Skeleton + ICL → exec/common checker → consistency / BR.
Fair budget: n_rounds × (n_dc+n_skeleton+n_icl) generation calls.

Also used as MCTS final-SQL generator when MCTS_FINAL_DEEPEYE_PLUGIN=1
(mid CTE expand stays Mode-C / DeepEye-full; only the end-of-rollout SQL uses this).
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from openai import OpenAI

from workflows.mcts_v4.actions.deepeye_cte_plugin import (
    exec_rows,
    format_result_preview,
    result_sig,
    _llm,
    _parse_ab,
)
from workflows.mcts_v4.utils.llm_chat import create_chat_completion

DEEPEYE_ROOT = "/hpc2hdd/home/sshen190/wtao565/related_project/DeepEye-SQL"
# Official DeepEye default SQL exec timeout (app/db_utils/defaults.py).
OFFICIAL_SQL_EXEC_TIMEOUT_S = 600.0


def _official_make_hashable(obj: Any) -> Any:
    """Mirror DeepEye app.pipeline.utils.make_hashable (BIRD frozenset rows)."""
    if DEEPEYE_ROOT not in sys.path:
        sys.path.insert(0, DEEPEYE_ROOT)
    try:
        from app.pipeline.utils import make_hashable  # type: ignore

        return make_hashable(obj)
    except Exception:
        if hasattr(obj, "tolist") and callable(obj.tolist):
            obj = obj.tolist()
        if isinstance(obj, list):
            return tuple(_official_make_hashable(x) for x in obj)
        if isinstance(obj, tuple):
            return tuple(_official_make_hashable(x) for x in obj)
        if isinstance(obj, dict):
            return tuple(sorted((k, _official_make_hashable(v)) for k, v in obj.items()))
        return obj


def _official_result_hash(rows: Optional[List[Any]]) -> Any:
    if rows is None:
        return None
    return frozenset(_official_make_hashable(rows))


def _parse_ab_tie(text: str) -> str:
    """Official BR parser: A / B / TIE (fallback A)."""
    raw = (text or "").strip()
    m = re.search(r"<result>\s*(.*?)\s*</result>", raw, re.I | re.S)
    if m:
        ans = m.group(1).strip().upper()
        if ans in ("A", "B", "TIE"):
            return ans
    m = re.search(r"\b(TIE|A|B)\b", raw, re.I)
    if m:
        return m.group(1).upper()
    return "A"


def final_deepeye_plugin_enabled() -> bool:
    return os.environ.get("MCTS_FINAL_DEEPEYE_PLUGIN", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default)).strip()))
    except ValueError:
        return default


def _split_k_across_arms(k: int, has_icl: bool) -> Tuple[int, int, int]:
    """Split K parallel gens across DC / Skeleton / ICL (same budget as old final SQL×K)."""
    k = max(1, int(k))
    if has_icl:
        n_dc = max(1, k // 3)
        n_sk = max(1, k // 3)
        n_icl = max(1, k - n_dc - n_sk)
        return n_dc, n_sk, n_icl
    n_dc = max(1, (k + 1) // 2)
    n_sk = max(1, k - n_dc)
    return n_dc, n_sk, 0


def generate_final_sqls_via_deepeye_plugin(
    *,
    db_path: Path,
    schema: str,
    question: str,
    evidence: str = "",
    preceding_cte_info: str = "",
    llm_config: Optional[dict] = None,
    few_shots: Optional[List[Dict[str, str]]] = None,
    value_retrieval_hint: str = "",
    num_variants: int = 6,
    max_workers: int = 4,
    cte_probe_limit: int = 15,
) -> Tuple[List[str], Dict[str, Any]]:
    """Run full DeepEye final-SQL plugin; return [winner, ...other cluster sqls].

    Path context matches complete_sql_generator: Existing CTE + execution results.
    Parallel gen budget defaults to num_variants (K), split across DC/Skeleton/ICL.
    """
    hint_parts = [value_retrieval_hint or ""]
    prec = (preceding_cte_info or "").strip()
    # Same block as CompleteSQLGenerator.generate_multiple_complete_sqls_parallel
    # (CTE SQL + Execution Result / sample values). Not optional soft hint.
    if prec and prec not in ("None", "No preceding CTE"):
        hint_parts.append(
            f"**Existing CTE and Results (Quick verification with LIMIT {cte_probe_limit})**:\n"
            f"{prec[:12000]}"
        )
    combined_vr = "\n\n".join(p for p in hint_parts if p).strip()

    has_icl = bool(few_shots)
    # Env overrides win; else auto-split K like old final SQL×K parallel.
    if os.environ.get("MCTS_FINAL_DEEPEYE_N_DC", "").strip():
        n_dc = _int_env("MCTS_FINAL_DEEPEYE_N_DC", 4)
        n_sk = _int_env("MCTS_FINAL_DEEPEYE_N_SKELETON", 4)
        n_icl = _int_env("MCTS_FINAL_DEEPEYE_N_ICL", 4 if has_icl else 0)
    else:
        n_dc, n_sk, n_icl = _split_k_across_arms(num_variants, has_icl)
    if not has_icl:
        n_icl = 0

    workers = max(1, max(max_workers, n_dc + n_sk + n_icl))
    out = deepeye_fullsql_fair_plugin(
        db_path=db_path,
        schema=schema or "",
        question=question,
        evidence=evidence or "",
        llm_config=llm_config,
        few_shots=few_shots,
        value_retrieval_hint=combined_vr,
        n_dc=n_dc,
        n_skeleton=n_sk,
        n_icl=n_icl,
        n_rounds=1,
        filter_top_k=2,
        evaluator_votes=_int_env("MCTS_FINAL_DEEPEYE_BR_VOTES", 5),
        checker_budget=_int_env("MCTS_FINAL_DEEPEYE_CHECKER_BUDGET", 3),
        max_workers=workers,
        revise_max_unique=_int_env("MCTS_FINAL_DEEPEYE_REVISE_UNIQUE", 0),
        progress_prefix="[final-deepeye-plugin]",
    )
    sqls: List[str] = []
    seen = set()
    winner = (out.get("winner_sql") or "").strip()
    if winner:
        sqls.append(winner)
        seen.add(re.sub(r"\s+", " ", winner.lower()))
    for s in out.get("pool_sqls") or []:
        s = (s or "").strip()
        if not s:
            continue
        key = re.sub(r"\s+", " ", s.lower())
        if key in seen:
            continue
        sqls.append(s)
        seen.add(key)
        if len(sqls) >= max(1, num_variants):
            break
    audit = {
        "mode": "final_deepeye_fullsql_plugin",
        "n_returned": len(sqls),
        "n_dc": n_dc,
        "n_skeleton": n_sk,
        "n_icl": n_icl,
        "path_block": "existing_cte_and_results",
        "selection_mode": (out.get("audit") or {}).get("selection_mode"),
        "n_clusters": len(out.get("clusters") or []),
        "winner_consistency": out.get("winner_consistency"),
        "plugin_audit": out.get("audit") or {},
    }
    return sqls, audit


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


def _llm_n(
    prompt: str,
    *,
    client,
    model: str,
    temperature: float,
    n: int,
) -> List[str]:
    """Official-style: one prompt, fixed temperature, n completions."""
    n = max(1, int(n))
    try:
        resp = create_chat_completion(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=float(temperature),
            n=n,
        )
        outs = []
        for ch in resp.choices or []:
            outs.append((ch.message.content or "") if ch.message else "")
        if len(outs) < n and n > 1:
            for _ in range(n - len(outs)):
                outs.append(
                    _llm(prompt, client=client, model=model, temperature=temperature)
                )
        return outs[:n] if outs else [""] * n
    except Exception:
        return [
            _llm(prompt, client=client, model=model, temperature=temperature)
            for _ in range(n)
        ]


def _llm_max_model_len(llm_config: Optional[dict]) -> int:
    max_model_len = 128000
    if llm_config:
        try:
            max_model_len = int(llm_config.get("max_model_len") or max_model_len)
        except Exception:
            pass
    return max(4096, int(max_model_len))


def _max_prompt_chars(llm_config: Optional[dict]) -> int:
    max_model_len = _llm_max_model_len(llm_config)
    max_tokens = 4096
    if llm_config:
        cl = (llm_config.get("config_list") or [{}])[0]
        try:
            max_tokens = int(
                cl.get("max_tokens") or llm_config.get("max_tokens") or max_tokens
            )
        except Exception:
            pass
    return max(8000, (max_model_len - max_tokens) * 3)


def _fit_prompt_with_schema(
    format_fn,
    schema: str,
    *,
    max_prompt_chars: int,
    linked_schema: Optional[Dict[str, Any]] = None,
    encoding_model_name: Optional[str] = None,
) -> Tuple[str, int]:
    """Progressive schema fit: prefer official 4-level semantic strip when linked_schema given."""
    if linked_schema:
        try:
            from workflows.mcts_v4.actions.deepeye_plugin import (
                fit_prompt_with_official_schema_strip,
            )

            # Official API is token-based; chars≈3×tokens is a safe upper bound inverse.
            max_tokens = max(2048, int(max_prompt_chars) // 3)
            prompt, level = fit_prompt_with_official_schema_strip(
                linked_schema,
                format_fn,
                encoding_model_name=encoding_model_name or "gpt-4o",
                max_prompt_len=max_tokens,
                item_id="fair_gen",
            )
            if prompt:
                return prompt, int(level)
        except Exception:
            pass
    fracs = [1.0, 0.85, 0.7, 0.55, 0.4, 0.3, 0.2, 0.12, 0.08]
    last_prompt = ""
    for level, frac in enumerate(fracs):
        sch = schema if frac >= 0.999 else schema[: max(800, int(len(schema) * frac))]
        prompt = format_fn(sch)
        last_prompt = prompt
        if len(prompt) <= max_prompt_chars:
            return prompt, level
    return last_prompt[:max_prompt_chars], len(fracs)


_IDENT = r'(?:`[^`]+`|\[[^\]]+\]|"[^"]+"|[\w\.]+)'


def _suggestion_join(sql: str) -> Optional[str]:
    join_pattern = re.compile(
        rf"JOIN\s+{_IDENT}(\s+AS\s+{_IDENT}){{0,1}}\s+ON(\s+{_IDENT}\.{_IDENT}\s*"
        rf"(=\s*{_IDENT}\.{_IDENT}(?:\s+OR\s+{_IDENT}\.{_IDENT}\s*=\s*{_IDENT}\.{_IDENT})+"
        rf"|IN\s+\(.*?\)))",
        re.IGNORECASE | re.DOTALL,
    )
    if join_pattern.findall(sql):
        return (
            "The SQL uses the JOIN function incorrectly, due to using "
            "`JOIN table AS T ON Ta.column1 = Tb.column2 OR Ta.column1 = Tb.column3` or "
            "`JOIN table AS T ON Ta.column1 IN`, please only keep the highest priority group of "
            "`Ta.column = Tb.column` in `OR`."
        )
    return None


def _suggestion_order_by_limit(sql: str) -> Optional[str]:
    order_by_pattern = re.compile(
        rf"ORDER BY ((MIN|MAX)\(\s*({_IDENT})\s*\)).*? LIMIT \d+",
        re.IGNORECASE | re.DOTALL,
    )
    res = order_by_pattern.search(sql)
    if not res:
        return None
    return (
        f"The SQL uses the ORDER BY function incorrectly, using MIN/MAX in ORDER BY clause "
        f"is incorrect (`{res.group()}`), please correct the SQL. If the SQL contains GROUP BY, "
        f"please judge whether the content of `{res.groups()[0]}` needs to use "
        f"`SUM({res.groups()[2]})`."
    )


def _fix_time(sql: str) -> Optional[str]:
    res = re.sub(
        r"(strftime *\([^\(]*?\) *[>=<]+ *)(\d{4,})",
        r"\1'\2'",
        sql,
    )
    return res if res != sql else None


def _suggestion_select(sql: str) -> Tuple[str, Optional[str]]:
    """Returns (possibly rewritten sql, suggestion)."""
    cur = sql
    if re.findall(r"^SELECT.*?\|\| ' ' \|\| .*?FROM", cur, re.IGNORECASE | re.DOTALL | re.MULTILINE):
        cur = cur.replace("|| ' ' ||", ", ").replace("|| ', ' ||", ", ")
    select_amb = re.findall(
        rf"^SELECT.*? ({_IDENT}\.\*).*?FROM",
        cur,
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    if not select_amb:
        return cur, None
    suggestion = ""
    for idx, x in enumerate(select_amb, 1):
        suggestion += (
            f"{idx}. We have specified that the ambiguous query is the corresponding id column, "
            f"please replace {x} with the corresponding id column in the above SQL\n"
        )
    return cur, suggestion


def _suggestion_max_min(sql: str) -> Optional[str]:
    max_min_pattern = re.compile(
        rf"=\s*\(\s*SELECT\s*(MAX|MIN)\s*\(\s*({_IDENT})\s*\)\s*FROM\s*({_IDENT})",
        re.IGNORECASE | re.DOTALL,
    )
    fun_amb = max_min_pattern.findall(sql)
    order_amb = set(re.findall(r"= (\(SELECT .* LIMIT \d\))", sql, re.IGNORECASE | re.DOTALL))
    select_amb_pattern = re.compile(
        rf"^SELECT[^\(\)]*? ((MIN|MAX)\(\s*{_IDENT}\s*\)).*?LIMIT 1",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    select_amb = set(select_amb_pattern.findall(sql))
    suggestions: List[str] = []
    for fun in fun_amb:
        fuc, col, table = fun
        order = "DESC" if fuc.upper() == "MAX" else "ASC"
        suggestions.append(
            f"WHERE {col} = (SELECT {fuc}({col}) FROM {table}): "
            f"Please use ORDER BY {table}.{col} {order} LIMIT 1 instead of nested SQL"
        )
    for fun in order_amb:
        suggestions.append(f"{fun}: Please use JOIN instead of nested SQL")
    for fun in select_amb:
        suggestions.append(
            f"{fun[0]}: {fun[1]} function is redundant due to LIMIT clause, "
            "please use ORDER BY + LIMIT instead"
        )
    if not suggestions:
        return None
    return "\n".join(f"{i+1}. {s}" for i, s in enumerate(suggestions))


def _suggestion_order_by_null(sql: str) -> Optional[str]:
    inn = re.findall(r"ORDER BY .*?(?<!DESC )LIMIT +\d+;{0,1}", sql)
    if not inn:
        return None
    for x in inn:
        if re.findall(r"SUM\(|COUNT\(", x):
            return None
    suggestion = ""
    for x in inn:
        suggestion += (
            f"Please add `IS NOT NULL` condition **in the WHERE clause** "
            f"for the ORDER BY column: {x}\n"
        )
    return suggestion


def _llm_common_fix(
    *,
    sql: str,
    schema: str,
    question: str,
    evidence: str,
    suggestion: str,
    client,
    model: str,
    PF,
) -> str:
    prompt = PF.format_common_checker_prompt(
        schema[:8000],
        question,
        evidence or "None",
        sql[:2500],
        suggestion,
    )
    text = _llm(prompt, client=client, model=model, temperature=0.3)
    return _extract_sql(text) or sql


def _llm_exec_fix(
    *,
    sql: str,
    schema: str,
    question: str,
    evidence: str,
    err: str,
    client,
    model: str,
    PF,
) -> str:
    # Avoid hard schema/sql chops that drop Value Examples needed for empty→LIKE fixes.
    sch = schema if len(schema) <= 24000 else schema[:24000]
    prompt = PF.format_execution_checker_prompt(
        sch,
        question,
        evidence or "None",
        sql,
        err if str(err).startswith("ERROR:") or "empty result" in str(err).lower() or "null" in str(err).lower()
        else f"ERROR: {err}",
    )
    text = _llm(prompt, client=client, model=model, temperature=0.7)
    return _extract_sql(text) or sql


def _revise_full_sql_simple(
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
    exec_timeout_s: float = 30.0,
) -> Tuple[str, Dict[str, Any]]:
    """Legacy: exec-fail → execution_checker; exec-ok → one generic common_checker."""
    audit: Dict[str, Any] = {"mode": "simple_exec_common", "rounds": [], "final_ok": False}
    orig = (sql or "").strip().rstrip(";")
    cur = orig
    hint = evidence or "None"
    to = float(exec_timeout_s)

    for round_i in range(max(1, checker_budget)):
        rows, err = exec_rows(db_path, cur, timeout_s=to)
        if err is None:
            try:
                fixed = _llm_common_fix(
                    sql=cur,
                    schema=schema,
                    question=question,
                    evidence=hint,
                    suggestion="Check JOIN/SELECT/NULL/ORDER issues; return one complete SQLite SQL.",
                    client=client,
                    model=model,
                    PF=PF,
                )
                if fixed:
                    rows2, err2 = exec_rows(db_path, fixed, timeout_s=to)
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
            fixed = _llm_exec_fix(
                sql=cur,
                schema=schema,
                question=question,
                evidence=hint,
                err=str(err),
                client=client,
                model=model,
                PF=PF,
            )
            if fixed and fixed != cur:
                cur = fixed
                audit["rounds"].append({"round": round_i, "mode": "exec_fix", "err": (err or "")[:120]})
                continue
        except Exception as e:
            audit["rounds"].append({"round": round_i, "mode": "exec_fix_err", "error": str(e)[:120]})
            break
        audit["rounds"].append({"round": round_i, "mode": "exec_fail", "err": (err or "")[:120]})
        break

    _, err = exec_rows(db_path, cur, timeout_s=to)
    audit["final_ok"] = err is None
    # Official-style: keep last SQL (never drop candidate from pool).
    return (cur or orig), audit


def _revise_full_sql_full8(
    *,
    sql: str,
    db_path: Path,
    schema: str,
    question: str,
    evidence: str,
    client,
    model: str,
    PF,
    checker_budget: int = 1,
    exec_timeout_s: float = OFFICIAL_SQL_EXEC_TIMEOUT_S,
) -> Tuple[str, Dict[str, Any]]:
    """Official DeepEye order (rule detectors + LLM revise), without DeepEye service stack.

    Order: Syntax → Join → OrderByLimit → Time → Select → MaxMin → OrderByNull → Result.
    On failure, keep last SQL (official checkers return original), do not drop candidate.
    """
    audit: Dict[str, Any] = {"mode": "full8_official_order", "rounds": [], "final_ok": False}
    orig = (sql or "").strip().rstrip(";")
    cur = orig
    hint = evidence or "None"
    budget = max(1, int(checker_budget))
    to = float(exec_timeout_s)

    def _exec_ok(s: str) -> Tuple[bool, Optional[str]]:
        _, err = exec_rows(db_path, s, timeout_s=to)
        return err is None, err

    # 1) SyntaxChecker
    ok, err = _exec_ok(cur)
    if not ok:
        for bi in range(budget):
            try:
                fixed = _llm_exec_fix(
                    sql=cur, schema=schema, question=question, evidence=hint,
                    err=str(err), client=client, model=model, PF=PF,
                )
                if fixed:
                    cur = fixed
                    audit["rounds"].append({"checker": "SyntaxChecker", "try": bi})
                ok, err = _exec_ok(cur)
                if ok:
                    break
            except Exception as e:
                audit["rounds"].append({"checker": "SyntaxChecker", "error": str(e)[:120]})
                break

    # 2) JoinChecker
    sug = _suggestion_join(cur)
    if sug:
        try:
            cur = _llm_common_fix(
                sql=cur, schema=schema, question=question, evidence=hint,
                suggestion=sug, client=client, model=model, PF=PF,
            )
            audit["rounds"].append({"checker": "JoinChecker", "fired": True})
        except Exception as e:
            audit["rounds"].append({"checker": "JoinChecker", "error": str(e)[:120]})

    # 3) OrderByLimitChecker
    sug = _suggestion_order_by_limit(cur)
    if sug:
        try:
            cur = _llm_common_fix(
                sql=cur, schema=schema, question=question, evidence=hint,
                suggestion=sug, client=client, model=model, PF=PF,
            )
            audit["rounds"].append({"checker": "OrderByLimitChecker", "fired": True})
        except Exception as e:
            audit["rounds"].append({"checker": "OrderByLimitChecker", "error": str(e)[:120]})

    # 4) TimeChecker (deterministic)
    fixed_t = _fix_time(cur)
    if fixed_t:
        cur = fixed_t
        audit["rounds"].append({"checker": "TimeChecker", "fired": True})

    # 5) SelectChecker
    cur2, sug = _suggestion_select(cur)
    cur = cur2
    if sug:
        try:
            cur = _llm_common_fix(
                sql=cur, schema=schema, question=question, evidence=hint,
                suggestion=sug, client=client, model=model, PF=PF,
            )
            audit["rounds"].append({"checker": "SelectChecker", "fired": True})
        except Exception as e:
            audit["rounds"].append({"checker": "SelectChecker", "error": str(e)[:120]})

    # 6) MaxMinChecker
    sug = _suggestion_max_min(cur)
    if sug:
        try:
            cur = _llm_common_fix(
                sql=cur, schema=schema, question=question, evidence=hint,
                suggestion=sug, client=client, model=model, PF=PF,
            )
            audit["rounds"].append({"checker": "MaxMinChecker", "fired": True})
        except Exception as e:
            audit["rounds"].append({"checker": "MaxMinChecker", "error": str(e)[:120]})

    # 7) OrderByNullChecker
    sug = _suggestion_order_by_null(cur)
    if sug:
        try:
            cur = _llm_common_fix(
                sql=cur, schema=schema, question=question, evidence=hint,
                suggestion=sug, client=client, model=model, PF=PF,
            )
            audit["rounds"].append({"checker": "OrderByNullChecker", "fired": True})
        except Exception as e:
            audit["rounds"].append({"checker": "OrderByNullChecker", "error": str(e)[:120]})

    # 8) ResultChecker — official: empty/all_null/error all trigger; only non-empty success accepts.
    rows_r, err_r = exec_rows(db_path, cur, timeout_s=to)
    empty = err_r is None and (not rows_r)
    all_null = False
    if err_r is None and rows_r:
        all_null = not any(any(v is not None for v in (row if isinstance(row, (list, tuple)) else [row])) for row in rows_r)
    need_result = (err_r is not None) or empty or all_null
    if not need_result:
        audit["rounds"].append({"checker": "ResultChecker", "fired": False, "note": "success"})
    else:
        trig = "error" if err_r else ("empty" if empty else "all_null")
        result_msg = (
            str(err_r)
            if err_r
            else (
                "The SQL query returned an empty result table."
                if empty
                else "The SQL query returned an result table with all null values."
            )
        )
        for bi in range(budget):
            try:
                fixed = _llm_exec_fix(
                    sql=cur, schema=schema, question=question, evidence=hint,
                    err=result_msg, client=client, model=model, PF=PF,
                )
                if fixed:
                    rows2, err2 = exec_rows(db_path, fixed, timeout_s=to)
                    # Official ResultChecker: only accept non-empty success.
                    if err2 is None and rows2 and any(
                        any(v is not None for v in (row if isinstance(row, (list, tuple)) else [row]))
                        for row in rows2
                    ):
                        cur = fixed
                        audit["rounds"].append(
                            {"checker": "ResultChecker", "try": bi, "fired": True, "from": trig}
                        )
                        break
                    audit["rounds"].append(
                        {"checker": "ResultChecker", "try": bi, "reject": "not_nonempty_success", "from": trig}
                    )
            except Exception as e:
                audit["rounds"].append({"checker": "ResultChecker", "error": str(e)[:120]})
                break

    rows_f, err_f = exec_rows(db_path, cur, timeout_s=to)
    audit["final_ok"] = err_f is None and bool(rows_f)
    # Keep last SQL even if still failing — official selection still sees the candidate.
    return (cur or orig), audit


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
    full_checkers: bool = False,
    exec_timeout_s: float = 30.0,
    linked_schema: Optional[Dict[str, Any]] = None,
    question_id: Optional[int] = None,
    database_id: Optional[str] = None,
    extra_evidence: str = "",
    max_model_len: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    if full_checkers:
        # Prefer official BaseChecker chain (or legacy import) via deepeye_plugin.
        try:
            from workflows.mcts_v4.actions.deepeye_plugin import (
                official_revise_enabled,
                revise_full_sql_official,
            )

            if official_revise_enabled():
                def _exec_fn(p: Path, s: str):
                    return exec_rows(p, s, timeout_s=float(exec_timeout_s))

                fixed, raudit = revise_full_sql_official(
                    sql=sql,
                    question=question,
                    evidence=evidence,
                    schema=schema,
                    db_path=db_path,
                    client=client,
                    model=model,
                    exec_fn=_exec_fn,
                    sampling_budget=max(1, int(checker_budget)),
                    linked_schema=linked_schema,
                    question_id=question_id,
                    database_id=database_id,
                    max_model_len=max_model_len,
                    exec_timeout_s=float(exec_timeout_s),
                    extra_evidence=extra_evidence,
                )
                audit = {
                    "mode": getattr(raudit, "mode", "") or "official_revise_import",
                    "n_llm_calls": getattr(raudit, "n_llm_calls", 0),
                    "fallback": getattr(raudit, "fallback", "") or "",
                    "events": [
                        {
                            "checker": getattr(e, "checker", ""),
                            "action": getattr(e, "action", ""),
                            "note": getattr(e, "note", "")[:160],
                        }
                        for e in (getattr(raudit, "events", None) or [])
                    ],
                    "final_ok": True,
                }
                return (fixed or sql or "").strip(), audit
        except Exception as e:
            # Fall back to in-plugin full8 order if import/revise fails.
            fallback_note = str(e)[:200]
        else:
            fallback_note = ""
        out_sql, audit = _revise_full_sql_full8(
            sql=sql,
            db_path=db_path,
            schema=schema,
            question=question,
            evidence=evidence,
            client=client,
            model=model,
            PF=PF,
            checker_budget=max(1, checker_budget),
            exec_timeout_s=exec_timeout_s,
        )
        if fallback_note:
            audit["official_revise_fallback"] = fallback_note
        return out_sql, audit
    return _revise_full_sql_simple(
        sql=sql,
        db_path=db_path,
        schema=schema,
        question=question,
        evidence=evidence,
        client=client,
        model=model,
        PF=PF,
        checker_budget=checker_budget,
        exec_timeout_s=exec_timeout_s,
    )


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
    checker_budget: int = 3,
    max_workers: int = 6,
    revise_max_unique: int = 0,
    progress_prefix: str = "",
    full_checkers: bool = False,
    gen_temperature: float = 0.7,
    exec_timeout_s: Optional[float] = None,
    official_select: bool = True,
    linked_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Full-SQL arm aligned with DeepEye bird defaults.

    - gen: fixed temperature (0.7), n completions per arm (not temp ladder)
    - evidence HINT = evidence only
    - schema: prefer official SchemaService profile (via load_deepeye_context /
      deepeye_plugin); VR footer only when not already embedded
    - progressive schema fit: official 4-level strip when linked_schema given
    - revise: official rule-checker import when full_checkers (deepeye_plugin)
    - revise keeps last SQL even if still failing (official keep-candidate)
    - selection: official hash / non-empty prefer / BR win-matrix + TIE
    """
    t0 = time.time()
    client, model = _client_model(llm_config)
    PF = _load_deepeye_prompt_factory()
    evidence_s = (evidence or "").strip() or "None"
    schema_profile = (schema or "").strip()
    vr = (value_retrieval_hint or "").strip()
    # schema_s used by revise/selection; includes optional mid/VR footer.
    if vr:
        schema_s = (
            f"{schema_profile}\n\n-- Value retrieval / mid context:\n{vr}"
            if schema_profile
            else f"-- Value retrieval / mid context:\n{vr}"
        )
    else:
        schema_s = schema_profile
    pref = progress_prefix or "[fullsql-B]"
    n_rounds = max(1, int(n_rounds))
    if exec_timeout_s is None:
        exec_to = OFFICIAL_SQL_EXEC_TIMEOUT_S if full_checkers else 30.0
    else:
        exec_to = float(exec_timeout_s)
    gen_temperature = float(gen_temperature)
    max_prompt_chars = _max_prompt_chars(llm_config)
    if not full_checkers:
        full_checkers = os.environ.get("MCTS_FINAL_DEEPEYE_FULL_CHECKERS", "0").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    strip_levels: Dict[str, int] = {}

    def _dc_prompt(sch: str) -> str:
        return PF.format_dc_sql_generation_prompt(sch, question, evidence_s)

    def _sk_prompt(sch: str) -> str:
        return PF.format_skeleton_sql_generation_prompt(sch, question, evidence_s)

    def _icl_prompt(sch: str) -> str:
        return PF.format_icl_sql_generation_prompt(
            few_shots or [], sch, question, evidence_s
        )

    arms: List[Tuple[str, Any, int]] = []
    if n_dc > 0:
        arms.append(("dc", _dc_prompt, max(0, int(n_dc))))
    if n_skeleton > 0:
        arms.append(("skeleton", _sk_prompt, max(0, int(n_skeleton))))
    if n_icl > 0 and few_shots:
        arms.append(("icl", _icl_prompt, max(0, int(n_icl))))

    arm_jobs: List[Tuple[str, str, int]] = []
    for kind, fmt, n_samp in arms:
        if n_samp <= 0:
            continue
        if linked_schema and vr:

            def _fmt_with_vr(sch: str, _fmt=fmt) -> str:
                return f"{_fmt(sch)}\n\n-- Value retrieval / mid context:\n{vr}"

            fit_fmt = _fmt_with_vr
            fit_schema = schema_profile
        else:
            fit_fmt = fmt
            fit_schema = schema_s
        prompt, level = _fit_prompt_with_schema(
            fit_fmt,
            fit_schema,
            max_prompt_chars=max_prompt_chars,
            linked_schema=linked_schema,
            encoding_model_name=model,
        )
        strip_levels[kind] = level
        for _r in range(n_rounds):
            arm_jobs.append((kind, prompt, n_samp))

    print(
        f"  {pref} gen start arms={len(arm_jobs)} rounds={n_rounds} "
        f"temp={gen_temperature} strip={strip_levels} "
        f"workers={min(max_workers, max(1, len(arm_jobs)))}",
        flush=True,
    )
    raw_items: List[Dict[str, Any]] = []

    def _gen_arm(kind: str, prompt: str, n_samp: int) -> List[Dict[str, Any]]:
        texts = _llm_n(
            prompt,
            client=client,
            model=model,
            temperature=gen_temperature,
            n=n_samp,
        )
        out_items = []
        for text in texts:
            try:
                sql = _extract_sql(text)
                out_items.append(
                    {
                        "kind": kind,
                        "temp": gen_temperature,
                        "sql": sql,
                        "ok": bool(sql),
                    }
                )
            except Exception as e:
                out_items.append(
                    {
                        "kind": kind,
                        "temp": gen_temperature,
                        "sql": "",
                        "ok": False,
                        "error": str(e),
                    }
                )
        return out_items

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
        scored = []
        for it in uniq:
            rows, err = exec_rows(db_path, it["sql"], timeout_s=exec_to)
            scored.append((0 if err is None else 1, it))
        scored.sort(key=lambda x: x[0])
        uniq = [it for _, it in scored[:revise_max_unique]]

    print(
        f"  {pref} revise start unique={len(uniq)} timeout={exec_to:.0f}s "
        f"official_select={bool(official_select)}",
        flush=True,
    )
    revise_extra = ""
    if vr:
        # BaseChecker builds schema from linked dict; keep mid/VR via evidence.
        revise_extra = f"-- Value retrieval / mid context:\n{vr}"
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
            full_checkers=full_checkers,
            exec_timeout_s=exec_to,
            linked_schema=linked_schema,
            extra_evidence=revise_extra,
            max_model_len=_llm_max_model_len(llm_config),
        )
        # Always keep a SQL candidate (official revise never drops from pool).
        it["sql"] = (fixed or it.get("sql") or "").strip()
        _, err_chk = exec_rows(db_path, it["sql"], timeout_s=exec_to)
        it["exec_ok"] = err_chk is None
        it["revise"] = raudit
        if it["sql"]:
            revised.append(it)
        if (ui + 1) == len(uniq) or (ui + 1) % 4 == 0:
            print(
                f"  {pref} revise progress {ui+1}/{len(uniq)} kept={len(revised)}",
                flush=True,
            )

    selection_mode = "empty"
    winner = None
    br_audit: List[dict] = []
    clusters: List[Dict[str, Any]] = []

    if official_select:
        valid_pairs: List[Tuple[str, Any, List[tuple], str, float]] = []
        fallback_pairs: List[Tuple[str, Any, List[tuple], str, float]] = []
        for it in revised:
            sql = (it.get("sql") or "").strip()
            if not sql:
                continue
            t_exec0 = time.time()
            rows, err = exec_rows(db_path, sql, timeout_s=exec_to)
            exec_t = time.time() - t_exec0
            if err is not None or rows is None:
                continue
            h = _official_result_hash(rows)
            preview = format_result_preview(rows, None)
            item = (sql, h, rows, preview, exec_t)
            fallback_pairs.append(item)
            if len(rows) > 0:
                valid_pairs.append(item)
        use_pairs = valid_pairs if valid_pairs else fallback_pairs
        if not use_pairs:
            selection_mode = "empty"
            if revised and (revised[0].get("sql") or "").strip():
                winner = {
                    "sig": "fallback0",
                    "sql": revised[0]["sql"],
                    "size": 1,
                    "consistency": 0.0,
                    "sources": [revised[0].get("kind") or "?"],
                    "rows_preview": "",
                }
                selection_mode = "fallback_first_revised"
        else:
            ctr = Counter(h for _, h, _, _, _ in use_pairs)
            n_valid = len(use_pairs)
            dedup: List[Dict[str, Any]] = []
            seen_h = set()
            for sql, h, rows, preview, exec_t in use_pairs:
                if h in seen_h:
                    continue
                seen_h.add(h)
                cons = ctr[h] / float(n_valid)
                dedup.append(
                    {
                        "sig": repr(h)[:160],
                        "hash": h,
                        "sql": sql,
                        "size": int(ctr[h]),
                        "consistency": cons,
                        "exec_time": float(exec_t) if exec_t is not None else float("inf"),
                        "rows_preview": preview,
                        "sources": [],
                    }
                )
            for it in revised:
                sql = (it.get("sql") or "").strip()
                for c in dedup:
                    if c["sql"] == sql:
                        c["sources"].append(it.get("kind") or "?")
                        break
            dedup.sort(key=lambda c: (c["consistency"], -c["exec_time"]), reverse=True)
            clusters = dedup
            top = clusters[: max(1, int(filter_top_k))]
            if len(top) == 1:
                winner = top[0]
                selection_mode = "only_one"
            elif top[0]["consistency"] >= float(shortcut_threshold):
                winner = top[0]
                selection_mode = "shortcut_consistency"
            else:
                k = len(top)
                n_votes = max(1, int(evaluator_votes))
                win_matrix = np.zeros((k, k, n_votes), dtype=float)
                pairs = [(i, j) for i in range(k) for j in range(i + 1, k)]
                br_schema = schema_s if len(schema_s) <= 12000 else schema_s[:12000]
                any_ok = False
                for i, j in pairs:
                    a, b = top[i], top[j]
                    votes: List[str] = []
                    try:
                        prompt = PF.format_br_pair_selection_prompt(
                            br_schema,
                            question,
                            evidence_s,
                            a["sql"][:1800],
                            a.get("rows_preview") or "",
                            b["sql"][:1800],
                            b.get("rows_preview") or "",
                        )
                        for _vi in range(n_votes):
                            text = _llm(
                                prompt,
                                client=client,
                                model=model,
                                temperature=0.7,
                            )
                            votes.append(_parse_ab_tie(text))
                        any_ok = True
                    except Exception as e:
                        br_audit.append({"pair": (i, j), "error": str(e)[:160]})
                        continue
                    br_audit.append({"pair": (i, j), "votes": list(votes)})
                    for vi, vote in enumerate(votes):
                        if vote == "A":
                            win_matrix[i, j, vi] = 1.0
                            win_matrix[j, i, vi] = 0.0
                        elif vote == "B":
                            win_matrix[j, i, vi] = 1.0
                            win_matrix[i, j, vi] = 0.0
                        else:
                            win_matrix[i, j, vi] = 0.5
                            win_matrix[j, i, vi] = 0.5
                if not any_ok:
                    winner = top[0]
                    selection_mode = "br_fail_top1"
                else:
                    robust = np.zeros((k, k), dtype=float)
                    for i in range(k):
                        for j in range(k):
                            if i != j:
                                robust[i, j] = float(np.mean(win_matrix[i, j, :]))
                    ranking_scores = np.mean(robust, axis=1)
                    cons_w = np.array([c["consistency"] for c in top], dtype=float)
                    cons_w = cons_w / max(float(cons_w.sum()), 1e-12)
                    ranking_scores = ranking_scores * cons_w
                    best_i = int(np.argmax(ranking_scores))
                    winner = top[best_i]
                    selection_mode = "br_win_matrix"
    else:
        clusters_map: Dict[str, Dict[str, Any]] = {}
        for it in revised:
            sql = (it.get("sql") or "").strip()
            rows, err = exec_rows(db_path, sql, timeout_s=exec_to)
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
                        br_schema = schema_s if len(schema_s) <= 12000 else schema_s[:12000]
                        prompt = PF.format_br_pair_selection_prompt(
                            br_schema,
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
        "winner_consistency": (round(float(winner.get("consistency") or 0.0), 4) if winner else 0.0),
        "clusters": [
            {
                "sig": str(c.get("sig") or "")[:120],
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
            "n_icl": n_icl if (n_icl > 0 and few_shots) else 0,
            "n_gen_jobs": len(arm_jobs),
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
            "full_checkers": bool(full_checkers),
            "revise_max_unique": revise_max_unique,
            "gen_temperature": gen_temperature,
            "exec_timeout_s": exec_to,
            "official_select": bool(official_select),
            "schema_strip_levels": strip_levels,
            "br_pairs": br_audit,
            "elapsed_s": round(elapsed, 2),
            "sample_gen_error": (str(err_sample)[:200] if err_sample else None),
        },
    }
