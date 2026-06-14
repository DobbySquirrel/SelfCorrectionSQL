#!/usr/bin/env bash
# Five isolated ablation experiments on alpha-min2 recall-gap 30q cohort.
#
#   E0/P0  offline exec-time tiebreak (dual03 global 498, no vLLM)
#   E1     baseline min2 30q (all patch flags OFF)
#   E2/P1  dedup-before-revise ONLY
#   E3/P2  reversed schema 5th CTE ONLY
#   E4/P5  FK/PK closure 0.9 linking ONLY
#   E5     reversed + FK/PK on both reversed & 0.9 linking paths
#   E6     reversed + rollout1 bootstrap direct SQL (parallel 5-path)
#   E7     E5 + E6: bootstrap + FK/PK closure on reversed & 0.9 linking
#   E8     v2: bootstrap once/question + FK/PK on 0.9 linking only
#   E9     E6 + v2 bootstrap once/question, no FK/PK (recall candidate)
#   E6     reversed + rollout1 bootstrap direct SQL (parallel with 5 CTE paths)
#
# Usage:
#   bash run_ablation5_gap30.sh manifest          # build 30q cohort
#   bash run_ablation5_gap30.sh p0-offline        # P0 replay now
#   bash run_ablation5_gap30.sh start e1          # start one live arm
#   bash run_ablation5_gap30.sh start-all-screen   # P0 + E1-E4 全部 screen 并行
#   bash run_ablation5_gap30.sh status-screen      # 查看 screen / 进度
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
MANIFEST="${PLAN_DIR}/qids_alpha_min2_recall_gap30_manifest.json"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

export MCTS_COLUMN_BINDING_COT="per_subq@0.3+dual"
export MCTS_COLUMN_BINDING_SCOPE="global"
export DECOMPOSE_STRATEGY="S2"
export MCTS_DECOMPOSE_MIN_SUBQUESTIONS="2"
export ROLL_OUTS="${ROLL_OUTS:-12}"
export RANDOM_SEED=20240610
export TASK_TIMEOUT="${TASK_TIMEOUT:-900}"
export RESUME="${RESUME:-0}"
export SKIP_VLLM_WAIT=1
export POST_ANALYSIS=0
export N_SHARDS="${N_SHARDS:-2}"
export BASE_PORT="${BASE_PORT:-8000}"
export PORT_STRIDE=100
export MULTI_BASE_URLS="${MULTI_BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1}"
export SHARD_SPLIT="${SHARD_SPLIT:-time_balanced}"
export SHARD_TIME_JSON="${SHARD_TIME_JSON:-${OUT_DIR}/v4_colbind_v2_dual03_global_filter_498q_rollouts12.json}"

SCRIPT="scripts/run_clarify_a0_30q.sh"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_ablation5_gap30.sh"

have_screen() { command -v screen >/dev/null 2>&1; }

screen_running() {
  local name="$1"
  screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\.${name}[[:space:]]"
}

screen_launch() {
  local name="$1"
  shift
  have_screen || { echo "screen not installed" >&2; exit 1; }
  if screen_running "${name}"; then
    echo "[skip] screen ${name} already running"
    return 0
  fi
  screen -dmS "${name}" bash -lc "cd '${ROOT_DIR}' && $*"
  sleep 0.5
  if screen_running "${name}"; then
    echo "[started] screen -S ${name}"
  else
    echo "[warn] screen ${name} may have exited immediately" >&2
  fi
}

patch_all_off() {
  export MCTS_EXEC_TIME_TIEBREAK=0
  export MCTS_DEDUP_BEFORE_REVISE=0
  export MCTS_REVERSED_SCHEMA_LINKING=0
  export MCTS_FK_PK_CLOSURE=0
  export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=0
}

arm_env() {
  local arm="$1"
  patch_all_off
  case "${arm}" in
    e1|baseline)
      ;;
    e2|p1|dedup)
      export MCTS_DEDUP_BEFORE_REVISE=1
      ;;
    e3|p2|reversed)
      export MCTS_REVERSED_SCHEMA_LINKING=1
      ;;
    e4|p5|fkpk)
      export MCTS_FK_PK_CLOSURE=1
      ;;
    e5|e3fk|combo|reversed_fkpk)
      export MCTS_REVERSED_SCHEMA_LINKING=1
      export MCTS_FK_PK_CLOSURE=1
      ;;
    e6|bootstrap|reversed_bootstrap)
      export MCTS_REVERSED_SCHEMA_LINKING=1
      export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1
      ;;
    e7|bootstrap_fkpk|full_combo)
      export MCTS_REVERSED_SCHEMA_LINKING=1
      export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1
      export MCTS_FK_PK_CLOSURE=1
      ;;
    e8|v2|bootstrap_once_fkpk)
      export MCTS_REVERSED_SCHEMA_LINKING=1
      export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1
      export MCTS_FK_PK_CLOSURE=1
      ;;
    e9|bootstrap_once|e6_v2)
      export MCTS_REVERSED_SCHEMA_LINKING=1
      export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1
      ;;
    *)
      echo "unknown arm: ${arm}" >&2
      exit 1
      ;;
  esac
}

arm_tag() {
  case "$1" in
    e1|baseline) echo "abl5_e1_baseline" ;;
    e2|p1|dedup) echo "abl5_e2_dedup" ;;
    e3|p2|reversed) echo "abl5_e3_reversed" ;;
    e4|p5|fkpk) echo "abl5_e4_fkpk" ;;
    e5|e3fk|combo|reversed_fkpk) echo "abl5_e5_reversed_fkpk" ;;
    e6|bootstrap|reversed_bootstrap) echo "abl5_e6_reversed_bootstrap" ;;
    e7|bootstrap_fkpk|full_combo) echo "abl5_e7_bootstrap_fkpk" ;;
    e8|v2|bootstrap_once_fkpk) echo "abl5_e8_v2_bootstrap_fkpk" ;;
    e9|bootstrap_once|e6_v2) echo "abl5_e9_bootstrap_once" ;;
    *) echo "abl5_${1}" ;;
  esac
}

cmd_manifest() {
  python3 "${PLAN_DIR}/build_alpha_min2_recall_gap30_manifest.py"
}

cmd_p0_offline() {
  echo "[P0] offline exec-time tiebreak on dual03 global 498 ..."
  python3 "${PLAN_DIR}/offline_exec_time_tiebreak_dual03.py"
}

run_arm() {
  local arm="$1"
  local tag
  tag="$(arm_tag "${arm}")"
  arm_env "${arm}"
  export MODEL_TAG="${tag}"
  export QIDS_FILE="${MANIFEST}"
  export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_${MODEL_TAG}"
  export SHARD_BASENAME="v4_colbind_v2_dual03_min2sq_${tag}_gap30_r${ROLL_OUTS}"
  export JSON_OUT="${OUT_DIR}/${SHARD_BASENAME}.json"
  export SQL_OUT="${JSON_OUT%.json}.txt"
  export LOG="${JSON_OUT%.json}.log"
  export ORCH_LOG="${OUT_DIR}/run_${MODEL_TAG}_gap30.log"
  echo "[${tag}] flags: exec=${MCTS_EXEC_TIME_TIEBREAK} dedup=${MCTS_DEDUP_BEFORE_REVISE} rev=${MCTS_REVERSED_SCHEMA_LINKING} fk=${MCTS_FK_PK_CLOSURE} bootstrap=${MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL:-0}"
  "${SCRIPT}" prepare-shards
  "${SCRIPT}" start-sharded
}

cmd_start() {
  local arm="${1:-e1}"
  [[ -f "${MANIFEST}" ]] || cmd_manifest
  run_arm "${arm}"
}

cmd_start_all() {
  [[ -f "${MANIFEST}" ]] || cmd_manifest
  for arm in e1 e2 e3 e4; do
    run_arm "${arm}"
    sleep 3
  done
  echo "[abl5] started E1-E4; monitor with: bash $0 report"
}

cmd_start_all_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  [[ -f "${MANIFEST}" ]] || cmd_manifest

  local p0_json="${PLAN_DIR}/p0_exec_time_tiebreak_dual03.json"
  if [[ -f "${p0_json}" ]]; then
    echo "[skip] P0 offline already done: ${p0_json}"
  elif screen_running "abl5_p0_offline"; then
    echo "[skip] abl5_p0_offline already running"
  else
    screen_launch "abl5_p0_offline" \
      "bash '${SELF}' p0-offline 2>&1 | tee '${OUT_DIR}/abl5_p0_offline.log'; echo '[P0 done]' \$(date -Iseconds)"
  fi

  local arm tag inner
  for arm in e1 e2 e3 e4; do
    tag="$(arm_tag "${arm}")"
    inner="bash '${SELF}' start '${arm}' 2>&1 | tee '${OUT_DIR}/run_abl5_${tag}_orch.log'; echo '[${tag} orch done]' \$(date -Iseconds)"
    screen_launch "abl5_${tag}_orch" "${inner}"
  done

  echo ""
  echo "[abl5] parallel screens launched (P0 offline + E1-E4 MCTS)"
  echo "  monitor: bash '${SELF}' status-screen"
  echo "  report:  bash '${SELF}' report"
  cmd_status_screen
}

cmd_status_screen() {
  echo "======== ablation5 screens ========"
  screen -ls 2>/dev/null | grep -E "abl5_|clarify_a0_abl5" || echo "(none)"
  echo ""
  echo "======== progress (30q cohort) ========"
  python3 -u - <<'PY'
import json
from pathlib import Path

out = Path("workflows/mcts_v4/test/out/cte_diverse")
manifest = Path("workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q/qids_alpha_min2_recall_gap30_manifest.json")
target = len(json.loads(manifest.read_text())["qids"]) if manifest.exists() else 30
arms = [
    ("P0 offline", None, "p0_exec_time_tiebreak_dual03.json"),
    ("E1 baseline", "abl5_e1_baseline", "v4_colbind_v2_dual03_min2sq_abl5_e1_baseline_gap30_r12"),
    ("E2 dedup", "abl5_e2_dedup", "v4_colbind_v2_dual03_min2sq_abl5_e2_dedup_gap30_r12"),
    ("E3 reversed", "abl5_e3_reversed", "v4_colbind_v2_dual03_min2sq_abl5_e3_reversed_gap30_r12"),
    ("E4 fk_pk", "abl5_e4_fkpk", "v4_colbind_v2_dual03_min2sq_abl5_e4_fkpk_gap30_r12"),
    ("E5 reversed+fk_pk", "abl5_e5_reversed_fkpk", "v4_colbind_v2_dual03_min2sq_abl5_e5_reversed_fkpk_gap30_r12"),
    ("E6 reversed+bootstrap", "abl5_e6_reversed_bootstrap", "v4_colbind_v2_dual03_min2sq_abl5_e6_reversed_bootstrap_gap30_r12"),
    ("E7 bootstrap+fk_pk", "abl5_e7_bootstrap_fkpk", "v4_colbind_v2_dual03_min2sq_abl5_e7_bootstrap_fkpk_gap30_r12"),
    ("E8 v2 bootstrap+fk_link", "abl5_e8_v2_bootstrap_fkpk", "v4_colbind_v2_dual03_min2sq_abl5_e8_v2_bootstrap_fkpk_gap30_r12"),
    ("E9 bootstrap once", "abl5_e9_bootstrap_once", "v4_colbind_v2_dual03_min2sq_abl5_e9_bootstrap_once_gap30_r12"),
]
for label, tag, base in arms:
    if base.endswith(".json"):
        p = Path("workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q") / base
        if p.exists():
            j = json.loads(p.read_text())
            print(f"{label}: DONE  delta_vs_prod={j.get('delta_vs_prod')}")
        elif Path("workflows/mcts_v4/test/out/cte_diverse/abl5_p0_offline.log").exists():
            print(f"{label}: running (see abl5_p0_offline.log)")
        else:
            print(f"{label}: pending")
        continue
    n = 0
    for i in range(2):
        p = out / f"{base}_w{i}.json"
        if p.exists():
            n += len(json.loads(p.read_text()))
    merged = out / f"{base}.json"
    if merged.exists():
        n = len(json.loads(merged.read_text()))
    print(f"{label}: {n}/{target} qids" + (" MERGED" if merged.exists() else ""))
PY
}

cmd_start_e5_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  [[ -f "${MANIFEST}" ]] || cmd_manifest
  local tag inner
  tag="$(arm_tag e5)"
  inner="bash '${SELF}' start e5 2>&1 | tee '${OUT_DIR}/run_abl5_${tag}_orch.log'; echo '[${tag} done]' \$(date -Iseconds)"
  screen_launch "abl5_${tag}_orch" "${inner}"
  echo "[E5] reversed + FK/PK on both paths; flags: rev=1 fk=1"
  cmd_status_screen
}

cmd_start_e6_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  [[ -f "${MANIFEST}" ]] || cmd_manifest
  local tag inner
  tag="$(arm_tag e6)"
  inner="bash '${SELF}' start e6 2>&1 | tee '${OUT_DIR}/run_abl5_${tag}_orch.log'; echo '[${tag} done]' \$(date -Iseconds)"
  screen_launch "abl5_${tag}_orch" "${inner}"
  echo "[E6] rollout1 bootstrap direct SQL + reversed 5-path; flags: rev=1 bootstrap=1"
  cmd_status_screen
}

cmd_start_e7_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  [[ -f "${MANIFEST}" ]] || cmd_manifest
  local tag inner
  tag="$(arm_tag e7)"
  inner="bash '${SELF}' start e7 2>&1 | tee '${OUT_DIR}/run_abl5_${tag}_orch.log'; echo '[${tag} done]' \$(date -Iseconds)"
  screen_launch "abl5_${tag}_orch" "${inner}"
  echo "[E7] bootstrap + FK/PK; flags: rev=1 bootstrap=1 fk=1"
  cmd_status_screen
}

cmd_start_e8_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  [[ -f "${MANIFEST}" ]] || cmd_manifest
  local tag inner
  tag="$(arm_tag e8)"
  inner="bash '${SELF}' start e8 2>&1 | tee '${OUT_DIR}/run_abl5_${tag}_orch.log'; echo '[${tag} done]' \$(date -Iseconds)"
  screen_launch "abl5_${tag}_orch" "${inner}"
  echo "[E8 v2] bootstrap once/q + FK/PK on 0.9 linking only; flags: rev=1 bootstrap=1 fk=1"
  cmd_status_screen
}

cmd_start_e9_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  [[ -f "${MANIFEST}" ]] || cmd_manifest
  local tag inner
  tag="$(arm_tag e9)"
  inner="bash '${SELF}' start e9 2>&1 | tee '${OUT_DIR}/run_abl5_${tag}_orch.log'; echo '[${tag} done]' \$(date -Iseconds)"
  screen_launch "abl5_${tag}_orch" "${inner}"
  echo "[E9] E6 + bootstrap once/q, no FK/PK; flags: rev=1 bootstrap=1 fk=0"
  cmd_status_screen
}

cmd_report() {
  python3 -u "${PLAN_DIR}/report_ablation5_gap30.py"
}

case "${1:-}" in
  manifest) cmd_manifest ;;
  p0-offline|p0) cmd_p0_offline ;;
  start) cmd_start "${2:-e1}" ;;
  start-all) cmd_start_all ;;
  start-all-screen|screen-all) cmd_start_all_screen ;;
  start-e5-screen) cmd_start_e5_screen ;;
  start-e6-screen) cmd_start_e6_screen ;;
  start-e7-screen) cmd_start_e7_screen ;;
  start-e8-screen) cmd_start_e8_screen ;;
  start-e9-screen) cmd_start_e9_screen ;;
  status-screen|status) cmd_status_screen ;;
  report) cmd_report ;;
  *)
    echo "usage: $0 {manifest|p0-offline|start [e1|e2|e3|e4|e5|e6|e7|e8|e9]|start-all|start-all-screen|start-e5-screen|start-e6-screen|start-e7-screen|start-e8-screen|start-e9-screen|status-screen|report}" >&2
    exit 1
    ;;
esac
