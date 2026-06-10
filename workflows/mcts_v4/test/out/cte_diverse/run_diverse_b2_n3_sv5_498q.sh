#!/usr/bin/env bash
# 498q: B2′ — 3-call, N=3, sql_var=5, rollouts=12, M_verify OFF
#
#   bash workflows/mcts_v4/test/out/cte_diverse/run_diverse_b2_n3_sv5_498q.sh full
#   bash workflows/mcts_v4/test/out/cte_diverse/run_diverse_b2_n3_sv5_498q.sh status
#   RESUME=1 bash workflows/mcts_v4/test/out/cte_diverse/run_diverse_b2_n3_sv5_498q.sh start-sharded
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
export QIDS_FILE="${QIDS_FILE:-workflows/mcts_v4/test/out/clarify_a0_a2_coder/qids_498_manifest.json}"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_b2_n3_sv5_498_r12"
export JSON_OUT="${OUT_DIR}/v4_diverse_b2_n3_sv5_498q_coder_rollouts12.json"
export SHARD_BASENAME="v4_diverse_b2_n3_sv5_498q_coder_rollouts12"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_diverse_b2_n3_sv5_498q.log"

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
export MAX_CTE_NODES=5

export MODEL_TAG=coder
export ROLL_OUTS=12
export RANDOM_SEED=20240601
export RESUME="${RESUME:-0}"
export TASK_TIMEOUT="${TASK_TIMEOUT:-600}"
export SKIP_VLLM_WAIT=1
export POST_ANALYSIS=0
export N_SHARDS="${N_SHARDS:-4}"
export BASE_PORT="${BASE_PORT:-8000}"
export PORT_STRIDE=100
export MULTI_BASE_URLS="${MULTI_BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1,http://127.0.0.1:8200/v1,http://127.0.0.1:8300/v1}"

SCRIPT="scripts/run_clarify_a0_30q.sh"

cmd_full() {
  echo "[b2-n3-sv5-498q] temps=${MCTS_CTE_DIVERSE_TEMPS} N=${MCTS_CTE_DIVERSE_N} r=${ROLL_OUTS} sql_var=5 shards=${N_SHARDS}"
  echo "[b2-n3-sv5-498q] output: ${JSON_OUT}"
  "${SCRIPT}" prepare-shards
  "${SCRIPT}" start-sharded
  local target=498
  while true; do
    n=0
    for ((i = 0; i < N_SHARDS; i++)); do
      p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
      [[ -f "${p}" ]] && n=$((n + $(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)))
    done
    scr=$(screen -ls 2>/dev/null | grep -c "clarify_a0_coder_r${ROLL_OUTS}_w" || true)
    echo "[wait] qids=${n}/${target} screens=${scr}"
    [[ "${n}" -ge "${target}" && "${scr}" -eq 0 ]] && break
    sleep 60
  done
  "${SCRIPT}" merge-shards
  echo "[b2-n3-sv5-498q] done $(date -Iseconds)"
}

case "${1:-status}" in
  full) cmd_full ;;
  prepare-shards) "${SCRIPT}" prepare-shards ;;
  start-sharded) "${SCRIPT}" start-sharded ;;
  merge) "${SCRIPT}" merge-shards ;;
  status) OUT_DIR="${OUT_DIR}" JSON_OUT="${JSON_OUT}" SHARD_BASENAME="${SHARD_BASENAME}" ROLL_OUTS="${ROLL_OUTS}" "${SCRIPT}" status ;;
  stop) "${SCRIPT}" stop all ;;
  *)
    echo "Usage: $0 {full|prepare-shards|start-sharded|merge|status|stop}"
    ;;
esac
