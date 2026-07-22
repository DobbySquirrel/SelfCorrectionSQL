"""DeepEye-style / DeepEye-full CTE expand action.

Modes:
- thin (default legacy): short DC/Skeleton CTE prompts
- full (MCTS_EXPAND_DEEPEYE_FULL=1): authentic DeepEye DC+Skeleton full-SQL prompts
  (+ optional ICL-less multi-sample), then split WITH-chain into CTEs for the expand fork.
"""

from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from workflows.mcts_v4.query_clarifier.llm_client import default_openai_client
from workflows.mcts_v4.utils.llm_chat import create_chat_completion

_DEEPEYE_ROOT = Path("/hpc2hdd/home/sshen190/wtao565/related_project/DeepEye-SQL")


def expand_deepeye_cte_enabled() -> bool:
    return os.environ.get("MCTS_EXPAND_DEEPEYE_CTE", "0").strip() in ("1", "true", "yes")


def expand_deepeye_cte_only() -> bool:
    return os.environ.get("MCTS_EXPAND_DEEPEYE_ONLY", "0").strip() in ("1", "true", "yes")


def expand_deepeye_full_enabled() -> bool:
    return os.environ.get("MCTS_EXPAND_DEEPEYE_FULL", "0").strip() in ("1", "true", "yes")


def expand_deepeye_plugin_enabled() -> bool:
    """Use deepeye_cte_full_plugin (DC+Skeleton+ICL + exec cluster + BR) at expand."""
    return os.environ.get("MCTS_EXPAND_DEEPEYE_PLUGIN", "0").strip() in ("1", "true", "yes")


def expand_deepeye_plugin_top_k() -> int:
    return max(1, _int_env("MCTS_EXPAND_DEEPEYE_PLUGIN_TOP_K", 3))


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default)).strip()))
    except ValueError:
        return default


PROMPT_DC_THIN = """You generate ONE intermediate SQLite CTE for Text-to-SQL using divide-and-conquer.

Database schema:
{schema}

Question: {question}
Evidence/hint: {evidence}
Current sub-question (this CTE step only): {sub_question}
Preceding CTEs (if any):
{preceding}

Approach:
1. Divide the sub-question into tiny checks if needed (pseudo-SQL ok in thinking).
2. Conquer: output a single real SQLite CTE for THIS step only.

Rules:
- Output ONLY one CTE: `WITH cte_name AS ( ... )` optionally followed by `SELECT * FROM cte_name;`
- Do NOT output the full final multi-step query.
- Use only tables/columns from the schema.
- Prefer filters/joins justified by evidence.

Return the CTE inside:
```sql
...
```
"""

PROMPT_SKELETON_THIN = """You generate ONE intermediate SQLite CTE by first sketching a SQL skeleton, then filling it.

Database schema:
{schema}

Question: {question}
Evidence/hint: {evidence}
Current sub-question (this CTE step only): {sub_question}
Preceding CTEs (if any):
{preceding}

Steps:
1. Write a short skeleton (SELECT / FROM / JOIN / WHERE / GROUP BY placeholders).
2. Fill it into a real SQLite CTE for this step only.

Rules:
- Output ONLY one CTE: `WITH cte_name AS ( ... )` optionally + `SELECT * FROM cte_name;`
- Keep it one logical step for the current sub-question.

Return the CTE inside:
```sql
...
```
"""


def _extract_sql_block(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    m = re.search(r"<result>\s*([\s\S]*?)\s*</result>", raw, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", raw, re.I)
    if m:
        raw = m.group(1).strip()
    return raw


def _normalize_to_cte(text: str, extract_fn=None, *, depth: int = 0) -> str:
    """Normalize a SQL fragment into a WITH-CTE form for expand.

    Prefer keeping multi-CTE WITH chains intact. Only wrap bare SELECT into a
    depth-tagged CTE when there is no WITH clause (avoids collapsing full DeepEye
    SQL into an anonymous ``step`` that destroys diversity).
    """
    raw = _extract_sql_block(text)
    if not raw:
        return ""
    if extract_fn is not None:
        try:
            got = extract_fn(raw)
            if isinstance(got, str) and got.strip():
                return got.strip()
        except Exception:
            pass
    if re.search(r"\bWITH\b", raw, re.I):
        return raw.strip()
    if re.search(r"\bSELECT\b", raw, re.I):
        body = raw.strip()
        if body.endswith(";"):
            body = body[:-1]
        name = f"step_{max(0, int(depth))}"
        return f"WITH {name} AS (\n{body}\n)\nSELECT * FROM {name};"
    return ""


def _split_full_sql_to_ctes(sql: str, *, depth: int = 0, extract_split_fn=None) -> List[str]:
    """Turn a full SQL / WITH-chain into expand-ready CTE fragments."""
    raw = _extract_sql_block(sql)
    if not raw:
        return []
    if extract_split_fn is not None:
        try:
            parts = extract_split_fn(raw)
            if isinstance(parts, list) and parts:
                out = []
                for p in parts:
                    if isinstance(p, str) and p.strip():
                        out.append(p.strip())
                if out:
                    if 0 <= depth < len(out):
                        focus = out[depth]
                        rest = [c for j, c in enumerate(out) if j != depth]
                        return [focus] + rest
                    return out
        except Exception:
            pass
    # Keep whole WITH-chain as one candidate (do not rename/flatten to step).
    if re.search(r"\bWITH\b", raw, re.I):
        return [raw.strip()]
    one = _normalize_to_cte(raw, depth=depth)
    return [one] if one else []


def _llm_complete(prompt: str, *, client, model: str, temperature: float, n: int = 1) -> List[str]:
    resp = create_chat_completion(
        client,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        n=max(1, n),
    )
    out = []
    for ch in resp.choices or []:
        out.append((ch.message.content or "") if ch.message else "")
    return out


def _load_deepeye_prompt_factory():
    root = str(_DEEPEYE_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.prompt.factory import PromptFactory  # type: ignore

    return PromptFactory


def _client_model(llm_config: Optional[dict]):
    try:
        if llm_config:
            from workflows.mcts_v4.layered_probe.revision import client_from_llm_config

            return client_from_llm_config(llm_config)
    except Exception:
        pass
    return default_openai_client()


def _dedupe_ctes(ctes: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for c in ctes:
        cte = (c or "").strip()
        if not cte:
            continue
        key = re.sub(r"\s+", " ", cte.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(cte)
    return out


def generate_deepeye_style_ctes(
    *,
    question: str,
    evidence: str,
    schema_ddl: str,
    sub_question: str,
    preceding_cte_info: str = "",
    llm_config: Optional[dict] = None,
    extract_fn=None,
    extract_split_fn=None,
    n_dc: Optional[int] = None,
    n_skeleton: Optional[int] = None,
    depth: int = 0,
    db_path: Optional[str] = None,
    preceding_ctes: Optional[List[str]] = None,
    few_shots: Optional[List[Dict[str, str]]] = None,
    value_retrieval_hint: str = "",
) -> Tuple[List[str], Dict[str, Any]]:
    # Full plugin path: authentic DeepEye expand with exec clustering (heavy).
    if expand_deepeye_plugin_enabled() and db_path:
        return _generate_plugin(
            question=question,
            evidence=evidence,
            schema_ddl=schema_ddl,
            sub_question=sub_question,
            preceding_ctes=list(preceding_ctes or []),
            llm_config=llm_config,
            db_path=db_path,
            n_dc=n_dc,
            n_skeleton=n_skeleton,
            few_shots=few_shots,
            value_retrieval_hint=value_retrieval_hint or "",
        )

    full = expand_deepeye_full_enabled()
    if full:
        # Heavy defaults: more samples from authentic DeepEye prompts
        n_dc = _int_env("MCTS_EXPAND_DEEPEYE_N_DC", 5) if n_dc is None else n_dc
        n_skeleton = (
            _int_env("MCTS_EXPAND_DEEPEYE_N_SKELETON", 5) if n_skeleton is None else n_skeleton
        )
        return _generate_full(
            question=question,
            evidence=evidence,
            schema_ddl=schema_ddl,
            sub_question=sub_question,
            preceding_cte_info=preceding_cte_info,
            llm_config=llm_config,
            extract_fn=extract_fn,
            extract_split_fn=extract_split_fn,
            n_dc=n_dc,
            n_skeleton=n_skeleton,
            depth=depth,
        )

    n_dc = _int_env("MCTS_EXPAND_DEEPEYE_N_DC", 3) if n_dc is None else n_dc
    n_skeleton = (
        _int_env("MCTS_EXPAND_DEEPEYE_N_SKELETON", 2) if n_skeleton is None else n_skeleton
    )
    return _generate_thin(
        question=question,
        evidence=evidence,
        schema_ddl=schema_ddl,
        sub_question=sub_question,
        preceding_cte_info=preceding_cte_info,
        llm_config=llm_config,
        extract_fn=extract_fn,
        n_dc=n_dc,
        n_skeleton=n_skeleton,
    )


def _generate_plugin(
    *,
    question: str,
    evidence: str,
    schema_ddl: str,
    sub_question: str,
    preceding_ctes: List[str],
    llm_config: Optional[dict],
    db_path: str,
    n_dc: Optional[int],
    n_skeleton: Optional[int],
    few_shots: Optional[List[Dict[str, str]]] = None,
    value_retrieval_hint: str = "",
) -> Tuple[List[str], Dict[str, Any]]:
    from pathlib import Path

    from workflows.mcts_v4.actions.deepeye_cte_full_plugin import deepeye_cte_full_plugin

    n_dc = _int_env("MCTS_EXPAND_DEEPEYE_N_DC", 4) if n_dc is None else n_dc
    n_skeleton = (
        _int_env("MCTS_EXPAND_DEEPEYE_N_SKELETON", 4) if n_skeleton is None else n_skeleton
    )
    # Default ICL=4 when few-shots are provided; else 0 (legacy expand).
    n_icl_default = 4 if few_shots else 0
    n_icl = _int_env("MCTS_EXPAND_DEEPEYE_N_ICL", n_icl_default)
    if not few_shots:
        n_icl = 0
    top_k = expand_deepeye_plugin_top_k()
    out = deepeye_cte_full_plugin(
        db_path=Path(db_path),
        schema=schema_ddl or "",
        question=question,
        evidence=evidence or "",
        sub_question=sub_question or question,
        preceding_ctes=preceding_ctes,
        llm_config=llm_config,
        few_shots=few_shots,
        value_retrieval_hint=value_retrieval_hint or "",
        n_dc=n_dc,
        n_skeleton=n_skeleton,
        n_icl=n_icl,
        filter_top_k=min(2, top_k),
        evaluator_votes=_int_env("MCTS_EXPAND_DEEPEYE_BR_VOTES", 3),
        revise_max_unique=_int_env("MCTS_EXPAND_DEEPEYE_REVISE_UNIQUE", 6),
        max_workers=_int_env("MCTS_EXPAND_DEEPEYE_WORKERS", 8),
        progress_prefix="[expand-deepeye-plugin]",
        chain_mode=os.environ.get("MCTS_EXPAND_DEEPEYE_CHAIN_MODE", "soft").strip().lower()
        or "soft",
        strict_chain=os.environ.get("MCTS_EXPAND_DEEPEYE_STRICT_CHAIN", "0").strip()
        in ("1", "true", "yes"),
    )
    clusters = list(out.get("clusters") or [])
    ranked = sorted(
        clusters,
        key=lambda c: (-float(c.get("consistency") or 0), -int(c.get("size") or 0)),
    )
    ctes: List[str] = []
    seen = set()
    winner = (out.get("winner_cte") or "").strip()
    if winner:
        ctes.append(winner)
        seen.add(winner)
    for c in ranked:
        cte = (c.get("cte") or "").strip()
        if cte and cte not in seen:
            ctes.append(cte)
            seen.add(cte)
        if len(ctes) >= top_k:
            break
    audit = {
        "mode": "deepeye_cte_full_plugin",
        "n_clusters": len(clusters),
        "n_returned": len(ctes),
        "top_k": top_k,
        "n_icl": n_icl,
        "n_few_shots": len(few_shots or []),
        "has_vr_hint": bool(value_retrieval_hint),
        "plugin_audit": out.get("audit") or {},
        "winner_consistency": out.get("winner_consistency"),
    }
    return _dedupe_ctes(ctes), audit


def _generate_thin(
    *,
    question: str,
    evidence: str,
    schema_ddl: str,
    sub_question: str,
    preceding_cte_info: str,
    llm_config: Optional[dict],
    extract_fn,
    n_dc: int,
    n_skeleton: int,
) -> Tuple[List[str], Dict[str, Any]]:
    client, model = _client_model(llm_config)
    schema = (schema_ddl or "")[:8000]
    preceding = (preceding_cte_info or "").strip() or "(none)"
    evidence = evidence or "None"
    sub_question = sub_question or question

    jobs: List[Tuple[str, str, float]] = []
    for i in range(n_dc):
        jobs.append(
            (
                "dc",
                PROMPT_DC_THIN.format(
                    schema=schema,
                    question=question,
                    evidence=evidence,
                    sub_question=sub_question,
                    preceding=preceding,
                ),
                0.3 + 0.2 * i,
            )
        )
    for i in range(n_skeleton):
        jobs.append(
            (
                "skeleton",
                PROMPT_SKELETON_THIN.format(
                    schema=schema,
                    question=question,
                    evidence=evidence,
                    sub_question=sub_question,
                    preceding=preceding,
                ),
                0.35 + 0.25 * i,
            )
        )

    results: List[Dict[str, Any]] = []

    def _run(kind: str, prompt: str, temp: float) -> Dict[str, Any]:
        try:
            texts = _llm_complete(prompt, client=client, model=model, temperature=temp, n=1)
            cte = _normalize_to_cte(texts[0] if texts else "", extract_fn=extract_fn)
            return {"kind": kind, "temperature": temp, "cte": cte, "ok": bool(cte), "error": ""}
        except Exception as e:
            return {"kind": kind, "temperature": temp, "cte": "", "ok": False, "error": str(e)}

    if jobs:
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(jobs)))) as ex:
            futs = [ex.submit(_run, k, p, t) for k, p, t in jobs]
            for fut in as_completed(futs):
                results.append(fut.result())

    ctes = _dedupe_ctes([r.get("cte") or "" for r in results])
    audit = {
        "mode": "deepeye_cte_action_thin",
        "n_dc": n_dc,
        "n_skeleton": n_skeleton,
        "n_raw": len(results),
        "n_unique_ctes": len(ctes),
        "results": [
            {
                "kind": r.get("kind"),
                "temperature": r.get("temperature"),
                "ok": r.get("ok"),
                "error": r.get("error") or "",
                "cte_preview": ((r.get("cte") or "")[:160]),
            }
            for r in results
        ],
    }
    return ctes, audit


def _generate_full(
    *,
    question: str,
    evidence: str,
    schema_ddl: str,
    sub_question: str,
    preceding_cte_info: str,
    llm_config: Optional[dict],
    extract_fn,
    extract_split_fn,
    n_dc: int,
    n_skeleton: int,
    depth: int,
) -> Tuple[List[str], Dict[str, Any]]:
    """Heavy path: DeepEye full-SQL prompts + multi-sample, then CTE split."""
    client, model = _client_model(llm_config)
    schema = (schema_ddl or "")[:12000]
    # Prefer answering the original question with sub_question + preceding as hint enrichment
    hint_parts = [evidence or "", f"Current sub-step: {sub_question or question}"]
    if preceding_cte_info:
        hint_parts.append(f"Preceding CTEs:\n{preceding_cte_info[:2000]}")
    hint = "\n".join(p for p in hint_parts if p).strip() or "None"

    try:
        PF = _load_deepeye_prompt_factory()
        dc_prompt = PF.format_dc_sql_generation_prompt(schema, question, hint)
        sk_prompt = PF.format_skeleton_sql_generation_prompt(schema, question, hint)
        prompt_source = "deepeye_PromptFactory"
    except Exception as e:
        # Fallback to thin if DeepEye import fails
        ctes, audit = _generate_thin(
            question=question,
            evidence=evidence,
            schema_ddl=schema_ddl,
            sub_question=sub_question,
            preceding_cte_info=preceding_cte_info,
            llm_config=llm_config,
            extract_fn=extract_fn,
            n_dc=n_dc,
            n_skeleton=n_skeleton,
        )
        audit["full_fallback"] = f"import_failed:{e}"
        return ctes, audit

    temps_dc = [0.2, 0.5, 0.8][: max(1, min(3, n_dc))]
    temps_sk = [0.3, 0.6, 0.9][: max(1, min(3, n_skeleton))]
    # distribute n across temperatures
    def _ns(total: int, n_temps: int) -> List[int]:
        if n_temps <= 0:
            return []
        base, rem = divmod(max(total, 0), n_temps)
        return [base + (1 if i < rem else 0) for i in range(n_temps)]

    n_per_dc = _ns(n_dc, len(temps_dc))
    n_per_sk = _ns(n_skeleton, len(temps_sk))

    jobs: List[Tuple[str, str, float, int]] = []
    for t, n in zip(temps_dc, n_per_dc):
        if n > 0:
            jobs.append(("dc_full", dc_prompt, t, n))
    for t, n in zip(temps_sk, n_per_sk):
        if n > 0:
            jobs.append(("skeleton_full", sk_prompt, t, n))

    results: List[Dict[str, Any]] = []
    all_ctes: List[str] = []

    def _run(kind: str, prompt: str, temp: float, n: int) -> Dict[str, Any]:
        try:
            texts = _llm_complete(prompt, client=client, model=model, temperature=temp, n=n)
            local_ctes: List[str] = []
            for text in texts:
                parts = _split_full_sql_to_ctes(
                    text, depth=depth, extract_split_fn=extract_split_fn
                )
                if parts:
                    local_ctes.extend(parts)
                    continue
                # Bare SELECT fallback only when split yielded nothing
                one = _normalize_to_cte(text, extract_fn=extract_fn, depth=depth)
                if one:
                    local_ctes.append(one)
            return {
                "kind": kind,
                "temperature": temp,
                "n": n,
                "ok": bool(local_ctes),
                "error": "",
                "ctes": local_ctes,
                "sql_preview": (_extract_sql_block(texts[0])[:200] if texts else ""),
            }
        except Exception as e:
            return {
                "kind": kind,
                "temperature": temp,
                "n": n,
                "ok": False,
                "error": str(e),
                "ctes": [],
                "sql_preview": "",
            }

    if jobs:
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(jobs)))) as ex:
            futs = [ex.submit(_run, k, p, t, n) for k, p, t, n in jobs]
            for fut in as_completed(futs):
                r = fut.result()
                results.append(r)
                all_ctes.extend(r.get("ctes") or [])

    ctes = _dedupe_ctes(all_ctes)
    audit = {
        "mode": "deepeye_cte_action_full",
        "prompt_source": prompt_source,
        "n_dc": n_dc,
        "n_skeleton": n_skeleton,
        "depth": depth,
        "n_jobs": len(jobs),
        "n_unique_ctes": len(ctes),
        "results": [
            {
                "kind": r.get("kind"),
                "temperature": r.get("temperature"),
                "n": r.get("n"),
                "ok": r.get("ok"),
                "error": r.get("error") or "",
                "n_ctes": len(r.get("ctes") or []),
                "sql_preview": r.get("sql_preview") or "",
            }
            for r in results
        ],
    }
    return ctes, audit
