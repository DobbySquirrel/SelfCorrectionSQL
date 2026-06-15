#!/usr/bin/env python3
"""Offline: frozen overlay498 pools, fix final SQL selection with strict v2 execution buckets.

Legacy (sig0) top-5 buckets can merge SQLs whose full results differ.
This replays R4/gated shortcut on the same rollout_stats with v2-final signatures only.
Recall (pool oracle) is unchanged; only Hit@1 selection changes.
"""

from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[7]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

from selector_replay import build_clusters, select_sql  # noqa: E402
from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils  # noqa: E402
from workflows.mcts_v4.utils.r4_vote import collect_r4_cluster_votes  # noqa: E402
from workflows.mcts_v4.utils.gated_selection import _analyze_r4_gate  # noqa: E402
from workflows.mcts_v4.utils.execution_tiebreak import variants_have_row_collision  # noqa: E402

OUT = ROOT / "workflows/mcts_v4/test/out/cte_diverse"
HERE = Path(__file__).resolve().parent
OVERLAY = OUT / "v4_colbind_v2_dual03_min2sq_abl5_e6_bootstrap_full498_gated22_overlay_rollouts12.json"
BASELINE = OUT / "v4_colbind_v2_dual03_min2sq_abl5_e6_bootstrap_full498_rollouts12.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
PPL = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"


def _norm_sql(sql: str) -> str:
    return " ".join((sql or "").split()).strip().lower()


def label_map(rec: dict) -> Dict[str, bool]:
    return {
        (a.get("sql") or "").strip(): bool(a.get("is_correct"))
        for a in (rec.get("all_sqls_with_attributes") or [])
        if (a.get("sql") or "").strip()
    }


def hit_sql(rec: dict, sql: str) -> bool:
    return bool(label_map(rec).get((sql or "").strip()))


def has_recall(rec: dict) -> bool:
    return any(label_map(rec).values())


def exec_to_v2_sig(conn, sql: str, cache: dict) -> str:
    key = _norm_sql(sql)
    if key in cache:
        return cache[key]
    try:
        df, err = conn.execute_query(sql)
        if err or df is None:
            sig = f"invalid_{err or 'none'}"
        else:
            res = {"valid": True, "query_result": df}
            rows, cols = MCTSUtils.execution_result_to_rows_columns(res)
            sig = MCTSUtils.create_result_signature_v2(rows, cols, use_columns=False, topk=None)
    except Exception as e:
        sig = f"invalid_{e}"
    cache[key] = sig
    return sig


def remap_rollouts_v2(rss: List[dict], sql_to_v2: Dict[str, str]) -> List[dict]:
    out: List[dict] = []
    for rs in rss:
        rs2 = deepcopy(rs)
        rb: Counter = Counter()
        vs2 = []
        for v in rs.get("all_sql_variants") or []:
            v2 = dict(v)
            sql = (v.get("sql") or "").strip()
            if sql and v.get("valid"):
                sig = sql_to_v2.get(_norm_sql(sql), "")
                if sig:
                    v2["result_signature"] = sig
                    rb[sig] += 1
            vs2.append(v2)
        rs2["all_sql_variants"] = vs2
        rs2["result_buckets"] = dict(rb) if rb else {}
        out.append(rs2)
    return out


def remap_rollouts_row_split(rss: List[dict]) -> List[dict]:
    """Split legacy bucket by row_count (cheap proxy for execution mismatch)."""
    out: List[dict] = []
    for rs in rss:
        rs2 = deepcopy(rs)
        rb: Counter = Counter()
        vs2 = []
        for v in rs.get("all_sql_variants") or []:
            leg = (v.get("result_signature") or "").strip()
            rows = int(v.get("result_row_count") or 0) if v.get("valid") else 0
            sig = f"{leg}|r{rows}" if leg else ""
            v2 = dict(v)
            if sig:
                v2["result_signature"] = sig
                rb[sig] += 1
            vs2.append(v2)
        rs2["all_sql_variants"] = vs2
        rs2["result_buckets"] = dict(rb) if rb else {}
        out.append(rs2)
    return out


def pick_r4(rss: List[dict]) -> str:
    with redirect_stdout(io.StringIO()):
        return (select_sql("R4_majority_then_reward", rss) or "").strip()


def pick_gated_shortcut(rss: List[dict], margin: float = 0.7) -> str:
    ga = _analyze_r4_gate(rss, margin)
    return (ga.sql or "").strip()


def legacy_collision_stats(rec: dict) -> Tuple[int, int]:
    """Per-q: (# legacy sigs with mixed correct/incorrect SQL), (# with mixed row counts)."""
    lab = label_map(rec)
    leg_variants: Dict[str, List[Tuple[str, bool, int]]] = defaultdict(list)
    for rs in rec.get("rollout_stats") or []:
        for v in rs.get("all_sql_variants") or []:
            leg = (v.get("result_signature") or "").strip()
            sql = (v.get("sql") or "").strip()
            if not leg or not sql:
                continue
            leg_variants[leg].append((sql, lab.get(sql, False), int(v.get("result_row_count") or 0)))
    mixed_label = mixed_rows = 0
    for _leg, items in leg_variants.items():
        corr = {x[1] for x in items}
        rows = {x[2] for x in items}
        if True in corr and False in corr:
            mixed_label += 1
        if len(rows) > 1:
            mixed_rows += 1
    return mixed_label, mixed_rows


def main() -> None:
    data = json.loads(OVERLAY.read_text(encoding="utf-8"))
    qids = sorted(data.keys(), key=int)
    n = len(qids)

    gold_items = json.loads(GOLD.read_text(encoding="utf-8"))
    qdb = {str(x["question_id"]): x.get("db", "") for x in json.loads(PPL.read_text(encoding="utf-8"))}

    from workflows.mcts_v1.test.test_mcts import build_db_connector  # noqa: E402

    runtime_hit = sum(1 for q in qids if (data[q].get("stats") or {}).get("gold_match"))
    recall = sum(1 for q in qids if has_recall(data[q]))
    r4_legacy_hit = 0
    r4_v2_hit = 0
    r4_row_hit = 0
    gated_legacy_hit = 0
    gated_v2_hit = 0

    fixed_v2: List[str] = []
    broken_v2: List[str] = []
    fixed_row: List[str] = []
    collision_q = 0
    collision_clusters = 0

    print(f"=== replay final v2 selection on overlay ({n} qids) ===\n")
    print(f"runtime Hit@1={runtime_hit}/{n}  Recall={recall}/{n}\n")

    for i, qid in enumerate(qids, 1):
        rec = data[qid]
        rss = rec.get("rollout_stats") or []
        ml, mr = legacy_collision_stats(rec)
        if ml or mr:
            collision_q += 1
            collision_clusters += ml

        r4_legacy_sql = pick_r4(rss)
        if hit_sql(rec, r4_legacy_sql):
            r4_legacy_hit += 1

        gated_legacy_sql = pick_gated_shortcut(rss)
        if hit_sql(rec, gated_legacy_sql):
            gated_legacy_hit += 1

        # v2-final remap via re-execution
        db = qdb.get(qid, "")
        sql_to_v2: Dict[str, str] = {}
        if db and rss:
            conn = build_db_connector(db)
            try:
                cache: dict = {}
                seen: Set[str] = set()
                for rs in rss:
                    for v in rs.get("all_sql_variants") or []:
                        sql = (v.get("sql") or "").strip()
                        if not sql or not v.get("valid"):
                            continue
                        nk = _norm_sql(sql)
                        if nk in seen:
                            continue
                        seen.add(nk)
                        sig = exec_to_v2_sig(conn, sql, cache)
                        sql_to_v2[nk] = sig
            finally:
                conn.disconnect()

        rss_v2 = remap_rollouts_v2(rss, sql_to_v2) if sql_to_v2 else rss
        r4_v2_sql = pick_r4(rss_v2)
        if hit_sql(rec, r4_v2_sql):
            r4_v2_hit += 1
        gated_v2_sql = pick_gated_shortcut(rss_v2)
        if hit_sql(rec, gated_v2_sql):
            gated_v2_hit += 1

        rss_row = remap_rollouts_row_split(rss)
        r4_row_sql = pick_r4(rss_row)
        if hit_sql(rec, r4_row_sql):
            r4_row_hit += 1

        rt = (rec.get("stats") or {}).get("gold_match")
        v2h = hit_sql(rec, r4_v2_sql)
        if v2h and not rt:
            fixed_v2.append(qid)
        if rt and not v2h:
            broken_v2.append(qid)
        rowh = hit_sql(rec, r4_row_sql)
        if rowh and not rt:
            fixed_row.append(qid)

        if i % 50 == 0:
            print(f"  ... {i}/{n} v2_hit={r4_v2_hit}")

    summary = {
        "n": n,
        "recall": recall,
        "runtime_hit1": runtime_hit,
        "r4_legacy_replay_hit1": r4_legacy_hit,
        "r4_v2_final_hit1": r4_v2_hit,
        "r4_row_split_hit1": r4_row_hit,
        "gated_shortcut_legacy_hit1": gated_legacy_hit,
        "gated_shortcut_v2_final_hit1": gated_v2_hit,
        "legacy_collision_questions": collision_q,
        "legacy_collision_clusters_mixed_label": collision_clusters,
        "v2_fixed_vs_runtime": fixed_v2,
        "v2_broken_vs_runtime": broken_v2,
        "row_split_fixed_vs_runtime": fixed_row,
        "source": str(OVERLAY),
    }
    out_path = HERE / "replay_final_v2_selection_overlay498.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n--- SELECTOR Hit@1 (same pools, Recall fixed) ---")
    print(f"  runtime (overlay)     : {runtime_hit}/{n}")
    print(f"  R4 legacy replay      : {r4_legacy_hit}/{n}  (delta {r4_legacy_hit - runtime_hit:+d})")
    print(f"  R4 v2-final (scheme A): {r4_v2_hit}/{n}  (delta {r4_v2_hit - runtime_hit:+d})")
    print(f"  R4 row-split proxy    : {r4_row_hit}/{n}  (delta {r4_row_hit - runtime_hit:+d})")
    print(f"  gated shortcut legacy : {gated_legacy_hit}/{n}  (delta {gated_legacy_hit - runtime_hit:+d})")
    print(f"  gated shortcut v2     : {gated_v2_hit}/{n}  (delta {gated_v2_hit - runtime_hit:+d})")
    print(f"\n  legacy collision qids : {collision_q}/{n} (mixed correct/wrong in same legacy sig)")
    print(f"  v2 fixes vs runtime   : {len(fixed_v2)} qids")
    if fixed_v2[:15]:
        print(f"    sample: {fixed_v2[:15]}")
    print(f"  v2 hurts vs runtime   : {len(broken_v2)} qids")
    if broken_v2[:10]:
        print(f"    sample: {broken_v2[:10]}")
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    main()
