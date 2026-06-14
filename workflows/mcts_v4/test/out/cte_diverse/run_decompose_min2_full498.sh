#!/usr/bin/env bash
# min2 decompose: full 498q rerun aligned with production (R4 + gated R8 + filter)
#
#   bash run_decompose_min2_full498.sh prepare-manifest
#   bash run_decompose_min2_full498.sh full
#   bash run_decompose_min2_full498.sh report
#   bash run_decompose_min2_full498.sh status
#   bash run_decompose_min2_full498.sh stop
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

PLAN_DIR="workflows/mcts_v4/test/out/cte_diverse/analysis/colbind_v2_56q"
export OUT_DIR="${OUT_DIR:-workflows/mcts_v4/test/out/cte_diverse}"
MANIFEST="${MANIFEST:-${PLAN_DIR}/qids_min2_full498_manifest.json}"
PRIORITY_MANIFEST="${PLAN_DIR}/qids_min2_full498_priority_manifest.json"
PRIORITY_SCORES="${PLAN_DIR}/qids_min2_full498_priority_scores.json"
SCRIPT="scripts/run_clarify_a0_30q.sh"
STALE_SCREEN_CHECKS="${STALE_SCREEN_CHECKS:-10}"

# shellcheck source=../../config/bprime_env.sh
source "${ROOT_DIR}/workflows/mcts_v4/config/bprime_env.sh"

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

export MODEL_TAG="${MODEL_TAG:-colbind03dual_min2sq_full498}"
export QIDS_FILE="${MANIFEST}"
export QIDS_SHARD_DIR="${OUT_DIR}/qids_shards_${MODEL_TAG}"
export SHARD_BASENAME="v4_colbind_v2_dual03_min2sq_full498_rollouts${ROLL_OUTS}"
export JSON_OUT="${OUT_DIR}/${SHARD_BASENAME}.json"
export SQL_OUT="${JSON_OUT%.json}.txt"
export LOG="${JSON_OUT%.json}.log"
export ORCH_LOG="${OUT_DIR}/run_${MODEL_TAG}_r${ROLL_OUTS}.log"

_wait_shards_done() {
  local label="$1"
  local target="$2"
  local stale=0
  while true; do
    local n=0
    for ((i = 0; i < N_SHARDS; i++)); do
      local p="${OUT_DIR}/${SHARD_BASENAME}_w${i}.json"
      [[ -f "${p}" ]] && n=$((n + $(python3 -c "import json; print(len(json.load(open('${p}'))))" 2>/dev/null || echo 0)))
    done
    local scr
    scr=$(screen -ls 2>/dev/null | grep -c "clarify_a0_${MODEL_TAG}_r${ROLL_OUTS}_w" || true)
    echo "[wait ${label}] qids=${n}/${target} screens=${scr}"
    if [[ "${n}" -ge "${target}" && "${scr}" -eq 0 ]]; then
      break
    fi
    if [[ "${n}" -ge "${target}" && "${scr}" -gt 0 ]]; then
      stale=$((stale + 1))
      if [[ "${stale}" -ge "${STALE_SCREEN_CHECKS}" ]]; then
        echo "[warn ${label}] all qids present but ${scr} worker screens still up; stopping workers"
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

cmd_prepare_manifest() {
  python3 -u - <<'PY'
import json
from pathlib import Path

OUT = Path("workflows/mcts_v4/test/out/cte_diverse")
PLAN = OUT / "analysis/colbind_v2_56q"
all498 = sorted(
    json.loads((OUT / "v4_colbind_v2_dual03_global_filter_498q_rollouts12.json").read_text()).keys(),
    key=int,
)
manifest = {
    "source": "full 498 global baseline qids",
    "n": len(all498),
    "qids": all498,
}
out = PLAN / "qids_min2_full498_manifest.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out} n={len(all498)}")
PY
}

cmd_prepare_manifest_priority() {
  python3 -u - <<'PY'
import json
from collections import defaultdict
from pathlib import Path

OUT = Path("workflows/mcts_v4/test/out/cte_diverse")
PLAN = OUT / "analysis/colbind_v2_56q"
global_d = json.loads((OUT / "v4_colbind_v2_dual03_global_filter_498q_rollouts12.json").read_text())
all498 = sorted(global_d.keys(), key=int)

def load_pair(base: Path, pattern: str):
    d = {}
    for p in sorted(base.glob(pattern)):
        d.update(json.loads(p.read_text()))
    return d

def mr(d, q):
    return any(a.get("is_correct") for a in (d.get(q, {}).get("all_sqls_with_attributes") or []))

def ma(d, q):
    return bool(((d.get(q, {}) or {}).get("stats") or {}).get("gold_match"))

retry = load_pair(
    OUT / "analysis/colbind_v2_56q/backup_min2_retry_partial_20260613_234518",
    "v4_colbind_v2_dual03_min2sq_full498_rollouts12_w*.json",
)
soft = load_pair(
    OUT / "analysis/colbind_v2_56q/backup_min2_soft_sql5_partial_20260614_000855",
    "v4_colbind_v2_dual03_min2sq_full498_rollouts12_w*.json",
)
min2_43 = {}
p43 = OUT / "v4_colbind_v2_dual03_min2sq_filter_43q_rollouts12.json"
if p43.exists():
    min2_43 = json.loads(p43.read_text())

score = defaultdict(int)
reason = defaultdict(list)

def bump(q, pts, why):
    score[q] += pts
    reason[q].append(why)

for label, d in [("retry65", retry), ("min2_43", min2_43), ("soft36", soft)]:
    for q in d:
        if q not in global_d:
            continue
        gr, ga = mr(global_d, q), ma(global_d, q)
        rr, ra = mr(d, q), ma(d, q)
        if ra and not ga:
            bump(q, 10, f"{label}:Acc+")
        if rr and not gr:
            bump(q, 6, f"{label}:Recall+")
        if ra and ga and rr and gr:
            bump(q, 2, f"{label}:both_ok")
        if ra is False and ga:
            bump(q, -8, f"{label}:Acc-")
        if rr is False and gr:
            bump(q, -4, f"{label}:Recall-")

for q in set(retry) & set(soft):
    if ma(retry, q) and not ma(soft, q):
        bump(q, 4, "retry>soft:Acc+")
    if mr(retry, q) and not mr(soft, q):
        bump(q, 2, "retry>soft:Recall+")

ranked = sorted(all498, key=lambda q: (-score[q], int(q)))
positive = [q for q in ranked if score[q] > 0]
neutral = [q for q in ranked if score[q] == 0]
negative = [q for q in ranked if score[q] < 0]
ordered = positive + neutral + negative

manifest = {
    "source": "498 qids ordered by min2 historical uplift (retry65 + min2_43 + soft36 vs global)",
    "n": len(ordered),
    "qids": ordered,
    "priority_head_n": len(positive),
    "priority_head_qids": positive[:60],
    "deprioritized_tail_n": len(negative),
}
scores_out = {
    "scoring": "Acc+10 Recall+6 both_ok+2 retry>soft Acc+4 Recall+2; penalties Acc-8 Recall-4",
    "sources": {"retry65": len(retry), "min2_43": len(min2_43), "soft36": len(soft)},
    "per_qid": {q: {"score": score[q], "reasons": reason[q]} for q in ordered if score[q] or reason[q]},
    "top40": [{"qid": q, "score": score[q], "reasons": reason[q][:4]} for q in positive[:40]],
}
(PLAN / "qids_min2_full498_priority_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
(PLAN / "qids_min2_full498_priority_scores.json").write_text(json.dumps(scores_out, indent=2) + "\n")
print(f"wrote priority manifest n={len(ordered)} head={len(positive)} tail_neg={len(negative)}")
print(f"first20: {ordered[:20]}")
PY
}

cmd_prepare_shards_priority() {
  python3 -u - <<PY
import json
from pathlib import Path

manifest_path = Path("${PRIORITY_MANIFEST}")
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
        "description": f"min2 priority round-robin shard {i}/{n_shards} ({len(chunk)} qids)",
        "shard": i,
        "n_shards": n_shards,
        "rollouts": roll_outs,
        "qids": chunk,
    }
    path = out_dir / f"shard{i}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"[prepare-shards-priority] shard {i}: {len(chunk)} qids, first5={chunk[:5]} -> {path}")

(out_dir / "manifest.json").write_text(
    json.dumps({"n_total": len(qids), "n_shards": n_shards, "qids": qids, "split": "round_robin_priority"}, indent=2) + "\n"
)
print(f"[prepare-shards-priority] {len(qids)} qids round-robin -> {n_shards} shards under {out_dir}")
PY
}

cmd_start() {
  [[ -f "${PRIORITY_MANIFEST}" ]] || cmd_prepare_manifest_priority
  export QIDS_FILE="${PRIORITY_MANIFEST}"
  export MANIFEST="${PRIORITY_MANIFEST}"
  echo "[min2-full498] min_subq=${MCTS_DECOMPOSE_MIN_SUBQUESTIONS} selector=${MCTS_SELECTOR_STRATEGY} confidence=${MCTS_CONFIDENCE_MODE} resume=${RESUME}"
  echo "[min2-full498] priority manifest -> ${PRIORITY_MANIFEST}"
  cmd_prepare_shards_priority
  "${SCRIPT}" start-sharded
}

cmd_full() {
  {
    echo "[min2-full498] start $(date -Iseconds)"
    cmd_start
    target="$(python3 -c "import json; print(len(json.load(open('${MANIFEST}'))['qids']))")"
    _wait_shards_done "min2-full498" "${target}"
    "${SCRIPT}" merge-shards
    echo "[min2-full498] merge done -> ${JSON_OUT} $(date -Iseconds)"
    cmd_report
  } 2>&1 | tee -a "${ORCH_LOG}"
}

cmd_report() {
  MANIFEST_PATH="${MANIFEST}" MIN2_JSON="${JSON_OUT}" python3 -u - <<'PY'
import json, os
from pathlib import Path
from statistics import mean

OUT = Path("workflows/mcts_v4/test/out/cte_diverse")
manifest = json.loads(Path(os.environ["MANIFEST_PATH"]).read_text())
qids = manifest["qids"]

global_d = json.loads((OUT / "v4_colbind_v2_dual03_global_filter_498q_rollouts12.json").read_text())

min2_p = Path(os.environ["MIN2_JSON"])
min2 = json.loads(min2_p.read_text()) if min2_p.exists() else {}

def met(data, qs):
    done = [q for q in qs if q in data]
    r = sum(any(a.get("is_correct") for a in (data[q].get("all_sqls_with_attributes") or [])) for q in done)
    h = sum(bool((data[q].get("stats") or {}).get("gold_match")) for q in done)
    sq = [len(data[q].get("sub_questions") or []) for q in done if data[q].get("sub_questions")]
    return len(done), r, h, mean(sq) if sq else None

print(f"=== min2 full498 ({len(qids)} qids) ===")
for name, d in [("global_baseline", global_d), ("min2sq", min2)]:
    n, r, h, sq = met(d, qids)
    if name == "min2sq" and n == 0:
        print(f"{name:18} (not merged yet)")
        continue
    sqs = f"{sq:.2f}" if sq else "—"
    print(f"{name:18} n={n:3d} Recall={r:3d}/{n} ({100*r/max(n,1):5.1f}%)  Acc={h:3d}/{n} ({100*h/max(n,1):5.1f}%)  avg_sq={sqs}")

if min2:
    n, r, h, _ = met(global_d, qids)
    _, r2, h2, _ = met(min2, qids)
    print(f"Δ vs global: Recall {r2-r:+d}/{n}  Acc {h2-h:+d}/{n}")
    sq_m = [len(min2[q].get("sub_questions") or []) for q in qids if q in min2]
    print(f"still 1 subq: {sum(1 for x in sq_m if x==1)}/{len(sq_m)}")
PY
}

case "${1:-}" in
  prepare-manifest) cmd_prepare_manifest ;;
  prepare-manifest-priority) cmd_prepare_manifest_priority ;;
  prepare-shards-priority) cmd_prepare_shards_priority ;;
  start) cmd_start ;;
  full) cmd_full ;;
  merge) "${SCRIPT}" merge-shards ;;
  report) cmd_report ;;
  status)
    OUT_DIR="${OUT_DIR}" JSON_OUT="${JSON_OUT}" SHARD_BASENAME="${SHARD_BASENAME}" ROLL_OUTS="${ROLL_OUTS}" QIDS_SHARD_DIR="${QIDS_SHARD_DIR}" "${SCRIPT}" status || true
    ;;
  stop) MODEL_TAG="${MODEL_TAG}" ROLL_OUTS="${ROLL_OUTS}" "${SCRIPT}" stop all || true ;;
  *)
    echo "Usage: $0 {prepare-manifest|prepare-manifest-priority|prepare-shards-priority|start|full|merge|report|status|stop}"
    exit 1
    ;;
esac
