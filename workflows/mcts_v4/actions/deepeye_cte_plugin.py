#!/usr/bin/env python3
"""DeepEye-style CTE plugin (step-level).

Pipeline (ported from DeepEye SQL gen + BR selection, unit = one CTE):

  schema + question + evidence + sub_question + preceding
          │
     ┌────┴────┐
   DC-CTE   Skeleton-CTE   (multi-sample)
          │
   exec filter + cluster by result signature
          │
   BR pairwise on top-K clusters (optional)
          │
   weighted clusters  →  winner = argmax(weight) for Rollout-1

Not the original DeepEye end-to-end; keeps DC/Skeleton + BR pattern at CTE grain.
"""

from __future__ import annotations

import os

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from workflows.mcts_v4.query_clarifier.llm_client import default_openai_client
from workflows.mcts_v4.utils.llm_chat import create_chat_completion

PROMPT_DC_CTE = """# Task
You are an experienced database expert. Generate ONE intermediate SQLite CTE for the CURRENT sub-question only, using recursive divide-and-conquer reasoning.

# Approach (in <reasoning>)
1. Divide the current sub-question into tiny checks if needed (pseudo-SQL ok).
2. Conquer: write ONE real SQLite CTE that implements ONLY this sub-question.
3. Do NOT solve later steps; they come later. Do NOT output a full multi-step final query.

# Important Rules
- Output ONLY one CTE: WITH cte_name AS ( ... ) optionally followed by SELECT * FROM cte_name;
- Use only tables/columns from the schema; respect Evidence literals exactly.
- Prefer INNER JOIN over nested SELECT when combining with preceding CTEs.
- SQLite only; no comments inside the SQL in <result>.
- THIS STEP ONLY: implement the Current Sub-question. Do NOT re-solve the full original question.
- If Preceding CTEs are given (not "(none)"), you MUST reference them by name in FROM/JOIN
  and must NOT recompute filters/joins already done in those CTEs.
- Prefer a NEW cte_name such as step_k (do not reuse preceding names).

# Output Format
<reasoning>
...
</reasoning>
<result>
WITH cte_name AS (
  ...
)
SELECT * FROM cte_name;
</result>

# Input
## Database Schema:
{schema}

## Original Question:
{question}

## Evidence / Hint:
{evidence}

## Current Sub-question (THIS CTE ONLY):
{sub_question}

## Preceding CTEs (already decided; you may reference them by name):
{preceding}

# Output:
"""

PROMPT_SKELETON_CTE = """# Task
You are an experienced database expert. First sketch a SQL skeleton for the CURRENT sub-question, then fill it into ONE intermediate SQLite CTE.

# Steps (in <reasoning>)
1. Skeleton: SELECT / FROM / JOIN / WHERE / GROUP BY / ORDER BY placeholders for THIS step only.
2. Fill: produce one real SQLite CTE.

# Rules
- Output ONLY one CTE: WITH cte_name AS ( ... ) optionally + SELECT * FROM cte_name;
- Do NOT output the full final multi-step query.
- Use only schema tables/columns; respect Evidence literals.
- You may reference preceding CTE names.

# Output Format
<reasoning>
...
</reasoning>
<result>
WITH cte_name AS (
  ...
)
SELECT * FROM cte_name;
</result>

# Input
## Database Schema:
{schema}

## Original Question:
{question}

## Evidence / Hint:
{evidence}

## Current Sub-question (THIS CTE ONLY):
{sub_question}

## Preceding CTEs:
{preceding}

# Output:
"""

PROMPT_BR_CTE = """# Task
Given DB info and the CURRENT sub-question, compare two candidate intermediate CTEs. One is better aligned with the sub-question. Analyze query logic and execution results, then choose.

# Context
- Candidate A currently has higher prior weight (more votes / stronger size) than B.
- Prefer A unless B is clearly superior or A has obvious errors relative to the sub-question.

# Instructions
- Judge only whether the CTE correctly implements the CURRENT sub-question (not the full original question).
- Compare logic and execution results.
- In <result>, output only A or B.

# Output Format
<result>
A or B
</result>

# Input
## Database Schema:
{schema}

## Original Question:
{question}

## Evidence / Hint:
{evidence}

## Current Sub-question:
{sub_question}

## Preceding CTEs:
{preceding}

CTE Candidate A:
{query_a}

## Execution Result A:
{result_a}

CTE Candidate B:
{query_b}

## Execution Result B:
{result_b}

# Output:
"""


def _client_model(llm_config: Optional[dict]):
    if llm_config and llm_config.get("config_list"):
        c0 = llm_config["config_list"][0]
        from openai import OpenAI

        client = OpenAI(base_url=c0.get("base_url"), api_key=c0.get("api_key") or "EMPTY")
        return client, c0.get("model") or ""
    return default_openai_client()


def _llm(prompt: str, *, client, model: str, temperature: float) -> str:
    resp = create_chat_completion(
        client,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "") if resp.choices else ""


def _extract_result_sql(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    m = re.search(r"<result>\s*([\s\S]*?)\s*</result>", raw, re.I)
    if m:
        raw = m.group(1).strip()
    m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", raw, re.I)
    if m:
        raw = m.group(1).strip()
    return raw.strip()


def _normalize_cte(text: str, extract_fn=None) -> str:
    raw = _extract_result_sql(text)
    if not raw:
        return ""
    if extract_fn is not None:
        try:
            got = extract_fn(raw)
            if isinstance(got, str) and got.strip():
                return got.strip()
        except Exception:
            pass
    if re.search(r"\bWITH\b", raw, re.I) or re.search(r"\bAS\s*\(", raw, re.I):
        return raw.strip()
    if re.search(r"\bSELECT\b", raw, re.I):
        body = raw.rstrip(";").strip()
        return f"WITH step AS (\n{body}\n)\nSELECT * FROM step;"
    return ""


def _balanced_paren_end(raw: str, start: int) -> Optional[int]:
    """`start` at '('; return matching ')' index or None."""
    if start >= len(raw) or raw[start] != "(":
        return None
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_cte_defs(sql: str) -> List[Tuple[str, str]]:
    """Parse WITH a AS (...), b AS (...) into [(name, inner), ...] (all CTEs)."""
    raw = (sql or "").strip().rstrip(";")
    if not raw:
        return []
    m = re.match(r"(?is)^\s*WITH\s+", raw)
    if not m:
        return []
    i = m.end()
    m_rec = re.match(r"(?is)RECURSIVE\s+", raw[i:])
    if m_rec:
        i += m_rec.end()
    defs: List[Tuple[str, str]] = []
    n = len(raw)
    while i < n:
        while i < n and raw[i].isspace():
            i += 1
        mname = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", raw[i:])
        if not mname:
            break
        name = mname.group(1)
        i += mname.end()
        while i < n and raw[i].isspace():
            i += 1
        if i < n and raw[i] == "(":  # optional column list
            end_cols = _balanced_paren_end(raw, i)
            if end_cols is None:
                break
            i = end_cols + 1
            while i < n and raw[i].isspace():
                i += 1
        mas = re.match(r"(?is)AS\s*\(", raw[i:])
        if not mas:
            break
        i += mas.end() - 1
        end = _balanced_paren_end(raw, i)
        if end is None:
            break
        defs.append((name, raw[i + 1 : end].strip()))
        i = end + 1
        while i < n and raw[i].isspace():
            i += 1
        if i < n and raw[i] == ",":
            i += 1
            continue
        break
    return defs


def _cte_name_and_inner(cte: str) -> Tuple[str, str]:
    """Return (cte_name, inner) for the *last* CTE in a blob (back-compat)."""
    defs = extract_cte_defs(cte)
    if defs:
        return defs[-1]
    raw = (cte or "").strip().rstrip(";")
    return "step", raw


def _is_synthetic_cte_name(name: str) -> bool:
    """Names safe to remap across chain steps (not real base tables)."""
    n = (name or "").strip().lower()
    if re.match(r"^step(_\d+)?$", n):
        return True
    if n in {"revised", "fixed", "step"}:
        return True
    return n.startswith(
        ("dc_", "sk_", "icl_", "skeleton_", "revised", "fixed", "cte_", "gen_")
    )


def _apply_cte_rename(body: str, rename: Dict[str, str]) -> str:
    out = body
    for old in sorted(rename.keys(), key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(old)}\b", rename[old], out)
    return out


def build_sql_from_chain(cte_chain: List[str], extra: Optional[str] = None) -> str:
    """Compose WITH chain with unique step_0..step_k names (avoids SQLite duplicate WITH).

    Fix #1: expand multi-CTE blobs so later AS (...) are not dropped.
    Fix #2: within a blob remap freely; across steps only register synthetic CTE
    names (step_*/dc_*/...) so base-table names like `schools` are not rewritten.
    """
    raw_list = list(cte_chain) + ([extra] if extra else [])
    if not raw_list:
        return ""
    segments: List[List[Tuple[str, str]]] = []
    for c in raw_list:
        defs = extract_cte_defs(c)
        if defs:
            segments.append(defs)
            continue
        body = (c or "").strip().rstrip(";")
        if not body:
            continue
        segments.append([("step", body)])
    if not segments:
        return ""

    parts: List[str] = []
    global_rename: Dict[str, str] = {}
    idx = 0
    for seg in segments:
        local = dict(global_rename)
        first_idx = idx
        for old_name, inner in seg:
            new_name = f"step_{idx}"
            body = _apply_cte_rename(inner, local)
            parts.append(f"{new_name} AS (\n{body}\n)")
            local[old_name] = new_name
            local[new_name] = new_name
            idx += 1
        for k in range(first_idx, idx):
            sn = f"step_{k}"
            global_rename[sn] = sn
        for old_name, _inner in seg:
            mapped = local[old_name]
            if _is_synthetic_cte_name(old_name):
                global_rename[old_name] = mapped
    last = f"step_{idx - 1}"
    return f"WITH {', '.join(parts)} SELECT * FROM {last}"


def _cte_body_frag(cte: str) -> Tuple[str, str]:
    """Back-compat helper: (name, 'name AS (inner)')."""
    name, inner = _cte_name_and_inner(cte)
    return name, f"{name} AS (\n{inner}\n)"


def exec_rows(db_path: Path, sql: str, timeout_s: float = 30.0) -> Tuple[Optional[List[tuple]], Optional[str]]:
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return None, "empty"
    if not db_path.is_file():
        return None, f"missing_db:{db_path}"
    try:
        conn = sqlite3.connect(str(db_path), timeout=timeout_s)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            return cur.fetchall(), None
        finally:
            conn.close()
    except Exception as e:
        return None, str(e)[:240]


def result_sig(rows: Optional[List[tuple]]) -> str:
    if rows is None:
        return "ERR"
    # Bugfix #5 (MCTS_BUGFIX_RESULT_SIG=1): full-result hash.
    # Default: legacy top-200 truncate (matches pre-fix clustering).
    use_full = os.environ.get("MCTS_BUGFIX_RESULT_SIG", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    try:
        if not use_full:
            norm = sorted(tuple(r) for r in rows[:200])
            return repr(norm)[:800]
        import hashlib

        norm = sorted(tuple(r) for r in rows)
        payload = repr(norm)
        n = len(rows)
        if len(payload) <= 1200:
            return payload
        digest = hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
        head = repr(norm[:12])[:400]
        return f"N={n}|H={digest}|HEAD={head}"
    except Exception:
        return f"N={len(rows)}"


def format_result_preview(rows: Optional[List[tuple]], err: Optional[str], max_rows: int = 8) -> str:
    if err:
        return f"ERROR: {err}"
    if rows is None:
        return "(no rows)"
    lines = [str(r) for r in rows[:max_rows]]
    more = f"\n... ({len(rows)} rows total)" if len(rows) > max_rows else ""
    return "\n".join(lines) + more if lines else "(empty result)"


def _parse_ab(text: str) -> str:
    raw = (text or "").strip()
    m = re.search(r"<result>\s*([ABab])\s*</result>", raw)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([AB])\b", raw)
    return m.group(1).upper() if m else "A"


def cluster_and_select_ctes(
    *,
    db_path: Path,
    schema: str,
    question: str,
    evidence: str,
    sub_question: str,
    preceding_ctes: List[str],
    candidates: List[Dict[str, Any]],
    llm_config: Optional[dict] = None,
    br_top_k: int = 3,
    br_votes: int = 1,
    size_weight: float = 0.5,
    br_weight: float = 0.5,
    audit_extra: Optional[dict] = None,
) -> Dict[str, Any]:
    """Shared: exec-filter → signature clusters → optional BR → weighted winner.

    ``candidates`` items: ``{"cte": str, "source": str}``.
    """
    client, model = _client_model(llm_config)
    schema_s = (schema or "")[:8000]
    evidence_s = evidence or "None"
    sub_q = (sub_question or question or "").strip()
    preceding = "\n\n".join(preceding_ctes) if preceding_ctes else "(none)"

    raw_items: List[Dict[str, Any]] = []
    clusters_map: Dict[str, Dict[str, Any]] = {}
    for cand in candidates:
        cte = (cand.get("cte") or "").strip()
        src = cand.get("source") or "?"
        it: Dict[str, Any] = {"kind": src, "cte": cte, "ok": bool(cte)}
        if not cte:
            raw_items.append(it)
            continue
        sql = build_sql_from_chain(preceding_ctes, cte)
        rows, err = exec_rows(db_path, sql)
        if err is not None:
            it["exec_ok"] = False
            it["exec_err"] = err
            raw_items.append(it)
            continue
        it["exec_ok"] = True
        sig = result_sig(rows)
        it["sig"] = sig
        raw_items.append(it)
        if sig not in clusters_map:
            clusters_map[sig] = {
                "sig": sig,
                "cte": cte,
                "size": 0,
                "sources": [],
                "rows_preview": format_result_preview(rows, None),
                "sql_preview": sql[:240],
            }
        clusters_map[sig]["size"] += 1
        clusters_map[sig]["sources"].append(src)

    clusters = sorted(clusters_map.values(), key=lambda c: (-c["size"], c["sig"]))
    for c in clusters:
        c["br_score"] = 0.0
        c["weight"] = float(c["size"])

    br_audit: List[dict] = []
    top = clusters[: max(1, br_top_k)]
    if len(top) >= 2 and br_votes > 0 and br_weight > 0:
        wins = {c["sig"]: 0.0 for c in top}
        pairs = [(i, j) for i in range(len(top)) for j in range(i + 1, len(top))]
        for i, j in pairs:
            a, b = top[i], top[j]
            if a["size"] < b["size"]:
                a, b = b, a
            votes_ab = []
            for vi in range(br_votes):
                try:
                    prompt = PROMPT_BR_CTE.format(
                        schema=schema_s,
                        question=question,
                        evidence=evidence_s,
                        sub_question=sub_q,
                        preceding=preceding,
                        query_a=a["cte"][:1500],
                        result_a=a.get("rows_preview") or "",
                        query_b=b["cte"][:1500],
                        result_b=b.get("rows_preview") or "",
                    )
                    text = _llm(prompt, client=client, model=model, temperature=0.2 + 0.1 * vi)
                    choice = _parse_ab(text)
                except Exception:
                    choice = "A"
                votes_ab.append(choice)
                if choice == "A":
                    wins[a["sig"]] += 1.0
                else:
                    wins[b["sig"]] += 1.0
            br_audit.append(
                {"a_sig": a["sig"][:80], "b_sig": b["sig"][:80], "votes": votes_ab}
            )
        max_w = max(wins.values()) if wins else 1.0
        for c in top:
            c["br_score"] = (wins.get(c["sig"], 0.0) / max_w) if max_w > 0 else 0.0

    total_size = sum(c["size"] for c in clusters) or 1
    top_sigs = {t["sig"] for t in top}
    for c in clusters:
        size_n = c["size"] / total_size
        br_n = float(c.get("br_score") or 0.0)
        if len(top) >= 2 and br_weight > 0 and c["sig"] in top_sigs:
            c["weight"] = size_weight * size_n + br_weight * br_n
        else:
            c["weight"] = size_n

    clusters = sorted(clusters, key=lambda c: (-c["weight"], -c["size"], c["sig"]))
    winner = clusters[0] if clusters else None
    audit = {
        "n_candidates": len(candidates),
        "n_exec_ok": sum(1 for x in raw_items if x.get("exec_ok")),
        "n_clusters": len(clusters),
        "br_top_k": br_top_k,
        "br_pairs": br_audit,
        "raw_previews": [
            {
                "kind": x.get("kind"),
                "ok": x.get("ok"),
                "exec_ok": x.get("exec_ok"),
                "cte_preview": ((x.get("cte") or "")[:120]),
                "err": x.get("exec_err") or x.get("error") or "",
            }
            for x in raw_items
        ],
    }
    if audit_extra:
        audit.update(audit_extra)

    return {
        "clusters": [
            {
                "sig": c["sig"][:120],
                "cte": c["cte"],
                "size": c["size"],
                "weight": round(float(c["weight"]), 4),
                "br_score": round(float(c.get("br_score") or 0.0), 4),
                "sources": c.get("sources") or [],
                "rows_preview": (c.get("rows_preview") or "")[:400],
            }
            for c in clusters
        ],
        "winner_cte": (winner["cte"] if winner else ""),
        "winner_sig": (winner["sig"][:120] if winner else ""),
        "winner_weight": (round(float(winner["weight"]), 4) if winner else 0.0),
        "audit": audit,
    }


def deepeye_cte_plugin(
    *,
    db_path: Path,
    schema: str,
    question: str,
    evidence: str,
    sub_question: str,
    preceding_ctes: List[str],
    llm_config: Optional[dict] = None,
    extract_fn=None,
    n_dc: int = 2,
    n_skeleton: int = 2,
    br_top_k: int = 3,
    br_votes: int = 1,
    size_weight: float = 0.5,
    br_weight: float = 0.5,
    max_workers: int = 4,
) -> Dict[str, Any]:
    """Return weighted CTE clusters + Rollout-1 winner.

    Returns dict with keys: clusters, winner_cte, winner_sig, audit.
    """
    client, model = _client_model(llm_config)
    schema_s = (schema or "")[:8000]
    evidence_s = evidence or "None"
    sub_q = (sub_question or question or "").strip()
    preceding = "\n\n".join(preceding_ctes) if preceding_ctes else "(none)"

    jobs: List[Tuple[str, str, float]] = []
    for i in range(max(0, n_dc)):
        jobs.append(
            (
                "dc",
                PROMPT_DC_CTE.format(
                    schema=schema_s,
                    question=question,
                    evidence=evidence_s,
                    sub_question=sub_q,
                    preceding=preceding,
                ),
                0.25 + 0.2 * i,
            )
        )
    for i in range(max(0, n_skeleton)):
        jobs.append(
            (
                "skeleton",
                PROMPT_SKELETON_CTE.format(
                    schema=schema_s,
                    question=question,
                    evidence=evidence_s,
                    sub_question=sub_q,
                    preceding=preceding,
                ),
                0.3 + 0.25 * i,
            )
        )

    raw_items: List[Dict[str, Any]] = []

    def _gen(kind: str, prompt: str, temp: float) -> Dict[str, Any]:
        try:
            text = _llm(prompt, client=client, model=model, temperature=temp)
            cte = _normalize_cte(text, extract_fn=extract_fn)
            return {"kind": kind, "temperature": temp, "cte": cte, "ok": bool(cte)}
        except Exception as e:
            return {"kind": kind, "temperature": temp, "cte": "", "ok": False, "error": str(e)}

    if jobs:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as ex:
            futs = [ex.submit(_gen, k, p, t) for k, p, t in jobs]
            for fut in as_completed(futs):
                raw_items.append(fut.result())

    candidates = [
        {"cte": (it.get("cte") or ""), "source": it.get("kind") or "?"}
        for it in raw_items
        if it.get("cte")
    ]
    out = cluster_and_select_ctes(
        db_path=db_path,
        schema=schema,
        question=question,
        evidence=evidence,
        sub_question=sub_q,
        preceding_ctes=preceding_ctes,
        candidates=candidates,
        llm_config=llm_config,
        br_top_k=br_top_k,
        br_votes=br_votes,
        size_weight=size_weight,
        br_weight=br_weight,
        audit_extra={
            "mode": "deepeye_cte_plugin",
            "n_dc": n_dc,
            "n_skeleton": n_skeleton,
            "n_raw_ok": sum(1 for x in raw_items if x.get("ok")),
        },
    )
    return out
