"""Data classes for AutoClarify v0."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

Axis = Literal["Reference", "Value", "Measure", "Ranking", "Output", "None"]
ConstraintLevel = Literal["hard", "soft", "none"]

AXIS_PRIORITY = ("Measure", "Ranking", "Output", "Value", "Reference")


@dataclass
class NodeStats:
    """Aggregated rollout stats for trigger / cluster summary."""

    high_reward_rollouts: int = 0
    n_nonempty_result_buckets: int = 0
    top1_visit: int = 0
    top2_visit: int = 0
    exhaustion_triggered: bool = False


@dataclass
class ClusterSummary:
    rank: int
    visit: int
    reward: float
    representative_sql: str
    result_signature: str
    structure_signature: str = ""


@dataclass
class ClarifyInput:
    qid: int
    nl_question: str
    schema_ddl: str
    top_clusters: List[ClusterSummary]
    rollout_stats: NodeStats


@dataclass
class ClarificationCandidate:
    cid: str
    summary: str
    constraint_hint: Dict[str, Any] = field(default_factory=dict)
    maps_to_cluster_rank: int = 0  # 1-based rank in top_clusters


@dataclass
class ClarificationQuestion:
    qid: int
    axis: Axis
    question: str
    candidates: List[ClarificationCandidate] = field(default_factory=list)
    rationale: str = ""


@dataclass
class AnswerInput:
    qid: int
    nl_question: str
    schema_ddl: str
    clarification: ClarificationQuestion


@dataclass
class ClarificationAnswer:
    qid: int
    choice: Optional[str] = None
    confidence: float = 0.0
    evidence: str = ""
    abstain: bool = True


@dataclass
class Predicate:
    column: str
    op: str
    value: Any = None


@dataclass
class CompiledConstraint:
    qid: int
    axis: str
    level: ConstraintLevel = "none"
    required_tables: Set[str] = field(default_factory=set)
    banned_tables: Set[str] = field(default_factory=set)
    required_columns: Set[str] = field(default_factory=set)
    banned_columns: Set[str] = field(default_factory=set)
    required_agg: Optional[Tuple[str, Optional[str]]] = None
    banned_aggs: Set[Tuple[str, Optional[str]]] = field(default_factory=set)
    required_order: Optional[str] = None
    required_limit: Optional[int] = None
    required_select_columns: Set[str] = field(default_factory=set)
    required_group_by: Set[str] = field(default_factory=set)
    required_predicates: List[Predicate] = field(default_factory=list)
    source_answer: Optional[ClarificationAnswer] = None
    self_check_failed: bool = False
    self_check_rep_rank: Optional[int] = None
    violated_fields: List[str] = field(default_factory=list)

    def to_prompt_hint(self) -> str:
        parts = [f"axis={self.axis}", f"level={self.level}"]
        if self.required_agg:
            parts.append(f"agg={self.required_agg}")
        if self.required_order:
            parts.append(f"order={self.required_order}")
        if self.required_limit is not None:
            parts.append(f"limit={self.required_limit}")
        if self.required_select_columns:
            parts.append(f"select={sorted(self.required_select_columns)}")
        if self.required_group_by:
            parts.append(f"group_by={sorted(self.required_group_by)}")
        if self.required_predicates:
            parts.append(f"predicates={self.required_predicates}")
        return "; ".join(parts)


@dataclass
class SatResult:
    satisfied: bool
    violated_fields: List[str] = field(default_factory=list)


@dataclass
class PoolEntry:
    sql: str
    reward: float = 0.0
    result_signature: str = ""
    valid: bool = True


@dataclass
class EnforcementResult:
    applied: bool = False
    pool_before: int = 0
    pool_after: int = 0
    regenerate_used: bool = False
    safety_fallback: bool = False
    mode: str = "none"


@dataclass
class ClarifyTraceRecord:
    qid: int
    trigger: bool = False
    trigger_reason: str = ""
    clarify: Optional[Dict[str, Any]] = None
    answer: Optional[Dict[str, Any]] = None
    constraint: Optional[Dict[str, Any]] = None
    enforcement: Optional[Dict[str, Any]] = None
    outcome: Optional[Dict[str, Any]] = None
    known_limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "qid": self.qid,
            "trigger": self.trigger,
            "trigger_reason": self.trigger_reason,
        }
        if self.clarify is not None:
            d["clarify"] = self.clarify
        if self.answer is not None:
            d["answer"] = self.answer
        if self.constraint is not None:
            d["constraint"] = self.constraint
        if self.enforcement is not None:
            d["enforcement"] = self.enforcement
        if self.outcome is not None:
            d["outcome"] = self.outcome
        if self.known_limitations:
            d["known_limitations"] = self.known_limitations
        return d
