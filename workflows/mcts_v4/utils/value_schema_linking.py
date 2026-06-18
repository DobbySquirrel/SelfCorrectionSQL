"""
Alpha-SQL LSH value retrieval → Mode C 6th path (DeepEye value-linking style, no embedding API).

Uses precomputed ``relevant_values_for_all_tasks.pkl`` (LSH + edit + embedding at preprocess time).
At MCTS runtime: narrow schema to value-linked tables/columns + inject retrieved cell values.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schema_diversity import enforce_fk_closure, filter_schema_tables, parse_table_blocks

logger = logging.getLogger(__name__)

ENV_VALUE_SCHEMA_LINKING = "MCTS_VALUE_SCHEMA_LINKING"
ENV_ALPHA_RELEVANT_VALUES_PKL = "MCTS_ALPHA_RELEVANT_VALUES_PKL"
ENV_ALPHA_PPL_FOR_QID = "MCTS_ALPHA_PPL_FOR_QID"
ENV_VALUE_HINT_MAX_PAIRS = "MCTS_VALUE_HINT_MAX_PAIRS"

DEFAULT_PKL = (
    Path(__file__).resolve().parents[3]
    / "Alpha-SQL-2.2.4/data/preprocessed/arcwise/dev/relevant_values_for_all_tasks.pkl"
)
DEFAULT_PPL = (
    Path(__file__).resolve().parents[3]
    / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"
)

MODE_C_VALUE_PATH_TEMP = 0.75

_BY_QID: Optional[Dict[str, Dict[Tuple[str, str], List[str]]]] = None
_INDEX_ERROR: Optional[str] = None

COLUMN_LINE_RE = re.compile(
    r"^(\t`[^`]+`[^\n]*)",
    re.MULTILINE,
)


def value_schema_linking_enabled() -> bool:
    raw = os.environ.get(ENV_VALUE_SCHEMA_LINKING, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _pkl_path() -> Path:
    return Path(os.environ.get(ENV_ALPHA_RELEVANT_VALUES_PKL, str(DEFAULT_PKL)))


def _ppl_path() -> Path:
    return Path(os.environ.get(ENV_ALPHA_PPL_FOR_QID, str(DEFAULT_PPL)))


def _max_hint_pairs() -> int:
    try:
        return max(1, int(os.environ.get(ENV_VALUE_HINT_MAX_PAIRS, "48")))
    except ValueError:
        return 48


def _ensure_index() -> None:
    global _BY_QID, _INDEX_ERROR
    if _BY_QID is not None or _INDEX_ERROR is not None:
        return
    pkl_path = _pkl_path()
    ppl_path = _ppl_path()
    if not pkl_path.is_file():
        _INDEX_ERROR = f"missing pkl: {pkl_path}"
        _BY_QID = {}
        return
    if not ppl_path.is_file():
        _INDEX_ERROR = f"missing ppl: {ppl_path}"
        _BY_QID = {}
        return
    try:
        with pkl_path.open("rb") as f:
            rv_list = pickle.load(f)
        ppl_rows = json.loads(ppl_path.read_text(encoding="utf-8"))
        by_qid: Dict[str, Dict[Tuple[str, str], List[str]]] = {}
        for i, row in enumerate(ppl_rows):
            qid = str(row.get("question_id", i))
            raw = rv_list[i] if i < len(rv_list) else {}
            norm: Dict[Tuple[str, str], List[str]] = {}
            if hasattr(raw, "items"):
                for (tbl, col), vals in raw.items():
                    tbl_n = str(tbl).strip().lower()
                    col_n = str(col).strip()
                    vlist = [str(v) for v in (vals or []) if str(v).strip()]
                    if vlist:
                        norm[(tbl_n, col_n)] = vlist
            by_qid[qid] = norm
        _BY_QID = by_qid
        logger.info("value_schema_linking: loaded %d qids from %s", len(by_qid), pkl_path)
    except Exception as e:
        _INDEX_ERROR = str(e)
        _BY_QID = {}
        logger.warning("value_schema_linking index failed: %s", e)


def get_relevant_values_for_qid(question_id: int | str) -> Dict[Tuple[str, str], List[str]]:
    _ensure_index()
    return dict((_BY_QID or {}).get(str(question_id), {}))


def format_value_hint_block(
    relevant_values: Dict[Tuple[str, str], List[str]],
    *,
    max_pairs: Optional[int] = None,
) -> str:
    """DeepEye-style value examples block for additional_context."""
    if not relevant_values:
        return ""
    cap = max_pairs if max_pairs is not None else _max_hint_pairs()
    lines = [
        "## Retrieved cell values (Alpha-SQL LSH + embedding, for WHERE/JOIN literals)",
    ]
    n = 0
    for (tbl, col), vals in sorted(relevant_values.items()):
        if n >= cap:
            lines.append("... (truncated)")
            break
        shown = ", ".join(f"`{v}`" for v in vals[:5])
        lines.append(f"- `{tbl}`.`{col}`: {shown}")
        n += 1
    return "\n".join(lines)


def _filter_table_columns(ddl: str, keep_cols: List[str]) -> str:
    """Keep only listed columns in one CREATE TABLE block (best-effort)."""
    if not keep_cols:
        return ddl
    want = {c.lower() for c in keep_cols}
    m = re.search(r"(CREATE\s+TABLE\s+`[^`]+`\s*\()", ddl, re.IGNORECASE)
    if not m:
        return ddl
    prefix = ddl[: m.end()]
    rest = ddl[m.end() :]
    end_paren = rest.rfind(");")
    if end_paren < 0:
        return ddl
    body = rest[:end_paren]
    suffix = rest[end_paren:]
    kept_lines: List[str] = []
    for line in body.split("\n"):
        col_m = re.match(r"\s*`([^`]+)`", line)
        if col_m and col_m.group(1).lower() in want:
            kept_lines.append(line)
    if not kept_lines:
        return ddl
    return prefix + "\n".join(kept_lines) + suffix


def filter_schema_tables_and_columns(
    schema_info: str,
    linked: Dict[Tuple[str, str], List[str]],
) -> str:
    """Reduce schema to value-linked tables; trim columns where possible."""
    tables = sorted({tbl for tbl, _ in linked.keys()})
    if not tables:
        return schema_info
    header, _, fk_tail = schema_info, "", ""
    from .schema_diversity import _split_schema_parts

    header, _, fk_tail = _split_schema_parts(schema_info)
    blocks = {n: d for n, d in parse_table_blocks(schema_info)}
    parts: List[str] = []
    for tbl in tables:
        ddl = blocks.get(tbl)
        if not ddl:
            continue
        cols = [col for t, col in linked.keys() if t == tbl]
        parts.append(_filter_table_columns(ddl, cols))
    if not parts:
        return filter_schema_tables(schema_info, tables)
    ddl_joined = "\n".join(parts)
    return f"{header}{ddl_joined}\n{fk_tail}".strip()


def prepare_schema_value_linked(
    *,
    question_id: int | str,
    schema_info: str,
    question: str = "",
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Value-linked schema (0 LLM). Returns (schema_for_prompt, value_hint_block, audit).
    Inspired by DeepEye ValueLinker: keep columns with retrieved values + FK closure.
    """
    audit: Dict[str, Any] = {
        "strategy": "value_linked_alpha",
        "llm_calls_per_cte": 0,
        "question_id": str(question_id),
    }
    relevant = get_relevant_values_for_qid(question_id)
    audit["n_value_pairs"] = len(relevant)
    if _INDEX_ERROR:
        audit["index_error"] = _INDEX_ERROR

    if not relevant:
        audit["linking_ok"] = False
        audit["linking_reason"] = "no_relevant_values"
        return schema_info, "", audit

    tables = sorted({tbl for tbl, _ in relevant.keys()})
    closed, added = enforce_fk_closure(tables, schema_info, fk_pk_enhanced=False)
    audit["selected_tables"] = tables
    audit["closed_tables"] = closed
    audit["fk_closure_added"] = added
    audit["n_tables_after_closure"] = len(closed)

    linked_for_schema = {
        (tbl, col): vals
        for (tbl, col), vals in relevant.items()
        if tbl in {t.lower() for t in closed}
    }
    reduced = filter_schema_tables_and_columns(schema_info, linked_for_schema)
    value_block = format_value_hint_block(relevant)
    audit["linking_ok"] = True
    audit["linking_reason"] = "alpha_lsh_relevant_values"
    audit["n_tables"] = len(closed)
    audit["value_hint_chars"] = len(value_block)
    return reduced, value_block, audit
