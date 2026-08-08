#!/usr/bin/env python3
"""Official DeepEye backend used by SelfCorrectionSQL plugins.

Goal: one importable layer so CTE gen + Full SQL gen share the same fixed
DeepEye pieces (SchemaService profile, PromptFactory, rule revise), and we can
iterate inside `deepeye_plugin` without rewriting callers.

Env:
  MCTS_DEEPEYE_ROOT              — DeepEye-SQL repo root
  MCTS_DEEPEYE_OFFICIAL_SCHEMA   — default 1: SchemaService.build_schema_profile
  MCTS_DEEPEYE_VR_FOOTER         — default auto: 0 when official schema (examples
                                   already embedded); set 1 to force VR footer
  MCTS_DEEPEYE_OFFICIAL_REVISE   — default 1: full_checkers uses official_revise
  MCTS_DEEPEYE_REVISE_MODE       — basechecker (default) | legacy (__new__ steal)
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

_DEFAULT_ROOT = "/hpc2hdd/home/sshen190/wtao565/related_project/DeepEye-SQL"
_schema_service = None


def deepeye_root() -> Path:
    return Path(os.environ.get("MCTS_DEEPEYE_ROOT", _DEFAULT_ROOT)).resolve()


def ensure_deepeye_path() -> Path:
    root = deepeye_root()
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)
    return root


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def official_schema_enabled() -> bool:
    return _env_bool("MCTS_DEEPEYE_OFFICIAL_SCHEMA", True)


def official_revise_enabled() -> bool:
    return _env_bool("MCTS_DEEPEYE_OFFICIAL_REVISE", True)


def vr_footer_enabled(*, schema_source: str = "") -> bool:
    """Official profile already embeds value_examples; skip duplicate VR footer."""
    raw = os.environ.get("MCTS_DEEPEYE_VR_FOOTER")
    if raw is not None and str(raw).strip():
        return _env_bool("MCTS_DEEPEYE_VR_FOOTER", False)
    return (schema_source or "").strip().lower() != "official"


def get_schema_service():
    global _schema_service
    ensure_deepeye_path()
    if _schema_service is None:
        from app.services.schema_service import SchemaService  # type: ignore

        _schema_service = SchemaService()
    return _schema_service


def build_official_schema_profile(linked_schema: Dict[str, Any]) -> str:
    """Render linked schema with official SchemaService (same as DeepEye gen)."""
    if not isinstance(linked_schema, dict) or not linked_schema:
        return ""
    ss = get_schema_service()
    # SchemaService may mutate / cache by id; copy so callers keep pristine dict.
    sch = copy.deepcopy(linked_schema)
    try:
        return (ss.build_schema_profile(sch) or "").strip()
    except Exception:
        return ""


def schema_profile_from_linked(
    linked_schema: Dict[str, Any],
    *,
    fallback: Optional[Callable[[Dict[str, Any]], str]] = None,
) -> Tuple[str, str]:
    """Return (profile, source) where source is 'official' or 'local'."""
    if official_schema_enabled():
        profile = build_official_schema_profile(linked_schema)
        if profile:
            return profile, "official"
    if fallback is not None:
        return (fallback(linked_schema) or "").strip(), "local"
    return "", "local"


def fit_prompt_with_official_schema_strip(
    linked_schema: Dict[str, Any],
    prompt_format_func: Callable[[str], str],
    *,
    encoding_model_name: str,
    max_prompt_len: int,
    item_id: Any = "gen",
) -> Tuple[Optional[str], int]:
    """Official 4-level semantic strip (Value Examples dropped only when needed)."""
    if not isinstance(linked_schema, dict) or not linked_schema:
        return None, -1
    ss = get_schema_service()
    sch = copy.deepcopy(linked_schema)
    try:
        prompt, level = ss.build_prompt_with_progressive_schema_stripping(
            sch,
            encoding_model_name=encoding_model_name,
            max_prompt_len=int(max_prompt_len),
            prompt_format_func=prompt_format_func,
            item_id=item_id,
            log_prefix="plugin_gen",
        )
        return prompt, int(level) if level is not None else -1
    except Exception:
        return None, -1


def revise_full_sql_official(
    *,
    sql: str,
    question: str,
    evidence: str,
    schema: str,
    db_path: Path,
    client: Any,
    model: str,
    exec_fn: Callable[[Path, str], Tuple[Optional[list], Optional[str]]],
    sampling_budget: Optional[int] = None,
    linked_schema: Optional[Dict[str, Any]] = None,
    question_id: Optional[int] = None,
    database_id: Optional[str] = None,
    max_model_len: Optional[int] = None,
    exec_timeout_s: Optional[float] = None,
    extra_evidence: str = "",
):
    """Thin wrapper → deepeye_fullsql_official_revise (BaseChecker or legacy)."""
    from workflows.mcts_v4.actions.deepeye_fullsql_official_revise import (
        official_revise_full_sql,
    )

    return official_revise_full_sql(
        sql=sql,
        question=question,
        evidence=evidence,
        schema=schema,
        db_path=db_path,
        client=client,
        model=model,
        exec_fn=exec_fn,
        sampling_budget=sampling_budget,
        linked_schema=linked_schema,
        question_id=question_id,
        database_id=database_id,
        max_model_len=max_model_len,
        exec_timeout_s=exec_timeout_s,
        extra_evidence=extra_evidence,
    )
