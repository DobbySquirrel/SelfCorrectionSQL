#!/usr/bin/env bash
# E1: Decomposition Plan experiment on new30 (3 plans × 4 rollouts = 12)
#
#   bash workflows/mcts_v4/test/out/cte_diverse/run_e1_plan_new30q.sh full
#   bash workflows/mcts_v4/test/out/cte_diverse/run_e1_plan_new30q.sh status
#   bash workflows/mcts_v4/test/out/cte_diverse/run_e1_plan_new30q.sh report
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/new30_plan"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
export QIDS_FILE="${PLAN_DIR}/qids_manifest.json"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_e1_plan_new30_r12"
export JSON_OUT="${OUT_DIR}/v4_plan_e1_new30_coder_rollouts12.json"
export SHARD_BASENAME="v4_plan_e1_new30_coder_rollouts12"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_e1_plan_new30q.log"

# B′ invariants + multi-plan
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

_prepare_qids_manifest() {
  python3 - <<'PY'
import json
from pathlib import Path
plan = Path("workflows/mcts_v4/test/out/cte_diverse/analysis/new30_plan")
qids = [ln.strip() for ln in (plan / "qids.txt").read_text().splitlines() if ln.strip()]
out = {"source": "new30_plan resolution 30q", "qids": qids}
(plan / "qids_manifest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"[e1] qids_manifest.json n={len(qids)}")
PY
}

cmd_stage0() {
  python3 "${ANALYSIS}/build_manifest.py"
  python3 "${ANALYSIS}/extract_e0_bprime.py"
}

cmd_full() {
  cmd_stage0
  _prepare_qids_manifest
  echo "[e1-plan-new30] multi-plan=1 rollouts=${ROLL_OUTS} (3×4) shards=${N_SHARDS}"
  echo "[e1-plan-new30] output: ${JSON_OUT}"
  "${SCRIPT}" prepare-shards
  "${SCRIPT}" start-sharded
  local target=30
  while true; do
    n=0
    for ((i = 0; i < N_SHARDS; i++)); do
      p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
      [[ -f "${p}" ]] && n=$((n + $(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)))
    done
    scr=$(screen -ls 2>/dev/null | grep -c "clarify_a0_coder_r${ROLL_OUTS}_w" || true)
    echo "[wait] qids=${n}/${target} screens=${scr}"
    [[ "${n}" -ge "${target}" && "${scr}" -eq 0 ]] && break
    sleep 45
  done
  "${SCRIPT}" merge-shards
  bash "$(dirname "$0")/run_e1_plan_new30q.sh" report
}

cmd_report() {
  python3 "${ANALYSIS}/e1_plan_topk.py"
}

case "${1:-status}" in
  stage0) cmd_stage0 ;;
  full) cmd_full ;;
  report) cmd_report ;;
  status) OUT_DIR="${OUT_DIR}" JSON_OUT="${JSON_OUT}" SHARD_BASENAME="${SHARD_BASENAME}" ROLL_OUTS="${ROLL_OUTS}" "${SCRIPT}" status ;;
  stop) "${SCRIPT}" stop all ;;
  merge) "${SCRIPT}" merge-shards ;;
  start) _prepare_qids_manifest; "${SCRIPT}" prepare-shards; "${SCRIPT}" start-sharded ;;
  prepare-shards) _prepare_qids_manifest; "${SCRIPT}" prepare-shards ;;
  start-sharded) "${SCRIPT}" start-sharded ;;
  *)
    echo "Usage: $0 {stage0|full|start|prepare-shards|start-sharded|report|status|stop|merge}"
    ;;
esac
