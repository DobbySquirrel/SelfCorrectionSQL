"""
Decomposition Plan proposer — multi-plan root for E1 experiment.

Generates structured JSON plans (relation_first / measure_first / output_first),
deduplicates by plan hash, converts to sub_questions for CTE-MCTS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import autogen

PLAN_STRATEGIES = ("relation_first", "measure_first", "output_first")

_STRATEGY_PROMPTS = {
    "relation_first": """You are an SQL decomposition planner using **Relation-First** strategy (aligned with S2).
- Prioritize correct table relationships and join paths first.
- Subquery order: build joins → apply filters → aggregate/sort/limit.
- Each subquery must be one verifiable CTE step.""",
    "measure_first": """You are an SQL decomposition planner using **Measure/Ranking-First** strategy (aligned with S7).
- First determine result grain/keys, then per-table aggregates at that grain.
- Subquery order: define grain → fact-table measures → dimension alignment → final join.
- Each subquery must be one verifiable CTE step.""",
    "output_first": """You are an SQL decomposition planner using **Output/Constraint-First** strategy (aligned with S1).
- Start from required output columns and hard constraints (filters, ranking, limit).
- Subquery order: output skeleton → source tables → filters → aggregation.
- Each subquery must be one verifiable CTE step.""",
}


def multi_plan_enabled() -> bool:
    return os.environ.get("MCTS_MULTI_PLAN", "").strip() in ("1", "true", "yes", "on")


def plan_rollouts_per() -> int:
    try:
        return max(1, int(os.environ.get("MCTS_PLAN_ROLLOUTS_PER", "4")))
    except ValueError:
        return 4


def _last_content(chat_result) -> str:
    if hasattr(chat_result, "chat_history") and chat_result.chat_history:
        last = chat_result.chat_history[-1]
        return getattr(last, "content", "") or str(last)
    return str(chat_result)


def _extract_json_obj(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _sorted_flat(vals: Any) -> List[str]:
    if not vals:
        return []
    if isinstance(vals, list):
        return sorted(str(v).strip().lower() for v in vals if str(v).strip())
    return [str(vals).strip().lower()]


def compute_plan_hash(plan: Dict[str, Any]) -> str:
    """hash(strategy + sorted tables/joins/group_by/measures/filters + final_output)."""
    parts: List[str] = [str(plan.get("strategy") or "")]
    for sq in plan.get("subqueries") or []:
        parts.append("|".join(_sorted_flat(sq.get("tables"))))
        parts.append("|".join(_sorted_flat(sq.get("filters"))))
        parts.append("|".join(_sorted_flat(sq.get("group_by"))))
        parts.append("|".join(_sorted_flat(sq.get("measures"))))
        parts.append("|".join(_sorted_flat(sq.get("outputs"))))
    fo = plan.get("final_output") or {}
    parts.append(json.dumps(fo, sort_keys=True, ensure_ascii=False))
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def plan_to_sub_questions(plan: Dict[str, Any]) -> List[str]:
    """Convert structured plan to NL sub-question strings for CTE-MCTS."""
    out: List[str] = []
    for sq in plan.get("subqueries") or []:
        name = sq.get("name") or "step"
        purpose = sq.get("purpose") or ""
        tables = ", ".join(sq.get("tables") or [])
        filters = "; ".join(sq.get("filters") or [])
        outputs = ", ".join(sq.get("outputs") or [])
        gb = ", ".join(sq.get("group_by") or [])
        measures = ", ".join(sq.get("measures") or [])
        deps = ", ".join(sq.get("depends_on") or [])
        chunks = [f"[{name}] {purpose}"]
        if tables:
            chunks.append(f"tables: {tables}")
        if filters:
            chunks.append(f"filters: {filters}")
        if outputs:
            chunks.append(f"outputs: {outputs}")
        if gb:
            chunks.append(f"group_by: {gb}")
        if measures:
            chunks.append(f"measures: {measures}")
        if deps:
            chunks.append(f"depends_on: {deps}")
        out.append(" | ".join(chunks))
    fo = plan.get("final_output") or {}
    if fo:
        cols = ", ".join(fo.get("columns") or [])
        ord_ = fo.get("ordering")
        lim = fo.get("limit")
        tail = f"[final_output] columns: {cols}"
        if ord_:
            tail += f" | ordering: {ord_}"
        if lim is not None:
            tail += f" | limit: {lim}"
        out.append(tail)
    return out or [purpose or "Answer the original question"]


def dedup_plans(plans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for p in plans:
        h = p.get("plan_hash") or compute_plan_hash(p)
        p["plan_hash"] = h
        if h not in seen:
            seen[h] = p
    return list(seen.values())


def build_rollout_schedule(plans: List[Dict[str, Any]], total_rollouts: int) -> List[Dict[str, Any]]:
    """Evenly split total_rollouts across unique plans (post-dedup budget merge)."""
    if not plans:
        return []
    n = len(plans)
    base, rem = divmod(total_rollouts, n)
    schedule: List[Dict[str, Any]] = []
    for i, plan in enumerate(plans):
        k = base + (1 if i < rem else 0)
        schedule.extend([plan] * k)
    return schedule[:total_rollouts]


class PlanDecomposer:
    """Propose 3 structured decomposition plans at MCTS root."""

    def __init__(self, llm_config: Dict):
        self.llm_config = llm_config
        self._lock = threading.Lock()

    def _agent_for(self, strategy: str, temperature: float = 0.2):
        cfg = self.llm_config.copy()
        if "config_list" in cfg:
            for c in cfg["config_list"]:
                c["temperature"] = temperature
                if isinstance(c.get("openai"), dict):
                    c["openai"]["temperature"] = temperature
        system = _STRATEGY_PROMPTS.get(strategy, _STRATEGY_PROMPTS["relation_first"])
        system += """

Output **strict JSON only** (no markdown fences) matching this schema:
{
  "plan_id": "string",
  "strategy": "relation_first | measure_first | ranking_first | output_first",
  "subqueries": [
    {
      "name": "snake_case",
      "purpose": "natural language",
      "tables": ["..."],
      "filters": ["..."],
      "outputs": ["..."],
      "depends_on": ["other_subquery_name"],
      "group_by": ["..."],
      "measures": ["..."]
    }
  ],
  "final_output": {
    "columns": ["..."],
    "ordering": "string|null",
    "limit": "int|null"
  }
}"""
        agent = autogen.AssistantAgent(
            name=f"PlanDecomposer_{strategy}",
            llm_config=cfg,
            system_message=system,
        )
        proxy = autogen.UserProxyAgent(
            name="PlanProxy",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=0,
            code_execution_config=False,
        )
        return agent, proxy

    def _propose_one(
        self,
        strategy: str,
        question: str,
        schema_info: str,
        additional_context: str,
        plan_id: str,
    ) -> Dict[str, Any]:
        user_msg = f"""**Original question**:
{question}

**Database schema** (excerpt):
{schema_info[:3000]}{"..." if len(schema_info) > 3000 else ""}

**Additional context**:
{additional_context or "None"}

Produce one decomposition plan with strategy="{strategy}" and plan_id="{plan_id}".
Use 2-5 subqueries. Output strict JSON only."""

        fallback = {
            "plan_id": plan_id,
            "strategy": strategy,
            "subqueries": [
                {
                    "name": "answer",
                    "purpose": question,
                    "tables": [],
                    "filters": [],
                    "outputs": [],
                    "depends_on": [],
                    "group_by": [],
                    "measures": [],
                }
            ],
            "final_output": {"columns": [], "ordering": None, "limit": None},
            "parse_ok": False,
        }
        try:
            with self._lock:
                agent, proxy = self._agent_for(strategy)
                chat_result = proxy.initiate_chat(agent, message=user_msg, max_turns=1, silent=True)
            obj = _extract_json_obj(_last_content(chat_result))
            if not obj or not isinstance(obj.get("subqueries"), list):
                fallback["parse_ok"] = False
                fallback["plan_hash"] = compute_plan_hash(fallback)
                return fallback
            obj["plan_id"] = plan_id
            obj["strategy"] = strategy
            obj["parse_ok"] = True
            obj["plan_hash"] = compute_plan_hash(obj)
            return obj
        except Exception as e:
            fallback["error"] = str(e)
            fallback["plan_hash"] = compute_plan_hash(fallback)
            return fallback

    def propose_all(
        self,
        question: str,
        schema_info: str,
        additional_context: str = "",
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Returns (raw_proposals[3], deduped_plans).
        """
        specs = [
            ("plan_relation", "relation_first"),
            ("plan_measure", "measure_first"),
            ("plan_output", "output_first"),
        ]
        raw = [
            self._propose_one(st, question, schema_info, additional_context, pid)
            for pid, st in specs
        ]
        deduped = dedup_plans(raw)
        return raw, deduped
