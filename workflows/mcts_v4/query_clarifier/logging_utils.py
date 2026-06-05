"""Structured logging for AutoClarify traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from workflows.mcts_v4.query_clarifier.schemas import ClarifyTraceRecord

_trace_path: Optional[Path] = None


def set_trace_path(path: Path | str | None) -> None:
    global _trace_path
    _trace_path = Path(path) if path else None


def get_trace_path() -> Optional[Path]:
    return _trace_path


def log_event(event_type: str, payload: Dict[str, Any]) -> None:
    if _trace_path is None:
        return
    _trace_path.parent.mkdir(parents=True, exist_ok=True)
    row = {"event": event_type, **payload}
    with _trace_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_trace_record(record: ClarifyTraceRecord) -> None:
    log_event("clarify_decision", record.to_dict())
