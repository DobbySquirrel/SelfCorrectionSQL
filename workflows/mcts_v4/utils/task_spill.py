"""Periodic task spill for partial rollout recovery on hard timeout.

Writes best-so-far rollout_stats to disk during solve(); on timeout the runner
re-selects with production SQLSelector (R4 / gated R4+R8) — no gold labels.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


ENV_SPILL_DIR = "MCTS_TASK_SPILL_DIR"
ENV_SPILL_ENABLED = "MCTS_TASK_SPILL"
ENV_SPILL_INTERVAL_S = "MCTS_TASK_SPILL_INTERVAL_S"


def task_spill_enabled() -> bool:
    if os.environ.get(ENV_SPILL_ENABLED, "1").strip().lower() in ("0", "false", "no"):
        return False
    return bool(os.environ.get(ENV_SPILL_DIR, "").strip())


def spill_dir_from_json_out(json_out: str) -> Path:
    """`.task_spill/{json_stem}/` next to the run JSON output file."""
    p = Path(json_out)
    return p.parent / ".task_spill" / p.stem


def spill_interval_s(default: float = 90.0) -> float:
    raw = os.environ.get(ENV_SPILL_INTERVAL_S, "").strip()
    if raw == "0":
        return 0.0
    if not raw:
        return default
    try:
        val = float(raw)
        return 0.0 if val <= 0 else max(10.0, val)
    except ValueError:
        return default


def spill_path(qid: str) -> Optional[Path]:
    root = os.environ.get(ENV_SPILL_DIR, "").strip()
    if not root:
        return None
    return Path(root) / f"{qid}.json"


def spill_has_selectable_candidates(rollout_stats_list: List[Dict[str, Any]]) -> bool:
    for rs in rollout_stats_list or []:
        if (rs.get("selected_sql") or "").strip():
            return True
        for v in rs.get("all_sql_variants") or []:
            if (v.get("sql") or "").strip():
                return True
    return False


def collect_all_sqls(rollout_stats_list: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: set = set()
    for rs in rollout_stats_list or []:
        sel = (rs.get("selected_sql") or "").strip()
        if sel and sel not in seen:
            seen.add(sel)
            out.append({"sql": sel})
        for info in rs.get("all_sql_variants") or []:
            s = (info.get("sql") or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append({"sql": s})
    return out


def merge_bootstrap_rollout(
    rollout_stats_list: List[Dict[str, Any]],
    bootstrap_sql: Optional[str],
) -> List[Dict[str, Any]]:
    merged = list(rollout_stats_list or [])
    bsql = (bootstrap_sql or "").strip()
    if not bsql:
        return merged
    if any((rs.get("selected_sql") or "").strip() == bsql for rs in merged):
        return merged
    merged.insert(
        0,
        {
            "rollout_id": 0,
            "selected_sql": bsql,
            "all_sql_variants": [{"sql": bsql, "reward": 0.0}],
            "reward": 0.0,
            "is_quick_path": True,
            "source": "bootstrap_direct_sql",
        },
    )
    return merged


def build_spill_payload(
    *,
    qid: str,
    idx: int,
    question: str,
    schema_info: str,
    rollout_stats_list: List[Dict[str, Any]],
    bootstrap_sql: Optional[str] = None,
    sub_questions: Optional[List[Any]] = None,
    decompose_expand_traces: Optional[List[Any]] = None,
    timing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rss = merge_bootstrap_rollout(rollout_stats_list, bootstrap_sql)
    return {
        "spill_version": 1,
        "spilled_at": time.time(),
        "qid": str(qid),
        "idx": idx,
        "question": question,
        "schema_info": schema_info,
        "rollout_stats": rss,
        "all_sqls_with_attributes": collect_all_sqls(rss),
        "sub_questions": list(sub_questions or []),
        "decompose_expand_traces": list(decompose_expand_traces or []),
        "stats": {
            "timing": timing or {},
            "rollout_count": len(rss),
        },
    }


def write_task_spill(payload: Dict[str, Any]) -> Optional[Path]:
    if not task_spill_enabled():
        return None
    path = spill_path(str(payload.get("qid", "")))
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fw:
        json.dump(payload, fw, ensure_ascii=False)
    tmp.replace(path)
    return path


def read_task_spill(qid: str) -> Optional[Dict[str, Any]]:
    path = spill_path(str(qid))
    if path is None or not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fr:
            return json.load(fr)
    except Exception:
        return None


def delete_task_spill(qid: str) -> None:
    path = spill_path(str(qid))
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def select_sql_from_spill(
    spill: Dict[str, Any],
    *,
    db_connector=None,
    llm_config: Optional[dict] = None,
) -> str:
    from workflows.mcts_v4.utils.sql_selector import SQLSelector

    rss = spill.get("rollout_stats") or []
    if not spill_has_selectable_candidates(rss):
        return ""
    return SQLSelector.select(
        rss,
        question=spill.get("question") or "",
        schema_ddl=spill.get("schema_info") or "",
        db_connector=db_connector,
        llm_config=llm_config,
    )
