"""
Per-temperature schema diversity for Mode C diverse CTE generation.

| Temp | Strategy                          | LLM calls/CTE |
|------|-----------------------------------|---------------|
| 0.3  | full schema, original order       | 1             |
| 0.6  | full schema, relevance-sorted     | 1             |
| 0.9  | two-step linking → reduced schema | 2             |

Default ON when MCTS_CTE_DIVERSE_PROMPT=1. Set MCTS_SCHEMA_DIVERSITY=0 to disable.
"""

from __future__ import annotations

import json
import logging
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from openai import OpenAI

logger = logging.getLogger(__name__)

ENV_SCHEMA_DIVERSITY = "MCTS_SCHEMA_DIVERSITY"
ENV_FK_PK_CLOSURE = "MCTS_FK_PK_CLOSURE"
ENV_REVERSED_SCHEMA_LINKING = "MCTS_REVERSED_SCHEMA_LINKING"
ENV_COMBINED_SCHEMA_LINKING = "MCTS_COMBINED_SCHEMA_LINKING"
LINKING_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "schema_linking_prompt.txt"
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+`?([^`\s(]+)`?\s*\(",
    re.IGNORECASE,
)
FK_PAIR_RE = re.compile(
    r"([`']?(\w+)[`']?)\s*\.\s*([`']?(\w+)[`']?)\s*=\s*([`']?(\w+)[`']?)\s*\.\s*([`']?(\w+)[`']?)",
    re.IGNORECASE,
)


def schema_diversity_enabled() -> bool:
    raw = os.environ.get(ENV_SCHEMA_DIVERSITY)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    # B′ default: schema diversity follows Mode C diverse prompt
    from .cte_diverse import diverse_prompt_enabled

    return diverse_prompt_enabled()


def fk_pk_closure_enabled() -> bool:
    raw = os.environ.get(ENV_FK_PK_CLOSURE, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def reversed_schema_linking_enabled() -> bool:
    raw = os.environ.get(ENV_REVERSED_SCHEMA_LINKING, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def combined_schema_linking_enabled() -> bool:
    """6th Mode C branch: union narrow-linking schema + reversed prior-SQL schema."""
    raw = os.environ.get(ENV_COMBINED_SCHEMA_LINKING, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _split_schema_parts(schema_info: str) -> Tuple[str, str, str]:
    """Return (header, ddl_body, fk_tail)."""
    if not schema_info:
        return "", "", ""
    fk_idx = schema_info.find("foreign_key:")
    if fk_idx >= 0:
        head = schema_info[:fk_idx]
        fk_tail = schema_info[fk_idx:]
    else:
        head = schema_info
        fk_tail = ""
    lines = head.split("\n", 1)
    if lines and lines[0].strip().lower().startswith("db_name:"):
        header = lines[0] + "\n"
        ddl_body = lines[1] if len(lines) > 1 else ""
    else:
        header = ""
        ddl_body = head
    return header, ddl_body, fk_tail


def parse_table_blocks(schema_info: str) -> List[Tuple[str, str]]:
    """Parse CREATE TABLE blocks from schema_info."""
    _, ddl_body, _ = _split_schema_parts(schema_info)
    if not ddl_body.strip():
        return []
    blocks: List[Tuple[str, str]] = []
    parts = re.split(r"(?=CREATE\s+TABLE\s+)", ddl_body, flags=re.IGNORECASE)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = CREATE_TABLE_RE.search(part)
        if not m:
            continue
        name = m.group(1).strip("`").lower()
        blocks.append((name, part.strip()))
    return blocks


def _table_relevance_score(question: str, table_name: str, ddl: str) -> float:
    q_lower = question.lower()
    q_words = set(re.findall(r"[a-z0-9_]+", q_lower))
    name = table_name.lower()
    name_score = SequenceMatcher(None, q_lower, name).ratio()
    t_words = set(name.split("_"))
    overlap = len(q_words & t_words) / max(len(q_words), 1)
    desc_blob = ddl.lower()
    desc_score = SequenceMatcher(None, q_lower, desc_blob[:500]).ratio() * 0.5
    return name_score * 0.3 + overlap * 0.4 + desc_score * 0.3


def sort_tables_by_relevance(question: str, schema_info: str) -> Tuple[str, List[str]]:
    """Reorder tables by question relevance; keep all tables."""
    header, _, fk_tail = _split_schema_parts(schema_info)
    blocks = parse_table_blocks(schema_info)
    if not blocks:
        return schema_info, []
    scored = [
        (name, ddl, _table_relevance_score(question, name, ddl))
        for name, ddl in blocks
    ]
    scored.sort(key=lambda x: -x[2])
    order = [x[0] for x in scored]
    ddl_sorted = "\n".join(x[1] for x in scored)
    rebuilt = f"{header}{ddl_sorted}\n{fk_tail}".strip()
    return rebuilt, order


def _parse_fk_tables(fk_tail: str) -> Set[str]:
    tables: Set[str] = set()
    for m in FK_PAIR_RE.finditer(fk_tail or ""):
        tables.add(m.group(2).lower())
        tables.add(m.group(6).lower())
    return tables


def _parse_pk_tables(ddl_body: str) -> Dict[str, Set[str]]:
    """Map table_name -> set of PK column names (lower) from CREATE TABLE blocks."""
    out: Dict[str, Set[str]] = {}
    for name, ddl in parse_table_blocks(ddl_body):
        pk_cols: Set[str] = set()
        m = re.search(r"PRIMARY\s+KEY\s*\(([^)]+)\)", ddl, re.IGNORECASE)
        if m:
            for part in m.group(1).split(","):
                col = part.strip().strip("`\"'[]")
                if col:
                    pk_cols.add(col.lower())
        if pk_cols:
            out[name] = pk_cols
    return out


def extract_tables_from_sql(sql: str) -> List[str]:
    """Extract referenced table names from SQL (sqlglot if available, else regex)."""
    s = (sql or "").strip()
    if not s:
        return []
    try:
        from sqlglot import parse_one, exp

        ast = parse_one(s, dialect="sqlite", error_level="ignore")
        names = {t.name.lower() for t in ast.find_all(exp.Table) if t.name}
        return sorted(names)
    except Exception:
        pass
    found: Set[str] = set()
    for m in re.finditer(
        r"\b(?:FROM|JOIN|INTO|UPDATE)\s+`?([A-Za-z_][\w]*)`?",
        s,
        re.IGNORECASE,
    ):
        found.add(m.group(1).lower())
    return sorted(found)


def extract_tables_from_sqls(sqls: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for sql in sqls or []:
        for t in extract_tables_from_sql(sql):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def enforce_fk_closure(
    selected: List[str],
    schema_info: str,
    *,
    max_tables: int = 12,
    fk_pk_enhanced: Optional[bool] = None,
) -> Tuple[List[str], List[str]]:
    """Add FK-neighbor tables until closure (bounded).

    fk_pk_enhanced: None = follow MCTS_FK_PK_CLOSURE env; False = basic one-hop FK only.
    """
    blocks = {n: d for n, d in parse_table_blocks(schema_info)}
    sel = {s.strip().lower() for s in selected if s}
    _, ddl_body, fk_tail = _split_schema_parts(schema_info)

    use_enhanced = fk_pk_closure_enabled() if fk_pk_enhanced is None else fk_pk_enhanced
    if use_enhanced:
        pk_map = _parse_pk_tables(ddl_body)
        for t in list(sel):
            if t in pk_map:
                sel.add(t)
        for name, ddl in blocks.items():
            for m in re.finditer(r"REFERENCES\s+`?(\w+)`?", ddl, re.IGNORECASE):
                ref = m.group(1).lower()
                if name in sel:
                    sel.add(ref)
                elif ref in sel:
                    sel.add(name)

    # also parse inline FOREIGN KEY in DDL
    for name, ddl in blocks.items():
        for m in re.finditer(r"REFERENCES\s+`?(\w+)`?", ddl, re.IGNORECASE):
            if name in sel:
                sel.add(m.group(1).lower())
            elif m.group(1).lower() in sel:
                sel.add(name)
    # expand one hop via fk_tail pairs
    for m in FK_PAIR_RE.finditer(fk_tail or ""):
        t1, t2 = m.group(2).lower(), m.group(6).lower()
        if t1 in sel:
            sel.add(t2)
        if t2 in sel:
            sel.add(t1)
    ordered = [t for t, _ in parse_table_blocks(schema_info) if t in sel]
    if len(ordered) > max_tables:
        ordered = ordered[:max_tables]
    added = [t for t in ordered if t not in {s.strip().lower() for s in selected}]
    return ordered, added


def prepare_schema_reversed_from_sqls(
    *,
    prior_sqls: List[str],
    schema_info: str,
    question: str = "",
    sub_question: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """
    Build reduced schema by extracting tables from prior rollout SQLs + FK/PK closure.
    Zero LLM cost (DeepEye ReversedLinker-style, without few-shot SQL gen).
    """
    audit: Dict[str, Any] = {
        "strategy": "reversed_prior_rollout",
        "llm_calls_per_cte": 0,
        "prior_sql_count": len(prior_sqls or []),
        "fk_pk_closure": False,
    }
    extracted = extract_tables_from_sqls(prior_sqls)
    audit["extracted_tables"] = extracted
    if not extracted:
        audit["linking_ok"] = False
        audit["linking_reason"] = "no_prior_sql"
        return schema_info, audit

    closed, added = enforce_fk_closure(extracted, schema_info, fk_pk_enhanced=False)
    audit["linking_ok"] = True
    audit["linking_reason"] = "reversed_from_prior_rollout"
    audit["selected_tables"] = extracted
    audit["closed_tables"] = closed
    audit["fk_closure_added"] = added
    audit["n_tables_after_closure"] = len(closed)
    reduced = filter_schema_tables(schema_info, closed)
    audit["n_tables"] = len(closed)
    return reduced, audit


def fetch_narrow_linking(
    *,
    question: str,
    sub_question: str,
    schema_info: str,
    preceding_cte_info: str,
    llm_config: Dict[str, Any],
    cache: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], str, List[str], Dict[str, Any]]:
    """Resolve narrow schema-linking tables; reuse expand-level cache when provided."""
    meta: Dict[str, Any] = {"reused": False}
    if cache is not None:
        meta["reused"] = True
        obj = cache.get("linking_obj")
        raw = str(cache.get("linking_raw") or "")
        tables = [str(t) for t in (cache.get("narrow_tables") or []) if str(t).strip()]
        return obj, raw, tables, meta

    linking_obj, linking_raw = call_schema_linking(
        llm_config=llm_config,
        question=question,
        sub_question=sub_question,
        schema_info=schema_info,
        preceding_cte_info=preceding_cte_info,
        temperature=0.3,
    )
    narrow_tables: List[str] = []
    if linking_obj and isinstance(linking_obj.get("selected_tables"), list):
        narrow_tables = [str(t) for t in linking_obj["selected_tables"] if str(t).strip()]
    return linking_obj, linking_raw, narrow_tables, meta


def precompute_narrow_linking_cache(
    *,
    question: str,
    sub_question: str,
    schema_info: str,
    preceding_cte_info: str,
    llm_config: Dict[str, Any],
) -> Dict[str, Any]:
    """One LLM linking call per expand; shared by 0.9 two-step and combined paths."""
    linking_obj, linking_raw, narrow_tables, meta = fetch_narrow_linking(
        question=question,
        sub_question=sub_question,
        schema_info=schema_info,
        preceding_cte_info=preceding_cte_info,
        llm_config=llm_config,
    )
    return {
        "linking_obj": linking_obj,
        "linking_raw": linking_raw,
        "narrow_tables": narrow_tables,
        **meta,
    }


def prepare_schema_combined_narrow_reversed(
    *,
    question: str,
    sub_question: str,
    schema_info: str,
    preceding_cte_info: str,
    llm_config: Dict[str, Any],
    original_schema_info: str,
    prior_rollout_sqls: List[str],
    cached_narrow_linking: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    6th path: merge path-3 narrow linking tables with path-5 reversed prior-SQL tables.
    """
    base_schema = original_schema_info or schema_info
    reused = cached_narrow_linking is not None
    audit: Dict[str, Any] = {
        "strategy": "combined_narrow_reversed",
        "llm_calls_per_cte": 1 if reused else 2,
        "prior_sql_count": len(prior_rollout_sqls or []),
        "narrow_linking_reused": reused,
    }
    _, linking_raw, narrow_tables, link_meta = fetch_narrow_linking(
        llm_config=llm_config,
        question=question,
        sub_question=sub_question,
        schema_info=base_schema,
        preceding_cte_info=preceding_cte_info,
        cache=cached_narrow_linking,
    )
    audit["narrow_linking_reused"] = bool(link_meta.get("reused"))
    audit["linking_raw"] = linking_raw[:2000] if linking_raw else ""
    audit["narrow_selected_tables"] = narrow_tables
    audit["narrow_linking_ok"] = bool(narrow_tables)

    reversed_tables = extract_tables_from_sqls(prior_rollout_sqls or [])
    audit["reversed_extracted_tables"] = reversed_tables
    if reversed_tables:
        reversed_closed, reversed_added = enforce_fk_closure(
            reversed_tables, base_schema, fk_pk_enhanced=False
        )
    else:
        reversed_closed, reversed_added = [], []
    audit["reversed_closed_tables"] = reversed_closed
    audit["reversed_fk_closure_added"] = reversed_added

    merged = list(dict.fromkeys([*narrow_tables, *reversed_closed]))
    audit["merged_tables_before_closure"] = merged
    if not merged:
        audit["linking_ok"] = False
        audit["linking_reason"] = "empty_narrow_and_reversed"
        header, _, fk_tail = _split_schema_parts(base_schema)
        empty = f"{header}{fk_tail}".strip() if header or fk_tail else ""
        return empty or base_schema[:200], audit

    closed, added = enforce_fk_closure(merged, base_schema, fk_pk_enhanced=False)
    audit["linking_ok"] = True
    audit["linking_reason"] = "combined_narrow_plus_reversed"
    audit["closed_tables"] = closed
    audit["fk_closure_added"] = added
    audit["n_tables_after_closure"] = len(closed)
    reduced = filter_schema_tables(base_schema, closed)
    audit["n_tables"] = len(closed)
    return reduced, audit


def filter_schema_tables(schema_info: str, table_names: List[str]) -> str:
    header, _, fk_tail = _split_schema_parts(schema_info)
    want = {t.strip().lower() for t in table_names}
    blocks = [(n, d) for n, d in parse_table_blocks(schema_info) if n in want]
    if not blocks:
        return schema_info
    ddl = "\n".join(d for _, d in blocks)
    return f"{header}{ddl}\n{fk_tail}".strip()


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def call_schema_linking(
    *,
    llm_config: Dict[str, Any],
    question: str,
    sub_question: str,
    schema_info: str,
    preceding_cte_info: str,
    timeout_s: float = 90.0,
    temperature: float = 0.3,
) -> Tuple[Optional[Dict[str, Any]], str]:
    template = LINKING_PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = template.format(
        question=question,
        sub_question=sub_question,
        schema_info=schema_info[:6000] + ("..." if len(schema_info) > 6000 else ""),
        preceding_cte_info=preceding_cte_info or "None",
    )
    config = llm_config.get("config_list", [{}])[0]
    client = OpenAI(
        base_url=config.get("base_url"),
        api_key=config.get("api_key", "dummy-key"),
        timeout=timeout_s,
    )
    response = client.chat.completions.create(
        model=config.get("model"),
        messages=[
            {"role": "system", "content": "Output strict JSON only for schema linking."},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        n=1,
    )
    raw = response.choices[0].message.content or ""
    obj = _extract_json_object(raw)
    return obj, raw


def strategy_for_temperature(temp: float) -> str:
    if temp <= 0.45:
        return "full_original"
    if temp <= 0.75:
        return "full_sorted_by_relevance"
    return "two_step_linking"


def prepare_schema_for_temp(
    *,
    temperature: float,
    question: str,
    sub_question: str,
    schema_info: str,
    preceding_cte_info: str,
    llm_config: Dict[str, Any],
    original_schema_info: str,
    prior_rollout_sqls: Optional[List[str]] = None,
    schema_strategy_override: Optional[str] = None,
    cached_narrow_linking: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Return (schema_for_prompt, per_temp_audit_record).
    For 0.9: performs linking LLM call; no fallback to full schema on failure.
    """
    if schema_strategy_override == "reversed_prior_rollout":
        return prepare_schema_reversed_from_sqls(
            prior_sqls=prior_rollout_sqls or [],
            schema_info=original_schema_info or schema_info,
            question=question,
            sub_question=sub_question,
        )
    if schema_strategy_override == "combined_narrow_reversed":
        return prepare_schema_combined_narrow_reversed(
            question=question,
            sub_question=sub_question,
            schema_info=schema_info,
            preceding_cte_info=preceding_cte_info,
            llm_config=llm_config,
            original_schema_info=original_schema_info or schema_info,
            prior_rollout_sqls=prior_rollout_sqls or [],
            cached_narrow_linking=cached_narrow_linking,
        )

    strat = strategy_for_temperature(temperature)
    n_tables = len(parse_table_blocks(schema_info))
    audit: Dict[str, Any] = {
        "temp": temperature,
        "strategy": strat,
        "n_tables": n_tables,
        "llm_calls_per_cte": 1,
    }
    if strat == "full_original":
        audit["table_order"] = [n for n, _ in parse_table_blocks(schema_info)]
        return schema_info, audit

    if strat == "full_sorted_by_relevance":
        sorted_schema, order = sort_tables_by_relevance(question, schema_info)
        audit["table_order"] = order
        audit["n_tables"] = len(order)
        return sorted_schema, audit

    # two_step_linking @ 0.9
    audit["llm_calls_per_cte"] = 2
    linking_obj, linking_raw, narrow_tables, link_meta = fetch_narrow_linking(
        llm_config=llm_config,
        question=question,
        sub_question=sub_question,
        schema_info=original_schema_info or schema_info,
        preceding_cte_info=preceding_cte_info,
        cache=cached_narrow_linking,
    )
    audit["narrow_linking_reused"] = bool(link_meta.get("reused"))
    audit["linking_raw"] = linking_raw[:2000] if linking_raw else ""
    if not linking_obj or not narrow_tables:
        audit["linking_ok"] = False
        audit["linking_reason"] = "parse_failed"
        audit["selected_tables"] = []
        audit["n_tables_after_closure"] = 0
        # reduced empty — no fallback per experiment spec
        header, _, fk_tail = _split_schema_parts(schema_info)
        empty = f"{header}{fk_tail}".strip() if header or fk_tail else ""
        return empty or schema_info[:200], audit

    selected = list(narrow_tables)
    if prior_rollout_sqls and reversed_schema_linking_enabled():
        rev_tables = extract_tables_from_sqls(prior_rollout_sqls)
        merged = list(dict.fromkeys([*selected, *rev_tables]))
        audit["reversed_merged_tables"] = rev_tables
        selected = merged
    closed, added = enforce_fk_closure(selected, original_schema_info or schema_info)
    audit["linking_ok"] = True
    audit["linking_reason"] = str(linking_obj.get("reason") or "")
    audit["fk_pk_closure"] = fk_pk_closure_enabled()
    audit["selected_tables"] = selected
    audit["closed_tables"] = closed
    audit["fk_closure_added"] = added
    audit["n_tables_after_closure"] = len(closed)
    reduced = filter_schema_tables(original_schema_info or schema_info, closed)
    return reduced, audit
