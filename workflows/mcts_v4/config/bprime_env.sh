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
export MCTS_SELECTOR_STRATEGY="${MCTS_SELECTOR_STRATEGY:-R4}"
export MCTS_CONFIDENCE_MODE="${MCTS_CONFIDENCE_MODE:-gated}"
export MCTS_R4_GATE_MARGIN="${MCTS_R4_GATE_MARGIN:-0.7}"
export MCTS_CONFIDENCE_THRESHOLD="${MCTS_CONFIDENCE_THRESHOLD:-0.7}"
export MCTS_CONFIDENCE_TOP_K="${MCTS_CONFIDENCE_TOP_K:-3}"
export MCTS_CONFIDENCE_VOTE_SAMPLES="${MCTS_CONFIDENCE_VOTE_SAMPLES:-3}"
export MCTS_REWARD_CALIBRATED="${MCTS_REWARD_CALIBRATED:-1}"

export MCTS_CTE_DIVERSE_PROMPT="${MCTS_CTE_DIVERSE_PROMPT:-1}"
export MCTS_CTE_DIVERSE_N="${MCTS_CTE_DIVERSE_N:-3}"
export MCTS_CTE_DIVERSE_N_HINT="${MCTS_CTE_DIVERSE_N_HINT:-3}"
export MCTS_CTE_DIVERSE_TEMPS="${MCTS_CTE_DIVERSE_TEMPS:-0.3,0.6,0.9}"
export MCTS_SQL_GEN_TEMPS="${MCTS_SQL_GEN_TEMPS:-0.3,0.6,0.9}"
export MCTS_SCHEMA_DIVERSITY="${MCTS_SCHEMA_DIVERSITY:-1}"

export MCTS_COLUMN_BINDING_COT="${MCTS_COLUMN_BINDING_COT:-per_subq@0.3+dual}"
export MCTS_COLUMN_BINDING_SCOPE="${MCTS_COLUMN_BINDING_SCOPE:-global}"

export MCTS_SKIP_M_VERIFY="${MCTS_SKIP_M_VERIFY:-1}"
export MCTS_USE_DECOMPOSE_FLOW="${MCTS_USE_DECOMPOSE_FLOW:-1}"
export DECOMPOSE_STRATEGY="${DECOMPOSE_STRATEGY:-S2}"
export MCTS_DECOMPOSE_MIN_SUBQUESTIONS="${MCTS_DECOMPOSE_MIN_SUBQUESTIONS:-1}"
export MCTS_STRATEGY_MODE="${MCTS_STRATEGY_MODE:-FORCE_S2}"
export MCTS_MULTI_PLAN="${MCTS_MULTI_PLAN:-0}"
export MAX_CTE_NODES="${MAX_CTE_NODES:-5}"
# R4 timeout vote (498q offline replay: +0 acc; timeout SQL 通常无 result_signature)
# off | downweight | exclude
export MCTS_R4_TIMEOUT_VOTE_MODE="${MCTS_R4_TIMEOUT_VOTE_MODE:-off}"
export MCTS_R4_TIMEOUT_VOTE_WEIGHT="${MCTS_R4_TIMEOUT_VOTE_WEIGHT:-0.5}"
