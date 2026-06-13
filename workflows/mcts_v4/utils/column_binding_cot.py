"""
SQL-pre column/value/function binding CoT.

Modes (MCTS_COLUMN_BINDING_COT):
  - 1 / unified: once per question after decompose (v1)
  - per_subq: binding prompt for sub-questions (v2)

Scopes (MCTS_COLUMN_BINDING_SCOPE):
  - global / question / once: one binding for the whole question (dual03 default)
  - per_subq / per_decompose / decompose: one binding per decomposed sub-question index
  - expand (default when unset): one binding per expand path (sub-q + preceding CTE hash)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .openai_client_pool import get_openai_client

logger = logging.getLogger(__name__)

ENV_COLUMN_BINDING_COT = "MCTS_COLUMN_BINDING_COT"
ENV_COLUMN_BINDING_SCOPE = "MCTS_COLUMN_BINDING_SCOPE"
ENV_COLUMN_BINDING_HINT_CHAIN = "MCTS_COLUMN_BINDING_HINT_CHAIN"
MODE_UNIFIED = "unified"
MODE_PER_SUBQ = "per_subq"
UNIFIED_TEMPLATE = Path(__file__).resolve().parent.parent / "prompts" / "column_binding_unified.txt"
PER_SUBQ_TEMPLATE = Path(__file__).resolve().parent.parent / "prompts" / "column_binding_per_subq.txt"
_SCHEMA_MAX_CHARS = 8000
_MAX_TOKENS = 800


def _parse_column_binding_env() -> Tuple[Optional[str], Optional[float], bool]:
    raw = os.environ.get(ENV_COLUMN_BINDING_COT, "0").strip().lower()
    if not raw or raw in ("0", "false", "no", "off"):
        return None, None, False
    temp_gate: Optional[float] = None
    dual_low_temp = False
    if "@" in raw:
        base, gate_s = raw.split("@", 1)
        raw = base.strip()
        if "+dual" in gate_s or gate_s.endswith(":dual"):
            dual_low_temp = True
            gate_s = gate_s.replace("+dual", "").replace(":dual", "").strip()
        try:
            temp_gate = float(gate_s.strip())
        except ValueError:
            temp_gate = None
    if raw in ("1", "true", "yes", "unified"):
        return MODE_UNIFIED, temp_gate, False
    if raw in ("per_subq", "v2", "subq"):
        return MODE_PER_SUBQ, temp_gate, dual_low_temp
    return None, None, False


def column_binding_mode() -> Optional[str]:
    mode, _, _ = _parse_column_binding_env()
    return mode


def column_binding_temp_gate() -> Optional[float]:
    _, gate, _ = _parse_column_binding_env()
    return gate


def column_binding_dual_low_temp() -> bool:
    _, _, dual = _parse_column_binding_env()
    return dual


def _temp_in_binding_gate(temperature: float, gate: Optional[float]) -> bool:
    if gate is None:
        return True
    return float(temperature) <= gate + 0.15


def column_binding_dual_at_temp(temperature: float) -> bool:
    mode, gate, dual = _parse_column_binding_env()
    if mode != MODE_PER_SUBQ or not dual:
        return False
    return _temp_in_binding_gate(temperature, gate)


def column_binding_replace_at_temp(temperature: float) -> bool:
    """Single call at temp with binding context (replaces plain)."""
    mode, gate, dual = _parse_column_binding_env()
    if mode != MODE_PER_SUBQ:
        return False
    if dual and column_binding_dual_at_temp(temperature):
        return False
    return _temp_in_binding_gate(temperature, gate)


def column_binding_applies_at_temp(temperature: float) -> bool:
    return column_binding_replace_at_temp(temperature) or column_binding_dual_at_temp(temperature)


def column_binding_cot_enabled() -> bool:
    return column_binding_mode() is not None


def column_binding_unified_enabled() -> bool:
    return column_binding_mode() == MODE_UNIFIED


def column_binding_per_subq_enabled() -> bool:
    return column_binding_mode() == MODE_PER_SUBQ


def _column_binding_scope_raw() -> str:
    return os.environ.get(ENV_COLUMN_BINDING_SCOPE, "expand").strip().lower()


def column_binding_scope_global() -> bool:
    return _column_binding_scope_raw() in (
        "global",
        "question",
        "once",
    )


def column_binding_scope_per_subq_decompose() -> bool:
    """One binding per decomposed sub-question index (not per expand path)."""
    return _column_binding_scope_raw() in (
        "per_subq",
        "per_decompose",
        "decompose",
    )


def column_binding_hint_chain_enabled() -> bool:
    """Per expand: fresh binding LLM on hint@0.3 branch, then CTE (not global reuse)."""
    return os.environ.get(ENV_COLUMN_BINDING_HINT_CHAIN, "0") == "1"


def _schema_for_prompt(schema_info: str) -> str:
    text = (schema_info or "").strip()
    if len(text) > _SCHEMA_MAX_CHARS:
        return text[:_SCHEMA_MAX_CHARS] + "\n..."
    return text


def parse_column_binding(text: str) -> Dict[str, Any]:
    """Light parse of unified binding output for audit."""
    columns: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        col_m = re.match(r"^Column:\s*(.+)$", stripped, re.I)
        if col_m:
            if current:
                columns.append(current)
            current = {"column": col_m.group(1).strip()}
            continue
        for key, pat in (
            ("condition", r"^Condition:\s*(.+)$"),
            ("function", r"^Function:\s*(.+)$"),
            ("reasoning", r"^Reasoning:\s*(.+)$"),
        ):
            m = re.match(pat, stripped, re.I)
            if m and current is not None:
                current[key] = m.group(1).strip()
                break
    if current:
        columns.append(current)
    return {
        "columns": columns,
        "n_columns": len(columns),
    }


def _call_per_subq_binding(
    *,
    original_question: str,
    sub_question: str,
    preceding_cte_info: str,
    schema_info: str,
    additional_context: str,
    llm_config: Dict[str, Any],
    timeout_s: float = 120.0,
) -> str:
    config = (llm_config.get("config_list") or [{}])[0]
    base_url = config.get("base_url")
    if not base_url:
        return ""
    client = get_openai_client(
        base_url,
        config.get("api_key", "dummy-key"),
        timeout=timeout_s,
    )
    template = PER_SUBQ_TEMPLATE.read_text(encoding="utf-8")
    prompt = template.format(
        original_question=original_question,
        sub_question=sub_question,
        preceding_cte_info=(preceding_cte_info or "No preceding CTE").strip(),
        schema=_schema_for_prompt(schema_info),
        additional_context=additional_context.strip() or "(none)",
    )
    response = client.chat.completions.create(
        model=config.get("model"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=_MAX_TOKENS,
        n=1,
    )
    return (response.choices[0].message.content or "").strip()


def expand_cache_key(*, sub_question_index: int, preceding_cte_info: str) -> str:
    digest = hashlib.md5((preceding_cte_info or "").encode("utf-8")).hexdigest()[:12]
    return f"{sub_question_index}:{digest}"


def binding_cache_key(*, sub_question_index: int, preceding_cte_info: str) -> str:
    if column_binding_scope_per_subq_decompose():
        return f"sq:{sub_question_index}"
    return expand_cache_key(
        sub_question_index=sub_question_index,
        preceding_cte_info=preceding_cte_info,
    )


def _call_unified_binding(
    *,
    question: str,
    schema_info: str,
    additional_context: str,
    llm_config: Dict[str, Any],
    timeout_s: float = 120.0,
) -> str:
    config = (llm_config.get("config_list") or [{}])[0]
    base_url = config.get("base_url")
    if not base_url:
        return ""
    client = get_openai_client(
        base_url,
        config.get("api_key", "dummy-key"),
        timeout=timeout_s,
    )
    template = UNIFIED_TEMPLATE.read_text(encoding="utf-8")
    prompt = template.format(
        question=question,
        schema=_schema_for_prompt(schema_info),
        additional_context=additional_context.strip() or "(none)",
    )
    response = client.chat.completions.create(
        model=config.get("model"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=_MAX_TOKENS,
        n=1,
    )
    return (response.choices[0].message.content or "").strip()


def format_binding_block(binding_text: str) -> str:
    text = (binding_text or "").strip()
    if not text:
        return ""
    return "Here are my previous thoughts (column binding):\n" + text


def run_column_binding_cot(
    *,
    question: str,
    schema_info: str,
    additional_context: str = "",
    llm_config: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """
    Single-call unified column / value / function binding.

    Returns:
        (updated additional_context, audit dict)
    """
    audit: Dict[str, Any] = {
        "enabled": True,
        "mode": "unified",
        "column_binding": "",
        "parsed": {},
        "llm_calls": 0,
        "elapsed_s": 0.0,
        "error": None,
    }
    t0 = time.time()
    base_hint = additional_context or ""
    try:
        binding_text = _call_unified_binding(
            question=question,
            schema_info=schema_info,
            additional_context=base_hint,
            llm_config=llm_config,
        )
        audit["llm_calls"] = 1
        audit["column_binding"] = binding_text
        audit["parsed"] = parse_column_binding(binding_text)
    except Exception as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("column_binding_cot failed: %s", audit["error"])
        return additional_context, audit

    audit["elapsed_s"] = time.time() - t0
    block = format_binding_block(binding_text)
    if not block:
        return additional_context, audit
    if base_hint.strip():
        return f"{base_hint.strip()}\n\n{block}", audit
    return block, audit


def run_column_binding_per_subq(
    *,
    original_question: str,
    sub_question: str,
    sub_question_index: int,
    preceding_cte_info: str,
    schema_info: str,
    additional_context: str,
    llm_config: Dict[str, Any],
    cache: Optional[Dict[str, Tuple[str, Dict[str, Any]]]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Binding scoped to sub-question; cache granularity follows MCTS_COLUMN_BINDING_SCOPE."""
    key = binding_cache_key(
        sub_question_index=sub_question_index,
        preceding_cte_info=preceding_cte_info,
    )
    prec_for_prompt = (
        "No preceding CTE"
        if column_binding_scope_per_subq_decompose()
        else (preceding_cte_info or "No preceding CTE")
    )
    if cache is not None and key in cache:
        block, audit = cache[key]
        audit = dict(audit)
        audit["cache_hit"] = True
        if block:
            base = additional_context or ""
            if base.strip():
                return f"{base.strip()}\n\n{block}", audit
            return block, audit
        return additional_context, audit

    audit: Dict[str, Any] = {
        "enabled": True,
        "mode": MODE_PER_SUBQ,
        "scope": (
            "per_subq"
            if column_binding_scope_per_subq_decompose()
            else "expand"
        ),
        "sub_question_index": sub_question_index,
        "sub_question": sub_question,
        "cache_key": key,
        "cache_hit": False,
        "column_binding": "",
        "parsed": {},
        "llm_calls": 0,
        "elapsed_s": 0.0,
        "error": None,
    }
    t0 = time.time()
    base_hint = additional_context or ""
    try:
        binding_text = _call_per_subq_binding(
            original_question=original_question,
            sub_question=sub_question,
            preceding_cte_info=prec_for_prompt,
            schema_info=schema_info,
            additional_context=base_hint,
            llm_config=llm_config,
        )
        audit["llm_calls"] = 1
        audit["column_binding"] = binding_text
        audit["parsed"] = parse_column_binding(binding_text)
    except Exception as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("column_binding_per_subq failed: %s", audit["error"])
        if cache is not None:
            cache[key] = ("", audit)
        return additional_context, audit

    audit["elapsed_s"] = time.time() - t0
    block = format_binding_block(binding_text)
    if cache is not None:
        cache[key] = (block, dict(audit))
    if not block:
        return additional_context, audit
    if base_hint.strip():
        return f"{base_hint.strip()}\n\n{block}", audit
    return block, audit


def run_column_binding_global_once(
    *,
    question: str,
    schema_info: str,
    additional_context: str,
    llm_config: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """One binding LLM call per question; reused at hint branches instead of per expand."""
    block, audit = run_column_binding_per_subq(
        original_question=question,
        sub_question=question,
        sub_question_index=0,
        preceding_cte_info="No preceding CTE",
        schema_info=schema_info,
        additional_context=additional_context,
        llm_config=llm_config,
        cache=None,
    )
    audit = dict(audit or {})
    audit["scope"] = "global"
    return block or "", audit


def augment_additional_context_with_column_binding(
    *,
    question: str,
    schema_info: str,
    additional_context: str,
    llm_config: Dict[str, Any],
) -> Tuple[str, Optional[Dict[str, Any]]]:
    if not column_binding_unified_enabled():
        return additional_context, None
    return run_column_binding_cot(
        question=question,
        schema_info=schema_info,
        additional_context=additional_context,
        llm_config=llm_config,
    )


def augment_additional_context_for_expand(
    *,
    original_question: str,
    sub_question: str,
    sub_question_index: int,
    preceding_cte_info: str,
    schema_info: str,
    additional_context: str,
    llm_config: Dict[str, Any],
    cache: Optional[Dict[str, Tuple[str, Dict[str, Any]]]] = None,
    global_binding_block: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    if not column_binding_per_subq_enabled():
        return additional_context, None
    if column_binding_hint_chain_enabled():
        ctx, audit = run_column_binding_per_subq(
            original_question=original_question,
            sub_question=sub_question,
            sub_question_index=sub_question_index,
            preceding_cte_info=preceding_cte_info,
            schema_info=schema_info,
            additional_context=additional_context,
            llm_config=llm_config,
            cache=cache,
        )
        if audit is not None:
            audit = dict(audit)
            audit["scope"] = "hint_chain"
        return ctx, audit
    if column_binding_scope_global():
        block = (global_binding_block or "").strip()
        if not block:
            return additional_context, {"scope": "global", "llm_calls": 0, "cache_hit": True, "elapsed_s": 0.0}
        base = additional_context or ""
        merged = f"{base.strip()}\n\n{block}" if base.strip() else block
        return merged, {"scope": "global", "llm_calls": 0, "cache_hit": True, "elapsed_s": 0.0}
    ctx, audit = run_column_binding_per_subq(
        original_question=original_question,
        sub_question=sub_question,
        sub_question_index=sub_question_index,
        preceding_cte_info=preceding_cte_info,
        schema_info=schema_info,
        additional_context=additional_context,
        llm_config=llm_config,
        cache=cache,
    )
    if audit is not None and column_binding_scope_per_subq_decompose():
        audit = dict(audit)
        audit["scope"] = "per_subq"
    return ctx, audit
