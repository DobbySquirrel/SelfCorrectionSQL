#!/usr/bin/env python3
"""Audit Hit@1 eval paths: gold_match vs stored sql vs R2 replay (D2b口径).

Phased output + ThreadPool + tqdm/periodic progress for tail -f.
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[6]
PAR = ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PAR))

import _loaders as pld  # noqa: E402
from selector_replay import build_clusters, pick_r2  # noqa: E402

OUT_MD = Path(__file__).resolve().parent / "eval_pipeline_audit.md"
OUT_JSON = Path(__file__).resolve().parent / "eval_pipeline_audit.json"
CALIB_498 = pld.OUT_BASE / "v4_calib_498q_coder_rollouts8.json"

WORKERS = int(os.environ.get("AUDIT_WORKERS", "8"))
EVAL_TIMEOUT = float(os.environ.get("AUDIT_EVAL_TIMEOUT", "120"))


def log(msg: str) -> None:
    print(msg, flush=True)


def _eval_one(task: Tuple[str, str, str, str]) -> Tuple[str, str, bool]:
    qid, sql, gs, db = task
    if not sql or not gs or not db:
        return qid, sql, False
    from workflows.mcts_v1.test.test_mcts import build_db_connector, compare_with_gold

    conn = build_db_connector(db)
    try:
        return qid, sql, bool(compare_with_gold(sql, gs, conn))
    finally:
        conn.disconnect()


def collect_rows(name: str, data: dict) -> List[dict]:
    rows = []
    for qid in sorted(data.keys(), key=int):
        rec = data[qid]
        rss = rec.get("rollout_stats") or []
        stored = (rec.get("sql") or "").strip()
        r2 = pick_r2(build_clusters(rss)).strip()
        rows.append(
            {
                "dataset": name,
                "qid": qid,
                "gold_match": bool((rec.get("stats") or {}).get("gold_match")),
                "stored_sql": stored,
                "r2_sql": r2,
                "sql_eq_r2": stored == r2,
            }
        )
    return rows


def build_tasks(rows: List[dict], gold_sqls: dict, qid_to_db: dict) -> List[Tuple[str, str, str, str]]:
    seen: Set[Tuple[str, str]] = set()
    tasks: List[Tuple[str, str, str, str]] = []
    for row in rows:
        qid = row["qid"]
        gs = gold_sqls.get(qid, "")
        db = qid_to_db.get(qid, "")
        for sql in (row["stored_sql"], row["r2_sql"]):
            if not sql:
                continue
            key = (qid, sql)
            if key in seen:
                continue
            seen.add(key)
            tasks.append((qid, sql, gs, db))
    return tasks


def run_parallel_eval(tasks: List[Tuple[str, str, str, str]], label: str) -> Dict[Tuple[str, str], bool]:
    import concurrent.futures as cf

    cache: Dict[Tuple[str, str], bool] = {}
    n = len(tasks)
    if not n:
        return cache
    log(f"[audit] {label}: eval {n} unique (qid,sql) workers={WORKERS} timeout={EVAL_TIMEOUT}s")
    t0 = datetime.now()
    done = 0
    timeouts = 0
    report = max(10, n // 20)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_eval_one, t): t for t in tasks}
        start = {f: t0 for f in futs}
        pending = set(futs)
        while pending:
            finished, _ = cf.wait(pending, timeout=2.0, return_when=cf.FIRST_COMPLETED)
            now = datetime.now()
            for fut in list(finished):
                pending.discard(fut)
                qid, sql, ok = fut.result()
                cache[(qid, sql)] = ok
                done += 1
            for fut in list(pending):
                t = futs[fut]
                if (now - start[fut]).total_seconds() > EVAL_TIMEOUT:
                    pending.discard(fut)
                    cache[(t[0], t[1])] = False
                    done += 1
                    timeouts += 1
            if done % report == 0 or (not pending and done == n):
                elapsed = (now - t0).total_seconds()
                eta = (n - done) * elapsed / max(done, 1)
                log(
                    f"[audit] {label}: {done}/{n} ({100*done/n:.0f}%) "
                    f"elapsed={elapsed:.0f}s eta={eta:.0f}s timeouts={timeouts}"
                )
    elapsed = (datetime.now() - t0).total_seconds()
    log(f"[audit] {label}: done in {elapsed:.0f}s timeouts={timeouts}")
    return cache


def enrich(rows: List[dict], cache: Dict[Tuple[str, str], bool]) -> List[dict]:
    out = []
    for row in rows:
        qid = row["qid"]
        stored, r2 = row["stored_sql"], row["r2_sql"]
        stored_hit = cache.get((qid, stored), False) if stored else False
        r2_hit = cache.get((qid, r2), False) if r2 else False
        out.append(
            {
                **row,
                "stored_hit": stored_hit,
                "r2_hit": r2_hit,
                "gm_vs_stored": row["gold_match"] != stored_hit,
                "gm_vs_r2": row["gold_match"] != r2_hit,
                "stored_vs_r2": stored_hit != r2_hit,
            }
        )
    return out


def summarize(name: str, rows: List[dict]) -> dict:
    n = len(rows)
    return {
        "name": name,
        "n": n,
        "gold_match": sum(1 for r in rows if r["gold_match"]),
        "stored_sql_hit": sum(1 for r in rows if r["stored_hit"]),
        "r2_replay_hit": sum(1 for r in rows if r["r2_hit"]),
        "sql_ne_r2": sum(1 for r in rows if not r["sql_eq_r2"]),
        "gm_vs_stored_mismatch": sum(1 for r in rows if r["gm_vs_stored"]),
        "gm_vs_r2_mismatch": sum(1 for r in rows if r["gm_vs_r2"]),
        "stored_vs_r2_hit_diff": sum(1 for r in rows if r["stored_vs_r2"]),
    }


def paired_r2(rows_a: List[dict], rows_b: List[dict]) -> dict:
    by_a = {r["qid"]: r for r in rows_a}
    by_b = {r["qid"]: r for r in rows_b}
    saved, hurt = [], []
    for qid in sorted(by_a.keys(), key=int):
        if qid not in by_b:
            continue
        ha, hb = by_a[qid]["r2_hit"], by_b[qid]["r2_hit"]
        if ha and not hb:
            saved.append(qid)
        if hb and not ha:
            hurt.append(qid)
    return {
        "a_r2_hit": sum(1 for r in rows_a if r["r2_hit"]),
        "b_r2_hit": sum(1 for r in rows_b if r["r2_hit"]),
        "saved": saved,
        "hurt": hurt,
        "net": len(saved) - len(hurt),
    }


def write_report(
    summaries: List[dict],
    ef2: dict,
    paired: dict,
    elapsed: float,
    n_tasks: int,
) -> None:
    merged_r2 = next(s["r2_replay_hit"] for s in summaries if s["name"] == "merged_ef2")
    raw_gm = next(s["gold_match"] for s in summaries if s["name"] == "final_raw")
    raw_sum = next(s for s in summaries if s["name"] == "final_raw")
    merged_sum = next(s for s in summaries if s["name"] == "merged_ef2")

    lines = [
        "# Eval Pipeline Audit — Hit@1 口径对齐",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Eval: **{n_tasks}** unique `(qid, exact_sql)`, **{WORKERS}** threads, **{elapsed:.0f}s**",
        "",
        "## Ground truth（锁定）",
        "",
        "**Hit@1 = Method C: R2 replay** — `pick_r2(build_clusters(rollout_stats))` + `compare_with_gold`.",
        "与 D2b / `selector_replay_498_merged.md` 同口径。",
        "",
        "`stats.gold_match` / `rec[\"sql\"]` 是 MCTS **落盘字段**，不等于 R2 replay。",
        "",
        "---",
        "",
        "## 1. 三口径对照",
        "",
        "| Dataset | A: gold_match | B: eval stored sql | C: R2 replay | B−C | A−C | sql≠R2 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        lines.append(
            f"| **{s['name']}** | {s['gold_match']}/{s['n']} | {s['stored_sql_hit']}/{s['n']} | "
            f"**{s['r2_replay_hit']}/{s['n']}** | {s['stored_sql_hit']-s['r2_replay_hit']:+d} | "
            f"{s['gold_match']-s['r2_replay_hit']:+d} | {s['sql_ne_r2']} |"
        )

    lines += [
        "",
        "## 2. 364 vs 309 根因",
        "",
        "| 数字 | 来源 | 本脚本 |",
        "|---|---|---|",
        f"| **364** | D2b R2 on `load_merged_498()` | **{merged_r2}/498** |",
        f"| **309** | `stats.gold_match` on raw `v4_final_498q` | **{raw_gm}/498** |",
        "",
        "**不是 bug，是口径 + 数据混用：**",
        "",
        f"1. **数据集不同**：364 用 merged（final + ef2 51 题 overlay）；309 用 raw final JSON。",
        f"2. **指标不同**：364 = R2 selector replay；309 = 落盘 `gold_match`（MCTS 写 JSON 时算的，当时 selector 可能不是 R2）。",
        f"3. **stored sql ≠ R2 pick**：raw final 上 {raw_sum['sql_ne_r2']}/498 题；其中 hit 差 {raw_sum['stored_vs_r2_hit_diff']} 题。",
        f"4. **ef2 overlay**：R2 {ef2['r2_old']}→{ef2['r2_new']}/51；gold_match {ef2['gm_old']}→{ef2['gm_new']}/51。",
        "",
        "**364 是 ground truth；309 不能当 R2 baseline。**",
        "",
        "## 3. calib vs merged (R2 replay)",
        "",
        f"| | Hit@1 |",
        f"|---|---:|",
        f"| calib_498 | **{paired['a_r2_hit']}/498** |",
        f"| merged_ef2 | **{paired['b_r2_hit']}/498** |",
        f"| net | **{paired['net']:+d}** (saved {len(paired['saved'])}, hurt {len(paired['hurt'])}) |",
        "",
        f"saved: `{', '.join(paired['saved'][:30])}{'…' if len(paired['saved'])>30 else ''}`",
        "",
        f"hurt: `{', '.join(paired['hurt'][:30])}{'…' if len(paired['hurt'])>30 else ''}`",
        "",
        "## 4. 冻结规则",
        "",
        "- Hit@1 baseline = **merged_ef2 R2 replay**",
        "- Calib Hit@1 = **calib_498 R2 replay**（非 gold_match）",
        "- Recall 单独报，不与 Hit@1 混读",
        "",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    t0 = datetime.now()
    gold_sqls, qid_to_db = pld.load_gold_meta()
    final_raw = pld.load_json(pld.FINAL_PATH)
    merged = pld.load_merged_498()
    calib = pld.load_json(CALIB_498) if CALIB_498.exists() else {}
    ef2_set = pld.load_ef2()

    log("[audit] phase 0: collect rows (no DB)")
    raw_rows = collect_rows("final_raw", final_raw)
    merged_rows = collect_rows("merged_ef2", merged)
    calib_rows = collect_rows("calib_498", calib) if calib else []

    log("[audit] phase 1: gold_match (instant)")
    for name, rows in [("final_raw", raw_rows), ("merged_ef2", merged_rows), ("calib_498", calib_rows)]:
        if rows:
            gm = sum(1 for r in rows if r["gold_match"])
            log(f"[audit]   {name} gold_match={gm}/{len(rows)}")

    all_rows = raw_rows + merged_rows + calib_rows
    tasks = build_tasks(all_rows, gold_sqls, qid_to_db)
    cache = run_parallel_eval(tasks, "all_datasets")

    raw_rows = enrich(raw_rows, cache)
    merged_rows = enrich(merged_rows, cache)
    calib_rows = enrich(calib_rows, cache)

    summaries = [summarize("final_raw", raw_rows), summarize("merged_ef2", merged_rows)]
    if calib_rows:
        summaries.append(summarize("calib_498", calib_rows))

    log("[audit] phase 3: results")
    for s in summaries:
        log(
            f"[audit]   {s['name']}: gm={s['gold_match']} stored={s['stored_sql_hit']} "
            f"R2={s['r2_replay_hit']}"
        )

    ef2_qids = sorted(ef2_set, key=int)
    by_raw = {r["qid"]: r for r in raw_rows}
    by_merged = {r["qid"]: r for r in merged_rows}
    ef2 = {
        "gm_old": sum(1 for q in ef2_qids if by_raw.get(q, {}).get("gold_match")),
        "gm_new": sum(1 for q in ef2_qids if by_merged.get(q, {}).get("gold_match")),
        "r2_old": sum(1 for q in ef2_qids if by_raw.get(q, {}).get("r2_hit")),
        "r2_new": sum(1 for q in ef2_qids if by_merged.get(q, {}).get("r2_hit")),
    }
    log(f"[audit]   ef2 overlay R2: {ef2['r2_old']}->{ef2['r2_new']}/51")

    paired = paired_r2(calib_rows, merged_rows) if calib_rows else {}
    if paired:
        log(f"[audit]   calib vs merged R2 net={paired['net']:+d}")

    elapsed = (datetime.now() - t0).total_seconds()
    write_report(summaries, ef2, paired, elapsed, len(tasks))
    OUT_JSON.write_text(
        json.dumps(
            {"elapsed_sec": elapsed, "summaries": summaries, "ef2_overlay": ef2, "paired": paired},
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"[audit] wrote {OUT_MD} ({elapsed:.0f}s)")


if __name__ == "__main__":
    main()
