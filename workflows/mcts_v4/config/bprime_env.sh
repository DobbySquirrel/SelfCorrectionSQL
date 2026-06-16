#!/usr/bin/env bash
# B′ production defaults (hard30 verified: recall +4, hit@1 +6 vs plain B′).
# full498 (2026-06): Scheme A dual + nomin2 → 386/498 Hit@1 est. (mul_purity + gated R8 on ~69 ambiguous q).
# Runner: workflows/mcts_v4/test/out/cte_diverse/run_sigA_full498.sh
#
# 3-temp Mode C + per-temp schema diversity:
#   0.3 full schema original | 0.6 relevance-sorted | 0.9 two-step linking
#
# Source from run scripts:
#   source "$(dirname "$0")/../config/bprime_env.sh"   # from workflows/mcts_v4/test/out/...
#   source workflows/mcts_v4/config/bprime_env.sh      # from repo root
#
# Disable schema diversity for ablation: MCTS_SCHEMA_DIVERSITY=0

# Scheme A (production): CTE/search legacy + final SQL v2 strict buckets (R4/gated vote).
# Avoids legacy top-5 collision: same family, different execution → separate final clusters.
export MCTS_USE_SIGNATURE_V2="${MCTS_USE_SIGNATURE_V2:-0}"
export MCTS_FINAL_SIGNATURE_V2="${MCTS_FINAL_SIGNATURE_V2:-1}"
export MCTS_SELECTOR_STRATEGY="${MCTS_SELECTOR_STRATEGY:-R4}"
# R4 final: mul_purity cluster score; gated → R4 shortcut when clear, R8 LLM pairwise when ambiguous (~14% q).
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
export NUM_SQL_VARIANTS="${NUM_SQL_VARIANTS:-6}"
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
# mc: per-rollout vote max-count bucket only; all_buckets: every sig in result_buckets +1
export MCTS_R4_VOTE_MODE="${MCTS_R4_VOTE_MODE:-all_buckets}"

# Online clustering ablations (gap30):
#   MCTS_CTE_JACCARD_MERGE=1 + MCTS_CTE_JACCARD_THRESHOLD=0.85  — CTE probe row-set merge
#   MCTS_FINAL_JACCARD_MERGE=1 + MCTS_FINAL_JACCARD_THRESHOLD=0.85 — final SQL merge at R4
# R4 cluster score: mul_purity = votes(legacy) × v2 purity within cluster (+1 Hit@1 on full498).
# MCTS_R4_WITH_BIAS=1 — prefer WITH-cluster on close R4 margin
# MCTS_R4_TOPK_BOOTSTRAP=ambig_purity — only when MCTS_CONFIDENCE_MODE=0 (no LLM); superseded by gated R8
export MCTS_CTE_JACCARD_MERGE="${MCTS_CTE_JACCARD_MERGE:-0}"
export MCTS_CTE_JACCARD_THRESHOLD="${MCTS_CTE_JACCARD_THRESHOLD:-0.85}"
export MCTS_FINAL_JACCARD_MERGE="${MCTS_FINAL_JACCARD_MERGE:-0}"
export MCTS_FINAL_JACCARD_THRESHOLD="${MCTS_FINAL_JACCARD_THRESHOLD:-0.85}"
export MCTS_R4_SCORE_MODE="${MCTS_R4_SCORE_MODE:-mul_purity}"
export MCTS_R4_WITH_BIAS="${MCTS_R4_WITH_BIAS:-0}"
export MCTS_R4_WITH_BIAS_MARGIN="${MCTS_R4_WITH_BIAS_MARGIN:-1.5}"
export MCTS_R4_TOPK_BOOTSTRAP="${MCTS_R4_TOPK_BOOTSTRAP:-0}"
export MCTS_R4_TOPK="${MCTS_R4_TOPK:-3}"
