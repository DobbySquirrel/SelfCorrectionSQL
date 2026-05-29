"""Shared datatypes for Beam-CTE stage 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AxisCandidate:
    """One CTE variant produced for a given axis."""

    axis_id: str
    form_tag: str
    cte_sql: str
    cte_name: Optional[str] = None
    probe_rows: Optional[List[dict]] = None
    probe_columns: Optional[List[str]] = None
    probe_hash: Optional[str] = None
    is_valid: bool = False
    is_skip: bool = False
    error: Optional[str] = None


@dataclass
class BeamPath:
    """One root-to-current path through the taxonomy axes."""

    path_id: str
    axis_candidates: List[AxisCandidate] = field(default_factory=list)
    axis_rows: List[dict] = field(default_factory=list)
    final_sql: Optional[str] = None
    executable: bool = False
    exec_result_hash: Optional[str] = None
    exec_row_count: Optional[int] = None
    exec_sample_rows: Optional[List[dict]] = None
    exec_match_gold: Optional[bool] = None
    judge_score: Optional[float] = None
    judge_reason: Optional[str] = None
    error: Optional[str] = None

    @property
    def form_a(self) -> str:
        for c in self.axis_candidates:
            if c.axis_id == "A":
                return c.form_tag
        return ""

    @property
    def form_c(self) -> str:
        for c in self.axis_candidates:
            if c.axis_id == "C":
                return c.form_tag
        return ""

    @property
    def form_d(self) -> str:
        for c in self.axis_candidates:
            if c.axis_id == "D":
                return c.form_tag
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path_id": self.path_id,
            "form_a": self.form_a,
            "form_c": self.form_c,
            "form_d": self.form_d,
            "executable": self.executable,
            "exec_result_hash": self.exec_result_hash,
            "exec_match_gold": self.exec_match_gold,
            "judge_score": self.judge_score,
            "judge_reason": self.judge_reason,
            "error": self.error,
            "final_sql_preview": (self.final_sql or "")[:400],
        }
