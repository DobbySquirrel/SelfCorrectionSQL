#!/usr/bin/env bash
# B′ production defaults (hard30 verified: recall +4, hit@1 +6 vs plain B′).
#
# 3-temp Mode C + per-temp schema diversity:
#   0.3 full schema original | 0.6 relevance-sorted | 0.9 two-step linking
#
# Source from run scripts:
#   source "$(dirname "$0")/../config/bprime_env.sh"   # from workflows/mcts_v4/test/out/...
#   source workflows/mcts_v4/config/bprime_env.sh      # from repo root
#
# Disable schema diversity for ablation: MCTS_SCHEMA_DIVERSITY=0

export MCTS_USE_SIGNATURE_V2="${MCTS_USE_SIGNATURE_V2:-1}"
export MCTS_SELECTOR_STRATEGY="${MCTS_SELECTOR_STRATEGY:-R2}"
export MCTS_REWARD_CALIBRATED="${MCTS_REWARD_CALIBRATED:-1}"

export MCTS_CTE_DIVERSE_PROMPT="${MCTS_CTE_DIVERSE_PROMPT:-1}"
export MCTS_CTE_DIVERSE_N="${MCTS_CTE_DIVERSE_N:-3}"
export MCTS_CTE_DIVERSE_TEMPS="${MCTS_CTE_DIVERSE_TEMPS:-0.3,0.6,0.9}"
export MCTS_SQL_GEN_TEMPS="${MCTS_SQL_GEN_TEMPS:-0.3,0.6,0.9}"
export MCTS_SCHEMA_DIVERSITY="${MCTS_SCHEMA_DIVERSITY:-1}"

export MCTS_SKIP_M_VERIFY="${MCTS_SKIP_M_VERIFY:-1}"
export MCTS_USE_DECOMPOSE_FLOW="${MCTS_USE_DECOMPOSE_FLOW:-1}"
export DECOMPOSE_STRATEGY="${DECOMPOSE_STRATEGY:-S2}"
export MCTS_STRATEGY_MODE="${MCTS_STRATEGY_MODE:-FORCE_S2}"
export MCTS_MULTI_PLAN="${MCTS_MULTI_PLAN:-0}"
export MAX_CTE_NODES="${MAX_CTE_NODES:-5}"
