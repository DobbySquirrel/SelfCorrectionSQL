#!/usr/bin/env bash
# Scheme A: legacy signature for CTE/search + v2 for final SQL buckets (gap30).
#
#   bash run_signature_gap30_dual.sh start-4-screen   # 4 shard screens (:8000+:8100 each)
#   bash run_signature_gap30_dual.sh status
#   bash run_signature_gap30_dual.sh merge
#   bash run_signature_gap30_dual.sh report
#   bash run_signature_gap30_dual.sh stop
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
MANIFEST="${PLAN_DIR}/qids_alpha_min2_recall_gap30_manifest.json"
SCRIPT="scripts/run_clarify_a0_30q.sh"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_signature_gap30_dual.sh"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

export MCTS_EXEC_TIME_TIEBREAK=0
export MCTS_DEDUP_BEFORE_REVISE=0
export MCTS_REVERSED_SCHEMA_LINKING=1
export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1
export MCTS_BOOTSTRAP_ONCE_PER_QUESTION=0
export MCTS_FK_PK_CLOSURE=0

export MCTS_COLUMN_BINDING_COT="per_subq@0.3+dual"
export MCTS_COLUMN_BINDING_SCOPE="global"
export DECOMPOSE_STRATEGY="S2"
export MCTS_DECOMPOSE_MIN_SUBQUESTIONS="2"
export MCTS_SELECTOR_STRATEGY="R4"
export MCTS_CONFIDENCE_MODE="gated"
export MCTS_R4_GATE_MARGIN="0.7"
export MCTS_CONFIDENCE_THRESHOLD="0.7"
export MCTS_CONFIDENCE_TOP_K="${MCTS_CONFIDENCE_TOP_K:-3}"
export MCTS_CONFIDENCE_VOTE_SAMPLES="${MCTS_CONFIDENCE_VOTE_SAMPLES:-3}"
export MCTS_R4_VOTE_MODE="${MCTS_R4_VOTE_MODE:-all_buckets}"

# Scheme A: search legacy, final v2
export MCTS_USE_SIGNATURE_V2="0"
export MCTS_FINAL_SIGNATURE_V2="1"

export MODEL_TAG="abl5_sigA_dual_gap30"
export ROLL_OUTS="${ROLL_OUTS:-12}"
export RANDOM_SEED=20240612
export TASK_TIMEOUT="${TASK_TIMEOUT:-900}"
export RESUME="${RESUME:-0}"
export SKIP_VLLM_WAIT="${SKIP_VLLM_WAIT:-1}"
export POST_ANALYSIS=0
export N_SHARDS="${N_SHARDS:-4}"
export SHARD_MULTI_VLLM="${SHARD_MULTI_VLLM:-1}"
export BASE_PORT="${BASE_PORT:-8000}"
export PORT_STRIDE=100
export MULTI_BASE_URLS="${MULTI_BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1}"
export SHARD_SPLIT="${SHARD_SPLIT:-time_balanced}"
export SHARD_TIME_JSON="${SHARD_TIME_JSON:-${OUT_DIR}/v4_colbind_v2_dual03_global_filter_498q_rollouts12.json}"

export QIDS_FILE="${MANIFEST}"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_${MODEL_TAG}"
export SHARD_BASENAME="v4_colbind_v2_dual03_min2sq_${MODEL_TAG}_r${ROLL_OUTS}"
export JSON_OUT="${OUT_DIR}/${SHARD_BASENAME}.json"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_${MODEL_TAG}_r${ROLL_OUTS}.log"

have_screen() { command -v screen >/dev/null 2>&1; }

cmd_manifest() {
  [[ -f "${MANIFEST}" ]] || python3 "${PLAN_DIR}/build_alpha_min2_recall_gap30_manifest.py"
}

_launch_shard_worker() {
  local idx="$1"
  local qids_file="${QIDS_SHARD_DIR}/shard${idx}.json"
  [[ -f "${qids_file}" ]] || { echo "missing ${qids_file}" >&2; return 1; }

  local screen_name="clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${idx}"
  local json_out="${OUT_DIR}/${SHARD_BASENAME}_w${idx}.json"
  local sql_out="${json_out%.json}.txt"
  local log="${json_out%.json}.log"

  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${screen_name}[[:space:]]"; then
    echo "[skip] ${screen_name} already running"
    return 0
  fi

  echo "[shard] sigA w${idx} search=legacy final=v2 gated=1 multi=${MULTI_BASE_URLS}"
  screen -dmS "${screen_name}" bash -lc "$(cat <<EOF
set -euo pipefail
cd '${ROOT_DIR}'
source '${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh'
export MCTS_EXEC_TIME_TIEBREAK=0
export MCTS_DEDUP_BEFORE_REVISE=0
export MCTS_REVERSED_SCHEMA_LINKING=1
export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1
export MCTS_BOOTSTRAP_ONCE_PER_QUESTION=0
export MCTS_FK_PK_CLOSURE=0
export MCTS_COLUMN_BINDING_COT='per_subq@0.3+dual'
export MCTS_COLUMN_BINDING_SCOPE='global'
export DECOMPOSE_STRATEGY='S2'
export MCTS_DECOMPOSE_MIN_SUBQUESTIONS='2'
export MCTS_SELECTOR_STRATEGY='R4'
export MCTS_CONFIDENCE_MODE='gated'
export MCTS_R4_GATE_MARGIN='0.7'
export MCTS_CONFIDENCE_THRESHOLD='0.7'
export MCTS_CONFIDENCE_TOP_K='${MCTS_CONFIDENCE_TOP_K:-3}'
export MCTS_CONFIDENCE_VOTE_SAMPLES='${MCTS_CONFIDENCE_VOTE_SAMPLES:-3}'
export MCTS_R4_VOTE_MODE='${MCTS_R4_VOTE_MODE:-all_buckets}'
export MCTS_USE_SIGNATURE_V2='0'
export MCTS_FINAL_SIGNATURE_V2='1'
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
  sleep 0.5
}

cmd_start_4_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  cmd_stop_quiet
  cmd_manifest
  mkdir -p "${QIDS_SHARD_DIR}"
  QIDS_FILE="${MANIFEST}" QIDS_SHARD_DIR="${QIDS_SHARD_DIR}" N_SHARDS="${N_SHARDS}" ROLL_OUTS="${ROLL_OUTS}" \
    "${SCRIPT}" prepare-shards
  local i
  for ((i = 0; i < N_SHARDS; i++)); do
    _launch_shard_worker "${i}"
  done
  sleep 1
  cmd_status
}

cmd_merge() {
  export MODEL_TAG QIDS_SHARD_DIR SHARD_BASENAME JSON_OUT
  MODEL_TAG="${MODEL_TAG}" QIDS_SHARD_DIR="${QIDS_SHARD_DIR}" SHARD_BASENAME="${SHARD_BASENAME}" \
    JSON_OUT="${JSON_OUT}" "${SCRIPT}" merge-shards
}

cmd_status() {
  local target
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo 30)"
  local n=0 i
  for ((i = 0; i < N_SHARDS; i++)); do
    local p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
    local sn=0
    [[ -f "${p}" ]] && sn=$(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)
    n=$((n + sn))
    echo "  sigA w${i}: ${sn} qids"
  done
  echo "sigA dual (${MODEL_TAG}): ${n}/${target}"
  screen -ls 2>/dev/null | grep -E "${MODEL_TAG}" || echo "(no sigA screens)"
}

cmd_stop_quiet() {
  local i
  for ((i = 0; i < N_SHARDS; i++)); do
    screen -S "clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${i}" -X quit 2>/dev/null || true
  done
}

cmd_stop() {
  cmd_stop_quiet
  echo "[stop] sigA dual gap30 (${N_SHARDS} screens)"
}

cmd_report() {
  python3 -u - <<PY
import json, re
from pathlib import Path
out = Path("${OUT_DIR}")
manifest = json.loads(Path("${MANIFEST}").read_text())
qids = [str(q) for q in manifest["qids"]]
merged = {}
for i in range(${N_SHARDS}):
    p = out / "${SHARD_BASENAME}_w%d.json" % i
    if p.exists():
        merged.update(json.loads(p.read_text()))
rec = sum(1 for q in qids if q in merged and any(a.get("is_correct") for a in merged[q].get("all_sqls_with_attributes") or []))
hit = sum(1 for q in qids if q in merged and (merged[q].get("stats") or {}).get("gold_match"))
fmt = "?"
for q in qids:
    if q not in merged: continue
    rb = ((merged[q].get("rollout_stats") or [{}])[0].get("result_buckets") or {})
    if rb:
        s = next(iter(rb))
        fmt = "v2_final" if re.fullmatch(r"[0-9a-f]{32}", s) else "legacy"
        break
print(f"sigA dual gap30: done={len([q for q in qids if q in merged])}/30 Recall={rec}/30 Hit@1={hit}/30 final_sig_fmt={fmt}")
PY
}

case "${1:-status}" in
  start-4-screen) cmd_start_4_screen ;;
  merge) cmd_merge ;;
  status) cmd_status ;;
  report) cmd_report ;;
  stop) cmd_stop ;;
  *)
    echo "Usage: $0 {start-4-screen|merge|status|report|stop}"
    exit 1
    ;;
esac
