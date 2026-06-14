#!/usr/bin/env bash
# min2 full498 with legacy E6: reversed + per-expand bootstrap, no FK/PK.
# Skips gap30 cohort (reuse abl5_e6_reversed_bootstrap_gap30_r12.json).
#
#   bash run_e6_bootstrap_full498.sh prepare-manifest
#   bash run_e6_bootstrap_full498.sh start-screen
#   bash run_e6_bootstrap_full498.sh status
#   bash run_e6_bootstrap_full498.sh merge-full498
#   bash run_e6_bootstrap_full498.sh report
#   bash run_e6_bootstrap_full498.sh stop
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
GAP30_MANIFEST="${PLAN_DIR}/qids_alpha_min2_recall_gap30_manifest.json"
FULL498_MANIFEST="${PLAN_DIR}/qids_min2_full498_priority_manifest.json"
MANIFEST="${MANIFEST:-${PLAN_DIR}/qids_e6_bootstrap_full468_manifest.json}"
GAP30_JSON="${OUT_DIR}/v4_colbind_v2_dual03_min2sq_abl5_e6_reversed_bootstrap_gap30_r12.json"
SCRIPT="scripts/run_clarify_a0_30q.sh"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_e6_bootstrap_full498.sh"
STALE_SCREEN_CHECKS="${STALE_SCREEN_CHECKS:-10}"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

# Legacy E6 arm env (per-expand bootstrap)
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
export ROLL_OUTS="${ROLL_OUTS:-12}"
export RANDOM_SEED=20240605
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

export MODEL_TAG="abl5_e6_per_expand_full468"
export QIDS_FILE="${MANIFEST}"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_${MODEL_TAG}"
export SHARD_BASENAME="v4_colbind_v2_dual03_min2sq_abl5_e6_per_expand_full468_rollouts${ROLL_OUTS}"
export JSON_OUT="${OUT_DIR}/${SHARD_BASENAME}.json"
export FINAL_JSON="${OUT_DIR}/v4_colbind_v2_dual03_min2sq_abl5_e6_bootstrap_full498_rollouts${ROLL_OUTS}.json"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_${MODEL_TAG}_r${ROLL_OUTS}.log"

have_screen() { command -v screen >/dev/null 2>&1; }

cmd_prepare_manifest() {
  python3 -u - <<PY
import json
from pathlib import Path

plan = Path("${PLAN_DIR}")
out = Path("${OUT_DIR}")
gap30_path = Path("${GAP30_MANIFEST}")
full_path = Path("${FULL498_MANIFEST}")

if not full_path.exists():
    raise SystemExit(f"missing {full_path}; run run_decompose_min2_full498.sh prepare-manifest-priority first")

gap30 = {str(q) for q in json.loads(gap30_path.read_text())["qids"]}
all498 = [str(q) for q in json.loads(full_path.read_text())["qids"]]
remaining = [q for q in all498 if q not in gap30]
manifest = {
    "source": "full498 priority minus gap30 E6 cohort",
    "n_full498": len(all498),
    "n_gap30_skip": len(gap30),
    "n": len(remaining),
    "skip_qids": sorted(gap30, key=int),
    "reuse_json": "${GAP30_JSON}",
    "qids": remaining,
}
dest = Path("${MANIFEST}")
dest.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {dest} n={len(remaining)} (498 - {len(gap30)} gap30)")
PY
}

cmd_prepare_shards() {
  python3 -u - <<PY
import json
from pathlib import Path

manifest_path = Path("${MANIFEST}")
out_dir = Path("${QIDS_SHARD_DIR}")
n_shards = int("${N_SHARDS}")
roll_outs = int("${ROLL_OUTS}")

manifest = json.loads(manifest_path.read_text())
qids = manifest["qids"]
shards = [[] for _ in range(n_shards)]
for i, q in enumerate(qids):
    shards[i % n_shards].append(q)

out_dir.mkdir(parents=True, exist_ok=True)
for i, chunk in enumerate(shards):
    out = {
        "description": f"E6 per-expand full468 shard {i}/{n_shards} ({len(chunk)} qids)",
        "shard": i,
        "n_shards": n_shards,
        "rollouts": roll_outs,
        "qids": chunk,
    }
    path = out_dir / f"shard{i}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"[prepare-shards] shard {i}: {len(chunk)} qids -> {path}")

(out_dir / "manifest.json").write_text(
    json.dumps({"n_total": len(qids), "n_shards": n_shards, "qids": qids, "split": "round_robin_priority"}, indent=2) + "\n"
)
print(f"[prepare-shards] {len(qids)} qids -> {n_shards} shards under {out_dir}")
PY
}

cmd_start() {
  [[ -f "${GAP30_JSON}" ]] || { echo "missing gap30 E6 json: ${GAP30_JSON}" >&2; exit 1; }
  [[ -f "${MANIFEST}" ]] || cmd_prepare_manifest
  echo "[E6-full498] rev=1 bootstrap=per_expand fk=0 skip_gap30=30 resume=${RESUME} shards=${N_SHARDS}"
  cmd_prepare_shards
  "${SCRIPT}" start-sharded
}

cmd_start_screen() {
  have_screen || { echo "screen not installed" >&2; exit 1; }
  local scr="e6_per_expand_full468_r${ROLL_OUTS}"
  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\\.${scr}[[:space:]]"; then
    echo "[skip] screen ${scr} already running"
    cmd_status
    return 0
  fi
  screen -dmS "${scr}" bash -lc "unset MODEL_TAG QIDS_SHARD_DIR SHARD_BASENAME JSON_OUT; cd '${ROOT_DIR}' && bash '${SELF}' full 2>&1 | tee '${ORCH_LOG}'; echo '[E6 per-expand full468 done]' \$(date -Iseconds)"
  sleep 1
  echo "[started] screen -S ${scr}"
  cmd_status
}

_wait_shards_done() {
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
    echo "[wait E6-full468] qids=${n}/${target} screens=${scr}"
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

cmd_merge_full498() {
  python3 -u - <<PY
import json
from pathlib import Path

gap30 = json.loads(Path("${GAP30_JSON}").read_text())
run468 = json.loads(Path("${JSON_OUT}").read_text()) if Path("${JSON_OUT}").exists() else {}
merged = dict(gap30)
merged.update(run468)
out = Path("${FINAL_JSON}")
out.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
print(f"merged gap30={len(gap30)} + full468={len(run468)} -> {out} n={len(merged)}")
PY
}

cmd_full() {
  {
    echo "[E6-full468] start $(date -Iseconds)"
    cmd_start
    _wait_shards_done
    "${SCRIPT}" merge-shards
    echo "[E6-full468] shard merge -> ${JSON_OUT} $(date -Iseconds)"
    cmd_merge_full498
    cmd_report
  } 2>&1 | tee -a "${ORCH_LOG}"
}

cmd_merge() {
  "${SCRIPT}" merge-shards
  cmd_merge_full498
}

cmd_report() {
  MANIFEST468="${MANIFEST}" FINAL_JSON="${FINAL_JSON}" GAP30_JSON="${GAP30_JSON}" RUN468_JSON="${JSON_OUT}" python3 -u - <<'PY'
import json, os
from pathlib import Path

OUT = Path("workflows/mcts_v4/test/out/cte_diverse")
full498 = sorted(
    json.loads((OUT / "v4_colbind_v2_dual03_global_filter_498q_rollouts12.json").read_text()).keys(),
    key=int,
)
prod = json.loads((OUT / "v4_colbind_v2_dual03_min2sq_full498_rollouts12.json").read_text())
global_d = json.loads((OUT / "v4_colbind_v2_dual03_global_filter_498q_rollouts12.json").read_text())
gap30 = json.loads(Path(os.environ["GAP30_JSON"]).read_text())
run468 = json.loads(Path(os.environ["RUN468_JSON"]).read_text()) if Path(os.environ["RUN468_JSON"]).exists() else {}
final_p = Path(os.environ["FINAL_JSON"])
final_d = json.loads(final_p.read_text()) if final_p.exists() else {}

def met(data, qs):
    done = [q for q in qs if q in data and not data[q].get("error")]
    r = sum(any(a.get("is_correct") for a in (data[q].get("all_sqls_with_attributes") or [])) for q in done)
    h = sum(bool((data[q].get("stats") or {}).get("gold_match")) for q in done)
    return len(done), r, h

print("=== E6 per-expand bootstrap full498 ===")
print(f"gap30 reuse: {len(gap30)}  run468: {len(run468)}  merged: {len(final_d)}")
for name, d in [("prod_min2", prod), ("global", global_d), ("e6_merged", final_d)]:
    n, r, h = met(d, full498)
    if name == "e6_merged" and n == 0:
        print(f"{name:14} (not merged yet)")
        continue
    print(f"{name:14} n={n:3d} Recall={r:3d}/{n} Acc={h:3d}/{n}")

if final_d:
    _, r0, h0 = met(prod, full498)
    _, r1, h1 = met(final_d, full498)
    print(f"Δ vs prod_min2: Recall {r1-r0:+d}  Acc {h1-h0:+d}")
PY
}

cmd_status() {
  local target n=0
  target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))" 2>/dev/null || echo 468)"
  for ((i = 0; i < N_SHARDS; i++)); do
    local p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
    [[ -f "${p}" ]] && n=$((n + $(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)))
  done
  [[ -f "${JSON_OUT}" ]] && n=$(python3 -c "import json; print(len(json.load(open('${JSON_OUT}'))))")
  local merged=0
  [[ -f "${FINAL_JSON}" ]] && merged=$(python3 -c "import json; print(len(json.load(open('${FINAL_JSON}'))))" 2>/dev/null || echo 0)
  echo "E6 per-expand: run468=${n}/${target}  merged_full498=${merged}/498 (gap30 reused separately)"
  screen -ls 2>/dev/null | grep -E "e6_per_expand_full468|clarify_a0_${MODEL_TAG}" || echo "(no screens)"
}

cmd_stop() {
  MODEL_TAG="${MODEL_TAG}" ROLL_OUTS="${ROLL_OUTS}" "${SCRIPT}" stop all || true
  screen -S "e6_bootstrap_full498_r${ROLL_OUTS}" -X quit 2>/dev/null || true
  screen -S "e6_per_expand_full468_r${ROLL_OUTS}" -X quit 2>/dev/null || true
}

case "${1:-}" in
  prepare-manifest) cmd_prepare_manifest ;;
  prepare-shards) cmd_prepare_shards ;;
  start) cmd_start ;;
  start-screen) cmd_start_screen ;;
  full) cmd_full ;;
  merge) cmd_merge ;;
  merge-full498) cmd_merge_full498 ;;
  report) cmd_report ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  *)
    echo "Usage: $0 {prepare-manifest|prepare-shards|start|start-screen|full|merge|merge-full498|report|status|stop}"
    exit 1
    ;;
esac
