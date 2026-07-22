#!/usr/bin/env python3
"""Load DeepEye contested14 / full-dev snapshot context for CTE plugin.

Reuses value_retrieval + schema_linking artifacts (no re-run).
Optional cheap CTE-step filter/rank over already-linked tables/columns/values.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_C14 = Path(
    "/hpc2hdd/home/sshen190/wtao565/related_project/DeepEye-SQL/workspace/"
    "schema_linking/bird/dev_c14.snapshot.data/items.jsonl"
)
_FULL = Path(
    "/hpc2hdd/home/sshen190/wtao565/related_project/DeepEye-SQL/workspace/"
    "schema_linking/bird/dev.snapshot.data/items.jsonl"
)
_FEW = Path(
    "/hpc2hdd/home/sshen190/wtao565/related_project/DeepEye-SQL/results/bird_dev_few_shots.json"
)

_cache: Dict[str, Dict[str, Any]] = {}
_few_cache: Optional[dict] = None

_STOP = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "is",
    "are",
    "be",
    "by",
    "with",
    "from",
    "that",
    "this",
    "as",
    "at",
    "it",
    "its",
    "into",
    "over",
    "under",
    "where",
    "when",
    "what",
    "which",
    "who",
    "how",
    "return",
    "final",
    "answer",
    "original",
    "question",
    "project",
    "only",
    "requested",
    "columns",
    "select",
    "join",
    "filter",
    "rank",
    "table",
    "rows",
    "result",
    "results",
    "step",
    "cte",
}


def _tok(text: str) -> Set[str]:
    toks = re.findall(r"[a-z0-9_]+", (text or "").lower())
    out: Set[str] = set()
    for t in toks:
        if len(t) < 2 or t in _STOP:
            continue
        out.add(t)
        # split camel / snake leftovers already handled; keep numeric tokens
    return out


def _schema_profile_from_linked(sch: dict, *, keep_cols: Optional[Dict[str, Set[str]]] = None) -> str:
    """Render DeepEye linked schema dict into prompt-ready DDL-like text."""
    if not isinstance(sch, dict):
        return ""
    tables = sch.get("tables") or {}
    lines: List[str] = []
    db_id = sch.get("db_id") or ""
    if db_id:
        lines.append(f"-- database: {db_id}")
    for tname, tinfo in tables.items():
        if not isinstance(tinfo, dict):
            continue
        cols = tinfo.get("columns") or {}
        allowed = None if keep_cols is None else keep_cols.get(tname)
        if keep_cols is not None and allowed is not None and not allowed:
            continue
        lines.append(f"CREATE TABLE `{tname}` (")
        col_lines = []
        for cname, cinfo in cols.items():
            if not isinstance(cinfo, dict):
                continue
            if allowed is not None and cname not in allowed:
                continue
            ctype = cinfo.get("column_type") or "TEXT"
            pk = " PRIMARY KEY" if cinfo.get("primary_key") else ""
            desc = (cinfo.get("description") or "").strip()
            examples = cinfo.get("value_examples") or []
            stats = cinfo.get("value_statistics") or {}
            extra = []
            if desc:
                extra.append(desc)
            if examples:
                extra.append("Value Examples: " + ", ".join(str(x) for x in examples[:5]))
            if stats:
                extra.append(
                    f"stats(total={stats.get('total_count')}, "
                    f"distinct={stats.get('distinct_count')}, null={stats.get('null_count')})"
                )
            comment = (" -- " + " | ".join(extra)) if extra else ""
            col_lines.append(f"  `{cname}` {ctype}{pk},{comment}")
        if not col_lines:
            lines.pop()  # drop CREATE TABLE header
            continue
        col_lines[-1] = col_lines[-1].rstrip(",")
        lines.extend(col_lines)
        lines.append(");")
        for cname, cinfo in cols.items():
            if not isinstance(cinfo, dict):
                continue
            if allowed is not None and cname not in allowed:
                continue
            for fk in cinfo.get("foreign_keys") or []:
                lines.append(f"-- FK: {tname}.{cname} -> {fk}")
    return "\n".join(lines)


def _vr_hint_from(
    keywords: List[Any],
    retrieved: dict,
    *,
    keep_cols: Optional[Dict[str, Set[str]]] = None,
    query_tokens: Optional[Set[str]] = None,
    max_bits: int = 20,
) -> str:
    parts: List[str] = []
    if keywords:
        if query_tokens:
            ranked = sorted(
                keywords,
                key=lambda k: len(_tok(str(k)) & query_tokens),
                reverse=True,
            )
        else:
            ranked = list(keywords)
        parts.append("Value-retrieval keywords: " + ", ".join(str(k) for k in ranked[:30]))
    if isinstance(retrieved, dict):
        scored: List[Tuple[int, str]] = []
        for t, cols in retrieved.items():
            if not isinstance(cols, dict):
                continue
            if keep_cols is not None and t not in keep_cols:
                continue
            for c, vals in cols.items():
                if keep_cols is not None and c not in (keep_cols.get(t) or set()):
                    continue
                if not isinstance(vals, list) or not vals:
                    continue
                vs = ", ".join(str(v.get("value") if isinstance(v, dict) else v) for v in vals[:3])
                bit = f"{t}.{c}=[{vs}]"
                score = 0
                if query_tokens:
                    score = len(_tok(f"{t} {c} {vs}") & query_tokens)
                scored.append((score, bit))
        scored.sort(key=lambda x: (-x[0], x[1]))
        bits = [b for _, b in scored[:max_bits]]
        if bits:
            parts.append("Retrieved cell values: " + "; ".join(bits))
    return "\n".join(parts)


_ANSWER_COL_HINTS = {
    "phone",
    "telephone",
    "email",
    "mail",
    "address",
    "name",
    "county",
    "city",
    "district",
    "school",
    "admemail",
    "admemail1",
    "admemail2",
    "admemail3",
}


def filter_context_for_step(
    ctx: Dict[str, Any],
    *,
    sub_question: str,
    preceding_ctes: Optional[List[str]] = None,
    evidence: str = "",
    question: str = "",
    min_cols_per_table: int = 2,
    max_cols_per_table: int = 8,
    max_tables: int = 2,
    always_keep_keys: bool = True,
) -> Dict[str, Any]:
    """Cheap step filter v2 over already-linked schema (no LLM / no re-link).

    Guards vs v1 failure modes:
      - never introduce tables outside final_linked / linked schema
      - table selection prefers names mentioned in the sub-question / preceding SQL
      - column scoring uses names+descriptions only (not value examples → less false district hits)
      - always keep PK/FK and answer-ish columns from the original question
    """
    sch = ctx.get("linked_schema") or {}
    tables_all = (sch.get("tables") or {}) if isinstance(sch, dict) else {}
    final_linked = ctx.get("final_linked") or {}
    # Restrict universe to final_linked tables when available
    if isinstance(final_linked, dict) and final_linked:
        allowed_tables = [t for t in final_linked.keys() if t in tables_all]
    else:
        allowed_tables = list(tables_all.keys())

    qtok = _tok(sub_question) | _tok(evidence)
    qtok_orig = _tok(question) | qtok
    prec_text = "\n".join(preceding_ctes or [])
    prec_tok = _tok(prec_text)
    mentioned: Set[str] = set()
    for m in re.findall(r"`([^`]+)`|\"([^\"]+)\"|\[([^\]]+)\]", prec_text + "\n" + (sub_question or "")):
        name = next((x for x in m if x), "")
        if name:
            mentioned.add(name.lower())
    for m in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{1,40})\b", prec_text):
        if len(m) >= 3:
            mentioned.add(m.strip().lower())

    # --- score tables (name-first; avoid example-driven false positives) ---
    table_scores: Dict[str, float] = {}
    for tname in allowed_tables:
        ttok = _tok(tname)
        score = 0.0
        if ttok & qtok:
            score += 10.0  # explicit mention in sub-question/evidence
        if tname.lower() in mentioned or any(x in mentioned for x in ttok):
            score += 12.0  # used in preceding SQL
        if ttok & prec_tok:
            score += 6.0
        # light boost if any column name overlaps subq (not examples)
        tinfo = tables_all.get(tname) or {}
        cols = tinfo.get("columns") or {} if isinstance(tinfo, dict) else {}
        col_hit = 0
        for cname in cols:
            if _tok(cname) & qtok:
                col_hit += 1
        score += min(3.0, col_hit * 0.5)
        table_scores[tname] = score

    # select tables: prefer strong name hits; do NOT pad with weak false-positive tables
    ranked_tables = sorted(table_scores.items(), key=lambda x: (-x[1], x[0]))
    strong = [t for t, s in ranked_tables if s >= 6.0]
    medium = [t for t, s in ranked_tables if 3.0 <= s < 6.0]
    if strong:
        selected = strong[:]
        # only add medium if we still have room and they are join-needed via preceding mention
        for t in medium:
            if len(selected) >= max_tables:
                break
            if table_scores.get(t, 0) >= 3.0 and (
                t.lower() in mentioned or (_tok(t) & prec_tok)
            ):
                selected.append(t)
    else:
        # no strong hit: take top medium/positive up to max_tables
        selected = [t for t, s in ranked_tables if s > 0][:max_tables]
    if not selected:
        selected = [t for t, _ in ranked_tables[:1]] or allowed_tables[:1]

    keep: Dict[str, Set[str]] = {}
    scores_audit: Dict[str, List[Tuple[str, float]]] = {}

    def _is_answer_col(cname: str) -> bool:
        ct = _tok(cname)
        # original question asks for phone/email/etc.
        if ct & qtok_orig & _ANSWER_COL_HINTS:
            return True
        if ct & _ANSWER_COL_HINTS and (_ANSWER_COL_HINTS & qtok_orig):
            return True
        # column name itself is phone/email-like and question has those words
        cl = cname.lower().replace(" ", "").replace("_", "")
        for h in ("phone", "email", "admemail", "telephone"):
            if h in cl and (h in qtok_orig or "email" in qtok_orig or "phone" in qtok_orig or "telephone" in qtok_orig):
                return True
        return False

    for tname in selected:
        tinfo = tables_all.get(tname) or {}
        if not isinstance(tinfo, dict):
            continue
        cols = tinfo.get("columns") or {}
        # if final_linked lists columns, prefer that universe
        fl_cols = None
        if isinstance(final_linked, dict) and isinstance(final_linked.get(tname), list):
            fl_cols = set(final_linked[tname])
        scored: List[Tuple[float, str, bool]] = []
        for cname, cinfo in cols.items():
            if not isinstance(cinfo, dict):
                continue
            if fl_cols is not None and cname not in fl_cols and not (
                cinfo.get("primary_key") or cinfo.get("foreign_keys") or _is_answer_col(cname)
            ):
                # still allow PK/FK/answer cols even if not in final_linked list
                continue
            must = False
            if always_keep_keys and (cinfo.get("primary_key") or cinfo.get("foreign_keys")):
                must = True
            if _is_answer_col(cname):
                must = True
            ctok = _tok(cname) | _tok(cinfo.get("description") or "")
            # NOTE: do NOT fold value_examples into score (avoids Fresno→District Name trap)
            score = float(len(ctok & qtok) * 3 + len(ctok & prec_tok))
            if cname.lower() in mentioned or any(x in mentioned for x in ctok):
                score += 5.0
                must = True
            if must:
                score += 8.0
            scored.append((score, cname, must))
        scored.sort(key=lambda x: (-x[0], x[1]))
        scores_audit[tname] = [(c, s) for s, c, _ in scored]
        chosen: Set[str] = set()
        for s, c, must in scored:
            if must:
                chosen.add(c)
        for s, c, must in scored:
            if must or s > 0:
                chosen.add(c)
            if len(chosen) >= max_cols_per_table:
                break
        # ensure must-haves even beyond cap
        for s, c, must in scored:
            if must:
                chosen.add(c)
        if len(chosen) < min_cols_per_table:
            for _, c, _ in scored[:min_cols_per_table]:
                chosen.add(c)
        if not chosen:
            chosen = set(cols.keys()) if fl_cols is None else set(fl_cols) & set(cols.keys())
            if not chosen:
                chosen = set(cols.keys())
        keep[tname] = chosen

    profile = _schema_profile_from_linked(sch, keep_cols=keep)
    if not profile.strip():
        profile = ctx.get("schema_profile") or _schema_profile_from_linked(sch)
        keep = {
            t: set((ti.get("columns") or {}).keys())
            for t, ti in tables_all.items()
            if isinstance(ti, dict) and t in allowed_tables
        }
        used_full = True
    else:
        used_full = False

    vr_hint = _vr_hint_from(
        ctx.get("value_keywords") or [],
        ctx.get("retrieved_values") or {},
        keep_cols=keep,
        query_tokens=qtok,
    )
    if not vr_hint:
        vr_hint = ctx.get("value_retrieval_hint") or ""

    n_full = sum(
        len((tables_all.get(t) or {}).get("columns") or {})
        for t in allowed_tables
        if isinstance(tables_all.get(t), dict)
    )
    n_kept = sum(len(v) for v in keep.values())
    return {
        "schema_profile": profile,
        "value_retrieval_hint": vr_hint,
        "keep_cols": {k: sorted(v) for k, v in keep.items()},
        "audit": {
            "step_schema_filter": True,
            "step_schema_filter_version": "v2",
            "used_full_fallback": used_full,
            "n_cols_full": n_full,
            "n_cols_kept": n_kept,
            "n_tables_allowed": len(allowed_tables),
            "n_tables_kept": len(keep),
            "table_scores": {t: table_scores.get(t, 0) for t in allowed_tables},
            "selected_tables": list(keep.keys()),
            "top_scores": {t: scores_audit[t][:5] for t in list(scores_audit)[:6]},
        },
    }


def load_few_shots(qid: str, k: int = 4) -> List[Dict[str, str]]:
    global _few_cache
    if _few_cache is None:
        _few_cache = json.loads(_FEW.read_text(encoding="utf-8"))
    raw = _few_cache.get(str(qid)) or _few_cache.get(int(qid) if str(qid).isdigit() else qid) or []
    out = []
    for ex in raw[:k]:
        if isinstance(ex, dict) and ex.get("question") and ex.get("sql"):
            out.append({"question": ex["question"], "sql": ex["sql"]})
    return out


def load_deepeye_context(qid: str) -> Dict[str, Any]:
    """Return linked schema profile + value-retrieval meta for one qid."""
    qid = str(qid)
    if qid in _cache:
        return _cache[qid]

    paths = [_C14, _FULL]
    found = None
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                o = json.loads(line)
                if str((o.get("input") or {}).get("question_id")) != qid:
                    continue
                found = o
                break
        if found:
            break

    if not found:
        ctx = {"ok": False, "schema_profile": "", "source": None}
        _cache[qid] = ctx
        return ctx

    art = found.get("pipeline_artifacts") or {}
    sl = art.get("schema_linking") or {}
    vr = art.get("value_retrieval") or {}
    sch = sl.get("database_schema_after_schema_linking") or {}
    profile = _schema_profile_from_linked(sch)
    keywords = vr.get("question_keywords") or []
    retrieved = vr.get("retrieved_values") or {}
    vr_hint = _vr_hint_from(keywords, retrieved if isinstance(retrieved, dict) else {})

    ctx = {
        "ok": bool(profile),
        "schema_profile": profile,
        "linked_schema": sch,
        "final_linked": sl.get("final_linked_tables_and_columns") or {},
        "value_keywords": keywords,
        "retrieved_values": retrieved if isinstance(retrieved, dict) else {},
        "value_retrieval_hint": vr_hint,
        "few_shots": load_few_shots(qid, k=4),
        "source": str(_C14 if found else _FULL),
        "db_id": (found.get("input") or {}).get("database_id"),
        "question": (found.get("input") or {}).get("question"),
        "evidence": (found.get("input") or {}).get("evidence"),
    }
    _cache[qid] = ctx
    return ctx
