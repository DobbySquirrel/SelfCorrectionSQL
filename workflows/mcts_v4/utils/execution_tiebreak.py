"""Execution-time tie-break for cluster representative SQL selection."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

ENV_EXEC_TIME_TIEBREAK = "MCTS_EXEC_TIME_TIEBREAK"
ENV_EXEC_TIME_REPEATS = "MCTS_EXEC_TIME_REPEATS"

_time_cache: Dict[str, float] = {}


def exec_time_tiebreak_enabled() -> bool:
    raw = os.environ.get(ENV_EXEC_TIME_TIEBREAK, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def exec_time_repeats(default: int = 2) -> int:
    raw = os.environ.get(ENV_EXEC_TIME_REPEATS, str(default)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _norm_sql(sql: str) -> str:
    return " ".join((sql or "").split()).strip().lower()


def measure_sql_execution_time(db_connector: Any, sql: str, *, repeats: Optional[int] = None) -> float:
    """Return mean execution seconds; cache per normalized SQL within a solve."""
    s = (sql or "").strip()
    if not s or db_connector is None:
        return float("inf")
    key = _norm_sql(s)
    if key in _time_cache:
        return _time_cache[key]
    n = repeats if repeats is not None else exec_time_repeats()
    times: List[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            db_connector.execute_query(s)
        except Exception:
            pass
        times.append(time.perf_counter() - t0)
    avg = sum(times) / len(times) if times else float("inf")
    _time_cache[key] = avg
    return avg


def clear_execution_time_cache() -> None:
    _time_cache.clear()


def tiebreak_pick_variants(
    variants: List[Tuple[str, float, int]],
    *,
    db_connector: Any = None,
) -> str:
    """
    Pick representative SQL from cluster variants.
    Default: min row_count, then shortest SQL.
    With MCTS_EXEC_TIME_TIEBREAK=1: among min-row ties, pick fastest execution.
    """
    if not variants:
        return ""
    if not exec_time_tiebreak_enabled() or db_connector is None:
        sql, _rows = min(variants, key=lambda x: (x[2] if x[2] else 0, len(x[0] or "")))[:2]
        return (sql or "").strip()

    scored: List[Tuple[str, int, float, int]] = []
    for sql, _rw, rows in variants:
        s = (sql or "").strip()
        if not s:
            continue
        row_n = rows if rows else 0
        exec_t = measure_sql_execution_time(db_connector, s)
        scored.append((s, row_n, exec_t, len(s)))
    if not scored:
        return ""
    best = min(scored, key=lambda x: (x[1], x[2], x[3]))
    return best[0]
