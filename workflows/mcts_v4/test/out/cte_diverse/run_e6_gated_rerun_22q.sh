#!/usr/bin/env bash
# E6 per-expand + gated R4→R8 rerun on 22 qids (9 oracle-gap + 13 timeout).
# Requires 2× vLLM @ :8000,:8100 (start_vllm_qwencoder30b_2vllm.sh).
#
#   bash run_e6_gated_rerun_22q.sh prepare-manifest   # refresh qid list from baseline json
#   bash run_e6_gated_rerun_22q.sh start-screen       # wait vLLM → 4-shard MCTS (each :8000+:8100)
#   bash run_e6_gated_rerun_22q.sh status
#   bash run_e6_gated_rerun_22q.sh merge-overlay      # patch 22 qids into full498 json
#   bash run_e6_gated_rerun_22q.sh replay-vote        # offline all_buckets R4 on overlay (no MCTS)
#   bash run_e6_gated_rerun_22q.sh start-extra-shard [qids]  # w4 on :8100, tail pending, no stop w1/w2
#   bash run_e6_gated_rerun_22q.sh report
#   bash run_e6_gated_rerun_22q.sh stop
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
MANIFEST="${PLAN_DIR}/qids_e6_gated_rerun_22_manifest.json"
BASELINE_JSON="${OUT_DIR}/v4_colbind_v2_dual03_min2sq_abl5_e6_bootstrap_full498_rollouts12.json"
SCRIPT="scripts/run_clarify_a0_30q.sh"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_e6_gated_rerun_22q.sh"
STALE_SCREEN_CHECKS="${STALE_SCREEN_CHECKS:-10}"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

# E6 per-expand bootstrap (same as full498 run)
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

export ROLL_OUTS="${ROLL_OUTS:-12}"
export RANDOM_SEED=20240605
export TASK_TIMEOUT="${TASK_TIMEOUT:-900}"
export RESUME="${RESUME:-0}"
export SKIP_VLLM_WAIT="${SKIP_VLLM_WAIT:-0}"
export POST_ANALYSIS=0
export N_SHARDS="${N_SHARDS:-4}"
export SHARD_MULTI_VLLM="${SHARD_MULTI_VLLM:-1}"
export BASE_PORT="${BASE_PORT:-8000}"
export PORT_STRIDE=100
export MULTI_BASE_URLS="${MULTI_BASE_URLS:-http://127.0.0.1:8000/v1,http://127.0.0.1:8100/v1}"
export SHARD_SPLIT="${SHARD_SPLIT:-time_balanced}"
export SHARD_TIME_JSON="${SHARD_TIME_JSON:-${OUT_DIR}/v4_colbind_v2_dual03_global_filter_498q_rollouts12.json}"

export MODEL_TAG="abl5_e6_gated_rerun22"
export QIDS_FILE="${MANIFEST}"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_${MODEL_TAG}"
export SHARD_BASENAME="v4_colbind_v2_dual03_min2sq_abl5_e6_gated_rerun22_rollouts${ROLL_OUTS}"
export JSON_OUT="${OUT_DIR}/${SHARD_BASENAME}.json"
export OVERLAY_JSON="${OUT_DIR}/v4_colbind_v2_dual03_min2sq_abl5_e6_bootstrap_full498_gated22_overlay_rollouts${ROLL_OUTS}.json"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_${MODEL_TAG}_r${ROLL_OUTS}.log"
# w0–w3 for 4-shard dual-vLLM; w4 optional via start-extra-shard
export MERGE_SHARD_INDICES="${MERGE_SHARD_INDICES:-0 1 2 3}"
# remaining-qids rerun: default 4 workers, each with :8000+:8100
export REMAIN_N_WORKERS="${REMAIN_N_WORKERS:-4}"
export EXTRA_SHARD_IDX="${EXTRA_SHARD_IDX:-4}"
export EXTRA_VLLM_PORT="${EXTRA_VLLM_PORT:-$((BASE_PORT + PORT_STRIDE))}"
export EXTRA_N_QIDS="${EXTRA_N_QIDS:-3}"

have_screen() { command -v screen >/dev/null 2>&1; }

cmd_prepare_manifest() {
  python3 -u - <<PY
import json
from pathlib import Path

baseline = Path("${BASELINE_JSON}")
out = Path("${MANIFEST}")
NINE = ["28", "125", "243", "347", "352", "371", "726", "955", "1198"]
if not baseline.exists():
    raise SystemExit(f"missing baseline {baseline}")

data = json.loads(baseline.read_text())
timeout = []
for qid, rec in data.items():
    st = rec.get("stats") or {}
    if st.get("timeout_fallback") or st.get("timeout_fallback_failed") or st.get("task_timeout"):
        timeout.append(str(qid))
qids = sorted(set(NINE) | set(timeout), key=int)
manifest = {
    "description": "E6 gated R4→R8 rerun: 9 oracle-gap qids + timeout qids from full498",
    "n": len(qids),
    "n_oracle_gap": len(NINE),
    "n_timeout": len(timeout),
    "oracle_gap_qids": NINE,
    "timeout_qids": sorted(timeout, key=int),
    "qids": qids,
}
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out} n={len(qids)} (9 gap + {len(timeout)} timeout, union={len(qids)})")
PY
}

cmd_start() {
  [[ -f "${MANIFEST}" ]] || cmd_prepare_manifest
  echo "[gated-rerun22] gated=1 v2=${MCTS_USE_SIGNATURE_V2} timeout=${TASK_TIMEOUT}s shards=${N_SHARDS} dual_vllm=${SHARD_MULTI_VLLM}"
  export SHARD_MULTI_VLLM="${SHARD_MULTI_VLLM}"
  "${SCRIPT}" prepare-shards
  "${SCRIPT}" start-sharded
}

cmd_start_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  local scr="e6_gated_rerun22_r${ROLL_OUTS}"
  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${scr}[[:space:]]"; then
    echo "[skip] screen ${scr} already running"
    cmd_status
    return 0
  fi
  screen -dmS "${scr}" bash -lc "unset MODEL_TAG QIDS_SHARD_DIR SHARD_BASENAME JSON_OUT; cd '${ROOT_DIR}' && bash '${SELF}' full 2>&1 | tee '${ORCH_LOG}'; echo '[E6 gated rerun22 done]' \$(date -Iseconds)"
  sleep 1
  echo "[started] screen -S ${scr}"
  cmd_status
}

_wait_done() {
  local target
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))")"
  local stale=0
  while true; do
    local n=0 scr
    for ((i = 0; i < N_SHARDS; i++)); do
      local p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
      [[ -f "${p}" ]] && n=$((n + $(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)))
    done
    scr=$(screen -ls 2>/dev/null | grep -c "clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w" || true)
    echo "[wait gated-rerun22] qids=${n}/${target} screens=${scr}"
    if [[ "${n}" -ge "${target}" && "${scr}" -eq 0 ]]; then
      break
    fi
    if [[ "${n}" -ge "${target}" && "${scr}" -gt 0 ]]; then
      stale=$((stale + 1))
      if [[ "${stale}" -ge "${STALE_SCREEN_CHECKS}" ]]; then
        MODEL_TAG="${MODEL_TAG}" ROLL_OUTS="${ROLL_OUTS}" "${SCRIPT}" stop all || true
        sleep 10
        break
      fi
    else
      stale=0
    fi
    sleep 45
  done
}

cmd_prepare_remaining_shards() {
  python3 -u - <<PY
import json
from pathlib import Path

manifest = json.loads(Path("${MANIFEST}").read_text())
all_qids = [str(q) for q in manifest["qids"]]
out_dir = Path("${OUT_DIR}")
shard_base = "${SHARD_BASENAME}"
done = set()
for i in range(int("${N_SHARDS}")):
    p = out_dir / f"{shard_base}_w{i}.json"
    if p.exists():
        done.update(json.loads(p.read_text()).keys())
pending = [q for q in all_qids if q not in done]
if not pending:
    raise SystemExit("no pending qids")

n_workers = max(1, int("${REMAIN_N_WORKERS}"))
chunk = (len(pending) + n_workers - 1) // n_workers
chunks = [pending[i * chunk : (i + 1) * chunk] for i in range(n_workers)]
chunks = [c for c in chunks if c]
shard_dir = Path("${QIDS_SHARD_DIR}")
shard_dir.mkdir(parents=True, exist_ok=True)
# drop stale remain_shard*.json from prior 3-way splits
for old in shard_dir.glob("remain_shard*.json"):
    old.unlink()
for i, qids in enumerate(chunks):
    path = shard_dir / f"remain_shard{i}.json"
    path.write_text(json.dumps({"qids": qids}, indent=2) + "\n")
    print(f"[remain] shard w{1 + i}: {len(qids)} qids -> {path}")
    print(f"         {', '.join(qids)}")
PY
}

_launch_shard_worker() {
  local idx="$1"
  local port="$2"
  local qids_file="$3"
  local screen_name="clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${idx}"
  local json_out="${OUT_DIR}/${SHARD_BASENAME}_w${idx}.json"
  local sql_out="${json_out%.json}.txt"
  local log="${json_out%.json}.log"
  local multi_urls="${MULTI_BASE_URLS}"
  local use_multi="${SHARD_MULTI_VLLM:-0}"

  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${screen_name}[[:space:]]"; then
    echo "[skip] ${screen_name} already running"
    return 0
  fi

  if [[ "${use_multi}" == "1" ]]; then
    echo "[shard] w${idx} multi=${multi_urls} qids=${qids_file} resume=1"
  else
    echo "[shard] w${idx} port=${port} qids=${qids_file} resume=1"
  fi
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
export MCTS_USE_SIGNATURE_V2='${MCTS_USE_SIGNATURE_V2:-1}'
export OUT_DIR='${OUT_DIR}'
export SHARD_BASENAME='${SHARD_BASENAME}'
export MODEL_TAG='${MODEL_TAG}'
export ROLL_OUTS='${ROLL_OUTS}'
export RANDOM_SEED='${RANDOM_SEED}'
export TASK_TIMEOUT='${TASK_TIMEOUT}'
export RESUME=1
export SKIP_VLLM_WAIT=1
export RUN_INLINE=1
export SHARD_MODE=1
export SHARD_MULTI_VLLM='${use_multi}'
if [[ "\${SHARD_MULTI_VLLM}" == "1" ]]; then
  export MULTI_BASE_URLS='${multi_urls}'
else
  export MULTI_BASE_URLS=
  export VLLM_HOST='127.0.0.1'
  export VLLM_PORT='${port}'
fi
export JSON_OUT='${json_out}'
export SQL_OUT='${sql_out}'
export LOG='${log}'
export QIDS_FILE='${qids_file}'
export PPL_FILE='workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json'
export GOLD_FILE='workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json'
'${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' start-mcts
EOF
)"
  sleep 1
}

_remain_vllm_port() {
  local worker_idx="$1"  # 0-based among remaining workers
  echo $((BASE_PORT + (worker_idx % 2) * PORT_STRIDE))
}

cmd_start_remaining_shards() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  cmd_prepare_remaining_shards

  echo "[remaining] stop w1-w${N_SHARDS} workers ..."
  local i
  for ((i = 1; i < N_SHARDS; i++)); do
    screen -S "clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${i}" -X quit 2>/dev/null || true
  done
  pkill -f "remain_shard.*${MODEL_TAG}" 2>/dev/null || true
  sleep 2

  local n=0 qids_file port
  while [[ -f "${QIDS_SHARD_DIR}/remain_shard${n}.json" ]]; do
    qids_file="${QIDS_SHARD_DIR}/remain_shard${n}.json"
    port="$(_remain_vllm_port "${n}")"
    _launch_shard_worker "$((n + 1))" "${port}" "${qids_file}"
    n=$((n + 1))
  done

  if [[ "${SHARD_MULTI_VLLM}" == "1" ]]; then
    echo "[remaining] started ${n} worker(s) on ${MULTI_BASE_URLS} (RESUME=1)"
  else
    echo "[remaining] started ${n} worker(s) on :${BASE_PORT}/:$((BASE_PORT + PORT_STRIDE)) (RESUME=1)"
  fi
  cmd_status
}

cmd_start_extra_shard() {
  # Add w4+ without stopping w1/w2; share a vLLM port (default :8100).
  have_screen || { echo "screen not installed" >&2; exit 1; }
  local idx="${EXTRA_SHARD_IDX}" port="${EXTRA_VLLM_PORT}" qids_file="${QIDS_SHARD_DIR}/remain_extra.json"
  local extra_qids="${EXTRA_QIDS:-${1:-}}"

  python3 -u - <<PY
import json
from pathlib import Path

manifest = json.loads(Path("${MANIFEST}").read_text())
all_qids = [str(q) for q in manifest["qids"]]
out_dir = Path("${OUT_DIR}")
shard_base = "${SHARD_BASENAME}"
done = set()
for i in range(8):
    p = out_dir / f"{shard_base}_w{i}.json"
    if p.exists():
        done.update(json.loads(p.read_text()).keys())
pending = sorted([q for q in all_qids if q not in done], key=int)
extra_raw = "${extra_qids}".strip()
if extra_raw:
    extra = [q.strip() for q in extra_raw.replace(";", ",").split(",") if q.strip()]
    extra = [q for q in extra if q in pending]
else:
    n = max(1, int("${EXTRA_N_QIDS}"))
    extra = pending[-n:]
if not extra:
    raise SystemExit("no extra pending qids")
Path("${QIDS_SHARD_DIR}").mkdir(parents=True, exist_ok=True)
Path("${qids_file}").write_text(json.dumps({"qids": extra}, indent=2) + "\n")
print(f"[extra] w${EXTRA_SHARD_IDX} tail {len(extra)} qids -> ${qids_file}")
print(f"        {', '.join(extra)} (pending was {len(pending)})")
PY

  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w${idx}[[:space:]]"; then
    echo "[skip] w${idx} already running"
    cmd_status
    return 0
  fi

  _launch_shard_worker "${idx}" "${port}" "${qids_file}"
  echo "[extra] w${idx} on :${port} (shared vLLM ok, RESUME=1)"
  cmd_status
}

cmd_merge() {
  python3 -u - <<PY
import json
from pathlib import Path

out_dir = Path("${OUT_DIR}")
shard_base = "${SHARD_BASENAME}"
merged_path = Path("${JSON_OUT}")
indices = [int(x) for x in "${MERGE_SHARD_INDICES}".split()]

by_qid = {}
for i in indices:
    path = out_dir / f"{shard_base}_w{i}.json"
    if not path.is_file():
        print(f"[merge] skip missing {path}")
        continue
    data = json.loads(path.read_text())
    for k, v in data.items():
        if str(k).isdigit():
            by_qid[str(k)] = v

merged = {k: by_qid[k] for k in sorted(by_qid, key=lambda x: int(x))}
merged_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
print(f"[merge] {len(merged)} qids -> {merged_path} (shards {indices})")
PY
}

cmd_merge_overlay() {
  cmd_merge
  python3 -u - <<PY
import json
from pathlib import Path

baseline_p = Path("${BASELINE_JSON}")
rerun_p = Path("${JSON_OUT}")
overlay_p = Path("${OVERLAY_JSON}")
manifest = json.loads(Path("${MANIFEST}").read_text())
qids = manifest["qids"]

if not baseline_p.exists():
    raise SystemExit(f"missing baseline {baseline_p}")
if not rerun_p.exists():
    raise SystemExit(f"missing rerun merge {rerun_p}")

base = json.loads(baseline_p.read_text())
rerun = json.loads(rerun_p.read_text())
patched = 0
for q in qids:
    if q in rerun:
        base[q] = rerun[q]
        patched += 1
overlay_p.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n")
print(f"overlay: patched {patched}/{len(qids)} qids -> {overlay_p} (total {len(base)})")
PY
}

cmd_replay_vote() {
  local src="${1:-${OVERLAY_JSON}}"
  [[ -f "${src}" ]] || src="${BASELINE_JSON}"
  export MCTS_R4_VOTE_MODE="${MCTS_R4_VOTE_MODE:-all_buckets}"
  echo "[replay-vote] src=${src} vote_mode=${MCTS_R4_VOTE_MODE}"
  python3 -u "${ROOT_DIR}/workflows/mcts_v4/test/out/cte_diverse/analysis/checker_498/replay_r4_vote_json.py" \
    "${src}" -o "${src%.json}_r4_${MCTS_R4_VOTE_MODE}_replay.json"
}

cmd_full() {
  {
    echo "[gated-rerun22] start $(date -Iseconds)"
    cmd_start
    _wait_done
    cmd_merge_overlay
    cmd_report
  } 2>&1 | tee -a "${ORCH_LOG}"
}

cmd_report() {
  MANIFEST="${MANIFEST}" BASELINE="${BASELINE_JSON}" RERUN="${JSON_OUT}" OVERLAY="${OVERLAY_JSON}" python3 -u - <<'PY'
import json, os
from pathlib import Path

manifest = json.loads(Path(os.environ["MANIFEST"]).read_text())
qids = manifest["qids"]

def met(path):
    p = Path(path)
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    sub = [q for q in qids if q in d]
    recall = sum(1 for q in sub if any(a.get("is_correct") for a in (d[q].get("all_sqls_with_attributes") or [])))
    hit1 = sum(1 for q in sub if (d[q].get("stats") or {}).get("gold_match"))
    timeout = sum(1 for q in sub if (d[q].get("stats") or {}).get("timeout_fallback") or (d[q].get("stats") or {}).get("timeout_fallback_failed"))
    gated = sum(1 for q in sub if "gated" in str((d[q].get("stats") or {}).get("selection_mode") or ""))
    return {"n": len(sub), "recall": recall, "hit1": hit1, "timeout": timeout, "gated_sel": gated}

print("=== E6 gated rerun 22q ===")
for label, key in [("baseline", "BASELINE"), ("rerun", "RERUN"), ("overlay", "OVERLAY")]:
    m = met(os.environ[key])
    if m:
        print(f"{label:10} n={m['n']} Recall={m['recall']}/{m['n']} Hit@1={m['hit1']}/{m['n']} timeout={m['timeout']} gated_mode={m['gated_sel']}")
    else:
        print(f"{label:10} (missing)")
PY
}

cmd_status() {
  local target n=0
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo 22)"
  local shard_lines=""
  for i in ${MERGE_SHARD_INDICES}; do
    local p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
    local sn=0
    if [[ -f "${p}" ]]; then
      sn=$(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)
    fi
    n=$((n + sn))
    local st=""
    [[ -f "${p}" ]] && st=" json"
    [[ -f "${OUT_DIR}/${SHARD_BASENAME}_w${i}.log" ]] && st="${st} log"
    shard_lines+="  w${i}: ${sn} qids${st}"$'\n'
  done
  [[ -f "${JSON_OUT}" ]] && n=$(python3 -c "import json; print(len(json.load(open('${JSON_OUT}'))))" 2>/dev/null || echo "${n}")

  local pct=0
  if [[ "${target}" -gt 0 ]]; then
    pct=$((n * 100 / target))
  fi

  echo "gated-rerun22: ${n}/${target} (${pct}%) merged=$( [[ -f ${JSON_OUT} ]] && echo yes || echo no ) overlay=$( [[ -f ${OVERLAY_JSON} ]] && echo yes || echo no )"
  printf "%s" "${shard_lines}"
  screen -ls 2>/dev/null | grep -E "e6_gated_rerun22|clarify_a0_${MODEL_TAG}" || echo "(no screens)"
}

cmd_stop() {
  MODEL_TAG="${MODEL_TAG}" ROLL_OUTS="${ROLL_OUTS}" N_SHARDS="${N_SHARDS}" "${SCRIPT}" stop all || true
  screen -S "e6_gated_rerun22_r${ROLL_OUTS}" -X quit 2>/dev/null || true
}

case "${1:-}" in
  prepare-manifest) cmd_prepare_manifest ;;
  start) cmd_start ;;
  start-screen) cmd_start_screen ;;
  full) cmd_full ;;
  merge) cmd_merge ;;
  merge-overlay) cmd_merge_overlay ;;
  replay-vote) cmd_replay_vote "${2:-}" ;;
  start-remaining-shards) cmd_start_remaining_shards ;;
  start-extra-shard) cmd_start_extra_shard "${2:-}" ;;
  report) cmd_report ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  *)
    echo "Usage: $0 {prepare-manifest|start|start-screen|start-remaining-shards|start-extra-shard|full|merge|merge-overlay|replay-vote|report|status|stop}"
    exit 1
    ;;
esac
