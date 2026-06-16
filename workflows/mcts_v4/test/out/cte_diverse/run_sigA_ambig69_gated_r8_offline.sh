#!/usr/bin/env bash
# Offline gated R4→R8 on sigA nomin2 ambiguous cohort (~69 qids, frozen pool + vLLM).
#
#   bash run_sigA_ambig69_gated_r8_offline.sh prepare-manifest
#   bash run_sigA_ambig69_gated_r8_offline.sh start-screen    # needs vLLM :8000/:8100
#   bash run_sigA_ambig69_gated_r8_offline.sh status
#   bash run_sigA_ambig69_gated_r8_offline.sh report
#   bash run_sigA_ambig69_gated_r8_offline.sh stop
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
INPUT_JSON="${INPUT_JSON:-workflows/mcts_v4/test/out/cte_diverse/v4_colbind_v2_dual03_abl5_sigA_nomin2_full498_r12.json}"
MANIFEST="${MANIFEST:-${PLAN_DIR}/qids_sigA_nomin2_ambiguous69_manifest.json}"
PY_BUILD="${PLAN_DIR}/build_sigA_nomin2_ambiguous_manifest.py"
PY_RUN="${PLAN_DIR}/gated_r8_ambiguous69_offline.py"
VOTE_MARGIN="${VOTE_MARGIN:-0.7}"
TAG="margin${VOTE_MARGIN}"
OUT_JSON="${PLAN_DIR}/gated_r8_sigA_nomin2_ambig69_${TAG}.json"
OUT_MD="${PLAN_DIR}/gated_r8_sigA_nomin2_ambig69_${TAG}.md"
LOG="${PLAN_DIR}/gated_r8_sigA_nomin2_ambig69_${TAG}.log"
SCREEN_NAME="sigA_ambig69_gated_r8_${TAG}"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

export MCTS_R4_GATE_MARGIN="${VOTE_MARGIN}"
export BASE_URLS="${BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1}"
export VLLM_MODEL="${VLLM_MODEL:-/hpc2hdd/home/sshen190/wtao565/models/Qwen3-Coder-30B}"

have_screen() { command -v screen >/dev/null 2>&1; }

cmd_prepare_manifest() {
  [[ -f "${INPUT_JSON}" ]] || { echo "missing pool ${INPUT_JSON}" >&2; exit 1; }
  MCTS_R4_GATE_MARGIN="${VOTE_MARGIN}" python3 "${PY_BUILD}" \
    --input "${INPUT_JSON}" \
    --output "${MANIFEST}" \
    --vote-margin "${VOTE_MARGIN}"
}

cmd_start_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  cmd_prepare_manifest
  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${SCREEN_NAME}[[:space:]]"; then
    echo "[skip] screen ${SCREEN_NAME} already running"
    cmd_status
    return 0
  fi
  echo "[start] ${SCREEN_NAME} ambiguous cohort gated+R8 offline"
  screen -dmS "${SCREEN_NAME}" bash -lc "$(cat <<EOF
set -euo pipefail
cd '${ROOT_DIR}'
source '${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh'
export MCTS_R4_GATE_MARGIN='${VOTE_MARGIN}'
python3 -u '${PY_RUN}' --input '${INPUT_JSON}' --manifest '${MANIFEST}' --vote-margin '${VOTE_MARGIN}' --base-urls '${BASE_URLS}' --model '${VLLM_MODEL}' --output-json '${OUT_JSON}' --output-md '${OUT_MD}' --resume 2>&1 | tee -a '${LOG}'
EOF
)"
  sleep 1
  cmd_status
}

cmd_status() {
  local n done amb
  amb="$(python3 -c "import json; print(json.load(open('${MANIFEST}')).get('n_ambiguous',0))" 2>/dev/null || echo 0)"
  done=0
  if [[ -f "${OUT_JSON}" ]]; then
    done="$(python3 -c "import json; d=json.load(open('${OUT_JSON}')); print(len(d.get('per_question') or []))" 2>/dev/null || echo 0)"
  fi
  echo "sigA nomin2 ambiguous gated+R8: done=${done}/${amb} margin=${VOTE_MARGIN}"
  echo "  manifest: ${MANIFEST}"
  echo "  output:   ${OUT_JSON}"
  screen -ls 2>/dev/null | grep -E "${SCREEN_NAME}" || echo "(no screen ${SCREEN_NAME})"
  if [[ -f "${LOG}" ]]; then
    tail -3 "${LOG}" 2>/dev/null || true
  fi
}

cmd_report() {
  [[ -f "${OUT_MD}" ]] && cat "${OUT_MD}" || echo "missing ${OUT_MD} — run start-screen first"
}

cmd_stop() {
  screen -S "${SCREEN_NAME}" -X quit 2>/dev/null || true
  echo "[stop] ${SCREEN_NAME}"
}

case "${1:-status}" in
  prepare-manifest) cmd_prepare_manifest ;;
  start-screen) cmd_start_screen ;;
  status) cmd_status ;;
  report) cmd_report ;;
  stop) cmd_stop ;;
  *)
    echo "Usage: $0 {prepare-manifest|start-screen|status|report|stop}"
    exit 1
    ;;
esac
