"""Environment flags for AutoClarify v0."""

from __future__ import annotations

import os
from typing import FrozenSet, Literal

ClarifyMode = Literal["log_only", "soft_only", "full"]

ENV_ENABLE = "MCTS_ENABLE_AUTO_CLARIFY"
ENV_MODE = "MCTS_CLARIFY_MODE"
ENV_HARD_CONF = "MCTS_CLARIFY_HARD_CONF"
ENV_SOFT_CONF = "MCTS_CLARIFY_SOFT_CONF"
ENV_SOFT_PENALTY = "MCTS_CLARIFY_SOFT_PENALTY"
ENV_REGEN_BUDGET = "MCTS_CLARIFY_REGEN_BUDGET"
ENV_MAX_PER_RUN = "MCTS_CLARIFY_MAX_PER_RUN"
ENV_AXIS_HARD_WHITELIST = "MCTS_CLARIFY_AXIS_HARD_WHITELIST"
ENV_HIGH_REWARD_THRESHOLD = "MCTS_CLARIFY_HIGH_REWARD_THRESHOLD"
ENV_TRACE_PATH = "MCTS_CLARIFY_TRACE_PATH"

DEFAULT_HARD_CONF = 0.80
DEFAULT_SOFT_CONF = 0.60
DEFAULT_SOFT_PENALTY = 0.10
DEFAULT_REGEN_BUDGET = 4
DEFAULT_MAX_PER_RUN = 200
DEFAULT_HIGH_REWARD_THRESHOLD = 0.9
DEFAULT_AXIS_HARD_WHITELIST = frozenset({"Measure", "Ranking", "Output"})


def env_enabled(name: str) -> bool:
    return os.environ.get(name, "0").strip() in ("1", "true", "True", "yes")


def clarify_enabled() -> bool:
    return env_enabled(ENV_ENABLE)


def clarify_mode() -> ClarifyMode:
    raw = os.environ.get(ENV_MODE, "log_only").strip().lower()
    if raw in ("log_only", "soft_only", "full"):
        return raw  # type: ignore[return-value]
    return "log_only"


def hard_confidence_threshold() -> float:
    try:
        return float(os.environ.get(ENV_HARD_CONF, str(DEFAULT_HARD_CONF)))
    except ValueError:
        return DEFAULT_HARD_CONF


def soft_confidence_threshold() -> float:
    try:
        return float(os.environ.get(ENV_SOFT_CONF, str(DEFAULT_SOFT_CONF)))
    except ValueError:
        return DEFAULT_SOFT_CONF


def soft_penalty() -> float:
    try:
        return float(os.environ.get(ENV_SOFT_PENALTY, str(DEFAULT_SOFT_PENALTY)))
    except ValueError:
        return DEFAULT_SOFT_PENALTY


def regen_budget() -> int:
    try:
        return int(os.environ.get(ENV_REGEN_BUDGET, str(DEFAULT_REGEN_BUDGET)))
    except ValueError:
        return DEFAULT_REGEN_BUDGET


def max_clarify_per_run() -> int:
    try:
        return int(os.environ.get(ENV_MAX_PER_RUN, str(DEFAULT_MAX_PER_RUN)))
    except ValueError:
        return DEFAULT_MAX_PER_RUN


def high_reward_threshold() -> float:
    try:
        return float(os.environ.get(ENV_HIGH_REWARD_THRESHOLD, str(DEFAULT_HIGH_REWARD_THRESHOLD)))
    except ValueError:
        return DEFAULT_HIGH_REWARD_THRESHOLD


def axis_hard_whitelist() -> FrozenSet[str]:
    raw = os.environ.get(ENV_AXIS_HARD_WHITELIST, "Measure,Ranking,Output")
    return frozenset(x.strip() for x in raw.split(",") if x.strip())
