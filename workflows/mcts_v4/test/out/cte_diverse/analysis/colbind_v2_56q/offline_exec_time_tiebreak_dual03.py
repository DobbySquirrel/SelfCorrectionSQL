#!/usr/bin/env python3
"""
P0 offline: execution-time tie-break on dual03 global JSON.

Compares prod vs R4 row tiebreak vs R4 exec-time tiebreak.
Uses DatabaseConnector for timing (repeat=2 per SQL).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[7]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

from selector_replay import build_clusters, select_sql
from workflows.mcts_v4.core.database_connector import DatabaseConnector
from workflows.mcts_v4.utils.execution_tiebreak import clear_execution_time_cache, tiebreak_pick_variants

GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
DEFAULT_INPUT = ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_colbind_v2_dual03_global_filter_498q_rollouts12.json"


def labels(rec: dict) -> Dict[str, bool]:
    return {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
        if (a.get("sql") or "").strip()
    }


def hit(rec: dict, sql: str) -> bool:
    return bool(labels(rec).get((sql or "").strip()))


def prod_sql(rec: dict) -> str:
    st = rec.get("stats") or {}
    return (st.get("final_sql") or rec.get("final_sql") or rec.get("optimal_sql") or "").strip()


def pick_r4_row(rss: List[dict]) -> str:
    return (select_sql("R4_majority_then_reward", rss) or "").strip()


def pick_r4_exec(rss: List[dict], db) -> str:
    from workflows.mcts_v4.utils.sql_selector import SQLSelector

    os.environ["MCTS_EXEC_TIME_TIEBREAK"] = "1"
    clear_execution_time_cache()
    return SQLSelector._select_r4_majority_then_reward(rss, db_connector=db).strip()


def build_db_map(gold_rows: list) -> Dict[str, DatabaseConnector]:
    out: Dict[str, DatabaseConnector] = {}
    for row in gold_rows:
        qid = str(row["question_id"])
        db_id = row["db_id"]
        if qid not in out:
            out[qid] = DatabaseConnector(db_id)
    return out


def exec_sql_timed(db: DatabaseConnector, sql: str, repeats: int = 2) -> Tuple[Any, Optional[str]]:
    last_err = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        df, err = db.execute_query(sql)
        _ = time.perf_counter() - t0
        last_err = err
        if err is None:
            return df, None
    return None, last_err


class QDbConnector:
    """Route execute_query to per-question DB (for tiebreak timing within one question)."""

    def __init__(self, db: DatabaseConnector):
        self._db = db

    def execute_query(self, sql: str):
        return exec_sql_timed(self._db, sql)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out-md", type=Path, default=HERE / "p0_exec_time_tiebreak_dual03.md")
    ap.add_argument("--out-json", type=Path, default=HERE / "p0_exec_time_tiebreak_dual03.json")
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args()

    data = json.loads(args.input.read_text())
    qids = sorted(data.keys(), key=int)
    n = len(qids)
    gold_rows = json.loads(GOLD.read_text())
    db_map = build_db_map(gold_rows)

    prod_h = r4_row_h = r4_time_h = 0
    improved: List[str] = []
    hurt: List[str] = []
    tie_wrong: List[str] = []
    tie_rescued: List[str] = []
    rep_changed = 0

    os.environ["MCTS_EXEC_TIME_REPEATS"] = str(args.repeats)

    for q in qids:
        rec = data[q]
        rss = rec.get("rollout_stats") or []
        lab = labels(rec)
        if not any(lab.values()):
            continue

        db = QDbConnector(db_map.get(q) or db_map.get(str(int(q))))
        p_sql = prod_sql(rec)
        p_ok = hit(rec, p_sql)
        r4_row = pick_r4_row(rss)
        r4_time = pick_r4_exec(rss, db)

        if p_ok:
            prod_h += 1
        if hit(rec, r4_row):
            r4_row_h += 1
        if hit(rec, r4_time):
            r4_time_h += 1

        if not p_ok and hit(rec, r4_time):
            improved.append(q)
        if p_ok and not hit(rec, r4_time):
            hurt.append(q)
        if not p_ok:
            tie_wrong.append(q)
            if hit(rec, r4_time) and not hit(rec, r4_row):
                tie_rescued.append(q)

        clusters = build_clusters(rss)
        os.environ["MCTS_EXEC_TIME_TIEBREAK"] = "0"
        clear_execution_time_cache()
        row_reps = {s: tiebreak_pick_variants(c.variants) for s, c in clusters.items() if c.variants}
        os.environ["MCTS_EXEC_TIME_TIEBREAK"] = "1"
        clear_execution_time_cache()
        time_reps = {
            s: tiebreak_pick_variants(c.variants, db_connector=db) for s, c in clusters.items() if c.variants
        }
        if row_reps != time_reps:
            rep_changed += 1

    recall_n = sum(1 for q in qids if any(labels(data[q]).values()))
    delta = r4_time_h - prod_h
    md = f"""# P0 — Execution time tie-break offline (dual03 global)

Generated: {datetime.now(timezone.utc).isoformat()}
Input: `{args.input}`

| 指标 | 值 |
|---|---:|
| 题数 | {n} |
| 有 recall 池 | {recall_n} |
| Prod Acc | {prod_h}/{n} |
| R4 row tiebreak Acc | {r4_row_h}/{n} |
| R4 exec-time tiebreak Acc | {r4_time_h}/{n} |
| **Δ exec-time vs prod** | **{delta:+d}** |
| Δ exec-time vs R4-row | {r4_time_h - r4_row_h:+d} |
| improved / hurt vs prod | {len(improved)} / {len(hurt)} |
| 有 recall 但 prod 选错 | {len(tie_wrong)} |
| exec-time 救回（R4-row 仍错） | {len(tie_rescued)} |
| 题级 cluster rep 改变 | {rep_changed} |

### improved
{", ".join(improved)}

### hurt
{", ".join(hurt)}
"""
    args.out_md.write_text(md, encoding="utf-8")
    payload = {
        "input": str(args.input),
        "n": n,
        "recall_n": recall_n,
        "prod_acc": prod_h,
        "r4_row_acc": r4_row_h,
        "r4_exec_time_acc": r4_time_h,
        "delta_vs_prod": delta,
        "improved": improved,
        "hurt": hurt,
        "tie_wrong": tie_wrong,
        "tie_rescued": tie_rescued,
        "rep_changed_q": rep_changed,
    }
    args.out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.out_md)
    print(f"Prod {prod_h}/{n}  R4-exec-time {r4_time_h}/{n}  Δ={delta:+d}")


if __name__ == "__main__":
    main()
