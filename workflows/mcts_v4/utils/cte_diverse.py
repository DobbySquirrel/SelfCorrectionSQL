"""Additive diverse-CTE prompt helpers for mcts_v4 decompose expand."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from openai import OpenAI

from .mcts_helpers import MCTSUtils

logger = logging.getLogger(__name__)

ENV_DIVERSE_PROMPT = "MCTS_CTE_DIVERSE_PROMPT"
ENV_DIVERSE_N = "MCTS_CTE_DIVERSE_N"
ENV_DIVERSE_TEMPS = "MCTS_CTE_DIVERSE_TEMPS"
ENV_SKIP_M_VERIFY = "MCTS_SKIP_M_VERIFY"
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "decompose_diverse_template.txt"


def diverse_prompt_enabled() -> bool:
    return os.environ.get(ENV_DIVERSE_PROMPT, "0") == "1"


def diverse_n(default: int = 3) -> int:
    try:
        return max(1, int(os.environ.get(ENV_DIVERSE_N, str(default))))
    except ValueError:
        return default


def skip_m_verify_enabled() -> bool:
    return os.environ.get(ENV_SKIP_M_VERIFY, "0") == "1"


def diverse_temps(default: Optional[List[float]] = None) -> List[float]:
    """Parse MCTS_CTE_DIVERSE_TEMPS (comma-separated floats). Default [0.3, 0.6]."""
    fallback = default if default is not None else MODE_C_TEMPERATURES
    raw = os.environ.get(ENV_DIVERSE_TEMPS, "").strip()
    if not raw:
        return list(fallback)
    out: List[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(float(part))
        except ValueError:
            continue
    return out if out else list(fallback)


def create_structure_signature(sql: str) -> str:
    """Normalize SQL structure for dedupe when execution results are unavailable."""
    s = (sql or "").lower()
    s = re.sub(r"\bwith\s+\w+\s+as\s*\(", "with _cte_ as (", s)
    s = re.sub(r"\bfrom\s+\w+\b", "from _ref_", s)
    s = re.sub(r"\bjoin\s+\w+\b", "join _ref_", s)
    s = re.sub(r"'[^']*'", "'?'", s)
    s = re.sub(r"\b\d+\b", "?", s)
    s = re.sub(r"\s+", " ", s).strip()
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def load_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def render_diverse_prompt(
    *,
    n: int,
    original_question: str,
    sub_question: str,
    sub_question_index: int,
    sub_questions_total: int,
    schema_info: str,
    additional_context: str,
    preceding_cte_info: str,
    used_cte_names: List[str],
) -> str:
    template = load_template()
    used = ", ".join(used_cte_names) if used_cte_names else "None"
    return template.format(
        N=n,
        original_question=original_question,
        sub_question=sub_question,
        sub_question_index=sub_question_index + 1,
        sub_questions_total=sub_questions_total,
        schema_info=schema_info,
        additional_context=additional_context or "(none)",
        preceding_cte_info=preceding_cte_info or "No preceding CTE",
        used_cte_names=used,
    )


def _extract_json_object(text: str) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if m:
            text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def parse_diverse_json(raw: str) -> List[Dict[str, Any]]:
    """Parse LLM JSON; return empty list on any failure."""
    blob = _extract_json_object(raw)
    if not blob:
        return []
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return []
    items = data.get("decompositions")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cte_sql = (item.get("cte_sql") or "").strip()
        if not cte_sql or "with" not in cte_sql.lower():
            continue
        out.append(
            {
                "id": item.get("id"),
                "rationale": item.get("rationale", ""),
                "cte_sql": cte_sql,
            }
        )
    return out


def finalize_diverse_cte(cte_sql: str, extract_fn) -> str:
    """Convert raw cte_sql to executable single-CTE string via CTEGenerator extractor."""
    wrapped = f"```sql\n{cte_sql}\n```"
    cte = extract_fn(wrapped)
    return cte if cte and cte != "<END>" else ""


def _structure_sig_for_cte(
    cte: str,
    exec_results: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    exec_results = exec_results or {}
    if cte in exec_results and exec_results[cte].get("valid"):
        return MCTSUtils.create_result_signature(exec_results[cte])
    return create_structure_signature(cte)


def _meta_from_parsed(item: Dict[str, Any], cte: str) -> Dict[str, Any]:
    return {
        "id": item.get("id"),
        "rationale": item.get("rationale", ""),
        "cte_sql_raw": item.get("cte_sql", ""),
        "cte": cte,
    }


def dedupe_by_signature(
    candidates: List[str],
    existing_signatures: Set[str],
    *,
    exec_results: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[str], int]:
    """
    Drop structurally duplicate CTEs. Prefer result_signature when exec_results provided.
    Returns (kept_ctes, n_dropped_dup).
    """
    kept, dropped_records, _ = dedupe_by_signature_with_records(
        candidates,
        existing_signatures,
        exec_results=exec_results,
    )
    return kept, len(dropped_records)


def dedupe_by_signature_with_records(
    candidates: List[str],
    existing_signatures: Set[str],
    *,
    exec_results: Optional[Dict[str, Dict[str, Any]]] = None,
    meta_by_cte: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[str], List[Dict[str, Any]], int]:
    """
    Drop structurally duplicate CTEs and return dropped records for audit traces.
    Returns (kept_ctes, dropped_records, n_dropped_dup).
    """
    kept: List[str] = []
    dropped_records: List[Dict[str, Any]] = []
    temp_sigs = set(existing_signatures)
    seen = set(existing_signatures)
    exec_results = exec_results or {}
    meta_by_cte = meta_by_cte or {}
    for cte in candidates:
        meta = meta_by_cte.get(cte, {})
        if not cte:
            dropped_records.append(
                {
                    "id": meta.get("id"),
                    "rationale": meta.get("rationale", ""),
                    "cte_sql_raw": meta.get("cte_sql_raw", ""),
                    "cte": cte,
                    "reason": "extract_empty",
                    "structure_sig": None,
                }
            )
            continue
        sig = _structure_sig_for_cte(cte, exec_results)
        if sig in seen:
            reason = "dup_vs_temp" if sig in temp_sigs else "dup_vs_diverse"
            dropped_records.append(
                {
                    "id": meta.get("id"),
                    "rationale": meta.get("rationale", ""),
                    "cte_sql_raw": meta.get("cte_sql_raw", ""),
                    "cte": cte,
                    "reason": reason,
                    "structure_sig": sig,
                }
            )
            continue
        seen.add(sig)
        kept.append(cte)
    return kept, dropped_records, len(dropped_records)


def call_diverse_prompt(
    *,
    llm_config: Dict[str, Any],
    user_prompt: str,
    timeout_s: float = 120.0,
    temperature: float = 0.7,
) -> str:
    config = llm_config.get("config_list", [{}])[0]
    client = OpenAI(
        base_url=config.get("base_url"),
        api_key=config.get("api_key", "dummy-key"),
        timeout=timeout_s,
    )
    response = client.chat.completions.create(
        model=config.get("model"),
        messages=[
            {
                "role": "system",
                "content": "You output strict JSON only for diverse CTE decompositions.",
            },
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        n=1,
    )
    return response.choices[0].message.content or ""


def collect_diverse_ctes(
    *,
    node,
    llm_config: Dict[str, Any],
    n_requested: int,
    extract_fn,
    temperature: float = 0.7,
    preceding_cte_info: str = "No preceding CTE",
    used_cte_names: Optional[List[str]] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """
    CT1-v2/CT2: fetch N diverse CTEs in one LLM call (no dedupe vs temp children).
    Returns (cte_strings, audit_record).
    """
    used_cte_names = used_cte_names or []
    sub_questions_total = getattr(node, "sub_questions_total", 1) or 1
    sub_question_index = getattr(node, "sub_question_index", 0)
    if sub_question_index < 0:
        sub_question_index = 0
    audit: Dict[str, Any] = {
        "n_requested": n_requested,
        "temperature": temperature,
        "n_llm_calls": 1,
        "sub_question": getattr(node, "sub_question", node.question),
        "parsed": [],
        "candidates": [],
    }
    user_prompt = render_diverse_prompt(
        n=n_requested,
        original_question=getattr(node, "_original_question", "") or node.question,
        sub_question=getattr(node, "sub_question", node.question),
        sub_question_index=sub_question_index,
        sub_questions_total=sub_questions_total,
        schema_info=node.schema_info,
        additional_context=node.additional_context or "",
        preceding_cte_info=preceding_cte_info,
        used_cte_names=used_cte_names,
    )
    raw = call_diverse_prompt(
        llm_config=llm_config,
        user_prompt=user_prompt,
        temperature=temperature,
    )
    parsed = parse_diverse_json(raw)
    audit["parsed"] = [
        {
            "id": item.get("id"),
            "rationale": item.get("rationale", ""),
            "cte_sql_raw": item.get("cte_sql", ""),
        }
        for item in parsed
    ]
    ctes: List[str] = []
    for item in parsed:
        cte = finalize_diverse_cte(item["cte_sql"], extract_fn)
        if cte:
            ctes.append(cte)
            audit["candidates"].append(
                {
                    "id": item.get("id"),
                    "rationale": item.get("rationale", ""),
                    "cte": cte,
                    "structure_sig": create_structure_signature(cte),
                }
            )
    audit["n_candidates"] = len(ctes)
    return ctes, audit


MODE_C_TEMPERATURES = [0.3, 0.6]


def generate_diverse_mode_c(
    *,
    node,
    llm_config: Dict[str, Any],
    extract_fn,
    n_per_call: int,
    preceding_cte_info: str,
    used_cte_names: List[str],
    temperatures: Optional[List[float]] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """
    Mode C: diverse prompt × len(temperatures) calls, N CTEs each, structure dedupe.
    Replaces temperature sampling when MCTS_CTE_DIVERSE_PROMPT=1.
    """
    temps = temperatures if temperatures is not None else MODE_C_TEMPERATURES
    trace: Dict[str, Any] = {
        "mode": "C",
        "n_llm_calls": len(temps),
        "temperatures": list(temps),
        "n_requested_per_call": n_per_call,
        "sub_question": getattr(node, "sub_question", node.question),
        "n_raw_candidates": 0,
        "n_candidates": 0,
        "n_struct_deduped_dropped": 0,
        "diverse_fallback": False,
        "diverse_fallback_reason": None,
        "diverse_kept": [],
        "diverse_dropped": [],
        "call_audits": [],
    }
    all_ctes: List[str] = []
    meta_by_cte: Dict[str, Dict[str, Any]] = {}
    try:
        for temp in temps:
            ctes, audit = collect_diverse_ctes(
                node=node,
                llm_config=llm_config,
                n_requested=n_per_call,
                extract_fn=extract_fn,
                temperature=temp,
                preceding_cte_info=preceding_cte_info,
                used_cte_names=used_cte_names,
            )
            trace["call_audits"].append(audit)
            for item in audit.get("candidates") or []:
                cte = item.get("cte")
                if not cte:
                    continue
                all_ctes.append(cte)
                meta_by_cte[cte] = {
                    "id": item.get("id"),
                    "rationale": item.get("rationale", ""),
                    "cte_sql_raw": item.get("cte_sql_raw", ""),
                    "temperature": temp,
                }
        trace["n_raw_candidates"] = len(all_ctes)
        if len(all_ctes) < 2:
            trace["diverse_fallback"] = True
            trace["diverse_fallback_reason"] = f"too_few_raw_ctes:{len(all_ctes)}"
            logger.info("diverse_mode_c fallback: %s", trace["diverse_fallback_reason"])
            return [], trace
        kept, dropped_records, _ = dedupe_by_signature_with_records(
            all_ctes,
            set(),
            meta_by_cte=meta_by_cte,
        )
        trace["diverse_kept"] = [
            {
                "id": meta_by_cte[cte].get("id"),
                "rationale": meta_by_cte[cte].get("rationale", ""),
                "cte_sql_raw": meta_by_cte[cte].get("cte_sql_raw", ""),
                "cte": cte,
                "temperature": meta_by_cte[cte].get("temperature"),
                "structure_sig": create_structure_signature(cte),
            }
            for cte in kept
        ]
        trace["diverse_dropped"] = dropped_records
        trace["n_struct_deduped_dropped"] = len(dropped_records)
        trace["n_candidates"] = len(kept)
        trace["n_diverse_extra_added"] = len(kept)  # legacy trace field alias
        if len(kept) < 2:
            trace["diverse_fallback"] = True
            trace["diverse_fallback_reason"] = f"too_few_after_dedupe:{len(kept)}"
            logger.info("diverse_mode_c fallback: %s", trace["diverse_fallback_reason"])
            return [], trace
        logger.info(
            "diverse_mode_c n_raw=%s n_kept=%s n_dropped=%s",
            len(all_ctes),
            len(kept),
            len(dropped_records),
        )
        return kept, trace
    except Exception as e:
        trace["diverse_fallback"] = True
        trace["diverse_fallback_reason"] = str(e)
        logger.info("diverse_mode_c fallback: %s", e)
        return [], trace
