"""Additive diverse-CTE prompt helpers for mcts_v4 decompose expand."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .openai_client_pool import get_openai_client

from .mcts_helpers import MCTSUtils
from .schema_diversity import prepare_schema_for_temp, schema_diversity_enabled

logger = logging.getLogger(__name__)

ENV_DIVERSE_PROMPT = "MCTS_CTE_DIVERSE_PROMPT"
ENV_DIVERSE_N = "MCTS_CTE_DIVERSE_N"
ENV_DIVERSE_N_LOW = "MCTS_CTE_DIVERSE_N_LOW"
ENV_DIVERSE_N_HINT = "MCTS_CTE_DIVERSE_N_HINT"
ENV_DIVERSE_TEMPS = "MCTS_CTE_DIVERSE_TEMPS"
ENV_ALPHA_DIVIDE_CONQUER = "MCTS_ALPHA_DIVIDE_CONQUER_PROMPT"
ENV_SKIP_M_VERIFY = "MCTS_SKIP_M_VERIFY"
ENV_TEMP_SWAP = "MCTS_CTE_TEMP_SWAP"
ENV_PARALLEL_WORKERS = "MCTS_CTE_PARALLEL_WORKERS"
ENV_DEDUP_BEFORE_REVISE = "MCTS_DEDUP_BEFORE_REVISE"
ENV_REVERSED_BOOTSTRAP_DIRECT_SQL = "MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL"
ENV_BOOTSTRAP_ONCE_PER_QUESTION = "MCTS_BOOTSTRAP_ONCE_PER_QUESTION"
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "decompose_diverse_template.txt"
ALPHA_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "decompose_diverse_alpha_divide_conquer.txt"
)


def diverse_prompt_enabled() -> bool:
    return os.environ.get(ENV_DIVERSE_PROMPT, "0") == "1"


def alpha_divide_conquer_prompt_enabled() -> bool:
    return os.environ.get(ENV_ALPHA_DIVIDE_CONQUER, "0") == "1"


def diverse_n(default: int = 3) -> int:
    try:
        return max(1, int(os.environ.get(ENV_DIVERSE_N, str(default))))
    except ValueError:
        return default


def diverse_n_for_temp(temperature: float, default: int = 3) -> int:
    """N for Mode C at this temp; low-temp branch can use MCTS_CTE_DIVERSE_N_LOW."""
    base = diverse_n(default)
    if float(temperature) > 0.45:
        return base
    raw = os.environ.get(ENV_DIVERSE_N_LOW, "").strip()
    if not raw:
        return base
    try:
        return max(1, int(raw))
    except ValueError:
        return base


def diverse_n_hint(default: int = 2) -> int:
    """N for binding-hint branch at low temp when dual mode is on."""
    raw = os.environ.get(ENV_DIVERSE_N_HINT, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def skip_m_verify_enabled() -> bool:
    return os.environ.get(ENV_SKIP_M_VERIFY, "0") == "1"


def temp_swap_enabled() -> bool:
    """Duplicate each Mode C call_spec with cyclically rotated temperature."""
    return os.environ.get(ENV_TEMP_SWAP, "0") == "1"


def dedup_before_revise_enabled() -> bool:
    return os.environ.get(ENV_DEDUP_BEFORE_REVISE, "0") == "1"


def reversed_bootstrap_direct_sql_enabled() -> bool:
    """Rollout-1: parallel direct complete SQL + global hint to seed reversed schema."""
    raw = os.environ.get(ENV_REVERSED_BOOTSTRAP_DIRECT_SQL, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def bootstrap_once_per_question_enabled() -> bool:
    """When bootstrap is on: 1 = one LLM call per question (v2); 0 = per expand (legacy E6)."""
    raw = os.environ.get(ENV_BOOTSTRAP_ONCE_PER_QUESTION, "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


def run_bootstrap_direct_sql_once(
    *,
    question: str,
    schema_info: str,
    additional_context: str = "",
    global_binding_block: str = "",
    llm_config: Dict[str, Any],
    multi_model_configs: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """One LLM complete SQL per question (global hint); reused for all Rollout1 expands."""
    from ..agents.complete_sql_generator import CompleteSQLGenerator

    class _Node:
        def __init__(self) -> None:
            self.question = question
            self.schema_info = schema_info
            self.additional_context = additional_context
            self.parent = None
            self.cte = ""
            self.execution_results: Dict[str, Any] = {}

    gen = CompleteSQLGenerator(llm_config, multi_model_configs=multi_model_configs or [])
    sql, audit = gen.generate_single_bootstrap_sql(
        _Node(),
        global_binding_block=global_binding_block,
    )
    audit = dict(audit or {})
    audit["scope"] = "question_once"
    return sql or "", audit


def _normalize_sql_key(sql: str) -> str:
    return " ".join((sql or "").split()).strip().lower()


def dedupe_candidates_before_revise(
    candidates: List[str],
    meta_by_cte: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], int]:
    """Drop byte-identical SQL (whitespace-normalized) before checker/revise passes."""
    if not dedup_before_revise_enabled():
        return candidates, 0
    kept: List[str] = []
    seen: set = set()
    dropped = 0
    for cte in candidates:
        key = _normalize_sql_key(cte)
        if not key:
            dropped += 1
            continue
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(cte)
    # meta_by_cte keys are full cte strings; prune stale entries
    stale = [k for k in meta_by_cte if _normalize_sql_key(k) not in seen]
    for k in stale:
        meta_by_cte.pop(k, None)
    return kept, dropped


def mode_c_parallel_workers(n_specs: int, *, extra: int = 0) -> int:
    total = max(1, n_specs + extra)
    raw = os.environ.get(ENV_PARALLEL_WORKERS, "").strip()
    if raw:
        try:
            return max(1, min(int(raw), total))
        except ValueError:
            pass
    default = MODE_C_PARALLEL_WORKERS * 2 if temp_swap_enabled() else MODE_C_PARALLEL_WORKERS
    return max(1, min(default, total))


def expand_call_specs_with_temp_swap(call_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep branch roles (plain/hint/binding) fixed; rerun each spec once with the next
    spec's temperature (left rotate). 4 specs -> 8 LLM calls when enabled.
    """
    if not call_specs or not temp_swap_enabled():
        return call_specs
    rotated_temps = [float(s["temp"]) for s in call_specs[1:]] + [float(call_specs[0]["temp"])]
    out: List[Dict[str, Any]] = []
    for spec in call_specs:
        tagged = dict(spec)
        tagged.setdefault("temp_swap_round", 1)
        out.append(tagged)
    for spec, new_temp in zip(call_specs, rotated_temps):
        swapped = dict(spec)
        swapped["orig_temp"] = spec["temp"]
        swapped["temp"] = new_temp
        swapped["temp_swap_round"] = 2
        out.append(swapped)
    return out


def diverse_temps(default: Optional[List[float]] = None) -> List[float]:
    """Parse MCTS_CTE_DIVERSE_TEMPS (comma-separated floats). Default [0.3, 0.6, 0.9]."""
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
    path = ALPHA_TEMPLATE_PATH if alpha_divide_conquer_prompt_enabled() else TEMPLATE_PATH
    return path.read_text(encoding="utf-8")


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
    model_config: Optional[Dict[str, Any]] = None,
) -> str:
    config = model_config or llm_config.get("config_list", [{}])[0]
    client = get_openai_client(
        config.get("base_url"),
        config.get("api_key", "dummy-key"),
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
    schema_info_override: Optional[str] = None,
    schema_strategy_audit: Optional[Dict[str, Any]] = None,
    additional_context: Optional[str] = None,
    model_config: Optional[Dict[str, Any]] = None,
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
    schema_for_prompt = schema_info_override if schema_info_override is not None else node.schema_info
    ctx_for_prompt = (
        additional_context
        if additional_context is not None
        else (node.additional_context or "")
    )
    audit: Dict[str, Any] = {
        "n_requested": n_requested,
        "temperature": temperature,
        "n_llm_calls": (schema_strategy_audit or {}).get("llm_calls_per_cte", 1),
        "sub_question": getattr(node, "sub_question", node.question),
        "parsed": [],
        "candidates": [],
    }
    if schema_strategy_audit:
        audit["schema_strategy"] = dict(schema_strategy_audit)
    user_prompt = render_diverse_prompt(
        n=n_requested,
        original_question=getattr(node, "_original_question", "") or node.question,
        sub_question=getattr(node, "sub_question", node.question),
        sub_question_index=sub_question_index,
        sub_questions_total=sub_questions_total,
        schema_info=schema_for_prompt,
        additional_context=ctx_for_prompt,
        preceding_cte_info=preceding_cte_info,
        used_cte_names=used_cte_names,
    )
    raw = call_diverse_prompt(
        llm_config=llm_config,
        user_prompt=user_prompt,
        temperature=temperature,
        model_config=model_config,
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


MODE_C_TEMPERATURES = [0.3, 0.6, 0.9]
MODE_C_PARALLEL_WORKERS = 4


def _pick_model_config(
    llm_config: Dict[str, Any],
    multi_model_configs: Optional[List[Dict[str, Any]]],
    counter: List[int],
    lock: threading.Lock,
) -> Dict[str, Any]:
    if multi_model_configs:
        with lock:
            idx = counter[0] % len(multi_model_configs)
            counter[0] += 1
        return multi_model_configs[idx]
    return llm_config.get("config_list", [{}])[0]


def _execute_mode_c_call_spec(
    spec: Dict[str, Any],
    *,
    node,
    llm_config: Dict[str, Any],
    multi_model_configs: Optional[List[Dict[str, Any]]],
    model_counter: List[int],
    model_lock: threading.Lock,
    extract_fn,
    preceding_cte_info: str,
    used_cte_names: List[str],
    orig_q: str,
    sub_question: str,
    sub_question_index: int,
    question: str,
    original_schema: str,
    global_binding_block: Optional[str],
    global_narrowed_schema: Optional[str],
    global_schema_audit: Optional[Dict[str, Any]],
    binding_cache: Optional[Dict[str, Tuple[str, Dict[str, Any]]]],
    checker_context: Optional[Dict[str, Any]],
    ctx_saved: str,
    use_schema_div: bool,
    prior_rollout_sqls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run one Mode C LLM branch (thread-safe; does not mutate node.additional_context)."""
    from .column_binding_cot import augment_additional_context_for_expand
    from .schema_diversity import prepare_schema_for_temp, reversed_schema_linking_enabled

    temp = spec["temp"]
    n_req = spec["n"]
    use_binding = spec["use_binding"]
    binding_branch = spec.get("binding_branch")
    schema_strategy = spec.get("schema_strategy")
    schema_override = None
    schema_audit = None
    ctx_for_prompt = ctx_saved or ""
    binding_audit: Optional[Dict[str, Any]] = None
    effective_prior = list(prior_rollout_sqls or [])

    if use_binding:
        bound_ctx, expand_audit = augment_additional_context_for_expand(
            original_question=orig_q,
            sub_question=sub_question,
            sub_question_index=sub_question_index,
            preceding_cte_info=preceding_cte_info,
            schema_info=node.schema_info,
            additional_context=ctx_saved or "",
            llm_config=llm_config,
            cache=binding_cache,
            global_binding_block=global_binding_block,
        )
        if expand_audit is not None:
            binding_audit = dict(expand_audit)
            binding_audit["temp"] = temp
            binding_audit["binding_branch"] = binding_branch
            ctx_for_prompt = bound_ctx

    if use_schema_div:
        if schema_strategy == "reversed_prior_rollout":
            if not (effective_prior and reversed_schema_linking_enabled()):
                return {
                    "ctes": [],
                    "audit": {"skipped": True, "reason": "no_prior_rollout_sql"},
                    "meta": [],
                    "binding_audit": None,
                    "schema_audit": None,
                    "checker_revision": [],
                    "n_checker_llm_calls": 0,
                    "n_checker_revised": 0,
                }
            schema_override, schema_audit = prepare_schema_for_temp(
                temperature=temp,
                question=question,
                sub_question=sub_question,
                schema_info=node.schema_info,
                preceding_cte_info=preceding_cte_info,
                llm_config=llm_config,
                original_schema_info=original_schema,
                prior_rollout_sqls=effective_prior,
                schema_strategy_override="reversed_prior_rollout",
            )
        else:
            schema_override, schema_audit = prepare_schema_for_temp(
                temperature=temp,
                question=question,
                sub_question=sub_question,
                schema_info=node.schema_info,
                preceding_cte_info=preceding_cte_info,
                llm_config=llm_config,
                original_schema_info=original_schema,
                prior_rollout_sqls=prior_rollout_sqls,
            )

    model_config = _pick_model_config(llm_config, multi_model_configs, model_counter, model_lock)
    endpoint = (model_config.get("base_url") or "").rstrip("/")
    ctes, audit = collect_diverse_ctes(
        node=node,
        llm_config=llm_config,
        n_requested=n_req,
        extract_fn=extract_fn,
        temperature=temp,
        preceding_cte_info=preceding_cte_info,
        used_cte_names=used_cte_names,
        schema_info_override=schema_override,
        schema_strategy_audit=schema_audit,
        additional_context=ctx_for_prompt,
        model_config=model_config,
    )
    if binding_branch is not None:
        audit["binding_branch"] = binding_branch
    if spec.get("temp_swap_round") is not None:
        audit["temp_swap_round"] = spec.get("temp_swap_round")
    if spec.get("orig_temp") is not None:
        audit["orig_temp"] = spec.get("orig_temp")
    audit["llm_endpoint"] = endpoint

    checker_revision = []
    n_checker_llm_calls = 0
    n_checker_revised = 0

    meta: List[Tuple[str, Dict[str, Any]]] = []
    for item in audit.get("candidates") or []:
        cte = item.get("cte")
        if not cte:
            continue
        meta.append(
            (
                cte,
                {
                    "id": item.get("id"),
                    "rationale": item.get("rationale", ""),
                    "cte_sql_raw": item.get("cte_sql_raw", ""),
                    "temperature": temp,
                    "binding_branch": binding_branch,
                },
            )
        )

    return {
        "ctes": ctes,
        "audit": audit,
        "meta": meta,
        "binding_audit": binding_audit,
        "schema_audit": schema_audit,
        "checker_revision": checker_revision,
        "n_checker_llm_calls": n_checker_llm_calls,
        "n_checker_revised": n_checker_revised,
    }

def generate_diverse_mode_c(
    *,
    node,
    llm_config: Dict[str, Any],
    extract_fn,
    n_per_call: int,
    preceding_cte_info: str,
    used_cte_names: List[str],
    temperatures: Optional[List[float]] = None,
    checker_context: Optional[Dict[str, Any]] = None,
    binding_cache: Optional[Dict[str, Tuple[str, Dict[str, Any]]]] = None,
    binding_audits: Optional[List[Dict[str, Any]]] = None,
    original_question: Optional[str] = None,
    global_binding_block: Optional[str] = None,
    global_narrowed_schema: Optional[str] = None,
    global_schema_audit: Optional[Dict[str, Any]] = None,
    multi_model_configs: Optional[List[Dict[str, Any]]] = None,
    prior_rollout_sqls: Optional[List[str]] = None,
    bootstrap_sql: Optional[str] = None,
    bootstrap_audit: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Mode C: dual@0.3 plain+hint, per-temp schema diversity, parallel call specs."""
    temps = temperatures if temperatures is not None else MODE_C_TEMPERATURES
    use_schema_div = schema_diversity_enabled()
    trace: Dict[str, Any] = {
        "mode": "C",
        "schema_diversity": use_schema_div,
        "n_llm_calls": 0,
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
        "per_temp_schema_strategy": [],
        "column_binding_per_temp": [],
        "parallel_call_specs": True,
        "checker_revision": [],
        "n_checker_llm_calls": 0,
        "n_checker_revised": 0,
        "dedup_before_revise": dedup_before_revise_enabled(),
        "reversed_schema_linking": False,
        "reversed_bootstrap_direct_sql": False,
        "bootstrap_direct_sql": None,
        "prior_rollout_sql_count": len(prior_rollout_sqls or []),
    }
    all_ctes: List[str] = []
    meta_by_cte: Dict[str, Dict[str, Any]] = {}
    original_schema = getattr(node, "_original_schema_info", None) or node.schema_info
    question = getattr(node, "_original_question", "") or node.question
    sub_question = getattr(node, "sub_question", node.question)
    sub_question_index = getattr(node, "sub_question_index", 0)
    if sub_question_index < 0:
        sub_question_index = 0
    orig_q = original_question or question
    try:
        from .column_binding_cot import (
            column_binding_dual_at_temp,
            column_binding_replace_at_temp,
        )
        from .schema_diversity import reversed_schema_linking_enabled

        rollout1 = not (prior_rollout_sqls or [])
        bootstrap_for_reversed = ""
        bootstrap_audit_local: Optional[Dict[str, Any]] = None
        if rollout1 and reversed_bootstrap_direct_sql_enabled():
            if bootstrap_once_per_question_enabled():
                bootstrap_for_reversed = (bootstrap_sql or "").strip()
                bootstrap_audit_local = bootstrap_audit
            else:
                bsql, baudit = run_bootstrap_direct_sql_once(
                    question=question,
                    schema_info=node.schema_info,
                    additional_context=node.additional_context or "",
                    global_binding_block=global_binding_block or "",
                    llm_config=llm_config,
                    multi_model_configs=multi_model_configs,
                )
                bootstrap_for_reversed = (bsql or "").strip()
                bootstrap_audit_local = {**(baudit or {}), "reused": False, "scope": "per_expand"}
        effective_prior = list(prior_rollout_sqls or [])
        if not effective_prior and bootstrap_for_reversed:
            effective_prior = [bootstrap_for_reversed]

        use_reversed = reversed_schema_linking_enabled() and bool(effective_prior)

        call_specs: List[Dict[str, Any]] = []
        for temp in temps:
            if column_binding_dual_at_temp(temp):
                call_specs.append(
                    {"temp": temp, "n": diverse_n(n_per_call), "use_binding": False, "binding_branch": "plain"}
                )
                call_specs.append(
                    {"temp": temp, "n": diverse_n_hint(), "use_binding": True, "binding_branch": "hint"}
                )
            else:
                use_binding = column_binding_replace_at_temp(temp)
                call_specs.append(
                    {
                        "temp": temp,
                        "n": diverse_n_for_temp(temp, default=n_per_call),
                        "use_binding": use_binding,
                        "binding_branch": "hint" if use_binding else None,
                    }
                )
        if use_reversed:
            call_specs.append(
                {
                    "temp": 0.9,
                    "n": diverse_n_for_temp(0.9, default=n_per_call),
                    "use_binding": False,
                    "binding_branch": "reversed",
                    "schema_strategy": "reversed_prior_rollout",
                }
            )
            trace["reversed_schema_linking"] = True
            trace["reversed_bootstrap_direct_sql"] = bool(rollout1 and bootstrap_for_reversed)
            if rollout1 and bootstrap_audit_local:
                trace["bootstrap_direct_sql"] = {**bootstrap_audit_local, "reused": bootstrap_once_per_question_enabled()}
        call_specs = expand_call_specs_with_temp_swap(call_specs)
        trace["n_llm_calls"] = len(call_specs)

        ctx_saved = node.additional_context or ""
        model_counter = [0]
        model_lock = threading.Lock()
        workers = mode_c_parallel_workers(len(call_specs))

        def _run_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
            return _execute_mode_c_call_spec(
                spec,
                node=node,
                llm_config=llm_config,
                multi_model_configs=multi_model_configs,
                model_counter=model_counter,
                model_lock=model_lock,
                extract_fn=extract_fn,
                preceding_cte_info=preceding_cte_info,
                used_cte_names=used_cte_names,
                orig_q=orig_q,
                sub_question=sub_question,
                sub_question_index=sub_question_index,
                question=question,
                original_schema=original_schema,
                global_binding_block=global_binding_block,
                global_narrowed_schema=global_narrowed_schema,
                global_schema_audit=global_schema_audit,
                binding_cache=binding_cache,
                checker_context=checker_context,
                ctx_saved=ctx_saved,
                use_schema_div=use_schema_div,
                prior_rollout_sqls=effective_prior,
            )

        spec_results: List[Dict[str, Any]] = []
        if workers <= 1:
            spec_results = [_run_spec(spec) for spec in call_specs]
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_run_spec, spec) for spec in call_specs]
                for fut in as_completed(futures):
                    spec_results.append(fut.result())

        for result in spec_results:
            audit = result["audit"]
            binding_audit = result.get("binding_audit")
            schema_audit = result.get("schema_audit")
            if binding_audit is not None and binding_audits is not None:
                binding_audits.append(binding_audit)
                trace["column_binding_per_temp"].append(
                    {
                        "temp": binding_audit.get("temp"),
                        "binding_branch": binding_audit.get("binding_branch"),
                        "llm_calls": binding_audit.get("llm_calls"),
                        "cache_hit": binding_audit.get("cache_hit"),
                    }
                )
            if schema_audit is not None:
                trace["per_temp_schema_strategy"].append(schema_audit)
                trace["n_llm_calls"] += max(0, int(schema_audit.get("llm_calls_per_cte", 1)) - 1)
            trace["call_audits"].append(audit)
            for cte, meta in result.get("meta") or []:
                all_ctes.append(cte)
                meta_by_cte[cte] = meta

        trace["n_raw_candidates"] = len(all_ctes)
        all_ctes, n_norm_deduped = dedupe_candidates_before_revise(all_ctes, meta_by_cte)
        trace["n_norm_deduped_dropped"] = n_norm_deduped
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
                "binding_branch": meta_by_cte[cte].get("binding_branch"),
                "structure_sig": create_structure_signature(cte),
            }
            for cte in kept
        ]
        trace["diverse_dropped"] = dropped_records
        trace["n_struct_deduped_dropped"] = len(dropped_records)
        trace["n_candidates"] = len(kept)
        trace["n_diverse_extra_added"] = len(kept)
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
