#!/usr/bin/env bash
# full498: sigA 5-path + 6th value-linked (Alpha-SQL LSH).
# Baseline compare: v4_colbind_v2_dual03_abl5_sigA_nomin2_full498_r12.json (~76.91% Hit@1)
#
#   bash run_sig6_full498.sh start-2-screen
#   bash run_sig6_full498.sh scale-4-screen
#   bash run_sig6_full498.sh status
#   bash run_sig6_full498.sh merge
#   bash run_sig6_full498.sh report
#   bash run_sig6_full498.sh stop
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
MANIFEST="${MANIFEST:-${PLAN_DIR}/qids_min2_full498_priority_manifest.json}"
SCRIPT="scripts/run_clarify_a0_30q.sh"
BASELINE_JSON="${BASELINE_JSON:-${OUT_DIR}/v4_colbind_v2_dual03_abl5_sigA_nomin2_full498_r12.json}"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"
# shellcheck source=../../config/patch_bundle_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/patch_bundle_env.sh"

export MCTS_COMBINED_SCHEMA_LINKING=0
export MCTS_VALUE_SCHEMA_LINKING=1
export MCTS_ALPHA_RELEVANT_VALUES_PKL="${MCTS_ALPHA_RELEVANT_VALUES_PKL:-${ROOT_DIR}/Alpha-SQL-2.2.4/data/preprocessed/arcwise/dev/relevant_values_for_all_tasks.pkl}"
export MCTS_ALPHA_PPL_FOR_QID="${MCTS_ALPHA_PPL_FOR_QID:-${ROOT_DIR}/workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json}"

export MCTS_EXEC_TIME_TIEBREAK=0
export MCTS_DEDUP_BEFORE_REVISE=0
export MCTS_REVERSED_SCHEMA_LINKING=1
export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1
export MCTS_BOOTSTRAP_ONCE_PER_QUESTION=0
export MCTS_FK_PK_CLOSURE=0

export MCTS_COLUMN_BINDING_COT="per_subq@0.3+dual"
export MCTS_COLUMN_BINDING_SCOPE="global"
export DECOMPOSE_STRATEGY="S2"
export MCTS_SELECTOR_STRATEGY="R4"
export MCTS_R4_GATE_MARGIN="0.7"
export MCTS_CONFIDENCE_THRESHOLD="0.7"
export MCTS_R4_VOTE_MODE="${MCTS_R4_VOTE_MODE:-all_buckets}"

export MODEL_TAG="${MODEL_TAG:-abl5_sig6_value_full498}"
export ROLL_OUTS="${ROLL_OUTS:-12}"
export RANDOM_SEED="${RANDOM_SEED:-20240616}"
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
export SHARD_BASENAME="v4_colbind_v2_dual03_${MODEL_TAG}_r${ROLL_OUTS}"
export JSON_OUT="${OUT_DIR}/${SHARD_BASENAME}.json"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_${MODEL_TAG}_r${ROLL_OUTS}.log"
export PPL_FILE="${PPL_FILE:-workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json}"
export GOLD_FILE="${GOLD_FILE:-workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json}"

have_screen() { command -v screen >/dev/null 2>&1; }

_screen_running() {
  screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${1}[[:space:]]"
}

_kill_screens_matching() {
  local pattern="$1"
  while IFS= read -r sess; do
    [[ -z "${sess}" ]] && continue
    screen -S "${sess}" -X quit 2>/dev/null || true
    echo "[stop] ${sess}"
  done < <(screen -ls 2>/dev/null | grep -oE "[0-9]+\\.${pattern}[^[:space:]]*" || true)
}

cmd_prepare_manifest() {
  [[ -f "${MANIFEST}" ]] || bash "${ROOT_DIR}/workflows/mcts_v4/test/out/cte_diverse/run_decompose_min2_full498.sh" prepare-manifest-priority
}

_launch_shard_worker() {
  local idx="$1"
  local qids_file="${QIDS_SHARD_DIR}/shard${idx}.json"
  [[ -f "${qids_file}" ]] || { echo "missing ${qids_file}" >&2; return 1; }

  local screen_name="clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${idx}"
  local json_out="${OUT_DIR}/${SHARD_BASENAME}_w${idx}.json"
  local sql_out="${json_out%.json}.txt"
  local log="${json_out%.json}.log"
  local spill_dir="${OUT_DIR}/.task_spill/${SHARD_BASENAME}_w${idx}"

  if _screen_running "${screen_name}"; then
    echo "[skip] ${screen_name} already running"
    return 0
  fi

  echo "[shard] sig6 w${idx} arcwise PPL/GOLD value=1 multi=${MULTI_BASE_URLS} timeout=${TASK_TIMEOUT}s"
  screen -dmS "${screen_name}" bash -lc "$(cat <<EOF
set -euo pipefail
cd '${ROOT_DIR}'
export PYTHONPATH='${ROOT_DIR}:${ROOT_DIR}/Alpha-SQL-2.2.4'
source workflows/mcts_v4/config/bprime_env.sh
source workflows/mcts_v4/config/patch_bundle_env.sh
export MCTS_COMBINED_SCHEMA_LINKING=0
export MCTS_VALUE_SCHEMA_LINKING=1
export MCTS_ALPHA_RELEVANT_VALUES_PKL='${MCTS_ALPHA_RELEVANT_VALUES_PKL}'
export MCTS_ALPHA_PPL_FOR_QID='${MCTS_ALPHA_PPL_FOR_QID}'
export MCTS_EXEC_TIME_TIEBREAK=0
export MCTS_DEDUP_BEFORE_REVISE=0
export MCTS_REVERSED_SCHEMA_LINKING=1
export MCTS_REVERSED_BOOTSTRAP_DIRECT_SQL=1
export MCTS_BOOTSTRAP_ONCE_PER_QUESTION=0
export MCTS_FK_PK_CLOSURE=0
export MCTS_COLUMN_BINDING_COT='per_subq@0.3+dual'
export MCTS_COLUMN_BINDING_SCOPE='global'
export DECOMPOSE_STRATEGY='S2'
export MCTS_SELECTOR_STRATEGY='R4'
export MCTS_R4_GATE_MARGIN='0.7'
export MCTS_CONFIDENCE_THRESHOLD='0.7'
export MCTS_R4_VOTE_MODE='${MCTS_R4_VOTE_MODE}'
export OUT_DIR='${OUT_DIR}'
export SHARD_BASENAME='${SHARD_BASENAME}'
export MODEL_TAG='${MODEL_TAG}'
export ROLL_OUTS='${ROLL_OUTS}'
export RANDOM_SEED='${RANDOM_SEED}'
export TASK_TIMEOUT='${TASK_TIMEOUT}'
export RESUME='${RESUME}'
export SKIP_VLLM_WAIT=1
export POST_ANALYSIS=0
export RUN_INLINE=1
export SHARD_MODE=1
export SHARD_MULTI_VLLM='${SHARD_MULTI_VLLM}'
export MULTI_BASE_URLS='${MULTI_BASE_URLS}'
export JSON_OUT='${json_out}'
export SQL_OUT='${sql_out}'
export LOG='${log}'
export QIDS_FILE='${qids_file}'
export PPL_FILE='${PPL_FILE}'
export GOLD_FILE='${GOLD_FILE}'
export MCTS_TASK_SPILL=1
export MCTS_TASK_SPILL_DIR='${spill_dir}'
'${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' start-mcts
EOF
)"
  sleep 0.5
}

cmd_start_2_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  cmd_prepare_manifest
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

cmd_stop_all_screens() {
  _kill_screens_matching "clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}"
}

cmd_scale_to_n_screen() {
  local new_shards="${1:-4}"
  have_screen || { echo "screen not installed" >&2; exit 1; }
  echo "[scale] ${MODEL_TAG}: → ${new_shards} screens"
  cmd_prepare_manifest
  cmd_stop_all_screens
  sleep 2
  python3 "${ROOT_DIR}/workflows/mcts_v4/test/out/cte_diverse/scale_full498_shards.py" \
    --manifest "${MANIFEST}" \
    --shard-dir "${QIDS_SHARD_DIR}" \
    --out-dir "${OUT_DIR}" \
    --shard-basename "${SHARD_BASENAME}" \
    --new-shards "${new_shards}" \
    --roll-outs "${ROLL_OUTS}"
  export N_SHARDS="${new_shards}"
  export RESUME=1
  local i
  for ((i = 0; i < N_SHARDS; i++)); do
    _launch_shard_worker "${i}"
  done
  sleep 1
  cmd_status
}

cmd_scale_to_4_screen() { cmd_scale_to_n_screen 4; }
cmd_scale_to_8_screen() { cmd_scale_to_n_screen 8; }

_effective_n_shards() {
  local mf="${QIDS_SHARD_DIR}/manifest.json"
  if [[ -f "${mf}" ]]; then
    python3 -c "import json; print(json.load(open('${mf}'))['n_shards'])" 2>/dev/null || echo "${N_SHARDS}"
  else
    echo "${N_SHARDS}"
  fi
}

cmd_merge() {
  local n_shards
  n_shards="$(_effective_n_shards)"
  python3 -u - <<PY
import json
from pathlib import Path

out = Path("${OUT_DIR}")
base = "${SHARD_BASENAME}"
n = int("${n_shards}")
merged_path = Path("${JSON_OUT}")
by_qid = {}
ck = out / f"{base}_done_checkpoint.json"
if ck.is_file():
    by_qid.update(json.loads(ck.read_text(encoding="utf-8")))
    print(f"[merge] checkpoint {len(by_qid)} qids")
for i in range(n):
    p = out / f"{base}_w{i}.json"
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        by_qid.update(data)
        print(f"[merge] w{i}: +{len(data)} -> total {len(by_qid)}")
merged = {k: by_qid[k] for k in sorted(by_qid, key=lambda x: int(x))}
merged_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"[merge] {len(merged)} qids -> {merged_path}")
PY
}

cmd_status() {
  local target n_shards
  n_shards="$(_effective_n_shards)"
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo 498)"
  python3 -u - <<PY
import json
from pathlib import Path

out = Path("${OUT_DIR}")
base = "${SHARD_BASENAME}"
n_shards = int("${n_shards}")
target = int("${target}")
done = {}
ck = out / f"{base}_done_checkpoint.json"
if ck.is_file():
    done.update(json.loads(ck.read_text()))
for i in range(n_shards):
    p = out / f"{base}_w{i}.json"
    sn = 0
    rem = 0
    sp = Path("${QIDS_SHARD_DIR}") / f"shard{i}.json"
    if sp.is_file():
        rem = len(json.loads(sp.read_text()).get("qids") or [])
    if p.is_file():
        data = json.loads(p.read_text())
        sn = len(data)
        done.update(data)
    print(f"  sig6 w{i}: {sn} saved, {rem} assigned")
print(f"sig6 full498 (${MODEL_TAG}): {len(done)}/{target}  [{n_shards} screens] timeout=${TASK_TIMEOUT}s")
PY
  screen -ls 2>/dev/null | grep -E "${MODEL_TAG}" || echo "(no sig6 full498 screens)"
}

cmd_stop() {
  cmd_stop_all_screens
  echo "[stop] sig6 full498 (${MODEL_TAG})"
}

cmd_report() {
  local n_shards
  n_shards="$(_effective_n_shards)"
  python3 -u - <<PY
import json, re
from pathlib import Path

out = Path("${OUT_DIR}")
base = "${SHARD_BASENAME}"
manifest = json.loads(Path("${MANIFEST}").read_text())
qids = [str(q) for q in manifest["qids"]]
merged = {}
ck = out / f"{base}_done_checkpoint.json"
if ck.is_file():
    merged.update(json.loads(ck.read_text()))
for i in range(${n_shards}):
    p = out / f"{base}_w{i}.json"
    if p.exists():
        merged.update(json.loads(p.read_text()))
rec = sum(1 for q in qids if q in merged and any(a.get("is_correct") for a in merged[q].get("all_sqls_with_attributes") or []))
hit = sum(1 for q in qids if q in merged and (merged[q].get("stats") or {}).get("gold_match"))
done = len([q for q in qids if q in merged])

base_p = Path("${BASELINE_JSON}")
base_hit = base_rec = None
if base_p.is_file():
    base = json.loads(base_p.read_text())
    base_rec = sum(1 for q in qids if q in base and any(a.get("is_correct") for a in base[q].get("all_sqls_with_attributes") or []))
    base_hit = sum(1 for q in qids if q in base and (base[q].get("stats") or {}).get("gold_match"))

print(f"sig6 full498: done={done}/498 Recall={rec}/498 Hit@1={hit}/498")
if base_hit is not None:
    print(f"baseline sigA: Recall={base_rec}/498 Hit@1={base_hit}/498 (delta Hit@1 {hit-base_hit:+d})")
print(f"merged -> ${JSON_OUT}")
PY
}

cmd_rerun_failed_4_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  local plan="${PLAN_DIR}/qids_sig6_unfair288_arcwise_rerun.json"
  local failed_manifest="${PLAN_DIR}/qids_sig6_failed210_rerun.json"
  python3 -u - <<PY
import json
from pathlib import Path

out = Path("${OUT_DIR}")
base = "${SHARD_BASENAME}"
unfair = {str(q) for q in json.loads(Path("${plan}").read_text())["qids"]}
ok, fail = {}, []
for i in range(4):
    p = out / f"{base}_w{i}.json"
    if not p.is_file():
        continue
    for q, v in json.loads(p.read_text()).items():
        qs = str(q)
        if qs not in unfair:
            continue
        if isinstance(v, dict) and v.get("error"):
            fail.append(qs)
        elif isinstance(v, dict) and (v.get("sql") or v.get("all_sqls_with_attributes")):
            ok[qs] = v
fail = sorted(set(fail), key=int)
Path("${failed_manifest}").write_text(
    json.dumps({"description": "sig6 unfair288 shard failures (import/runtime)", "n": len(fail), "qids": fail}, indent=2) + "\n",
    encoding="utf-8",
)
# keep successful unfair reruns in shard files only
for i in range(4):
    p = out / f"{base}_w{i}.json"
    if not p.is_file():
        continue
    data = json.loads(p.read_text())
    kept = {q: v for q, v in data.items() if str(q) in ok}
    if kept:
        p.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif p.is_file():
        p.unlink()
print(f"[rerun-failed] ok={len(ok)} fail={len(fail)} -> {Path('${failed_manifest}').name}")
PY
  export MANIFEST="${failed_manifest}"
  export RESUME=0
  export N_SHARDS=4
  cmd_stop_all_screens
  sleep 2
  python3 "${ROOT_DIR}/workflows/mcts_v4/test/out/cte_diverse/scale_full498_shards.py" \
    --manifest "${failed_manifest}" \
    --shard-dir "${QIDS_SHARD_DIR}" \
    --out-dir "${OUT_DIR}" \
    --shard-basename "${SHARD_BASENAME}" \
    --new-shards 4 \
    --roll-outs "${ROLL_OUTS}" \
    --skip-errors
  local i
  for ((i = 0; i < 4; i++)); do
    _launch_shard_worker "${i}"
  done
  sleep 1
  cmd_status
}

case "${1:-status}" in
  start-2-screen) export N_SHARDS=2; cmd_start_2_screen ;;
  start-4-screen) export N_SHARDS=4; cmd_start_2_screen ;;
  scale-4-screen) cmd_scale_to_4_screen ;;
  scale-8-screen) cmd_scale_to_8_screen ;;
  rerun-failed-4-screen) cmd_rerun_failed_4_screen ;;
  merge) cmd_merge ;;
  status) cmd_status ;;
  report) cmd_report ;;
  stop) cmd_stop ;;
  *)
    echo "Usage: $0 {start-2-screen|start-4-screen|scale-4-screen|scale-8-screen|rerun-failed-4-screen|merge|status|report|stop}"
    exit 1
    ;;
esac
