#!/usr/bin/env python3
"""30q diverse-C report: gates, timing, marginal cluster gain per LLM call."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

import selector_replay as sr  # noqa: E402

MANIFEST = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_qwen32/qids_30_manifest.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
PPL = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"
DIVERSE_JSON = ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_30q_coder_rollouts8.json"
BASELINE_JSON = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/v4_calib_30q_coder_rollouts8.json"
LOG_GLOB = "workflows/mcts_v4/test/out/cte_diverse/v4_diverse_c_30q_coder_rollouts8_w*.log"
REPORT = ROOT / "workflows/mcts_v4/test/out/cte_diverse/analysis/cte_diverse_c_pipeline_30q.md"


def _load_gold_sqls() -> dict:
    raw = json.loads(GOLD.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    out = {}
    for item in raw:
        qid = str(item.get("question_id", item.get("qid", "")))
        sql = item.get("SQL") or item.get("sql") or item.get("gold_sql")
        if qid and sql:
            out[qid] = sql
    return out


def _hit_recall(data: dict, qids: list, gold_sqls: dict, qid_to_db: dict, cache: dict):
    hit1 = recall = 0
    for qid in qids:
        rec = data.get(qid) or {}
        rss = rec.get("rollout_stats") or []
        sql = (rec.get("optimal_sql") or rec.get("sql") or "").strip()
        if not sql and rss:
            sql = sr.select_sql("R2_max_cluster_visit", rss)
        ok = sr.eval_hit1_sql(sql, qid, gold_sqls, qid_to_db, cache) if sql else False
        if ok:
            hit1 += 1
        any_ok = any(s.get("is_correct") for s in (rec.get("all_sqls_with_attributes") or []))
        if not any_ok and rss:
            gs = gold_sqls.get(qid, "")
            db = qid_to_db.get(qid, "")
            if gs and db:
                for v in rss:
                    for info in v.get("all_sql_variants") or []:
                        s = (info.get("sql") or "").strip()
                        if s and sr.eval_hit1_sql(s, qid, gold_sqls, qid_to_db, cache):
                            any_ok = True
                            break
                    if any_ok:
                        break
        if any_ok:
            recall += 1
    return hit1, recall


def _timing_from_json(data: dict, qids: list) -> dict:
    totals, rollouts, sql_gens, db_execs = [], [], [], []
    for qid in qids:
        t = (data.get(qid) or {}).get("stats", {}).get("timing") or {}
        if t.get("total_s"):
            totals.append(float(t["total_s"]))
        if t.get("rollout_s"):
            rollouts.append(float(t["rollout_s"]))
        if t.get("sql_gen_s"):
            sql_gens.append(float(t["sql_gen_s"]))
        if t.get("db_exec_s"):
            db_execs.append(float(t["db_exec_s"]))
    def _agg(xs):
        return {"mean": mean(xs), "median": median(xs), "sum": sum(xs)} if xs else {}
    return {
        "total_s": _agg(totals),
        "rollout_s": _agg(rollouts),
        "sql_gen_s": _agg(sql_gens),
        "db_exec_s": _agg(db_execs),
    }


def _parse_log_wall_times() -> dict:
    """Wall clock per shard + per-qid from log timestamps."""
    shard_pat = re.compile(r"^======== clarify A0 MCTS (\S+) ========")
    done_pat = re.compile(r"^======== MCTS done (\S+)")
    qid_pat = re.compile(r">>> 样本#\d+ qid=(\d+)")
    qid_ts_pat = re.compile(r"^>>> 样本#\d+ qid=(\d+).*")  # same line, use prev ts
    ts_line = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d:+]+)")

    shards = {}
    qid_order = []
    current_qid = None
    current_ts = None

    for log_path in sorted(ROOT.glob(LOG_GLOB)):
        shard = log_path.stem.split("_w")[-1].replace(".log", "")
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = end = None
        for line in lines:
            m = shard_pat.search(line)
            if m:
                start = m.group(1)
            m = done_pat.search(line)
            if m:
                end = m.group(1)
            m = qid_pat.search(line)
            if m:
                if current_qid and current_ts:
                    qid_order.append((current_qid, current_ts, shard))
                current_qid = m.group(1)
                # timestamp often on previous line in tee output — use MCTS start for first
                current_ts = start
        if current_qid and current_ts:
            qid_order.append((current_qid, current_ts, shard))
        if start and end:
            try:
                t0 = datetime.fromisoformat(start)
                t1 = datetime.fromisoformat(end)
                shards[shard] = (t1 - t0).total_seconds()
            except ValueError:
                shards[shard] = None
    return {"shard_wall_s": shards, "qid_starts": qid_order}


def _marginal_struct_per_call(data: dict, qids: list) -> dict:
    """
    For each diverse call (temp 0.3/0.6/0.9), measure marginal NEW structure signatures.
    """
    per_call_raw = defaultdict(list)      # call_idx -> n_candidates this call
    per_call_marginal = defaultdict(list)   # call_idx -> new struct sigs vs prior calls
    per_call_cumul = defaultdict(list)      # call_idx -> cumulative unique struct after k calls
    exec_buckets = []  # final n_candidates after struct dedupe (pre-exec pool size)

    for qid in qids:
        for tr in (data.get(qid) or {}).get("decompose_expand_traces") or []:
            if tr.get("mode") != "C" or tr.get("diverse_fallback"):
                continue
            audits = tr.get("call_audits") or []
            seen = set()
            for i, audit in enumerate(audits):
                sigs = [c.get("structure_sig") for c in (audit.get("candidates") or []) if c.get("structure_sig")]
                per_call_raw[i].append(len(sigs))
                new = [s for s in sigs if s not in seen]
                per_call_marginal[i].append(len(new))
                seen.update(sigs)
                per_call_cumul[i].append(len(seen))
            exec_buckets.append(tr.get("n_candidates", 0))

    def _m(d):
        return {k: {"mean": mean(v), "median": median(v), "n": len(v)} for k, v in sorted(d.items())}

    # marginal efficiency: new_sigs / n_requested (5)
    eff = {}
    for i, marg in per_call_marginal.items():
        eff[i] = mean(marg) / 5.0 if marg else 0.0

    return {
        "raw_candidates_per_call": _m(per_call_raw),
        "marginal_new_struct_per_call": _m(per_call_marginal),
        "cumulative_unique_struct_per_call": _m(per_call_cumul),
        "marginal_efficiency": eff,
        "mean_exec_pool_after_dedupe": mean(exec_buckets) if exec_buckets else 0.0,
        "n_events": sum(len(v) for v in per_call_raw.values()) // max(len(per_call_raw), 1),
    }


def _parse_exec_buckets_from_logs() -> list:
    """Parse [去重统计] 总桶数 from logs — exec-result clusters after expand."""
    pat = re.compile(r"\[去重统计\] 总桶数: (\d+)")
    buckets = []
    for log_path in sorted(ROOT.glob(LOG_GLOB)):
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = pat.search(line)
            if m:
                buckets.append(int(m.group(1)))
    return buckets


def _marginal_exec_buckets(buckets: list) -> dict:
    """Within each qid, bucket counts appear in expand order — group by qid heuristically."""
    # buckets are global sequential; approximate marginal by sliding groups of 3 expands per sub_q
    if not buckets:
        return {}
    # For diminishing: compare first vs last third of bucket sequence (coarse)
    n = len(buckets)
    thirds = [buckets[: n // 3], buckets[n // 3 : 2 * n // 3], buckets[2 * n // 3 :]]
    return {
        "all_mean": mean(buckets),
        "all_median": median(buckets),
        "n_expand_events": n,
        "first_third_mean": mean(thirds[0]) if thirds[0] else 0,
        "middle_third_mean": mean(thirds[1]) if thirds[1] else 0,
        "last_third_mean": mean(thirds[2]) if thirds[2] else 0,
    }


def main():
    diverse_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DIVERSE_JSON
    baseline_path = Path(sys.argv[2]) if len(sys.argv) > 2 else BASELINE_JSON

    qids = json.loads(MANIFEST.read_text(encoding="utf-8"))["qids"]
    gold_sqls = _load_gold_sqls()
    qid_to_db = {str(x["question_id"]): x.get("db", "") for x in json.loads(PPL.read_text(encoding="utf-8"))}
    cache: dict = {}

    diverse = json.loads(diverse_path.read_text(encoding="utf-8"))
    d_hit, d_rec = _hit_recall(diverse, qids, gold_sqls, qid_to_db, cache)
    d_time = _timing_from_json(diverse, qids)
    marginal = _marginal_struct_per_call(diverse, qids)

    traces = []
    fallbacks = 0
    for qid in qids:
        for tr in (diverse.get(qid) or {}).get("decompose_expand_traces") or []:
            traces.append(tr)
            if tr.get("diverse_fallback"):
                fallbacks += 1
    fb_rate = fallbacks / len(traces) if traces else 0.0
    mean_cands = mean([t.get("n_candidates", 0) for t in traces]) if traces else 0.0

    b_hit = b_rec = None
    b_time = {}
    if baseline_path.is_file():
        base = json.loads(baseline_path.read_text(encoding="utf-8"))
        b_hit, b_rec = _hit_recall(base, qids, gold_sqls, qid_to_db, cache)
        b_time = _timing_from_json(base, qids)

    log_times = _parse_log_wall_times()
    exec_buckets = _parse_exec_buckets_from_logs()
    bucket_stats = _marginal_exec_buckets(exec_buckets)

    n = len(qids)
    gate_recall = d_rec >= (b_rec if b_rec is not None else 0)
    gate_hit = d_hit >= (b_hit - 2 if b_hit is not None else 0)
    gate_fb = fb_rate <= 0.2
    overall = gate_recall and gate_hit and gate_fb

    lines = [
        "# Diverse mode C — 30q pipeline sanity",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Diverse JSON: `{diverse_path.relative_to(ROOT)}`",
        f"Baseline: `{baseline_path.relative_to(ROOT)}`",
        "",
        "## Gate metrics",
        "",
        "| metric | calib 30q | diverse-C 30q |",
        "|---|---:|---:|",
        f"| recall (exec-equiv) | {b_rec}/{n} | **{d_rec}/{n}** |" if b_rec is not None else f"| recall | — | {d_rec}/{n} |",
        f"| Hit@1 (R2, exec-equiv) | {b_hit}/{n} | **{d_hit}/{n}** |" if b_hit is not None else f"| Hit@1 | — | {d_hit}/{n} |",
        f"| mean CTE candidates / decompose node | ~5 | **{mean_cands:.2f}** |",
        f"| fallback rate (diverse→temp) | — | **{fb_rate:.1%}** ({fallbacks}/{len(traces)}) |",
        "",
        f"- recall >= baseline: {'✓' if gate_recall else '✗'}",
        f"- Hit@1 >= baseline - 2: {'✓' if gate_hit else '✗'} (need >= {(b_hit - 2) if b_hit is not None else '?'})",
        f"- fallback <= 20%: {'✓' if gate_fb else '✗'}",
        "",
        f"**Overall: {'PASS' if overall else 'FAIL'}**",
        "",
        "## 时间统计 (JSON stats.timing, 每题 solve)",
        "",
    ]
    if d_time.get("total_s"):
        bt = b_time.get("total_s", {})
        lines.extend([
            "| 指标 | diverse-C mean | diverse-C sum | calib mean | Δ mean |",
            "|---|---:|---:|---:|---:|",
            f"| total_s | {d_time['total_s']['mean']:.1f} | {d_time['total_s']['sum']:.0f} | "
            f"{bt.get('mean', 0):.1f} | {d_time['total_s']['mean'] - bt.get('mean', 0):+.1f} |",
            f"| rollout_s | {d_time['rollout_s']['mean']:.1f} | {d_time['rollout_s']['sum']:.0f} | "
            f"{b_time.get('rollout_s', {}).get('mean', 0):.1f} | — |",
            f"| sql_gen_s | {d_time['sql_gen_s']['mean']:.1f} | — | "
            f"{b_time.get('sql_gen_s', {}).get('mean', 0):.1f} | — |",
            f"| db_exec_s | {d_time['db_exec_s']['mean']:.2f} | — | "
            f"{b_time.get('db_exec_s', {}).get('mean', 0):.2f} | — |",
            "",
        ])
    if log_times.get("shard_wall_s"):
        lines.append("### 分片 wall clock (log 起止)")
        lines.append("")
        total_wall = 0
        for shard, secs in sorted(log_times["shard_wall_s"].items()):
            if secs:
                lines.append(f"- w{shard}: **{secs/60:.1f} min** ({secs:.0f}s)")
                total_wall = max(total_wall, secs)
        lines.append(f"- 并行总耗时（最慢片）≈ **{total_wall/60:.1f} min**")
        lines.append("")

    lines.extend([
        "## Cluster 边际递减 — diverse 三次 LLM call（结构签名）",
        "",
        "每 decompose expand 事件内，按 temp 调用顺序 (0.3 → 0.6 → 0.9) 统计：",
        "",
        "| call | temp | mean raw CTE | mean **新增** struct | mean 累计 unique struct | 边际效率 (new/5) |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    temps = [0.3, 0.6, 0.9]
    raw = marginal["raw_candidates_per_call"]
    marg = marginal["marginal_new_struct_per_call"]
    cum = marginal["cumulative_unique_struct_per_call"]
    eff = marginal["marginal_efficiency"]
    for i in range(3):
        lines.append(
            f"| {i+1} | {temps[i]} | {raw.get(i, {}).get('mean', 0):.2f} | "
            f"**{marg.get(i, {}).get('mean', 0):.2f}** | {cum.get(i, {}).get('mean', 0):.2f} | "
            f"{eff.get(i, 0):.1%} |"
        )
    lines.extend([
        "",
        f"- struct dedupe 后进执行池 mean: **{marginal['mean_exec_pool_after_dedupe']:.2f}** CTE/expand",
        "",
        "**解读**：若边际递减成立，call 3 的「新增 struct」应明显低于 call 1。",
        "",
        "## Cluster 边际递减 — 执行结果分桶（log `[去重统计]`）",
        "",
    ])
    if bucket_stats:
        lines.extend([
            f"- expand 事件数（log 桶行）: **{bucket_stats['n_expand_events']}**",
            f"- 每桶 mean / median: **{bucket_stats['all_mean']:.2f}** / {bucket_stats['all_median']:.1f}",
            f"- 前1/3 expand mean 桶数: **{bucket_stats['first_third_mean']:.2f}**",
            f"- 中1/3 expand mean 桶数: **{bucket_stats['middle_third_mean']:.2f}**",
            f"- 后1/3 expand mean 桶数: **{bucket_stats['last_third_mean']:.2f}**",
            "",
            "（执行分桶 = 真正进 MCTS 树的 cluster 数；随搜索深入，候选间结果重复率通常上升。）",
            "",
        ])

    # diminishing ratio call3/call1
    m0 = marg.get(0, {}).get("mean", 0) or 1e-9
    m2 = marg.get(2, {}).get("mean", 0)
    lines.append(f"**结构边际比** call3/call1 新增 struct = **{m2/m0:.2f}×**")
    lines.append("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
