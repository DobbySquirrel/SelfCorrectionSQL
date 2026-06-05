"""Apply compiled constraints to SQL pool (hard prune / soft penalty)."""

from __future__ import annotations

from typing import Callable, List, Optional

from workflows.mcts_v4.query_clarifier.config import clarify_mode, soft_penalty
from workflows.mcts_v4.query_clarifier.constraint import sql_satisfies
from workflows.mcts_v4.query_clarifier.safety import restore_pool, should_fallback
from workflows.mcts_v4.query_clarifier.schemas import CompiledConstraint, EnforcementResult, PoolEntry


def rollout_stats_to_pool(rollout_stats_list: List[dict]) -> List[PoolEntry]:
    seen = set()
    pool: List[PoolEntry] = []
    for r in rollout_stats_list:
        rw = float(r.get("reward", 0.0))
        for v in r.get("all_sql_variants") or []:
            sql = (v.get("sql") or "").strip()
            if not sql or sql in seen:
                continue
            seen.add(sql)
            pool.append(
                PoolEntry(
                    sql=sql,
                    reward=rw,
                    result_signature=v.get("result_signature") or "",
                    valid=bool(v.get("valid", True)),
                )
            )
    return pool


def hard_prune(pool: List[PoolEntry], cc: CompiledConstraint) -> List[PoolEntry]:
    kept = []
    for e in pool:
        if not e.valid:
            continue
        sr = sql_satisfies(e.sql, cc)
        if sr.satisfied:
            kept.append(e)
    return kept


def apply_soft_penalty(pool: List[PoolEntry], cc: CompiledConstraint, penalty: Optional[float] = None) -> List[PoolEntry]:
    pen = soft_penalty() if penalty is None else penalty
    out: List[PoolEntry] = []
    for e in pool:
        sr = sql_satisfies(e.sql, cc)
        new_e = PoolEntry(
            sql=e.sql,
            reward=e.reward - (0.0 if sr.satisfied else pen),
            result_signature=e.result_signature,
            valid=e.valid,
        )
        out.append(new_e)
    return out


def regenerate_under_constraint(
    node_context: dict,
    cc: CompiledConstraint,
    budget: int,
    expand_fn: Optional[Callable[[dict, str], Optional[str]]] = None,
) -> List[PoolEntry]:
    """Placeholder for local regenerate; returns empty unless expand_fn provided."""
    if expand_fn is None:
        return []
    extra: List[PoolEntry] = []
    hint = cc.to_prompt_hint()
    for _ in range(budget):
        sql = expand_fn(node_context, hint)
        if not sql:
            continue
        if sql_satisfies(sql, cc).satisfied:
            extra.append(PoolEntry(sql=sql, reward=0.5, valid=True))
    return extra


def enforce(
    pool: List[PoolEntry],
    cc: CompiledConstraint,
    *,
    regen_budget: int = 0,
    expand_fn: Optional[Callable[[dict, str], Optional[str]]] = None,
    node_context: Optional[dict] = None,
) -> tuple[List[PoolEntry], EnforcementResult]:
    mode = clarify_mode()
    er = EnforcementResult(pool_before=len(pool), mode=cc.level)
    if cc.level == "none":
        er.applied = False
        er.pool_after = len(pool)
        return pool, er

    if mode == "log_only":
        er.applied = False
        sim = hard_prune(pool, cc) if cc.level == "hard" else pool
        er.pool_after = len(sim)
        return pool, er

    original = list(pool)
    if cc.level == "soft":
        out = apply_soft_penalty(pool, cc)
        er.applied = True
        er.pool_after = len(out)
        return out, er

    pruned = hard_prune(pool, cc)
    if should_fallback(original, pruned):
        regen_used = False
        if regen_budget > 0 and expand_fn is not None:
            extra = regenerate_under_constraint(node_context or {}, cc, regen_budget, expand_fn)
            regen_used = bool(extra)
            pruned = pruned + extra
        if should_fallback(original, pruned):
            er.safety_fallback = True
            er.regenerate_used = regen_used
            er.applied = False
            er.pool_after = len(original)
            return restore_pool(original), er
        er.regenerate_used = regen_used
    er.applied = True
    er.pool_after = len(pruned)
    return pruned, er


def apply_pool_to_rollouts(
    rollout_stats_list: List[dict],
    pool: List[PoolEntry],
    cc: CompiledConstraint,
) -> List[dict]:
    """Reflect enforced pool back into rollout_stats for selector."""
    if cc.level == "none" or clarify_mode() == "log_only":
        return rollout_stats_list
    allowed = {e.sql for e in pool}
    pen_by_sql = {e.sql: e.reward for e in pool}
    out = []
    for r in rollout_stats_list:
        r2 = dict(r)
        variants = []
        for v in r.get("all_sql_variants") or []:
            sql = (v.get("sql") or "").strip()
            if cc.level == "hard" and sql and sql not in allowed:
                continue
            if cc.level == "soft" and sql in pen_by_sql:
                v = dict(v)
                v["_clarify_penalized_reward"] = pen_by_sql[sql]
            variants.append(v)
        r2["all_sql_variants"] = variants
        out.append(r2)
    return out
