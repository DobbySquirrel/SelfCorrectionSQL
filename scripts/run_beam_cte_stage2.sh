#!/usr/bin/env bash
# Beam-CTE stage 2：30 题 × A+C+D form beam + oneshot B/E + judge V2 + screen
#
# 用法（仓库根目录）:
#   ./scripts/run_beam_cte_stage2.sh status
#   ./scripts/run_beam_cte_stage2.sh start-vllm
#   RUN_SUFFIX=v1 ./scripts/run_beam_cte_stage2.sh start
#   BEAM_LIMIT=3 ./scripts/run_beam_cte_stage2.sh start
#   ./scripts/run_beam_cte_stage2.sh attach [beam|vllm]
#   ./scripts/run_beam_cte_stage2.sh stop [beam|vllm|everything]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

: "${GPUS:=0,1,2,3}"
: "${MODEL_PATH:=$HOME/wtao565/models/Qwen3-32B}"
: "${VLLM_PORT:=8000}"
: "${VLLM_HOST:=0.0.0.0}"
: "${GPU_MEM_UTIL:=0.8}"
: "${MAX_MODEL_LEN:=8192}"
: "${TP_SIZE:=4}"
: "${VLLM_WAIT_SEC:=600}"
: "${CONDA_ENV_VLLM:=Qwen3-32B}"
: "${CONDA_ENV_LAB:=base}"
: "${CONDA_BASE:=/hpc2hdd/home/sshen190/miniconda3}"
: "${RUN_SUFFIX:=}"
: "${K_A:=3}"
: "${K_C:=3}"
: "${K_D:=3}"
: "${MAX_PATHS:=12}"
: "${K_SAMPLES:=5}"
: "${K_REGEN:=3}"
: "${LLM_BACKEND:=local}"
: "${LLM_PRESET:=}"
: "${QIDS_FILE:=workflows/mcts_v5/test/fixtures/qids_30sample.json}"
: "${BEAM_LIMIT:=0}"
: "${SKIP_VLLM_WAIT:=0}"
: "${JUDGE_MODE:=v2}"
: "${BEAM_STAGE1_JSON:=workflows/mcts_v5/test/out/beam_cte_stage1_stage1_v2.json}"

OUT_DIR="workflows/mcts_v5/test/out"
SCREEN_VLLM="vllm32b_tp4"
VLLM_URL="http://127.0.0.1:${VLLM_PORT}/v1"
BEAM_SCRIPT="workflows/mcts_v5/test/test_beam_cte_stage2.py"

_init_paths() {
  local suf=""
  if [[ -n "${RUN_SUFFIX}" ]]; then
    suf="_${RUN_SUFFIX//[^A-Za-z0-9._-]/_}"
  fi
  LOG="${OUT_DIR}/beam_cte_stage2${suf}.log"
  JSON="${OUT_DIR}/beam_cte_stage2${suf}.json"
  REPORT_MD="${OUT_DIR}/beam_cte_stage2_report${suf}.md"
  SCREEN="beam_stage2${suf}"
}

mkdir -p "$OUT_DIR"

die() { echo "[ERROR] $*" >&2; exit 1; }

have_screen() { command -v screen >/dev/null 2>&1; }

screen_running() {
  local name="$1"
  screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\.${name}[[:space:]]"
}

screen_cmd() {
  local name="$1"
  shift
  screen -dmS "$name" bash -lc "$*"
}

wait_vllm() {
  if [[ "${SKIP_VLLM_WAIT}" == "1" ]]; then
    echo "[skip] SKIP_VLLM_WAIT=1"
    return 0
  fi
  local deadline=$((SECONDS + VLLM_WAIT_SEC))
  echo "[wait] vLLM ${VLLM_URL}/models (max ${VLLM_WAIT_SEC}s) ..."
  while (( SECONDS < deadline )); do
    if curl -sf "${VLLM_URL}/models" >/dev/null 2>&1; then
      echo "[ok] vLLM is up."
      return 0
    fi
    sleep 5
  done
  die "vLLM not ready after ${VLLM_WAIT_SEC}s. Check: screen -r ${SCREEN_VLLM}"
}

_beam_py_invocation() {
  local json_out="$1"
  local -a cmd=(
    python -u "${BEAM_SCRIPT}"
    --qids-file "${QIDS_FILE}"
    --k-a "${K_A}"
    --k-c "${K_C}"
    --k-d "${K_D}"
    --max-paths "${MAX_PATHS}"
    --out "${json_out}"
    --k-samples "${K_SAMPLES}"
    --k-regen "${K_REGEN}"
    --llm-backend "${LLM_BACKEND}"
    --judge-mode "${JUDGE_MODE}"
  )
  if [[ "${BEAM_LIMIT}" -gt 0 ]]; then
    cmd+=(--limit "${BEAM_LIMIT}")
  fi
  if [[ -n "${LLM_PRESET}" ]]; then
    cmd+=(--llm-preset "${LLM_PRESET}")
  fi
  local q=()
  local a
  for a in "${cmd[@]}"; do
    q+=("$(printf '%q' "$a")")
  done
  (IFS=' '; echo "${q[*]}")
}

cmd_status() {
  _init_paths
  [[ -n "${RUN_SUFFIX}" ]] && echo "======== RUN_SUFFIX=${RUN_SUFFIX} ========"
  echo "======== screen ========"
  screen -ls 2>/dev/null | grep -E 'beam_stage2|vllm' || screen -ls 2>/dev/null || echo "(no screen)"
  echo
  echo "======== beam stage2 processes ========"
  ps aux 2>/dev/null | grep -E '[t]est_beam_cte_stage2' || echo "(none)"
  echo
  echo "======== vLLM ========"
  if curl -sf "${VLLM_URL}/models" >/dev/null 2>&1; then
    echo "  ${VLLM_URL} OK"
  else
    echo "  ${VLLM_URL} not reachable"
  fi
  echo
  echo "======== logs / json ========"
  for f in "$OUT_DIR"/beam_cte_stage2*.log "$OUT_DIR"/beam_cte_stage2*.json; do
    [[ -f "$f" ]] || continue
    if [[ "$f" == *.log ]]; then
      local n
      n=$(grep -c '^\[beam-ACD\]' "$f" 2>/dev/null) || n=0
      echo "  $(basename "$f"): ${n} qid lines"
    else
      echo "  $(basename "$f"): $(wc -c <"$f" | tr -d ' ') bytes"
    fi
  done
  echo
  echo "  attach: screen -r ${SCREEN}"
  echo "  tail:   tail -f ${LOG}"
}

cmd_cleanup() {
  echo "[cleanup] killing test_beam_cte_stage2.py ..."
  pkill -f 'test_beam_cte_stage2.py' 2>/dev/null || true
  sleep 1
  pgrep -af 'test_beam_cte_stage2' || echo "  no beam processes left"
}

cmd_start_vllm() {
  have_screen || die "screen not installed"
  GPUS="$GPUS" MODEL_PATH="$MODEL_PATH" VLLM_PORT="$VLLM_PORT" \
    VLLM_HOST="$VLLM_HOST" GPU_MEM_UTIL="$GPU_MEM_UTIL" \
    MAX_MODEL_LEN="$MAX_MODEL_LEN" TP_SIZE="$TP_SIZE" SCREEN_NAME="$SCREEN_VLLM" \
    CONDA_ENV="$CONDA_ENV_VLLM" CONDA_BASE="$CONDA_BASE" \
    "${ROOT_DIR}/scripts/start_vllm_qwen32b_4gpu.sh" screen
}

cmd_start() {
  have_screen || die "screen not installed"
  _init_paths
  if screen_running "$SCREEN"; then
    echo "[skip] screen ${SCREEN} already exists"
    return 0
  fi
  wait_vllm
  local py_inv
  py_inv=$(_beam_py_invocation "$JSON")
  local inner
  inner=$(cat <<EOF
set -e
source '${CONDA_BASE}/etc/profile.d/conda.sh'
conda activate '${CONDA_ENV_LAB}'
cd '${ROOT_DIR}'
export VLLM_API_URL='${VLLM_URL}'
export PYTHONUNBUFFERED=1
echo "======== beam_cte stage2 \$(date -Iseconds) ========" | tee '${LOG}'
echo "  log:  ${LOG}" | tee -a '${LOG}'
echo "  json: ${JSON}" | tee -a '${LOG}'
${py_inv} 2>&1 | tee -a '${LOG}'
echo "======== compare \$(date -Iseconds) ========" | tee -a '${LOG}'
python -u workflows/mcts_v5/test/test_beam_cte_stage1_compare.py \\
  --beam '${BEAM_STAGE1_JSON}' \\
  --beam-stage2 '${JSON}' \\
  --label stage2 \\
  --out-md '${REPORT_MD}' 2>&1 | tee -a '${LOG}'
echo "======== done \$(date -Iseconds) exit=\$? ========" | tee -a '${LOG}'
EOF
)
  screen_cmd "$SCREEN" "$inner"
  echo "[started] screen -S ${SCREEN}"
  echo "  log:  ${LOG}"
  echo "  json: ${JSON}"
}

cmd_attach() {
  _init_paths
  local target="${1:-beam}"
  case "$target" in
    beam) screen -r "$SCREEN" ;;
    vllm) screen -r "$SCREEN_VLLM" ;;
    *) die "attach target: beam|vllm" ;;
  esac
}

cmd_stop() {
  local what="${1:-beam}"
  case "$what" in
    beam) cmd_cleanup ;;
    vllm)
      screen -S "$SCREEN_VLLM" -X quit 2>/dev/null || true
      ;;
    everything)
      cmd_cleanup
      screen -S "$SCREEN_VLLM" -X quit 2>/dev/null || true
      ;;
    *) die "stop: beam|vllm|everything" ;;
  esac
}

case "${1:-status}" in
  status) cmd_status ;;
  start-vllm) cmd_start_vllm ;;
  start) cmd_start ;;
  attach) cmd_attach "${2:-beam}" ;;
  stop) cmd_stop "${2:-beam}" ;;
  cleanup) cmd_cleanup ;;
  *)
    echo "Usage: $0 {status|start-vllm|start|attach|stop|cleanup}"
    exit 1
    ;;
esac
