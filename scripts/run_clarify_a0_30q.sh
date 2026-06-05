#!/usr/bin/env bash
# Clarify A0：30 题 instrumentation dump（standard mode）
# 复用已启动的 vLLM（默认 4× Qwen3-Coder-30B @ :8000-8300），screen 断线续跑。
#
# 用法（仓库根目录）:
#   ./scripts/run_clarify_a0_30q.sh status
#   ./scripts/run_clarify_a0_30q.sh start          # 等 vLLM → screen 跑 MCTS → 可选后处理
#   ./scripts/run_clarify_a0_30q.sh start-mcts     # 仅 MCTS（screen）
#   ./scripts/run_clarify_a0_30q.sh start-both     # rollout8 → rollout20 串行（QwenCoder）
#   ./scripts/run_clarify_a0_30q.sh start-sharded # 4 shard 并行，每 shard 独占 1 个 vLLM
#   ./scripts/run_clarify_a0_30q.sh start-sharded-both  # rollout8/20 各 4 shard + merge
#   ./scripts/run_clarify_a0_30q.sh prepare-shards
#   ./scripts/run_clarify_a0_30q.sh migrate-shards  # 合并 json → 各 shard json（续跑用）
#   ./scripts/run_clarify_a0_30q.sh merge-shards
#   ./scripts/run_clarify_a0_30q.sh post           # MCTS 完成后跑 acc / A2 分析
#   ./scripts/run_clarify_a0_30q.sh attach
#   ./scripts/run_clarify_a0_30q.sh stop [mcts|all]
#   SKIP_VLLM_WAIT=1 ./scripts/run_clarify_a0_30q.sh start
#   POST_ANALYSIS=0 ./scripts/run_clarify_a0_30q.sh start   # 不自动跑 clarify 脚本
#
# QwenCoder 双版本（8 卡 4 vLLM）:
#   MULTI_BASE_URLS="http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1,http://127.0.0.1:8200/v1,http://127.0.0.1:8300/v1" \
#   MODEL_TAG=coder RESUME=0 ROLL_OUTS=8 ./scripts/run_clarify_a0_30q.sh start-mcts
#   MODEL_TAG=coder RESUME=0 ROLL_OUTS=20 ./scripts/run_clarify_a0_30q.sh start-mcts
#
# 30 题 manifest（前 30 key，勿改）:
#   workflows/mcts_v4/test/out/clarify_a0_a2_qwen32/qids_30_manifest.json

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

: "${VLLM_PORT:=8000}"
: "${VLLM_HOST:=127.0.0.1}"
: "${VLLM_WAIT_SEC:=600}"
: "${SKIP_VLLM_WAIT:=0}"
: "${CONDA_BASE:=/hpc2hdd/home/sshen190/miniconda3}"
: "${CONDA_ENV:=base}"
: "${RESUME:=1}"
: "${POST_ANALYSIS:=1}"
: "${MAX_WORKERS:=1}"
: "${PARALLEL_WORKERS:=5}"
: "${TASK_TIMEOUT:=1800}"
: "${ROLL_OUTS:=8}"
: "${RANDOM_SEED:=20240601}"
: "${MODEL_TAG:=coder}"
: "${RUN_INLINE:=0}"
: "${N_SHARDS:=4}"
: "${BASE_PORT:=8000}"
: "${PORT_STRIDE:=100}"
: "${MULTI_BASE_URLS:=http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1,http://127.0.0.1:8200/v1,http://127.0.0.1:8300/v1}"
: "${SHARD_MODE:=0}"
: "${SHARD_BASENAME:=}"

OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/clarify_a0_a2_${MODEL_TAG}}"
QIDS_SHARD_DIR="${QIDS_SHARD_DIR:-${OUT_DIR}/qids_shards_r${ROLL_OUTS}}"
PPL_FILE="${PPL_FILE:-workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json}"
GOLD_FILE="${GOLD_FILE:-workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json}"
: "${BASELINE_JSON:=workflows/mcts_v4/test/out/clarify_a0_a2_qwen32/v4_a0_30q_rollouts8.json}"
: "${BASELINE_498_JSON:=workflows/mcts_v4/test/out/v4_arcwise_full_result_rollouts_20.json}"
QIDS_FILE="${QIDS_FILE:-workflows/mcts_v4/test/out/clarify_a0_a2_qwen32/qids_30_manifest.json}"
JSON_OUT="${JSON_OUT:-${OUT_DIR}/v4_a0_30q_${MODEL_TAG}_rollouts${ROLL_OUTS}.json}"
SQL_OUT="${SQL_OUT:-${OUT_DIR}/v4_a0_30q_${MODEL_TAG}_rollouts${ROLL_OUTS}.txt}"
SCREEN="${SCREEN:-clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}}"
LOG="${LOG:-${OUT_DIR}/v4_a0_30q_${MODEL_TAG}_rollouts${ROLL_OUTS}.log}"
ORCH_LOG="${ORCH_LOG:-${OUT_DIR}/run_clarify_a0_30q_${MODEL_TAG}_r${ROLL_OUTS}.log}"
VLLM_URL="http://${VLLM_HOST}:${VLLM_PORT}/v1"

MCTS_SCRIPT="workflows/mcts_v4/test/test_mcts.py"
TASKS_PKL="${TASKS_PKL:-Alpha-SQL-2.2.4/data/preprocessed/arcwise/dev/tasks.pkl}"

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

_vllm_urls() {
  if [[ -n "${MULTI_BASE_URLS}" ]]; then
    echo "${MULTI_BASE_URLS}" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | sed '/^$/d'
  else
    echo "${VLLM_URL}"
  fi
}

_wait_one_vllm_url() {
  local url="$1"
  local deadline=$((SECONDS + VLLM_WAIT_SEC))
  echo "[wait] ${url}/models (max ${VLLM_WAIT_SEC}s) ..."
  while (( SECONDS < deadline )); do
    if curl -sf "${url}/models" >/dev/null 2>&1; then
      echo "[ok] vLLM ready: ${url}"
      return 0
    fi
    sleep 5
  done
  die "vLLM not ready after ${VLLM_WAIT_SEC}s — ${url}"
}

_wait_vllm() {
  if [[ "${SKIP_VLLM_WAIT}" == "1" ]]; then
    echo "[skip] SKIP_VLLM_WAIT=1"
    return 0
  fi
  local url
  while IFS= read -r url; do
    [[ -n "${url}" ]] || continue
    _wait_one_vllm_url "${url}"
  done < <(_vllm_urls)
}

_vllm_model_id() {
  if [[ -n "${VLLM_MODEL:-}" ]]; then
    echo "${VLLM_MODEL}"
    return 0
  fi
  python3 - <<PY 2>/dev/null || true
import json, urllib.request
url = "${VLLM_URL}/models"
with urllib.request.urlopen(url, timeout=15) as r:
    data = json.load(r)
ids = [m["id"] for m in data.get("data", []) if m.get("id")]
if ids:
    print(ids[0])
PY
}

_check_inputs() {
  [[ -f "${QIDS_FILE}" ]] || die "missing QIDS_FILE=${QIDS_FILE}"
  [[ -f "${GOLD_FILE}" ]] || die "missing GOLD_FILE=${GOLD_FILE}"
  if [[ ! -f "${PPL_FILE}" ]]; then
    die "missing PPL_FILE=${PPL_FILE} — run: $0 prepare-ppl"
  fi
  mkdir -p "${OUT_DIR}"
}

_mcts_py_invocation() {
  local -a cmd=(
    python -u "${MCTS_SCRIPT}"
    --ppl_file "${PPL_FILE}"
    --gold_file "${GOLD_FILE}"
    --qids_file "${QIDS_FILE}"
    --json_out "${JSON_OUT}"
    --sql_out "${SQL_OUT}"
    --rollouts_per_iteration "${ROLL_OUTS}"
    --random_seed "${RANDOM_SEED}"
    --max_workers "${MAX_WORKERS}"
    --parallel_workers "${PARALLEL_WORKERS}"
    --task_timeout "${TASK_TIMEOUT}"
  )
  if [[ "${RESUME}" == "1" ]]; then
    cmd+=(--skip_processed)
  fi
  if [[ -n "${MULTI_BASE_URLS}" && "${SHARD_MODE}" != "1" ]]; then
    cmd+=(--multi_base_urls "${MULTI_BASE_URLS}")
  fi
  local q=()
  local a
  for a in "${cmd[@]}"; do
    q+=("$(printf '%q' "$a")")
  done
  (IFS=' '; echo "${q[*]}")
}

cmd_prepare_ppl() {
  [[ -f "${TASKS_PKL}" ]] || die "missing TASKS_PKL=${TASKS_PKL}"
  echo "[prepare-ppl] ${TASKS_PKL} -> ${PPL_FILE}"
  export PYTHONPATH="${ROOT_DIR}:${ROOT_DIR}/Alpha-SQL-2.2.4:${PYTHONPATH:-}"
  python -u workflows/mcts_v1/test/alpha_sql_tasks_to_ppl.py \
    --tasks_pkl "${TASKS_PKL}" \
    --output_ppl "${PPL_FILE}"
}

cmd_post() {
  _check_inputs
  [[ -f "${JSON_OUT}" ]] || die "missing ${JSON_OUT} — run start-mcts first"
  export PYTHONPATH="${ROOT_DIR}:${ROOT_DIR}/Alpha-SQL-2.2.4:${PYTHONPATH:-}"
  export MCTS_USE_SIGNATURE_V2="${MCTS_USE_SIGNATURE_V2:-0}"
  local tee_mode="tee"
  [[ -f "${ORCH_LOG}" ]] && tee_mode="tee -a"
  {
    echo "======== clarify post $(date -Iseconds) ========"
    python -u workflows/mcts_v4/scripts/clarify_sanity_acc_a0.py \
      --new "${JSON_OUT}" \
      --baseline "${BASELINE_JSON}" \
      --gold_file "${GOLD_FILE}" \
      --acc_new "${OUT_DIR}/acc_a0_cmp_r${ROLL_OUTS}.json" \
      --acc_baseline "${OUT_DIR}/acc_baseline_cmp_r${ROLL_OUTS}.json" \
      --output "${OUT_DIR}/sanity_acc_a0_r${ROLL_OUTS}.md" || true
    python -u workflows/mcts_v4/scripts/clarify_sanity_30v498.py \
      --input_30 "${JSON_OUT}" \
      --input_498 "${BASELINE_498_JSON}" \
      --output "${OUT_DIR}/sanity_30v498_r${ROLL_OUTS}.md"
    python -u workflows/mcts_v4/scripts/clarify_expansion_bucket_stats.py \
      --input "${JSON_OUT}" \
      --output "${OUT_DIR}/expansion_bucket_stats_a0_r${ROLL_OUTS}.md"
    python -u workflows/mcts_v4/scripts/clarify_hash_paired_diff.py \
      --input "${JSON_OUT}" \
      --output "${OUT_DIR}/hash_paired_diff_a0_r${ROLL_OUTS}.md"
    python -u workflows/mcts_v4/scripts/clarify_h1_judge.py \
      --input "${JSON_OUT}" \
      --ppl_file "${PPL_FILE}" \
      --output "${OUT_DIR}/h1_judge_a0_r${ROLL_OUTS}.md" \
      --cache_json "${OUT_DIR}/h1_judge_a0_r${ROLL_OUTS}.judge_cache.json" \
      ${H1_DRY_RUN:+--dry_run} || true
    echo "======== clarify post done $(date -Iseconds) ========"
  } 2>&1 | ${tee_mode} "${ORCH_LOG}"
}

_run_mcts_body() {
  local model_id
  model_id="$(_vllm_model_id)"
  [[ -n "${model_id}" ]] || model_id="${VLLM_MODEL:-}"
  local py_inv resume_banner="" tee_first="tee"
  py_inv=$(_mcts_py_invocation)
  if [[ "${RESUME}" == "1" && -f "${JSON_OUT}" ]]; then
    tee_first="tee -a"
    resume_banner="resume: appending to existing ${JSON_OUT}"
  fi
  if [[ -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]]; then
    # shellcheck disable=SC1091
    source "${CONDA_BASE}/etc/profile.d/conda.sh"
    conda activate "${CONDA_ENV}" || true
  fi
  cd "${ROOT_DIR}"
  export PYTHONPATH="${ROOT_DIR}:${ROOT_DIR}/Alpha-SQL-2.2.4"
  export PYTHONUNBUFFERED=1
  export MCTS_USE_SIGNATURE_V2="${MCTS_USE_SIGNATURE_V2:-0}"
  export MCTS_SELECTOR_STRATEGY="${MCTS_SELECTOR_STRATEGY:-}"
  export MCTS_REWARD_CALIBRATED="${MCTS_REWARD_CALIBRATED:-0}"
  export VLLM_API_URL="${VLLM_URL}"
  [[ -n "${model_id}" ]] && export VLLM_MODEL="${model_id}"
  [[ -n "${resume_banner}" ]] && printf '%s\n' "${resume_banner}" | ${tee_first} "${LOG}"
  echo "======== clarify A0 MCTS $(date -Iseconds) ========" | ${tee_first} "${LOG}"
  echo "  qids:      ${QIDS_FILE}" | tee -a "${LOG}"
  echo "  ppl:       ${PPL_FILE}" | tee -a "${LOG}"
  echo "  json_out:  ${JSON_OUT}" | tee -a "${LOG}"
  echo "  rollouts:  ${ROLL_OUTS}  seed: ${RANDOM_SEED}  model_tag: ${MODEL_TAG}" | tee -a "${LOG}"
  echo "  vllm:      ${MULTI_BASE_URLS:-${VLLM_URL}}  model: ${VLLM_MODEL:-auto}" | tee -a "${LOG}"
  # shellcheck disable=SC2086
  eval "${py_inv}" 2>&1 | tee -a "${LOG}"
  echo "======== MCTS done $(date -Iseconds) exit=$? ========" | tee -a "${LOG}"
}

_shard_meta() {
  local i="$1"
  local port=$((BASE_PORT + i * PORT_STRIDE))
  echo "${port},clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${i},${QIDS_SHARD_DIR}/shard${i}.json"
}

_shard_basename() {
  if [[ -n "${SHARD_BASENAME}" ]]; then
    echo "${SHARD_BASENAME}"
  else
    echo "v4_a0_30q_${MODEL_TAG}_rollouts${ROLL_OUTS}"
  fi
}

_shard_json_out() {
  echo "${OUT_DIR}/$(_shard_basename)_w${1}.json"
}

_shard_sql_out() {
  echo "${OUT_DIR}/$(_shard_basename)_w${1}.txt"
}

_shard_log() {
  echo "${OUT_DIR}/$(_shard_basename)_w${1}.log"
}

cmd_prepare_shards() {
  [[ -f "${QIDS_FILE}" ]] || die "missing QIDS_FILE=${QIDS_FILE}"
  mkdir -p "${QIDS_SHARD_DIR}"
  python3 -u - <<PY
import json
from pathlib import Path

qids_path = Path("${QIDS_FILE}")
out_dir = Path("${QIDS_SHARD_DIR}")
n_shards = int("${N_SHARDS}")
roll_outs = "${ROLL_OUTS}"

manifest = json.loads(qids_path.read_text(encoding="utf-8"))
qids = manifest.get("qids") or []
n = len(qids)
size = (n + n_shards - 1) // n_shards

for i in range(n_shards):
    chunk = qids[i * size : (i + 1) * size]
    out = {
        "description": f"clarify A0 rollouts={roll_outs} shard {i}/{n_shards} ({len(chunk)} qids)",
        "shard": i,
        "n_shards": n_shards,
        "rollouts": int(roll_outs),
        "qids": chunk,
    }
    path = out_dir / f"shard{i}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[prepare-shards] shard {i}: {len(chunk)} qids -> {path}")

(out_dir / "manifest.json").write_text(
    json.dumps({"n_total": n, "n_shards": n_shards, "qids": qids}, indent=2),
    encoding="utf-8",
)
print(f"[prepare-shards] {n} qids -> {n_shards} shards under {out_dir}")
PY
}

cmd_migrate_shards() {
  local merged="${MERGED_JSON:-${JSON_OUT}}"
  [[ -f "${merged}" ]] || die "missing merged json ${merged}"
  [[ -d "${QIDS_SHARD_DIR}" ]] || cmd_prepare_shards
  python3 -u - <<PY
import json
from pathlib import Path

merged_path = Path("${merged}")
shard_dir = Path("${QIDS_SHARD_DIR}")
out_dir = Path("${OUT_DIR}")
model_tag = "${MODEL_TAG}"
roll_outs = "${ROLL_OUTS}"
n_shards = int("${N_SHARDS}")

merged = json.loads(merged_path.read_text(encoding="utf-8"))
for i in range(n_shards):
    shard_qids_path = shard_dir / f"shard{i}.json"
    shard_qids = set(json.loads(shard_qids_path.read_text(encoding="utf-8")).get("qids", []))
    shard_base = "${SHARD_BASENAME}" or f"v4_a0_30q_{model_tag}_rollouts{roll_outs}"
    shard_out = out_dir / f"{shard_base}_w{i}.json"
    subset = {k: v for k, v in merged.items() if str(k) in shard_qids}
    if subset:
        shard_out.write_text(json.dumps(subset, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[migrate] w{i}: {len(subset)}/{len(shard_qids)} -> {shard_out}")
    else:
        print(f"[migrate] w{i}: 0/{len(shard_qids)} (no existing results)")
PY
}

cmd_merge_shards() {
  local merged="${MERGED_JSON:-${JSON_OUT}}"
  python3 -u - <<PY
import json
from pathlib import Path

out_dir = Path("${OUT_DIR}")
shard_base = "${SHARD_BASENAME}" or f"v4_a0_30q_${MODEL_TAG}_rollouts${ROLL_OUTS}"
n_shards = int("${N_SHARDS}")
merged_path = Path("${merged}")

by_qid = {}
for i in range(n_shards):
    path = out_dir / f"{shard_base}_w{i}.json"
    if not path.is_file():
        print(f"[merge] skip missing {path}")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    for k, v in data.items():
        by_qid[str(k)] = v

merged = {k: by_qid[k] for k in sorted(by_qid, key=lambda x: int(x))}
merged_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"[merge] {len(merged)} qids -> {merged_path}")
PY
}

cmd_start_sharded() {
  have_screen || die "screen not installed"
  _check_inputs
  [[ -d "${QIDS_SHARD_DIR}" ]] || cmd_prepare_shards
  _wait_vllm
  local i port screen_name qids_file json_out sql_out log
  for ((i = 0; i < N_SHARDS; i++)); do
    IFS=',' read -r port screen_name qids_file <<< "$(_shard_meta "$i")"
    json_out="$(_shard_json_out "$i")"
    sql_out="$(_shard_sql_out "$i")"
    log="$(_shard_log "$i")"
    if screen_running "${screen_name}"; then
      echo "[skip] ${screen_name} already running"
      continue
    fi
    echo "[shard] w${i} port=${port} qids=${qids_file}"
    local inner
    inner=$(cat <<EOF
set -euo pipefail
export RUN_INLINE=1
export SHARD_MODE=1
export MULTI_BASE_URLS=
export MCTS_USE_SIGNATURE_V2='${MCTS_USE_SIGNATURE_V2:-0}'
export MCTS_SELECTOR_STRATEGY='${MCTS_SELECTOR_STRATEGY:-}'
export MCTS_REWARD_CALIBRATED='${MCTS_REWARD_CALIBRATED:-0}'
export ROOT_DIR='${ROOT_DIR}'
export CONDA_BASE='${CONDA_BASE}'
export CONDA_ENV='${CONDA_ENV}'
export VLLM_HOST='127.0.0.1'
export VLLM_PORT='${port}'
export MODEL_TAG='${MODEL_TAG}'
export ROLL_OUTS='${ROLL_OUTS}'
export RESUME='${RESUME}'
export RANDOM_SEED='${RANDOM_SEED}'
export MAX_WORKERS='${MAX_WORKERS}'
export PARALLEL_WORKERS='${PARALLEL_WORKERS}'
export TASK_TIMEOUT='${TASK_TIMEOUT}'
export JSON_OUT='${json_out}'
export SQL_OUT='${sql_out}'
export LOG='${log}'
export PPL_FILE='${PPL_FILE}'
export GOLD_FILE='${GOLD_FILE}'
export QIDS_FILE='${qids_file}'
export SKIP_VLLM_WAIT=1
'${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' start-mcts
EOF
)
    screen_cmd "${screen_name}" "$inner"
    sleep 1
  done
  echo "[started] ${N_SHARDS} shard workers (rollouts=${ROLL_OUTS}, 1 vLLM each)"
  echo "  merge when done: $0 merge-shards ROLL_OUTS=${ROLL_OUTS}"
}

cmd_start_sharded_both() {
  have_screen || die "screen not installed"
  local both_screen="clarify_a0_${MODEL_TAG}_sharded_both"
  if screen_running "${both_screen}"; then
    echo "[skip] screen ${both_screen} already running"
    return 0
  fi
  _wait_vllm
  local both_log="${OUT_DIR}/run_clarify_a0_${MODEL_TAG}_sharded_both.log"
  local inner
  inner=$(cat <<EOF
set -euo pipefail
if [[ -f '${CONDA_BASE}/etc/profile.d/conda.sh' ]]; then
  source '${CONDA_BASE}/etc/profile.d/conda.sh'
  conda activate '${CONDA_ENV}' || true
fi
cd '${ROOT_DIR}'
export MODEL_TAG='${MODEL_TAG}'
export RESUME='${RESUME}'
export SKIP_VLLM_WAIT=1
export MAX_WORKERS='${MAX_WORKERS}'
export PARALLEL_WORKERS='${PARALLEL_WORKERS}'
export TASK_TIMEOUT='${TASK_TIMEOUT}'
export N_SHARDS='${N_SHARDS}'
export BASE_PORT='${BASE_PORT}'
export PORT_STRIDE='${PORT_STRIDE}'
for ro in 8 20; do
  echo "======== sharded rollout=\${ro} \$(date -Iseconds) ========" | tee -a '${both_log}'
  export ROLL_OUTS="\${ro}"
  export QIDS_SHARD_DIR='${OUT_DIR}/qids_shards_r'\${ro}
  export JSON_OUT='${OUT_DIR}/v4_a0_30q_${MODEL_TAG}_rollouts'\${ro}'.json'
  export MERGED_JSON="\${JSON_OUT}"
  '${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' prepare-shards
  if [[ -f "\${JSON_OUT}" ]]; then
    '${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' migrate-shards
  fi
  '${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' start-sharded
  while true; do
    done=0
    total=0
    for ((i=0; i<${N_SHARDS}; i++)); do
      sj='${OUT_DIR}/v4_a0_30q_${MODEL_TAG}_rollouts'\${ro}'_w'\${i}'.json'
      sq='${OUT_DIR}/qids_shards_r'\${ro}'/shard'\${i}'.json'
      if [[ -f "\$sj" && -f "\$sq" ]]; then
        n=\$(python3 -c "import json;d=json.load(open('\$sj'));q=json.load(open('\$sq'))['qids'];print(len(d),len(q))")
        read nd nq <<< "\$n"
        done=\$((done + nd))
        total=\$((total + nq))
      else
        nq=\$(python3 -c "import json;print(len(json.load(open('\$sq'))['qids']))" 2>/dev/null || echo 0)
        total=\$((total + nq))
      fi
    done
    echo "[wait] rollout=\${ro} \${done}/\${total} \$(date -Iseconds)" | tee -a '${both_log}'
    [[ "\${done}" -ge "\${total}" && "\${total}" -gt 0 ]] && break
    sleep 60
  done
  export MERGED_JSON="\${JSON_OUT}"
  '${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' merge-shards
  for ((i=0; i<${N_SHARDS}; i++)); do
    screen -S "clarify_a0_${MODEL_TAG}_r\${ro}_w\${i}" -X quit 2>/dev/null || true
  done
done
echo "======== sharded both done \$(date -Iseconds) ========" | tee -a '${both_log}'
EOF
)
  screen_cmd "${both_screen}" "$inner"
  sleep 2
  echo "[started] screen -S ${both_screen}  (sharded rollouts 8 → 20)"
  echo "  log: ${both_log}"
}

cmd_start_mcts() {
  _check_inputs
  if [[ "${RUN_INLINE}" == "1" ]]; then
    _wait_vllm
    _run_mcts_body
    return 0
  fi
  have_screen || die "screen not installed"
  if screen_running "${SCREEN}"; then
    echo "[skip] screen ${SCREEN} already running — attach: screen -r ${SCREEN}"
    return 0
  fi
  _wait_vllm
  local inner
  inner=$(cat <<EOF
set -euo pipefail
export RUN_INLINE=1
export ROOT_DIR='${ROOT_DIR}'
export CONDA_BASE='${CONDA_BASE}'
export CONDA_ENV='${CONDA_ENV}'
export VLLM_URL='${VLLM_URL}'
export MULTI_BASE_URLS='${MULTI_BASE_URLS}'
export MODEL_TAG='${MODEL_TAG}'
export ROLL_OUTS='${ROLL_OUTS}'
export RESUME='${RESUME}'
export RANDOM_SEED='${RANDOM_SEED}'
export MAX_WORKERS='${MAX_WORKERS}'
export PARALLEL_WORKERS='${PARALLEL_WORKERS}'
export TASK_TIMEOUT='${TASK_TIMEOUT}'
export JSON_OUT='${JSON_OUT}'
export SQL_OUT='${SQL_OUT}'
export LOG='${LOG}'
export PPL_FILE='${PPL_FILE}'
export GOLD_FILE='${GOLD_FILE}'
export QIDS_FILE='${QIDS_FILE}'
export SKIP_VLLM_WAIT=1
'${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' start-mcts
EOF
)
  screen_cmd "${SCREEN}" "$inner"
  sleep 2
  if ! screen_running "${SCREEN}"; then
    die "screen ${SCREEN} exited immediately — check: tail -50 ${LOG}"
  fi
  echo "[started] screen -S ${SCREEN}"
  echo "  log:  ${LOG}"
  echo "  json: ${JSON_OUT}"
  echo "  attach: screen -r ${SCREEN}"
}

cmd_start_both() {
  have_screen || die "screen not installed"
  local both_screen="clarify_a0_${MODEL_TAG}_both"
  if screen_running "${both_screen}"; then
    echo "[skip] screen ${both_screen} already running — attach: screen -r ${both_screen}"
    return 0
  fi
  _wait_vllm
  local both_log="${OUT_DIR}/run_clarify_a0_${MODEL_TAG}_both.log"
  local inner
  inner=$(cat <<EOF
set -euo pipefail
if [[ -f '${CONDA_BASE}/etc/profile.d/conda.sh' ]]; then
  source '${CONDA_BASE}/etc/profile.d/conda.sh'
  conda activate '${CONDA_ENV}' || true
fi
cd '${ROOT_DIR}'
export MULTI_BASE_URLS='${MULTI_BASE_URLS}'
export MODEL_TAG='${MODEL_TAG}'
export RESUME=0
export SKIP_VLLM_WAIT=1
export RUN_INLINE=1
export MAX_WORKERS='${MAX_WORKERS}'
export PARALLEL_WORKERS='${PARALLEL_WORKERS}'
export TASK_TIMEOUT='${TASK_TIMEOUT}'
for ro in 8 20; do
  echo "======== start rollout=\${ro} \$(date -Iseconds) ========" | tee -a '${both_log}'
  export ROLL_OUTS="\${ro}"
  export JSON_OUT='${OUT_DIR}/v4_a0_30q_${MODEL_TAG}_rollouts'\${ro}'.json'
  export SQL_OUT='${OUT_DIR}/v4_a0_30q_${MODEL_TAG}_rollouts'\${ro}'.txt'
  export LOG='${OUT_DIR}/v4_a0_30q_${MODEL_TAG}_rollouts'\${ro}'.log'
  export ORCH_LOG='${OUT_DIR}/run_clarify_a0_${MODEL_TAG}_r'\${ro}'.log'
  export SCREEN='clarify_a0_${MODEL_TAG}_r'\${ro}''
  '${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' start-mcts
done
echo "======== both rollouts done \$(date -Iseconds) ========" | tee -a '${both_log}'
EOF
)
  screen_cmd "${both_screen}" "$inner"
  sleep 2
  if ! screen_running "${both_screen}"; then
    die "screen ${both_screen} exited immediately — check: tail -50 ${both_log}"
  fi
  echo "[started] screen -S ${both_screen}  (rollouts 8 → 20, MODEL_TAG=${MODEL_TAG})"
  echo "  log: ${both_log}"
  echo "  attach: screen -r ${both_screen}"
}

cmd_full() {
  # A3: v2 hash search + 4-shard parallel run + wait + merge
  export MCTS_USE_SIGNATURE_V2=1
  export RESUME="${RESUME:-0}"
  have_screen || die "screen not installed"
  _wait_vllm
  cmd_prepare_shards
  local orch="${OUT_DIR}/run_a3_r${ROLL_OUTS}.log"
  cmd_start_sharded
  echo "[full] waiting for ${N_SHARDS} shards (rollouts=${ROLL_OUTS}, v2 hash) ..." | tee -a "${orch}"
  while true; do
    local done=0 total=0 i nd nq
    for ((i = 0; i < N_SHARDS; i++)); do
      local sj sq
      sj="$(_shard_json_out "$i")"
      sq="${QIDS_SHARD_DIR}/shard${i}.json"
      if [[ -f "${sj}" && -f "${sq}" ]]; then
        read -r nd nq <<< "$(python3 -c "import json;d=json.load(open('${sj}'));q=json.load(open('${sq}'))['qids'];print(len(d),len(q))")"
        done=$((done + nd))
        total=$((total + nq))
      elif [[ -f "${sq}" ]]; then
        nq=$(python3 -c "import json;print(len(json.load(open('${sq}'))['qids']))")
        total=$((total + nq))
      fi
    done
    echo "[full] ${done}/${total} $(date -Iseconds)" | tee -a "${orch}"
    [[ "${done}" -ge "${total}" && "${total}" -gt 0 ]] && break
    sleep 60
  done
  export MERGED_JSON="${JSON_OUT}"
  cmd_merge_shards
  echo "[full] merged -> ${JSON_OUT}" | tee -a "${orch}"
}

cmd_start() {
  mkdir -p "${OUT_DIR}"
  {
    echo "======== clarify A0 orchestrator $(date -Iseconds) ========"
    cmd_start_mcts
    if [[ "${POST_ANALYSIS}" == "1" ]]; then
      echo "[note] post-analysis 需在 MCTS 结束后手动执行: $0 post"
      echo "       或: watch -n 60 '$0 status' 直到 30/30 后执行 post"
    fi
    echo "======== orchestrator launch done $(date -Iseconds) ========"
  } 2>&1 | tee -a "${ORCH_LOG}"
}

cmd_status() {
  echo "======== screen ========"
  screen -ls 2>/dev/null | grep -E "clarify_a0_${MODEL_TAG}" || echo "(no clarify screens)"
  echo
  echo "======== vLLM endpoints ========"
  local url
  while IFS= read -r url; do
    [[ -n "${url}" ]] || continue
    if curl -sf "${url}/models" >/dev/null 2>&1; then
      echo "  ${url} OK"
    else
      echo "  ${url} DOWN"
    fi
  done < <(_vllm_urls)
  _vllm_model_id | sed 's/^/  primary model: /' 2>/dev/null || true
  echo
  echo "======== MCTS progress (merged) ========"
  if [[ -f "${JSON_OUT}" ]]; then
    python3 -c "
import json, sys
p = sys.argv[1]
qf = sys.argv[2]
d = json.load(open(p))
nq = len(json.load(open(qf)).get('qids', []))
done = len(d) if isinstance(d, dict) else 0
print(f'  json: {done}/{nq} qids in {p}')
" "${JSON_OUT}" "${QIDS_FILE}" 2>/dev/null || echo "  json: ${JSON_OUT} (parse err)"
  else
    echo "  json: (not yet)"
  fi
  if [[ -d "${QIDS_SHARD_DIR}" ]]; then
    echo
    echo "======== shard progress (rollouts=${ROLL_OUTS}) ========"
    local i nd nq sj sq
    for ((i = 0; i < N_SHARDS; i++)); do
      sj="$(_shard_json_out "$i")"
      sq="${QIDS_SHARD_DIR}/shard${i}.json"
      if [[ -f "${sj}" && -f "${sq}" ]]; then
        read -r nd nq <<< "$(python3 -c "import json;d=json.load(open('${sj}'));q=json.load(open('${sq}'))['qids'];print(len(d),len(q))")"
        echo "  w${i}: ${nd}/${nq}  ${sj}"
      else
        echo "  w${i}: (not started)"
      fi
    done
  fi
  if [[ -f "${LOG}" ]]; then
    echo
    tail -3 "${LOG}" 2>/dev/null | sed 's/^/  log: /' || true
  fi
}

cmd_attach() {
  screen -r "${SCREEN}"
}

cmd_stop() {
  local what="${1:-mcts}"
  case "${what}" in
    mcts)
      screen -S "${SCREEN}" -X quit 2>/dev/null || true
      screen -S "clarify_a0_${MODEL_TAG}_both" -X quit 2>/dev/null || true
      screen -S "clarify_a0_${MODEL_TAG}_sharded_both" -X quit 2>/dev/null || true
      local i
      for ((i = 0; i < N_SHARDS; i++)); do
        screen -S "clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${i}" -X quit 2>/dev/null || true
        screen -S "clarify_a0_${MODEL_TAG}_r8_w${i}" -X quit 2>/dev/null || true
        screen -S "clarify_a0_${MODEL_TAG}_r20_w${i}" -X quit 2>/dev/null || true
      done
      pkill -f 'workflows/mcts_v4/test/test_mcts.py' 2>/dev/null || true
      echo "[stop] all clarify screens + test_mcts.py"
      ;;
    all)
      cmd_stop mcts
      ;;
    *) die "stop: mcts|all" ;;
  esac
}

case "${1:-status}" in
  prepare-ppl) cmd_prepare_ppl ;;
  prepare-shards) cmd_prepare_shards ;;
  migrate-shards) cmd_migrate_shards ;;
  merge-shards) cmd_merge_shards ;;
  start-mcts) cmd_start_mcts ;;
  start-sharded) cmd_start_sharded ;;
  start-sharded-both) cmd_start_sharded_both ;;
  full) cmd_full ;;
  start-both) cmd_start_both ;;
  start) cmd_start ;;
  post) cmd_post ;;
  status) cmd_status ;;
  attach) cmd_attach ;;
  stop) cmd_stop "${2:-mcts}" ;;
  *)
    echo "Usage: $0 {prepare-ppl|prepare-shards|migrate-shards|merge-shards|start|start-mcts|start-sharded|start-sharded-both|full|start-both|post|status|attach|stop [mcts|all]}"
    exit 1
    ;;
esac
