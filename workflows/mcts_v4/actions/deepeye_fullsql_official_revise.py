#!/usr/bin/env python3
"""Full-SQL revise that mirrors DeepEye's official rule → common_checker order.

Two modes (env ``MCTS_DEEPEYE_REVISE_MODE``):

* ``basechecker`` (default): real ``BaseChecker.check_and_revise`` with
  official ``DataItem`` + ``LLM`` + ``LLMExtractor`` + progressive schema strip
  + ``ExecutionService``. Requires ``linked_schema`` dict; falls back to legacy
  if missing or on hard error.
* ``legacy``: previous ``__new__`` rule-steal path (own LLM + soft schema cap).

Does NOT invent vague suggestions. For Join/Select/OrderBy*/MaxMin/Time:
  1) run the official regex/rule `_check_*`
  2) only if suggestion is non-empty, call PromptFactory.common_checker (n=1)
For Syntax/Result:
  1) execute SQL and classify like DeepEye SQLExecutionResult
  2) Syntax: skip success|empty|all_null; else execution_checker (n=budget)
  3) Result: skip ONLY success (non-empty); empty/all_null/error → execution_checker
     and only accept non-empty success candidates (majority hash)

Critical parity note: ResultChecker treating empty as OK was a local bug that
blocked literal→LIKE fixes (e.g. BIRD qid 871).
"""

from __future__ import annotations

import copy
import os
import re
import sys
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import OpenAI

DEEPEYE_ROOT = "/hpc2hdd/home/sshen190/wtao565/related_project/DeepEye-SQL"
if DEEPEYE_ROOT not in sys.path:
    sys.path.insert(0, DEEPEYE_ROOT)

# Official revise LLM temperature (config sql_revision.llm.temperature).
_REVISE_TEMP = 0.7

_services_lock = threading.Lock()
_services_ready = False
_checkers_lock = threading.Lock()
_checkers: Optional[List[Any]] = None
_llm_cache_lock = threading.Lock()
_llm_cache: Dict[Tuple[str, str, str, int], Any] = {}


def _revise_mode() -> str:
    raw = (os.environ.get("MCTS_DEEPEYE_REVISE_MODE") or "basechecker").strip().lower()
    if raw in ("legacy", "new", "steal", "local"):
        return "legacy"
    return "basechecker"


def _load_pf():
    from app.prompt.factory import PromptFactory  # type: ignore

    return PromptFactory


def _load_rule_checkers():
    """Import official rule helpers without constructing BaseChecker (avoids schema_service)."""
    from app.pipeline.sql_revision.checkers.join_checker import JoinChecker  # type: ignore
    from app.pipeline.sql_revision.checkers.order_by_limit_checker import (  # type: ignore
        OrderByLimitChecker,
    )
    from app.pipeline.sql_revision.checkers.time_checker import TimeChecker  # type: ignore
    from app.pipeline.sql_revision.checkers.select_checker import SelectChecker  # type: ignore
    from app.pipeline.sql_revision.checkers.max_min_checker import MaxMinChecker  # type: ignore
    from app.pipeline.sql_revision.checkers.order_by_null_checker import (  # type: ignore
        OrderByNullChecker,
    )

    return [
        ("JoinChecker", JoinChecker.__new__(JoinChecker), "join"),
        ("OrderByLimitChecker", OrderByLimitChecker.__new__(OrderByLimitChecker), "order_by_limit"),
        ("TimeChecker", TimeChecker.__new__(TimeChecker), "time"),
        ("SelectChecker", SelectChecker.__new__(SelectChecker), "select"),
        ("MaxMinChecker", MaxMinChecker.__new__(MaxMinChecker), "max_min"),
        ("OrderByNullChecker", OrderByNullChecker.__new__(OrderByNullChecker), "order_by_null"),
    ]


def _extract_sql(text: str) -> str:
    """Prefer official <result> tag; fallback to fenced/raw SQL."""
    raw = (text or "").strip()
    if not raw:
        return ""
    m = re.search(r"<result>\s*([\s\S]*?)\s*</result>", raw, re.I)
    if m:
        raw = m.group(1).strip()
        if raw.startswith("```sql") and raw.endswith("```"):
            raw = raw[len("```sql") : -len("```")].strip()
        elif raw.startswith("```") and raw.endswith("```"):
            raw = raw[3:-3].strip()
        return raw.strip().rstrip(";")
    m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", raw, re.I)
    if m:
        raw = m.group(1).strip()
    return raw.strip().rstrip(";")


def _llm(client: OpenAI, model: str, prompt: str, temperature: float = _REVISE_TEMP) -> str:
    from workflows.mcts_v4.utils.llm_chat import create_chat_completion

    resp = create_chat_completion(
        client,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=float(temperature),
    )
    return (resp.choices[0].message.content or "") if resp.choices else ""


def _llm_n(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    n: int,
    temperature: float = _REVISE_TEMP,
) -> List[str]:
    from workflows.mcts_v4.utils.llm_chat import create_chat_completion

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
        while len(outs) < n:
            outs.append(_llm(client, model, prompt, temperature=temperature))
        return outs[:n]
    except Exception:
        return [_llm(client, model, prompt, temperature=temperature) for _ in range(n)]


@dataclass
class ReviseEvent:
    checker: str
    action: str  # skip_ok | skip_no_rule | llm_common | llm_execution | keep_after_fail | basechecker
    suggestion: str = ""
    sql_before: str = ""
    sql_after: str = ""
    exec_err: str = ""
    note: str = ""


@dataclass
class ReviseAudit:
    events: List[ReviseEvent] = field(default_factory=list)
    final_sql: str = ""
    n_llm_calls: int = 0
    mode: str = ""
    fallback: str = ""


def _rule_suggestion(name: str, checker_obj: Any, kind: str, sql: str) -> Optional[str]:
    if kind == "join":
        return checker_obj._check_join(sql)
    if kind == "order_by_limit":
        return checker_obj._check_order_by_limit(sql)
    if kind == "time":
        return checker_obj._check_time(sql)
    if kind == "select":
        return checker_obj._check_select(sql)
    if kind == "max_min":
        return checker_obj._check_max_min(sql)
    if kind == "order_by_null":
        return checker_obj._check_order_by_null(sql)
    raise ValueError(kind)


def _classify_exec(rows: Optional[list], err: Optional[str]) -> str:
    """Mirror DeepEye SQLExecutionResult.result_type."""
    if err is not None or rows is None:
        msg = (err or "").lower()
        if "interrupt" in msg or "timeout" in msg or "timed out" in msg:
            return "timeout"
        return "execution_error"
    if len(rows) == 0:
        return "empty_result"
    def _row_vals(row: Any) -> List[Any]:
        if isinstance(row, (list, tuple)):
            return list(row)
        return [row]

    if not any(any(v is not None for v in _row_vals(row)) for row in rows):
        return "all_null_result"
    return "success"


def _result_table_str(rows: Optional[list], err: Optional[str], kind: str) -> str:
    """Approx DeepEye execution_result.result_table_str / error_message for prompts."""
    if kind == "empty_result":
        return "The SQL query returned an empty result table."
    if kind == "all_null_result":
        return "The SQL query returned an result table with all null values."
    if kind in ("execution_error", "timeout"):
        return f"ERROR: {err}" if err else "ERROR: execution failed"
    # success preview (rarely used as trigger)
    try:
        preview = rows[:5] if rows else []
        return f"RESULT preview (top {len(preview)}): {preview!r}"
    except Exception:
        return "RESULT ok"


def _hash_rows(rows: Optional[list]) -> Any:
    try:
        from workflows.mcts_v4.actions.deepeye_fullsql_fair_plugin import _official_result_hash

        return _official_result_hash(rows or [])
    except Exception:
        return tuple(tuple(r) if isinstance(r, (list, tuple)) else (r,) for r in (rows or [])[:200])


def _select_candidate(
    cands: List[str],
    exec_fn: Callable[[str], Tuple[Optional[list], Optional[str]]],
    *,
    accept_kinds: Tuple[str, ...],
) -> Optional[str]:
    """Official majority over accepted execution hashes."""
    valid: List[Tuple[str, Any]] = []
    for sql in cands:
        s = (sql or "").strip().rstrip(";")
        if not s:
            continue
        rows, err = exec_fn(s)
        kind = _classify_exec(rows, err)
        if kind in accept_kinds:
            valid.append((s, _hash_rows(rows)))
    if not valid:
        return None
    counter = Counter(h for _, h in valid)
    return max(valid, key=lambda x: counter[x[1]])[0]


def _client_endpoint(client: Any) -> Tuple[str, str]:
    base = getattr(client, "base_url", None)
    base_s = str(base or "").rstrip("/")
    # httpx.URL → "http://host/v1/"; strip trailing slash already done
    api_key = getattr(client, "api_key", None) or "EMPTY"
    return base_s, str(api_key)


def _ensure_official_services(*, exec_timeout_s: int = 600) -> None:
    global _services_ready
    with _services_lock:
        if _services_ready:
            return
        root = os.environ.get("MCTS_DEEPEYE_ROOT", DEEPEYE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from app.services import (  # type: ignore
            configure_execution_service,
            configure_schema_service,
        )

        max_val = int(os.environ.get("MCTS_DEEPEYE_MAX_VALUE_EXAMPLE_LENGTH", "100") or 100)
        configure_schema_service(max_value_example_length=max_val)
        configure_execution_service(default_timeout=int(exec_timeout_s))
        _services_ready = True


def _get_official_checkers() -> List[Any]:
    global _checkers
    with _checkers_lock:
        if _checkers is not None:
            return _checkers
        root = os.environ.get("MCTS_DEEPEYE_ROOT", DEEPEYE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from app.pipeline.sql_revision.checkers import (  # type: ignore
            JoinChecker,
            MaxMinChecker,
            OrderByLimitChecker,
            OrderByNullChecker,
            ResultChecker,
            SelectChecker,
            SyntaxChecker,
            TimeChecker,
        )

        max_retry = int(os.environ.get("MCTS_DEEPEYE_EXTRACTOR_MAX_RETRY", "3") or 3)
        _checkers = [
            SyntaxChecker(extractor_max_retry=max_retry),
            JoinChecker(extractor_max_retry=max_retry),
            OrderByLimitChecker(extractor_max_retry=max_retry),
            TimeChecker(extractor_max_retry=max_retry),
            SelectChecker(extractor_max_retry=max_retry),
            MaxMinChecker(extractor_max_retry=max_retry),
            OrderByNullChecker(extractor_max_retry=max_retry),
            ResultChecker(extractor_max_retry=max_retry),
        ]
        return _checkers


def _build_official_llm(client: Any, model: str, *, max_model_len: int = 128000) -> Any:
    base_s, api_key = _client_endpoint(client)
    key = (base_s, str(model), api_key, int(max_model_len))
    with _llm_cache_lock:
        cached = _llm_cache.get(key)
        if cached is not None:
            return cached
        root = os.environ.get("MCTS_DEEPEYE_ROOT", DEEPEYE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from app.config.config import LLMConfig  # type: ignore
        from app.llm import LLM  # type: ignore

        cfg = LLMConfig(
            model=str(model),
            base_url=base_s or "http://127.0.0.1:8000/v1",
            api_key=api_key,
            max_tokens=int(os.environ.get("MCTS_DEEPEYE_REVISE_MAX_TOKENS", "4096") or 4096),
            temperature=float(os.environ.get("MCTS_DEEPEYE_REVISE_TEMP", str(_REVISE_TEMP)) or _REVISE_TEMP),
            api_type="openai",
            max_model_len=int(max_model_len),
            fix_end_token=False,
        )
        llm = LLM(cfg)
        _llm_cache[key] = llm
        return llm


def _build_data_item(
    *,
    sql: str,
    question: str,
    evidence: str,
    db_path: Path,
    linked_schema: Dict[str, Any],
    question_id: Optional[int] = None,
    database_id: Optional[str] = None,
) -> Any:
    from app.dataset import DataItem  # type: ignore

    sch = copy.deepcopy(linked_schema)
    qid = 0 if question_id is None else int(question_id)
    db_id = (database_id or Path(db_path).stem or "db").strip() or "db"
    return DataItem(
        question_id=qid,
        question=question or "",
        evidence=evidence or "",
        gold_sql="SELECT 1",
        difficulty="",
        database_id=db_id,
        database_path=str(db_path),
        database_schema=sch,
        database_schema_after_schema_linking=copy.deepcopy(sch),
        sql_candidates=[(sql or "").strip()],
    )


def official_revise_full_sql_basechecker(
    *,
    sql: str,
    question: str,
    evidence: str,
    db_path: Path,
    client: OpenAI,
    model: str,
    linked_schema: Dict[str, Any],
    sampling_budget: Optional[int] = None,
    question_id: Optional[int] = None,
    database_id: Optional[str] = None,
    max_model_len: Optional[int] = None,
    exec_timeout_s: Optional[float] = None,
) -> Tuple[str, ReviseAudit]:
    """Run official BaseChecker chain (Syntax→…→Result) like SQLRevisionRunner."""
    audit = ReviseAudit(mode="basechecker")
    cur = (sql or "").strip().rstrip(";")
    if not cur:
        audit.final_sql = ""
        return "", audit
    if not isinstance(linked_schema, dict) or not linked_schema:
        raise ValueError("linked_schema required for basechecker revise")

    budget = sampling_budget
    if budget is None:
        budget = int(os.environ.get("MCTS_DEEPEYE_CHECKER_SAMPLING_BUDGET", "3") or 3)
    budget = max(1, int(budget))

    to = int(exec_timeout_s) if exec_timeout_s is not None else int(
        float(os.environ.get("MCTS_DEEPEYE_SQL_EXEC_TIMEOUT", "600") or 600)
    )
    _ensure_official_services(exec_timeout_s=to)
    llm = _build_official_llm(
        client,
        model,
        max_model_len=int(max_model_len or os.environ.get("MCTS_DEEPEYE_MAX_MODEL_LEN", "128000") or 128000),
    )
    data_item = _build_data_item(
        sql=cur,
        question=question,
        evidence=evidence,
        db_path=Path(db_path),
        linked_schema=linked_schema,
        question_id=question_id,
        database_id=database_id,
    )
    checkers = _get_official_checkers()
    n_llm_phases = 0
    for checker in checkers:
        cname = checker.__class__.__name__
        before = cur
        try:
            after, tokens = checker.check_and_revise(cur, data_item, llm, budget)
        except Exception as e:
            audit.events.append(
                ReviseEvent(
                    checker=cname,
                    action="keep_after_fail",
                    sql_before=before,
                    sql_after=before,
                    note=f"basechecker_error:{type(e).__name__}:{str(e)[:160]}",
                )
            )
            continue
        after = (after or before or "").strip().rstrip(";")
        tok_total = 0
        if isinstance(tokens, dict):
            tok_total = int(tokens.get("total_tokens") or 0)
        if tok_total > 0:
            n_llm_phases += 1
        changed = after != before
        if tok_total <= 0 and not changed:
            audit.events.append(
                ReviseEvent(
                    checker=cname,
                    action="skip_ok",
                    sql_before=before,
                    sql_after=after,
                    note="no_llm",
                )
            )
        else:
            audit.events.append(
                ReviseEvent(
                    checker=cname,
                    action="basechecker",
                    sql_before=before,
                    sql_after=after,
                    note=f"tokens={tok_total} changed={int(changed)}",
                )
            )
        cur = after or before

    audit.n_llm_calls = n_llm_phases
    audit.final_sql = cur
    return cur, audit


def _official_revise_full_sql_legacy(
    *,
    sql: str,
    question: str,
    evidence: str,
    schema: str,
    db_path: Path,
    client: OpenAI,
    model: str,
    exec_fn: Callable[[Path, str], Tuple[Optional[list], Optional[str]]],
    sampling_budget: Optional[int] = None,
) -> Tuple[str, ReviseAudit]:
    """DeepEye order via __new__ rule steal + local LLM (pre-basechecker path)."""
    PF = _load_pf()
    audit = ReviseAudit(mode="legacy")
    cur = (sql or "").strip().rstrip(";")
    schema_s = (schema or "").strip()
    soft_cap = int(os.environ.get("MCTS_DEEPEYE_REVISE_SCHEMA_CAP", "24000") or 24000)
    if soft_cap > 0 and len(schema_s) > soft_cap:
        schema_s = schema_s[:soft_cap]
    evidence_s = evidence or "None"
    q = question or ""
    budget = sampling_budget
    if budget is None:
        budget = int(os.environ.get("MCTS_DEEPEYE_CHECKER_SAMPLING_BUDGET", "3") or 3)
    budget = max(1, int(budget))
    # ResultChecker empty→LIKE is high-variance; allow a higher n (default 6).
    result_budget = int(os.environ.get("MCTS_DEEPEYE_RESULT_CHECKER_N", "6") or 6)
    result_budget = max(budget, result_budget)

    def _exec(s: str) -> Tuple[Optional[list], Optional[str]]:
        return exec_fn(db_path, s)

    # --- SyntaxChecker: skip success|empty|all_null; else n=budget ---
    rows, err = _exec(cur)
    kind = _classify_exec(rows, err)
    if kind in ("success", "empty_result", "all_null_result"):
        audit.events.append(
            ReviseEvent(
                checker="SyntaxChecker",
                action="skip_ok",
                sql_before=cur,
                sql_after=cur,
                note=kind,
            )
        )
    else:
        result_str = _result_table_str(rows, err, kind)
        prompt = PF.format_execution_checker_prompt(schema_s, q, evidence_s, cur, result_str)
        texts = _llm_n(client, model, prompt, n=budget, temperature=_REVISE_TEMP)
        audit.n_llm_calls += len(texts)
        cands = [_extract_sql(t) for t in texts]
        picked = _select_candidate(
            cands,
            _exec,
            accept_kinds=("success", "empty_result", "all_null_result"),
        )
        if picked:
            audit.events.append(
                ReviseEvent(
                    checker="SyntaxChecker",
                    action="llm_execution",
                    sql_before=cur,
                    sql_after=picked,
                    exec_err=(err or "")[:200],
                    note=f"from={kind} n={budget}",
                )
            )
            cur = picked
        else:
            audit.events.append(
                ReviseEvent(
                    checker="SyntaxChecker",
                    action="keep_after_fail",
                    sql_before=cur,
                    sql_after=cur,
                    exec_err=(err or "")[:200],
                    note=f"no accepted fix from={kind}",
                )
            )

    # --- rule checkers → common_checker only on hit (official n=1, take first) ---
    if "|| ' ' ||" in cur or "|| ', ' ||" in cur:
        before = cur
        cur = cur.replace("|| ' ' ||", ", ").replace("|| ', ' ||", ", ")
        audit.events.append(
            ReviseEvent(
                checker="SelectChecker",
                action="mechanical_concat_rewrite",
                sql_before=before,
                sql_after=cur,
                note="official SelectChecker pre-rewrite",
            )
        )

    for cname, cobj, kind_r in _load_rule_checkers():
        sug = _rule_suggestion(cname, cobj, kind_r, cur)
        if not sug:
            audit.events.append(
                ReviseEvent(checker=cname, action="skip_no_rule", sql_before=cur, sql_after=cur)
            )
            continue
        prompt = PF.format_common_checker_prompt(schema_s, q, evidence_s, cur, sug)
        text = _llm(client, model, prompt, temperature=_REVISE_TEMP)
        audit.n_llm_calls += 1
        fixed = _extract_sql(text)
        # Official Join/Select/… return results[0] without re-exec gate.
        if fixed:
            audit.events.append(
                ReviseEvent(
                    checker=cname,
                    action="llm_common",
                    suggestion=(sug or "")[:500],
                    sql_before=cur,
                    sql_after=fixed,
                )
            )
            cur = fixed
        else:
            audit.events.append(
                ReviseEvent(
                    checker=cname,
                    action="keep_after_fail",
                    suggestion=(sug or "")[:500],
                    sql_before=cur,
                    sql_after=cur,
                    note="empty LLM fix",
                )
            )

    # --- ResultChecker: ONLY success skips; empty/all_null/error → n=budget ---
    # Official extractor retries until n parses; we additionally re-sample once if
    # no non-empty-success candidate appears (empty→LIKE is high-variance).
    rows, err = _exec(cur)
    kind = _classify_exec(rows, err)
    if kind == "success":
        audit.events.append(
            ReviseEvent(
                checker="ResultChecker",
                action="skip_ok",
                sql_before=cur,
                sql_after=cur,
                note=kind,
            )
        )
    else:
        result_str = _result_table_str(rows, err, kind)
        prompt = PF.format_execution_checker_prompt(schema_s, q, evidence_s, cur, result_str)
        extra = int(os.environ.get("MCTS_DEEPEYE_RESULT_CHECKER_EXTRA_ROUNDS", "1") or 1)
        rounds = 1 + max(0, extra)
        picked = None
        for ri in range(rounds):
            texts = _llm_n(client, model, prompt, n=result_budget, temperature=_REVISE_TEMP)
            audit.n_llm_calls += len(texts)
            cands = [_extract_sql(t) for t in texts]
            picked = _select_candidate(cands, _exec, accept_kinds=("success",))
            if picked:
                audit.events.append(
                    ReviseEvent(
                        checker="ResultChecker",
                        action="llm_execution",
                        sql_before=cur,
                        sql_after=picked,
                        exec_err=(err or kind)[:200],
                        note=f"from={kind} n={result_budget} round={ri+1}/{rounds} accept=success_only",
                    )
                )
                cur = picked
                break
        if not picked:
            audit.events.append(
                ReviseEvent(
                    checker="ResultChecker",
                    action="keep_after_fail",
                    sql_before=cur,
                    sql_after=cur,
                    exec_err=(err or kind)[:200],
                    note=f"no non-empty success fix from={kind} rounds={rounds} n={result_budget}",
                )
            )

    audit.final_sql = cur
    return cur, audit


def official_revise_full_sql(
    *,
    sql: str,
    question: str,
    evidence: str,
    schema: str,
    db_path: Path,
    client: OpenAI,
    model: str,
    exec_fn: Callable[[Path, str], Tuple[Optional[list], Optional[str]]],
    sampling_budget: Optional[int] = None,
    linked_schema: Optional[Dict[str, Any]] = None,
    question_id: Optional[int] = None,
    database_id: Optional[str] = None,
    max_model_len: Optional[int] = None,
    exec_timeout_s: Optional[float] = None,
    extra_evidence: str = "",
) -> Tuple[str, ReviseAudit]:
    """Dispatch: basechecker (default) when linked_schema present, else legacy."""
    mode = _revise_mode()
    evidence_s = evidence or ""
    extra = (extra_evidence or "").strip()
    if extra:
        evidence_s = f"{evidence_s}\n\n{extra}" if evidence_s else extra

    if mode == "basechecker" and isinstance(linked_schema, dict) and linked_schema:
        try:
            return official_revise_full_sql_basechecker(
                sql=sql,
                question=question,
                evidence=evidence_s,
                db_path=db_path,
                client=client,
                model=model,
                linked_schema=linked_schema,
                sampling_budget=sampling_budget,
                question_id=question_id,
                database_id=database_id,
                max_model_len=max_model_len,
                exec_timeout_s=exec_timeout_s,
            )
        except Exception as e:
            fixed, audit = _official_revise_full_sql_legacy(
                sql=sql,
                question=question,
                evidence=evidence_s,
                schema=schema,
                db_path=db_path,
                client=client,
                model=model,
                exec_fn=exec_fn,
                sampling_budget=sampling_budget,
            )
            audit.fallback = f"basechecker_error:{type(e).__name__}:{str(e)[:200]}"
            return fixed, audit

    fixed, audit = _official_revise_full_sql_legacy(
        sql=sql,
        question=question,
        evidence=evidence_s,
        schema=schema,
        db_path=db_path,
        client=client,
        model=model,
        exec_fn=exec_fn,
        sampling_budget=sampling_budget,
    )
    if mode == "basechecker" and not (isinstance(linked_schema, dict) and linked_schema):
        audit.fallback = "no_linked_schema"
    return fixed, audit


def wrap_full_sql_to_step_cte(
    sql: str,
    *,
    step_name: str,
    preceding_ctes: Optional[List[str]] = None,
) -> str:
    """Rule wrap: full SQL → one CTE step (multi-CTE aware)."""
    os.environ["MCTS_BUGFIX_REVISE_MULTICTE"] = "1"
    from workflows.mcts_v4.actions.deepeye_cte_full_plugin import _sql_to_cte

    return _sql_to_cte(sql, step_name=step_name, preceding_ctes=list(preceding_ctes or []))
