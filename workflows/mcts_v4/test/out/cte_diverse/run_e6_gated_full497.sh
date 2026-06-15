#!/usr/bin/env bash
# E6 per-expand + gated R4→R8 + signature v2 + all_buckets on Alpha 497 cohort.
# Independent outputs — safe to run alongside gated_rerun22 workers (shared vLLM ok).
#
#   bash run_e6_gated_full497.sh prepare-manifest
#   bash run_e6_gated_full497.sh migrate-to-4-shard  # 2→4 shard，保留已有 w0/w1 进度
#   bash run_e6_gated_full497.sh start-screen    # RESUME=0 fresh run, 4 shards @ :8000+:8100 each
#   bash run_e6_gated_full497.sh status
#   bash run_e6_gated_full497.sh merge
#   bash run_e6_gated_full497.sh stop             # only this job's screens
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
ALPHA_EVAL="${ROOT_DIR}/results/arcwise_eval_result.json"
MANIFEST="${PLAN_DIR}/qids_e6_gated_full497_manifest.json"
SCRIPT="scripts/run_clarify_a0_30q.sh"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_e6_gated_full497.sh"

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
export MCTS_USE_SIGNATURE_V2="${MCTS_USE_SIGNATURE_V2:-1}"
export MCTS_R4_VOTE_MODE="${MCTS_R4_VOTE_MODE:-all_buckets}"

export ROLL_OUTS="${ROLL_OUTS:-12}"
export RANDOM_SEED=20240605
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

export MODEL_TAG="abl5_e6_gated_full497"
export QIDS_FILE="${MANIFEST}"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_${MODEL_TAG}"
export SHARD_BASENAME="v4_colbind_v2_dual03_min2sq_abl5_e6_gated_full497_rollouts${ROLL_OUTS}"
export JSON_OUT="${OUT_DIR}/${SHARD_BASENAME}.json"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_${MODEL_TAG}_r${ROLL_OUTS}.log"

have_screen() { command -v screen >/dev/null 2>&1; }

cmd_prepare_manifest() {
  python3 -u - <<PY
import json
from pathlib import Path

alpha_p = Path("${ALPHA_EVAL}")
out = Path("${MANIFEST}")
if not alpha_p.exists():
    raise SystemExit(f"missing {alpha_p}")

alpha = json.loads(alpha_p.read_text())
qids = sorted(alpha.get("per_question", alpha).keys(), key=int)
if not qids:
    raise SystemExit("no qids in alpha eval")

manifest = {
    "description": "Alpha arcwise cohort (497) — E6 gated full rerun from scratch",
    "n": len(qids),
    "source": str(alpha_p),
    "qids": [str(q) for q in qids],
}
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out} n={len(qids)}")
PY
}

cmd_prepare_shards() {
  [[ -f "${MANIFEST}" ]] || cmd_prepare_manifest
  "${SCRIPT}" prepare-shards
}

cmd_start() {
  [[ -f "${MANIFEST}" ]] || cmd_prepare_manifest
  echo "[gated-full497] n=$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))") RESUME=${RESUME} shards=${N_SHARDS} dual_vllm=${SHARD_MULTI_VLLM} timeout=${TASK_TIMEOUT}s"
  export SHARD_MULTI_VLLM="${SHARD_MULTI_VLLM}"
  cmd_prepare_shards
  "${SCRIPT}" start-sharded
}

cmd_start_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  local scr="e6_gated_full497_r${ROLL_OUTS}"
  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${scr}[[:space:]]"; then
    echo "[skip] screen ${scr} already running"
    cmd_status
    return 0
  fi
  screen -dmS "${scr}" bash -lc "cd '${ROOT_DIR}' && bash '${SELF}' start 2>&1 | tee -a '${ORCH_LOG}'; echo '[E6 gated full497 done]' \$(date -Iseconds)"
  sleep 1
  echo "[started] screen -S ${scr} (does not stop gated_rerun22 or other jobs)"
  cmd_status
}

cmd_merge_partial_shards() {
  python3 -u - <<PY
import json
from pathlib import Path

out_dir = Path("${OUT_DIR}")
shard_base = "${SHARD_BASENAME}"
merged_path = Path("${JSON_OUT}")

by_qid = {}
for path in sorted(out_dir.glob(f"{shard_base}_w*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    for k, v in data.items():
        if str(k).isdigit():
            by_qid[str(k)] = v
    print(f"[merge-partial] +{len(data)} from {path.name}")

if not by_qid:
    raise SystemExit("no shard json to migrate")

merged = {k: by_qid[k] for k in sorted(by_qid, key=lambda x: int(x))}
merged_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
print(f"[merge-partial] {len(merged)} qids -> {merged_path}")
PY
}

cmd_migrate_to_4_shard() {
  echo "[migrate] stop old workers ..."
  cmd_stop
  sleep 2

  export N_SHARDS=4
  export SHARD_MULTI_VLLM=1

  if compgen -G "${OUT_DIR}/${SHARD_BASENAME}_w"*.json >/dev/null; then
    cmd_merge_partial_shards
  else
    echo "[migrate] no existing shard json — skip merge-partial"
  fi

  echo "[migrate] prepare 4-shard manifests ..."
  cmd_prepare_shards

  if [[ -f "${JSON_OUT}" ]]; then
    echo "[migrate] redistribute -> w0..w3 ..."
    MERGED_JSON="${JSON_OUT}" "${SCRIPT}" migrate-shards
  fi

  python3 -u - <<PY
import json
from pathlib import Path

out_dir = Path("${OUT_DIR}")
shard_base = "${SHARD_BASENAME}"
shard_dir = Path("${QIDS_SHARD_DIR}")
done_total = 0
for i in range(4):
    sq = json.loads((shard_dir / f"shard{i}.json").read_text())["qids"]
    sj = out_dir / f"{shard_base}_w{i}.json"
    nd = len(json.loads(sj.read_text())) if sj.exists() else 0
    done_total += nd
    print(f"  w{i}: {nd}/{len(sq)} done")
print(f"[migrate] total done={done_total}/497 — run: RESUME=1 bash $0 start-screen")
PY
}

cmd_merge() {
  "${SCRIPT}" merge-shards
}

cmd_status() {
  local target="?" n=0
  [[ -f "${MANIFEST}" ]] && target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo "?")"
  local i
  for ((i = 0; i < N_SHARDS; i++)); do
    local p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
    local sn=0
    [[ -f "${p}" ]] && sn=$(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)
    n=$((n + sn))
    echo "  w${i}: ${sn} qids$( [[ -f ${p} ]] && echo ' json' )$( [[ -f ${OUT_DIR}/${SHARD_BASENAME}_w${i}.log ]] && echo ' log' )"
  done
  echo "gated-full497: ${n}/${target} merged=$( [[ -f ${JSON_OUT} ]] && echo yes || echo no )"
  screen -ls 2>/dev/null | grep -E "e6_gated_full497|clarify_a0_${MODEL_TAG}" || true
}

cmd_stop() {
  MODEL_TAG="${MODEL_TAG}" ROLL_OUTS="${ROLL_OUTS}" N_SHARDS="${N_SHARDS}" "${SCRIPT}" stop all || true
  screen -S "e6_gated_full497_r${ROLL_OUTS}" -X quit 2>/dev/null || true
  echo "[stop] gated-full497 only"
}

case "${1:-status}" in
  prepare-manifest) cmd_prepare_manifest ;;
  prepare-shards) cmd_prepare_shards ;;
  start) cmd_start ;;
  start-screen) cmd_start_screen ;;
  migrate-to-4-shard) cmd_migrate_to_4_shard ;;
  merge) cmd_merge ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  *)
    echo "Usage: $0 {prepare-manifest|prepare-shards|migrate-to-4-shard|start|start-screen|merge|status|stop}"
    exit 1
    ;;
esac
