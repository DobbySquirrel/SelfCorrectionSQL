#!/usr/bin/env bash
# E2: Decomposition Plan on Bucket E easy-baseline 20q (sanity / sampling-bias check)
#
#   bash workflows/mcts_v4/test/out/cte_diverse/run_e2_plan_easy20q.sh full
#   bash workflows/mcts_v4/test/out/cte_diverse/run_e2_plan_easy20q.sh status
#   bash workflows/mcts_v4/test/out/cte_diverse/run_e2_plan_easy20q.sh report
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/new30_plan"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
export QIDS_FILE="${PLAN_DIR}/qids_easy20_manifest.json"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_e2_plan_easy20_r12"
export JSON_OUT="${OUT_DIR}/v4_plan_e2_easy20_coder_rollouts12.json"
export SHARD_BASENAME="v4_plan_e2_easy20_coder_rollouts12"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_e2_plan_easy20q.log"

export MCTS_USE_SIGNATURE_V2=1
export MCTS_SELECTOR_STRATEGY=R2
export MCTS_REWARD_CALIBRATED=1
export MCTS_CTE_DIVERSE_PROMPT=1
export MCTS_CTE_DIVERSE_N=3
export MCTS_CTE_DIVERSE_TEMPS="0.3,0.6,0.9"
export MCTS_SQL_GEN_TEMPS="0.3,0.6,0.9"
export MCTS_SKIP_M_VERIFY=1
export MCTS_USE_DECOMPOSE_FLOW=1
export DECOMPOSE_STRATEGY=S2
export MCTS_STRATEGY_MODE=FORCE_S2
export MCTS_MULTI_PLAN=1
export MCTS_PLAN_ROLLOUTS_PER=4
export MAX_CTE_NODES=5

export MODEL_TAG=coder
export ROLL_OUTS=12
export RANDOM_SEED=20240601
export TASK_TIMEOUT=600
export RESUME="${RESUME:-0}"
export SKIP_VLLM_WAIT=1
export POST_ANALYSIS=0
export N_SHARDS="${N_SHARDS:-4}"
export BASE_PORT="${BASE_PORT:-8000}"
export PORT_STRIDE=100
export MULTI_BASE_URLS="${MULTI_BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1,http://127.0.0.1:8200/v1,http://127.0.0.1:8300/v1}"

SCRIPT="scripts/run_clarify_a0_30q.sh"
ANALYSIS="${PLAN_DIR}"
TARGET=20

cmd_full() {
  echo "[e2-plan-easy20] multi-plan=1 rollouts=${ROLL_OUTS} shards=${N_SHARDS} target=${TARGET}"
  echo "[e2-plan-easy20] output: ${JSON_OUT}"
  "${SCRIPT}" prepare-shards
  "${SCRIPT}" start-sharded
  while true; do
    n=0
    for ((i = 0; i < N_SHARDS; i++)); do
      p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
      [[ -f "${p}" ]] && n=$((n + $(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)))
    done
    scr=$(screen -ls 2>/dev/null | grep -c "clarify_a0_coder_r${ROLL_OUTS}_w" || true)
    echo "[wait] qids=${n}/${TARGET} screens=${scr}"
    [[ "${n}" -ge "${TARGET}" && "${scr}" -eq 0 ]] && break
    sleep 45
  done
  "${SCRIPT}" merge-shards
  bash "$(dirname "$0")/run_e2_plan_easy20q.sh" report
}

cmd_report() {
  python3 "${ANALYSIS}/e2_easy20_gateway.py"
}

case "${1:-status}" in
  full) cmd_full ;;
  report) cmd_report ;;
  status) OUT_DIR="${OUT_DIR}" JSON_OUT="${JSON_OUT}" SHARD_BASENAME="${SHARD_BASENAME}" QIDS_SHARD_DIR="${QIDS_SHARD_DIR}" QIDS_FILE="${QIDS_FILE}" ROLL_OUTS="${ROLL_OUTS}" "${SCRIPT}" status ;;
  stop) "${SCRIPT}" stop all ;;
  merge) "${SCRIPT}" merge-shards ;;
  start) "${SCRIPT}" prepare-shards; "${SCRIPT}" start-sharded ;;
  prepare-shards) "${SCRIPT}" prepare-shards ;;
  start-sharded) "${SCRIPT}" start-sharded ;;
  *)
    echo "Usage: $0 {full|start|prepare-shards|start-sharded|report|status|stop|merge}"
    ;;
esac
