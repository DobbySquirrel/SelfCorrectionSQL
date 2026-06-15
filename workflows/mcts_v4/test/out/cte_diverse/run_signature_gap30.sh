#!/usr/bin/env bash
# Signature v0 vs v1 ablation on alpha-min2 recall-gap 30q.
# v2 (sig1): column-name-blind + full rows + original float precision (fixed 2026-06-15).
#
#   bash run_signature_gap30.sh start-4-screen      # 4 screens: sig0×2 + sig1×2, 15q each, dual :8000+:8100
#   bash run_signature_gap30.sh start-screen sig0   # legacy, 2 shards via clarify screens
#   bash run_signature_gap30.sh start-screen sig1   # v2, 2 shards
#   bash run_signature_gap30.sh status
#   bash run_signature_gap30.sh report
#   bash run_signature_gap30.sh stop
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
MANIFEST="${PLAN_DIR}/qids_alpha_min2_recall_gap30_manifest.json"
BASELINE_JSON="${OUT_DIR}/v4_colbind_v2_dual03_min2sq_abl5_e6_reversed_bootstrap_gap30_r12.json"
SCRIPT="scripts/run_clarify_a0_30q.sh"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_signature_gap30.sh"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

# E6 per-expand bootstrap + gated selector + all_buckets
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

export ROLL_OUTS="${ROLL_OUTS:-12}"
export RANDOM_SEED=20240612
export TASK_TIMEOUT="${TASK_TIMEOUT:-900}"
export RESUME="${RESUME:-0}"
export SKIP_VLLM_WAIT="${SKIP_VLLM_WAIT:-1}"
export POST_ANALYSIS=0
export N_SHARDS="${N_SHARDS:-2}"
export SHARD_MULTI_VLLM="${SHARD_MULTI_VLLM:-1}"
export BASE_PORT="${BASE_PORT:-8000}"
export PORT_STRIDE=100
export MULTI_BASE_URLS="${MULTI_BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1}"
export SHARD_SPLIT="${SHARD_SPLIT:-time_balanced}"
export SHARD_TIME_JSON="${SHARD_TIME_JSON:-${OUT_DIR}/v4_colbind_v2_dual03_global_filter_498q_rollouts12.json}"

have_screen() { command -v screen >/dev/null 2>&1; }

arm_sig_flag() {
  case "$1" in
    sig0|legacy|v0) echo "0" ;;
    sig1|v2|v1) echo "1" ;;
    *) echo "unknown arm: $1 (use sig0|sig1)" >&2; exit 1 ;;
  esac
}

arm_tag() {
  case "$1" in
    sig0|legacy|v0) echo "abl5_sig0_gated_gap30" ;;
    sig1|v2|v1) echo "abl5_sig1_gated_gap30" ;;
    *) arm_sig_flag "$1" >/dev/null ;;
  esac
}

configure_arm() {
  local arm="$1"
  export MCTS_USE_SIGNATURE_V2="$(arm_sig_flag "${arm}")"
  export MODEL_TAG="$(arm_tag "${arm}")"
  export QIDS_FILE="${MANIFEST}"
  export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_${MODEL_TAG}"
  export SHARD_BASENAME="v4_colbind_v2_dual03_min2sq_${MODEL_TAG}_r${ROLL_OUTS}"
  export JSON_OUT="${OUT_DIR}/${SHARD_BASENAME}.json"
  export SQL_OUT="${JSON_OUT%.json}.txt"
  export LOG="${JSON_OUT%.json}.log"
  export ORCH_LOG="${OUT_DIR}/run_${MODEL_TAG}_r${ROLL_OUTS}.log"
}

cmd_manifest() {
  [[ -f "${MANIFEST}" ]] || python3 "${PLAN_DIR}/build_alpha_min2_recall_gap30_manifest.py"
}

cmd_start() {
  local arm="${1:?arm sig0|sig1}"
  cmd_manifest
  configure_arm "${arm}"
  echo "[signature-gap30] arm=${arm} sig_v2=${MCTS_USE_SIGNATURE_V2} gated=1 vote=${MCTS_R4_VOTE_MODE} shards=${N_SHARDS}"
  export SHARD_MULTI_VLLM="${SHARD_MULTI_VLLM}"
  "${SCRIPT}" prepare-shards
  "${SCRIPT}" start-sharded
}

_launch_shard_worker() {
  local arm="$1"
  local idx="$2"
  configure_arm "${arm}"
  local qids_file="${QIDS_SHARD_DIR}/shard${idx}.json"
  [[ -f "${qids_file}" ]] || { echo "missing ${qids_file} — run prepare-shards for ${arm}" >&2; return 1; }

  local screen_name="clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${idx}"
  local json_out="${OUT_DIR}/${SHARD_BASENAME}_w${idx}.json"
  local sql_out="${json_out%.json}.txt"
  local log="${json_out%.json}.log"
  local sig_v2="${MCTS_USE_SIGNATURE_V2}"

  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${screen_name}[[:space:]]"; then
    echo "[skip] ${screen_name} already running"
    return 0
  fi

  echo "[shard] ${arm} w${idx} sig_v2=${sig_v2} multi=${MULTI_BASE_URLS} qids=${qids_file} resume=${RESUME}"
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
export MCTS_USE_SIGNATURE_V2='${sig_v2}'
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

cmd_start_arm_shards() {
  local arm="${1:?sig0|sig1}"
  cmd_manifest
  configure_arm "${arm}"
  mkdir -p "${QIDS_SHARD_DIR}"
  QIDS_FILE="${MANIFEST}" QIDS_SHARD_DIR="${QIDS_SHARD_DIR}" N_SHARDS="${N_SHARDS}" ROLL_OUTS="${ROLL_OUTS}" \
    "${SCRIPT}" prepare-shards
  local i
  for ((i = 0; i < N_SHARDS; i++)); do
    _launch_shard_worker "${arm}" "${i}"
  done
}

cmd_start_4_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  cmd_stop_quiet
  echo "[signature-gap30] 4 screens: sig0 w0/w1 + sig1 w0/w1 (${N_SHARDS}×15q, dual ${MULTI_BASE_URLS})"
  cmd_start_arm_shards sig0
  cmd_start_arm_shards sig1
  sleep 1
  cmd_status
}

cmd_start_screen() {
  local arm="${1:?arm sig0|sig1}"
  have_screen || { echo "screen not installed" >&2; exit 1; }
  cmd_start_arm_shards "${arm}"
}

cmd_start_both_screen() {
  echo "[deprecated] use: bash $0 start-4-screen" >&2
  cmd_start_4_screen
}

cmd_stop_quiet() {
  screen -S "signature_gap30_sig0_r${ROLL_OUTS}" -X quit 2>/dev/null || true
  screen -S "signature_gap30_sig1_r${ROLL_OUTS}" -X quit 2>/dev/null || true
  screen -S "signature_gap30_both_r${ROLL_OUTS}" -X quit 2>/dev/null || true
  for arm in sig0 sig1; do
    local tag
    tag="$(arm_tag "${arm}")"
    local i
    for ((i = 0; i < N_SHARDS; i++)); do
      screen -S "clarify_a0_${tag}_r${ROLL_OUTS}_w${i}" -X quit 2>/dev/null || true
    done
  done
}

cmd_merge() {
  local arm="${1:-sig1}"
  configure_arm "${arm}"
  "${SCRIPT}" merge-shards
}

cmd_report_refs() {
  python3 -u "${PLAN_DIR}/report_gap30_references.py"
}

cmd_report() {
  cmd_report_refs
  echo ""
  python3 -u - <<PY
import json
from pathlib import Path

out = Path("${OUT_DIR}")
manifest = json.loads(Path("${MANIFEST}").read_text())
qids = [str(q) for q in manifest["qids"]]
n = len(qids)

def sample_sig(path):
    p = Path(path)
    if not p.exists():
        return "?"
    d = json.loads(p.read_text())
    for q in qids:
        if q not in d:
            continue
        rs = (d[q].get("rollout_stats") or [{}])[0]
        rb = rs.get("result_buckets") or {}
        if rb:
            s = next(iter(rb))
            return "legacy" if s.startswith("1_1_") or "_" in s[:4] else "v2_hex"
    return "?"

def metrics(path):
    p = Path(path)
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    sub = [q for q in qids if q in d]
    recall = sum(1 for q in sub if any(a.get("is_correct") for a in (d[q].get("all_sqls_with_attributes") or [])))
    hit1 = sum(1 for q in sub if (d[q].get("stats") or {}).get("gold_match"))
    return {"n": len(sub), "recall": recall, "hit1": hit1, "sig_fmt": sample_sig(p)}

rows = [
    ("E6 gap30 baseline (legacy sig)", "${BASELINE_JSON}"),
    ("sig0 legacy + gated + all_buckets", str(out / "v4_colbind_v2_dual03_min2sq_abl5_sig0_gated_gap30_r${ROLL_OUTS}.json")),
    ("sig1 v2     + gated + all_buckets", str(out / "v4_colbind_v2_dual03_min2sq_abl5_sig1_gated_gap30_r${ROLL_OUTS}.json")),
]
print("=== Signature gap30 ablation ({} qids) ===".format(n))
print("Fixed: E6 bootstrap | gated R4→R8 | all_buckets | min2+colbind")
print()
for label, path in rows:
    m = metrics(path)
    if m:
        print(f"{label:38} Recall={m['recall']}/{m['n']}  Hit@1={m['hit1']}/{m['n']}  sig_fmt={m['sig_fmt']}")
    else:
        print(f"{label:38} (missing)")

# pairwise if both done
p0 = out / "v4_colbind_v2_dual03_min2sq_abl5_sig0_gated_gap30_r${ROLL_OUTS}.json"
p1 = out / "v4_colbind_v2_dual03_min2sq_abl5_sig1_gated_gap30_r${ROLL_OUTS}.json"
if p0.exists() and p1.exists():
    d0 = json.loads(p0.read_text())
    d1 = json.loads(p1.read_text())
    sub = [q for q in qids if q in d0 and q in d1]
    wins0 = wins1 = 0
    for q in sub:
        h0 = (d0[q].get("stats") or {}).get("gold_match")
        h1 = (d1[q].get("stats") or {}).get("gold_match")
        r0 = any(a.get("is_correct") for a in (d0[q].get("all_sqls_with_attributes") or []))
        r1 = any(a.get("is_correct") for a in (d1[q].get("all_sqls_with_attributes") or []))
        if r0 and not r1: wins0 += 1
        if r1 and not r0: wins1 += 1
    print(f"\nRecall head-to-head (same {len(sub)} q): sig0 wins {wins0}  sig1 wins {wins1}")
PY
}

cmd_status() {
  local target
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo 30)"
  for arm in sig0 sig1; do
    local tag
    tag="$(arm_tag "${arm}")"
    local n=0 i
    for ((i = 0; i < N_SHARDS; i++)); do
      local p="${OUT_DIR}/v4_colbind_v2_dual03_min2sq_${tag}_r${ROLL_OUTS}_w${i}.json"
      local sn=0
      [[ -f "${p}" ]] && sn=$(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)
      n=$((n + sn))
      echo "  ${arm} w${i}: ${sn} qids"
    done
    echo "${arm} (${tag}): ${n}/${target}"
  done
  screen -ls 2>/dev/null | grep -E "signature_gap30|abl5_sig0_gated_gap30|abl5_sig1_gated_gap30" || echo "(no signature screens)"
}

cmd_stop() {
  cmd_stop_quiet
  echo "[stop] signature-gap30 (4 shard screens)"
}

case "${1:-status}" in
  manifest) cmd_manifest ;;
  start) cmd_start "${2:?sig0|sig1}" ;;
  start-4-screen) cmd_start_4_screen ;;
  start-screen) cmd_start_screen "${2:?sig0|sig1}" ;;
  start-both-screen) cmd_start_both_screen ;;
  merge) cmd_merge "${2:-sig1}" ;;
  report-refs) cmd_report_refs ;;
  report) cmd_report ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  *)
    echo "Usage: $0 {manifest|start|start-screen|start-4-screen|start-both-screen|merge|report|report-refs|status|stop} [sig0|sig1]"
    exit 1
    ;;
esac
