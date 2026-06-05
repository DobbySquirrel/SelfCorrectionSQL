"""Safety nets for hard prune and enforcement."""

from __future__ import annotations

from typing import List

from workflows.mcts_v4.query_clarifier.schemas import CompiledConstraint, PoolEntry


def all_low_reward(pool: List[PoolEntry], threshold: float = 0.1) -> bool:
    if not pool:
        return True
    return all(e.reward < threshold for e in pool)


def should_fallback(original: List[PoolEntry], pruned: List[PoolEntry]) -> bool:
    if not pruned:
        return True
    if all_low_reward(pruned):
        return True
    return False


def restore_pool(original: List[PoolEntry]) -> List[PoolEntry]:
    return list(original)
