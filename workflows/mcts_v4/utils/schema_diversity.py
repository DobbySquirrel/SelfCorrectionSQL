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


def enforce_fk_closure(
    selected: List[str],
    schema_info: str,
    *,
    max_tables: int = 12,
) -> Tuple[List[str], List[str]]:
    """Add FK-neighbor tables until closure (bounded)."""
    blocks = {n: d for n, d in parse_table_blocks(schema_info)}
    sel = {s.strip().lower() for s in selected if s}
    _, _, fk_tail = _split_schema_parts(schema_info)
    fk_tables = _parse_fk_tables(fk_tail)
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
) -> Tuple[str, Dict[str, Any]]:
    """
    Return (schema_for_prompt, per_temp_audit_record).
    For 0.9: performs linking LLM call; no fallback to full schema on failure.
    """
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
    linking_obj, linking_raw = call_schema_linking(
        llm_config=llm_config,
        question=question,
        sub_question=sub_question,
        schema_info=original_schema_info or schema_info,
        preceding_cte_info=preceding_cte_info,
        temperature=0.3,
    )
    audit["linking_raw"] = linking_raw[:2000] if linking_raw else ""
    if not linking_obj or not isinstance(linking_obj.get("selected_tables"), list):
        audit["linking_ok"] = False
        audit["linking_reason"] = "parse_failed"
        audit["selected_tables"] = []
        audit["n_tables_after_closure"] = 0
        # reduced empty — no fallback per experiment spec
        header, _, fk_tail = _split_schema_parts(schema_info)
        empty = f"{header}{fk_tail}".strip() if header or fk_tail else ""
        return empty or schema_info[:200], audit

    selected = [str(t) for t in linking_obj["selected_tables"] if str(t).strip()]
    closed, added = enforce_fk_closure(selected, original_schema_info or schema_info)
    audit["linking_ok"] = True
    audit["linking_reason"] = str(linking_obj.get("reason") or "")
    audit["selected_tables"] = selected
    audit["closed_tables"] = closed
    audit["fk_closure_added"] = added
    audit["n_tables_after_closure"] = len(closed)
    reduced = filter_schema_tables(original_schema_info or schema_info, closed)
    return reduced, audit
