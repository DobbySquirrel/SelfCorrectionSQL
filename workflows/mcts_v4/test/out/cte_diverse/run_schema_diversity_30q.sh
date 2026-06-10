#!/usr/bin/env bash
# Schema diversity 30q ablation runner (same as B′ default since hard30 validation).
# Prefer: run_diverse_b2_n3_sv5_30q.sh or any script sourcing bprime_env.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/new30_plan"
DIV_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/schema_diversity_30q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
export QIDS_FILE="${PLAN_DIR}/qids_manifest.json"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_schema_div_30_r12"
export JSON_OUT="${OUT_DIR}/v4_schema_div_30q_coder_rollouts12.json"
export SHARD_BASENAME="v4_schema_div_30q_coder_rollouts12"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_schema_diversity_30q.log"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

export MODEL_TAG=schema_div
export ROLL_OUTS=12
export RANDOM_SEED=20240601
export TASK_TIMEOUT=600
export RESUME="${RESUME:-0}"
export SKIP_VLLM_WAIT=1
export POST_ANALYSIS=0
export N_SHARDS="${N_SHARDS:-2}"
export BASE_PORT="${BASE_PORT:-8200}"
export PORT_STRIDE=100
export MULTI_BASE_URLS="${MULTI_BASE_URLS:-http://127.0.0.1:8200/v1,http://127.0.0.1:8300/v1}"

SCRIPT="scripts/run_clarify_a0_30q.sh"
mkdir -p "${DIV_DIR}"
cp -f workflows/mcts_v4/prompts/schema_linking_prompt.txt "${DIV_DIR}/schema_linking_prompt.txt"

_prepare_qids_manifest() {
  python3 - <<'PY'
import json
from pathlib import Path
plan = Path("workflows/mcts_v4/test/out/cte_diverse/analysis/new30_plan")
qids = [ln.strip() for ln in (plan / "qids.txt").read_text().splitlines() if ln.strip()]
out = {"source": "new30_plan hard30", "qids": qids}
(plan / "qids_manifest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"[schema-div] qids_manifest.json n={len(qids)}")
PY
}

cmd_full() {
  _prepare_qids_manifest
  echo "[schema-div-30] adapter=0 schema_div=1 rollouts=${ROLL_OUTS} shards=${N_SHARDS}"
  echo "[schema-div-30] output: ${JSON_OUT}"
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
  bash "$(dirname "$0")/run_schema_diversity_30q.sh" report
}

cmd_report() {
  python3 "${DIV_DIR}/per_temp_diversity.py"
}

case "${1:-status}" in
  full) cmd_full ;;
  report) cmd_report ;;
  status) OUT_DIR="${OUT_DIR}" JSON_OUT="${JSON_OUT}" SHARD_BASENAME="${SHARD_BASENAME}" ROLL_OUTS="${ROLL_OUTS}" "${SCRIPT}" status ;;
  stop) "${SCRIPT}" stop all ;;
  merge) "${SCRIPT}" merge-shards ;;
  start) _prepare_qids_manifest; "${SCRIPT}" prepare-shards; "${SCRIPT}" start-sharded ;;
  prepare-shards) _prepare_qids_manifest; "${SCRIPT}" prepare-shards ;;
  start-sharded) "${SCRIPT}" start-sharded ;;
  *)
    echo "Usage: $0 {full|start|prepare-shards|start-sharded|report|status|stop|merge}"
    ;;
esac
