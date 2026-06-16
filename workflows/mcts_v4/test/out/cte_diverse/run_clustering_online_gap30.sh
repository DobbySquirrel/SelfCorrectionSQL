#!/usr/bin/env bash
# Online clustering ablations on gap30 (30q, 4 shards, dual vLLM :8000+:8100).
#
# Arms (sequential queue — one at a time on shared vLLM):
#   cte_jac85      — dir2: CTE probe Jaccard merge @0.85 (search legacy)
#   cte_jac85_dual — dir2 + Scheme A final v2
#   mul_purity     — dir1: votes(legacy) × v2 purity
#   struct_bias    — dir6: R4 + WITH-cluster bias on close margin
#   final_jac85    — dir2-lite: final SQL Jaccard merge @ selection
#
# Usage:
#   bash run_clustering_online_gap30.sh list
#   bash run_clustering_online_gap30.sh start cte_jac85      # one arm, 4 screens
#   bash run_clustering_online_gap30.sh start-all              # all 5 arms in parallel
#   bash run_clustering_online_gap30.sh start-queue            # sequential (legacy)
#   bash run_clustering_online_gap30.sh status [arm]
#   bash run_clustering_online_gap30.sh report [arm|all]
#   bash run_clustering_online_gap30.sh stop [arm|all]
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
MANIFEST="${PLAN_DIR}/qids_alpha_min2_recall_gap30_manifest.json"
SCRIPT="scripts/run_clarify_a0_30q.sh"
QUEUE_LOG="${OUT_DIR}/run_clustering_online_gap30_queue.log"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

export ROLL_OUTS="${ROLL_OUTS:-12}"
export RANDOM_SEED="${RANDOM_SEED:-20240612}"
export TASK_TIMEOUT="${TASK_TIMEOUT:-900}"
export RESUME="${RESUME:-0}"
export SKIP_VLLM_WAIT="${SKIP_VLLM_WAIT:-1}"
export POST_ANALYSIS=0
export N_SHARDS="${N_SHARDS:-4}"
export SHARD_MULTI_VLLM="${SHARD_MULTI_VLLM:-1}"
export MULTI_BASE_URLS="${MULTI_BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1}"
export SHARD_SPLIT="${SHARD_SPLIT:-time_balanced}"
export SHARD_TIME_JSON="${SHARD_TIME_JSON:-${OUT_DIR}/v4_colbind_v2_dual03_global_filter_498q_rollouts12.json}"

# Shared E6-gap30 stack
_BASE=(
  "MCTS_EXEC_TIME_TIEBREAK=0"
  "MCTS_DEDUP_BEFORE_REVISE=0"
  "MCTS_REVERSED_SCHEMA_LINKING=1"
  "MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1"
  "MCTS_BOOTSTRAP_ONCE_PER_QUESTION=0"
  "MCTS_FK_PK_CLOSURE=0"
  "MCTS_COLUMN_BINDING_COT=per_subq@0.3+dual"
  "MCTS_COLUMN_BINDING_SCOPE=global"
  "DECOMPOSE_STRATEGY=S2"
  "MCTS_DECOMPOSE_MIN_SUBQUESTIONS=2"
  "MCTS_SELECTOR_STRATEGY=R4"
  "MCTS_CONFIDENCE_MODE=gated"
  "MCTS_R4_GATE_MARGIN=0.7"
  "MCTS_CONFIDENCE_THRESHOLD=0.7"
  "MCTS_R4_VOTE_MODE=all_buckets"
  "MCTS_USE_SIGNATURE_V2=0"
  "MCTS_FINAL_SIGNATURE_V2=0"
  "MCTS_CTE_JACCARD_MERGE=0"
  "MCTS_CTE_JACCARD_THRESHOLD=0.85"
  "MCTS_FINAL_JACCARD_MERGE=0"
  "MCTS_FINAL_JACCARD_THRESHOLD=0.85"
  "MCTS_R4_SCORE_MODE=votes"
  "MCTS_R4_WITH_BIAS=0"
)

declare -A ARM_TAG=(
  [cte_jac85]="abl5_cte_jac85_gap30"
  [cte_jac85_dual]="abl5_cte_jac85_dual_gap30"
  [mul_purity]="abl5_mul_purity_gap30"
  [struct_bias]="abl5_struct_bias_gap30"
  [final_jac85]="abl5_final_jac85_gap30"
)

declare -A ARM_EXTRA=(
  [cte_jac85]="MCTS_CTE_JACCARD_MERGE=1"
  [cte_jac85_dual]="MCTS_CTE_JACCARD_MERGE=1|MCTS_FINAL_SIGNATURE_V2=1"
  [mul_purity]="MCTS_R4_SCORE_MODE=mul_purity"
  [struct_bias]="MCTS_R4_WITH_BIAS=1"
  [final_jac85]="MCTS_FINAL_JACCARD_MERGE=1"
)

ALL_ARMS=(cte_jac85 cte_jac85_dual mul_purity struct_bias final_jac85)

have_screen() { command -v screen >/dev/null 2>&1; }

arm_tag() {
  local arm="$1"
  echo "${ARM_TAG[$arm]:-}"
}

arm_env_lines() {
  local arm="$1"
  local line extra
  for line in "${_BASE[@]}"; do
    echo "export ${line}"
  done
  extra="${ARM_EXTRA[$arm]:-}"
  if [[ -n "${extra}" ]]; then
    IFS='|' read -ra parts <<< "${extra}"
    for line in "${parts[@]}"; do
      echo "export ${line}"
    done
  fi
}

cmd_manifest() {
  [[ -f "${MANIFEST}" ]] || python3 "${PLAN_DIR}/build_alpha_min2_recall_gap30_manifest.py"
}

_stop_arm_quiet() {
  local arm="$1"
  local tag
  tag="$(arm_tag "${arm}")"
  [[ -n "${tag}" ]] || return 0
  local i
  for ((i = 0; i < N_SHARDS; i++)); do
    screen -S "clarify_a0_${tag}_r${ROLL_OUTS}_w${i}" -X quit 2>/dev/null || true
  done
}

_arm_done_count() {
  local arm="$1"
  local tag n=0 i p sn
  tag="$(arm_tag "${arm}")"
  for ((i = 0; i < N_SHARDS; i++)); do
    p="${OUT_DIR}/v4_colbind_v2_dual03_min2sq_${tag}_r${ROLL_OUTS}_w${i}.json"
    sn=0
    [[ -f "${p}" ]] && sn=$(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)
    n=$((n + sn))
  done
  echo "${n}"
}

_launch_arm() {
  local arm="$1"
  local tag
  tag="$(arm_tag "${arm}")"
  [[ -n "${tag}" ]] || { echo "unknown arm: ${arm}" >&2; exit 1; }

  have_screen || { echo "screen not installed" >&2; exit 1; }
  cmd_manifest

  if screen -ls 2>/dev/null | grep -qE "clarify_a0_${tag}_r${ROLL_OUTS}_w"; then
    echo "[skip] ${arm} already running (${tag})"
    return 0
  fi

  _stop_arm_quiet "${arm}"

  export MODEL_TAG="${tag}"
  export QIDS_FILE="${MANIFEST}"
  export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_${MODEL_TAG}"
  export SHARD_BASENAME="v4_colbind_v2_dual03_min2sq_${MODEL_TAG}_r${ROLL_OUTS}"

  mkdir -p "${QIDS_SHARD_DIR}"
  QIDS_FILE="${MANIFEST}" QIDS_SHARD_DIR="${QIDS_SHARD_DIR}" N_SHARDS="${N_SHARDS}" ROLL_OUTS="${ROLL_OUTS}" \
    "${SCRIPT}" prepare-shards

  local i qids_file screen_name json_out sql_out log env_block
  env_block="$(arm_env_lines "${arm}")"
  echo "[online-cluster] arm=${arm} tag=${tag} seed=${RANDOM_SEED}"

  for ((i = 0; i < N_SHARDS; i++)); do
    qids_file="${QIDS_SHARD_DIR}/shard${i}.json"
    screen_name="clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${i}"
    json_out="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
    sql_out="${json_out%.json}.txt"
    log="${json_out%.json}.log"

    if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${screen_name}[[:space:]]"; then
      echo "[skip] ${screen_name} already running"
      continue
    fi

    screen -dmS "${screen_name}" bash -lc "$(cat <<EOF
set -euo pipefail
cd '${ROOT_DIR}'
source '${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh'
${env_block}
export OUT_DIR='${OUT_DIR}'
export SHARD_BASENAME='${SHARD_BASENAME}'
export MODEL_TAG='${MODEL_TAG}'
export ROLL_OUTS='${ROLL_OUTS}'
export RANDOM_SEED='${RANDOM_SEED}'
export TASK_TIMEOUT='${TASK_TIMEOUT}'
export RESUME='${RESUME}'
export SKIP_VLLM_WAIT=1
export RUN_INLINE=1
export SHARD_MODE=1
export SHARD_MULTI_VLLM='${SHARD_MULTI_VLLM}'
export MULTI_BASE_URLS='${MULTI_BASE_URLS}'
export JSON_OUT='${json_out}'
export SQL_OUT='${sql_out}'
export LOG='${log}'
export QIDS_FILE='${qids_file}'
export PPL_FILE='workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json'
export GOLD_FILE='workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json'
'${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' start-mcts
EOF
)"
    sleep 0.3
  done
}

_wait_arm() {
  local arm="$1"
  local target tag n
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo 30)"
  tag="$(arm_tag "${arm}")"
  while true; do
    n="$(_arm_done_count "${arm}")"
    echo "[queue] ${arm} (${tag}): ${n}/${target}"
    if [[ "${n}" -ge "${target}" ]]; then
      return 0
    fi
    if ! screen -ls 2>/dev/null | grep -q "${tag}"; then
      echo "[queue] WARNING: no screens for ${arm} but only ${n}/${target} — check logs" >&2
      return 1
    fi
    sleep 120
  done
}

cmd_list() {
  echo "Online clustering arms (gap30):"
  for arm in "${ALL_ARMS[@]}"; do
    printf "  %-16s %s  extra: %s\n" "${arm}" "${ARM_TAG[$arm]}" "${ARM_EXTRA[$arm]:-(baseline env)}"
  done
}

cmd_start() {
  local arm="${1:?arm name — run 'list'}"
  _launch_arm "${arm}"
  sleep 1
  cmd_status "${arm}"
}

cmd_start_all() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  echo "[parallel] launching all ${#ALL_ARMS[@]} arms (skip if screens already up)"
  local arm
  for arm in "${ALL_ARMS[@]}"; do
    _launch_arm "${arm}"
  done
  sleep 1
  cmd_status all
}

cmd_start_queue() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  echo "[queue] starting sequential online arms → ${QUEUE_LOG}"
  {
    echo "=== clustering online queue $(date -Iseconds) ==="
    for arm in "${ALL_ARMS[@]}"; do
      echo "--- arm ${arm} $(date -Iseconds) ---"
      _launch_arm "${arm}"
      _wait_arm "${arm}" || true
      _stop_arm_quiet "${arm}"
      cmd_report_one "${arm}" || true
    done
    echo "=== queue done $(date -Iseconds) ==="
  } 2>&1 | tee -a "${QUEUE_LOG}"
}

cmd_status() {
  local only="${1:-all}"
  local target
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo 30)"
  for arm in "${ALL_ARMS[@]}"; do
    [[ "${only}" == "all" || "${only}" == "${arm}" ]] || continue
    local tag n
    tag="$(arm_tag "${arm}")"
    n="$(_arm_done_count "${arm}")"
    echo "${arm} (${tag}): ${n}/${target}"
  done
  screen -ls 2>/dev/null | grep -E "abl5_(cte_jac|mul_pur|struct_bias|final_jac)" || echo "(no clustering screens)"
}

cmd_stop() {
  local only="${1:-all}"
  for arm in "${ALL_ARMS[@]}"; do
    [[ "${only}" == "all" || "${only}" == "${arm}" ]] || continue
    _stop_arm_quiet "${arm}"
  done
  echo "[stop] clustering online (${only})"
}

cmd_report_one() {
  local arm="$1"
  local tag target qids merged rec hit
  tag="$(arm_tag "${arm}")"
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo 30)"
  qids="$(python3 -c "import json; print(','.join(str(q) for q in json.load(open('${MANIFEST}'))['qids']))")"
  python3 -u - <<PY
import json
from pathlib import Path
out = Path("${OUT_DIR}")
qids = "${qids}".split(",")
merged = {}
for i in range(${N_SHARDS}):
    p = out / "v4_colbind_v2_dual03_min2sq_${tag}_r${ROLL_OUTS}_w{i}.json"
    if p.exists():
        merged.update(json.loads(p.read_text()))
rec = sum(1 for q in qids if q in merged and any(a.get("is_correct") for a in merged[q].get("all_sqls_with_attributes") or []))
hit = sum(1 for q in qids if q in merged and (merged[q].get("stats") or {}).get("gold_match"))
print(f"${arm:16} Recall={rec}/{len(qids)} Hit@1={hit}/{len(qids)} done={len([q for q in qids if q in merged])}/${target}")
PY
}

cmd_report() {
  local only="${1:-all}"
  echo "=== online clustering gap30 report ==="
  for arm in "${ALL_ARMS[@]}"; do
    [[ "${only}" == "all" || "${only}" == "${arm}" ]] || continue
    cmd_report_one "${arm}"
  done
  echo ""
  echo "sigA dual (reference):"
  bash "$(dirname "$0")/run_signature_gap30_dual.sh" report 2>/dev/null || true
}

case "${1:-list}" in
  list) cmd_list ;;
  start) cmd_start "${2:?arm}" ;;
  start-all) cmd_start_all ;;
  start-queue) cmd_start_queue ;;
  status) cmd_status "${2:-all}" ;;
  stop) cmd_stop "${2:-all}" ;;
  report) cmd_report "${2:-all}" ;;
  *)
    echo "Usage: $0 {list|start|start-all|start-queue|status|report|stop} [arm]"
    exit 1
    ;;
esac
