#!/usr/bin/env bash
# w1 剩余题 4 卡并行续跑（w0/w2/w3 已完成，勿动）
#
#   bash workflows/mcts_v4/test/out/cte_diverse/finish_w1_remainder_4gpu.sh both
#   bash workflows/mcts_v4/test/out/cte_diverse/finish_w1_remainder_4gpu.sh status
#   bash workflows/mcts_v4/test/out/cte_diverse/finish_w1_remainder_4gpu.sh merge
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../" && pwd)"
cd "$ROOT_DIR"

export OUT_DIR="workflows/mcts_v4/test/out/cte_diverse"
export PPL_FILE="workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"
export GOLD_FILE="workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
export TASK_TIMEOUT="${TASK_TIMEOUT:-600}"
export RESUME=1
export MAX_WORKERS=1
export PARALLEL_WORKERS=5
export SKIP_VLLM_WAIT=1
export MODEL_TAG=coder
export N_SUB=4
export BASE_PORT=8000
export PORT_STRIDE=100

PORTS=(8000 8100 8200 8300)

_prepare_remainder_qids() {
  local tag="$1" shard_dir="$2" basename="$3"
  python3 - <<PY
import json
from pathlib import Path

shard = json.loads(Path("${shard_dir}/shard1.json").read_text())
target = [str(q) for q in shard["qids"]]
main = Path("${OUT_DIR}/${basename}_w1.json")
done = set(json.loads(main.read_text()).keys()) if main.exists() else set()
missing = [q for q in target if q not in done]
out_dir = Path("${OUT_DIR}/qids_w1_remainder_${tag}")
out_dir.mkdir(parents=True, exist_ok=True)
n_sub = int("${N_SUB}")
size = (len(missing) + n_sub - 1) // n_sub if missing else 0
for i in range(n_sub):
    chunk = missing[i * size : (i + 1) * size] if size else []
    p = out_dir / f"sub{i}.json"
    p.write_text(json.dumps({"qids": chunk, "n_total": len(chunk), "sub": i}, indent=2), encoding="utf-8")
    print(f"[${tag}] sub{i}: {len(chunk)} qids -> {p}")
print(f"[${tag}] w1 missing total: {len(missing)}")
if missing:
    print(f"  qids: {missing}")
PY
}

_stop_w1() {
  local roll="$1"
  screen -S "clarify_a0_coder_r${roll}_w1" -X quit 2>/dev/null || true
  for i in $(seq 0 $((N_SUB - 1))); do
    screen -S "clarify_a0_coder_r${roll}_w1sub${i}" -X quit 2>/dev/null || true
  done
}

_launch_sub() {
  local roll="$1" sv="$2" selector="$3" tag="$4" basename="$5" qids_sub="$6" sub_i="$7" port="$8"
  local screen_name="clarify_a0_coder_r${roll}_w1sub${sub_i}"
  local json_out="${OUT_DIR}/${basename}_w1_sub${sub_i}.json"
  local sql_out="${OUT_DIR}/${basename}_w1_sub${sub_i}.txt"
  local log="${OUT_DIR}/${basename}_w1_sub${sub_i}.log"

  if screen -ls 2>/dev/null | grep -qE "[[:space:]]+[0-9]+\.${screen_name}[[:space:]]"; then
    echo "[skip] ${screen_name} already running"
    return
  fi
  [[ -s "${qids_sub}" ]] || { echo "[skip] ${screen_name} empty qids"; return; }

  echo "[launch] ${tag} sub${sub_i} port=${port} qids=$(python3 -c "import json;print(len(json.load(open('${qids_sub}'))['qids']))")"
  screen -dmS "${screen_name}" bash -lc "
set -euo pipefail
export RUN_INLINE=1 SHARD_MODE=1 MULTI_BASE_URLS=
export MCTS_USE_SIGNATURE_V2='1' MCTS_SELECTOR_STRATEGY='${selector}' MCTS_REWARD_CALIBRATED='1'
export MCTS_USE_DECOMPOSE_FLOW='1' DECOMPOSE_STRATEGY='S2' MCTS_STRATEGY_MODE='FORCE_S2'
export MAX_CTE_NODES='5' MCTS_CTE_DIVERSE_PROMPT='1' MCTS_CTE_DIVERSE_N='3'
export MCTS_CTE_DIVERSE_TEMPS='0.3,0.6,0.9' MCTS_SQL_GEN_TEMPS='0.3,0.6,0.9' MCTS_SKIP_M_VERIFY='1'
export OUT_DIR='${OUT_DIR}' ROOT_DIR='${ROOT_DIR}'
export CONDA_BASE='${CONDA_BASE:-/hpc2hdd/home/sshen190/miniconda3}' CONDA_ENV='${CONDA_ENV:-base}'
export VLLM_HOST='127.0.0.1' VLLM_PORT='${port}' MODEL_TAG='coder'
export ROLL_OUTS='${roll}' NUM_SQL_VARIANTS='${sv}' RESUME='1' RANDOM_SEED='20240601'
export MAX_WORKERS='1' PARALLEL_WORKERS='5' TASK_TIMEOUT='${TASK_TIMEOUT}'
export JSON_OUT='${json_out}' SQL_OUT='${sql_out}' LOG='${log}'
export PPL_FILE='${PPL_FILE}' GOLD_FILE='${GOLD_FILE}' QIDS_FILE='${qids_sub}'
export SKIP_VLLM_WAIT=1
'${ROOT_DIR}/scripts/run_clarify_a0_30q.sh' start-mcts
"
}

_run_experiment() {
  local tag="$1" roll="$2" sv="$3" selector="$4" shard_dir="$5" basename="$6"
  echo ""
  echo "======== ${tag}: prepare w1 remainder ========"
  _prepare_remainder_qids "${tag}" "${shard_dir}" "${basename}"
  echo "======== ${tag}: stop old w1 ========"
  _stop_w1 "${roll}"
  pkill -f "test_mcts.py.*${basename}_w1\\.json" 2>/dev/null || true
  sleep 2
  echo "======== ${tag}: launch ${N_SUB} sub-workers ========"
  local qdir="${OUT_DIR}/qids_w1_remainder_${tag}"
  for i in $(seq 0 $((N_SUB - 1))); do
    _launch_sub "${roll}" "${sv}" "${selector}" "${tag}" "${basename}" \
      "${qdir}/sub${i}.json" "${i}" "${PORTS[$i]}"
  done
}

_wait_experiment() {
  local roll="$1" tag="$2" basename="$3"
  echo "======== ${tag}: waiting sub-workers ========"
  while true; do
    local running=0
    for i in $(seq 0 $((N_SUB - 1))); do
      if screen -ls 2>/dev/null | grep -q "clarify_a0_coder_r${roll}_w1sub${i}"; then
        running=$((running + 1))
      fi
    done
    echo "[wait ${tag}] screens=${running}  ($(date +%H:%M:%S))"
    [[ "${running}" -eq 0 ]] && break
    sleep 30
  done
  _merge_one "${tag}" "${basename}"
}

_merge_one() {
  local tag="$1" basename="$2"
  python3 - <<PY
import json
from pathlib import Path

main_p = Path("${OUT_DIR}/${basename}_w1.json")
base = json.loads(main_p.read_text()) if main_p.exists() else {}
n_sub = int("${N_SUB}")
added = 0
for i in range(n_sub):
    sp = Path("${OUT_DIR}/${basename}_w1_sub"+str(i)+".json")
    if not sp.exists():
        continue
    sub = json.loads(sp.read_text())
    for k, v in sub.items():
        if k not in base:
            added += 1
        base[k] = v
main_p.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[merge ${tag}] w1.json total={len(base)} newly_merged={added}")
PY
}

cmd_status() {
  python3 - <<'PY'
import json, subprocess
from pathlib import Path
from datetime import datetime

OUT = Path("workflows/mcts_v4/test/out/cte_diverse")
PORTS = [8000, 8100, 8200, 8300]
N_SUB = 4
scr = subprocess.run(["screen", "-ls"], capture_output=True, text=True).stdout

def mtime(p: Path) -> str:
    if not p.exists():
        return "-"
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")

specs = [
    ("B′", "b2", 12, "v4_diverse_b2_n3_sv5_498q_coder_rollouts12", "qids_shards_b2_n3_sv5_498_r12"),
    ("B″", "b2pp", 15, "v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15", "qids_shards_b2pp_n3_sv3_498_r15"),
]

print("======== w1 四卡续跑进度 ========")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

for label, tag, roll, base, sd in specs:
    shard1 = json.loads((OUT / sd / "shard1.json").read_text())
    w1_target = set(str(q) for q in shard1["qids"])

    main_p = OUT / f"{base}_w1.json"
    main_done = set(json.loads(main_p.read_text()).keys()) if main_p.exists() else set()

    # 本批 remainder 分片
    rem_dir = OUT / f"qids_w1_remainder_{tag}"
    batch_qids = set()
    sub_assign = {}
    for i in range(N_SUB):s
        rp = rem_dir / f"sub{i}.json"
        if rp.exists():
            qs = [str(q) for q in json.loads(rp.read_text()).get("qids", [])]
            sub_assign[i] = set(qs)
            batch_qids |= set(qs)

    sub_done = {}
    sub_unmerged = set()
    for i in range(N_SUB):
        sp = OUT / f"{base}_w1_sub{i}.json"
        if sp.exists():
            sub_done[i] = set(json.loads(sp.read_text()).keys())
            sub_unmerged |= sub_done[i] - main_done
        else:
            sub_done[i] = set()

    effective_w1 = main_done | sub_unmerged
    w1_miss = sorted(w1_target - effective_w1, key=int)
    batch_done = batch_qids & effective_w1
    batch_miss = sorted(batch_qids - effective_w1, key=int)

    # 全 498（四 shard）
    total498 = 0
    done498 = 0
    for wi in range(4):
        sq = json.loads((OUT / sd / f"shard{wi}.json").read_text())["qids"]
        jp = OUT / f"{base}_w{wi}.json"
        dn = set(json.loads(jp.read_text()).keys()) if jp.exists() else set()
        if wi == 1:
            dn = effective_w1 & set(str(q) for q in sq)
        total498 += len(sq)
        done498 += len(dn & set(str(q) for q in sq))

    print(f"{'─'*56}")
    print(f"{label}  全量 498: {done498}/{total498}  |  w1: {len(effective_w1)}/{len(w1_target)}")
    print(f"  w1.json 已 merge: {len(main_done)}  |  sub 未 merge: {len(sub_unmerged)}")
    if batch_qids:
        print(f"  本批四卡任务: {len(batch_done)}/{len(batch_qids)} 完成")
    print(f"  w1 还差: {len(w1_miss)} 题")
    print()
    print(f"  {'sub':<5} {'端口':<6} {'screen':<6} {'本批':<10} {'sub.json':<12} {'mtime':<12} 详情")
    for i in range(N_SUB):
        assigned = sub_assign.get(i, set())
        done_in_sub = sub_done.get(i, set()) & assigned
        run = f"r{roll}_w1sub{i}" in scr
        sp = OUT / f"{base}_w1_sub{i}.json"
        n_json = len(sub_done.get(i, set()))
        detail = ""
        if assigned:
            pending = sorted(assigned - done_in_sub, key=int)
            done_list = sorted(done_in_sub, key=int)
            if done_list:
                detail += f"done={done_list}"
            if pending:
                detail += f" pending={pending}"
        elif not rem_dir.exists():
            detail = "(无 remainder 分片)"
        else:
            detail = "(本批无题)"
        print(
            f"  sub{i:<3} {PORTS[i]:<6} {'RUN' if run else 'off':<6} "
            f"{len(done_in_sub)}/{len(assigned):<8} {n_json:<12} {mtime(sp):<12} {detail}"
        )
    if w1_miss:
        print(f"\n  w1 仍缺: {w1_miss}")
    print()

# orphan screens
orph = []
for line in scr.splitlines():
    if "clarify_a0_coder_r" in line and "w1" in line and "w1sub" not in line:
        orph.append(line.strip())
if orph:
    print("⚠️  旧版单卡 w1 screen 仍在（建议 stop）:")
    for o in orph:
        print(f"    {o}")
PY
}

cmd_merge() {
  _merge_one "b2" "v4_diverse_b2_n3_sv5_498q_coder_rollouts12"
  _merge_one "b2pp" "v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15"
}

case "${1:-both}" in
  b2)
    _run_experiment "b2" 12 5 R2 \
      "${OUT_DIR}/qids_shards_b2_n3_sv5_498_r12" \
      "v4_diverse_b2_n3_sv5_498q_coder_rollouts12"
    _wait_experiment 12 "b2" "v4_diverse_b2_n3_sv5_498q_coder_rollouts12"
    ;;
  b2pp)
    _run_experiment "b2pp" 15 3 R3 \
      "${OUT_DIR}/qids_shards_b2pp_n3_sv3_498_r15" \
      "v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15"
    _wait_experiment 15 "b2pp" "v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15"
    ;;
  both)
    # B′ 仅 14 题：先 4 卡跑完（约几分钟级），再 B″ 4 卡
    _run_experiment "b2" 12 5 R2 \
      "${OUT_DIR}/qids_shards_b2_n3_sv5_498_r12" \
      "v4_diverse_b2_n3_sv5_498q_coder_rollouts12"
    _wait_experiment 12 "b2" "v4_diverse_b2_n3_sv5_498q_coder_rollouts12"
    _run_experiment "b2pp" 15 3 R3 \
      "${OUT_DIR}/qids_shards_b2pp_n3_sv3_498_r15" \
      "v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15"
    _wait_experiment 15 "b2pp" "v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15"
    echo ""
    echo "======== final w1 counts ========"
    cmd_status
    ;;
  merge) cmd_merge ;;
  status)
    cmd_status
    echo "提示: sub 跑完但 w1.json 未涨 → 运行: $0 merge"
    ;;
  launch-only)
    # 只启动不 wait（both 实验并行占满 4 卡时勿用）
    _run_experiment "b2" 12 5 R2 "${OUT_DIR}/qids_shards_b2_n3_sv5_498_r12" "v4_diverse_b2_n3_sv5_498q_coder_rollouts12"
    _run_experiment "b2pp" 15 3 R3 "${OUT_DIR}/qids_shards_b2pp_n3_sv3_498_r15" "v4_diverse_b2pp_n3_sv3_498q_coder_rollouts15"
    ;;
  *)
    echo "Usage: $0 {both|b2|b2pp|launch-only|merge|status}"
    ;;
esac
