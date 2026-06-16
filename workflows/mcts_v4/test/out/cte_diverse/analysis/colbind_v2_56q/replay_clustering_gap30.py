#!/usr/bin/env python3
"""Offline clustering / selection ablations on frozen gap30 JSON (same pools, Recall fixed).

Strategies:
  runtime          — stored final pick
  r4_legacy        — R4 on legacy result_buckets
  gated_legacy     — gated R4 shortcut on legacy buckets
  v2_final_r4      — re-exec → strict v2 buckets → R4 (Scheme A final-only)
  mul_purity       — dir1: argmax votes(legacy) × max_v2_share within legacy
  jaccard_merge    — dir2-lite: merge variant row-sets with Jaccard≥τ → super-cluster vote → R4
  struct_with_bias — dir6-lite: R4 legacy then prefer WITH-cluster on tie / close margin

Usage:
  python replay_clustering_gap30.py
  python replay_clustering_gap30.py --input sig0 --jaccard-threshold 0.85
"""

from __future__ import annotations

import argparse
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
from workflows.mcts_v4.utils.gated_selection import _analyze_r4_gate  # noqa: E402
from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils  # noqa: E402
from workflows.mcts_v4.utils.r4_vote import collect_r4_cluster_votes  # noqa: E402

OUT = ROOT / "workflows/mcts_v4/test/out/cte_diverse"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "qids_alpha_min2_recall_gap30_manifest.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
PPL = ROOT / "workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl_rollouts_20_3_15_nigga.json"

INPUTS = {
    "sig0": "v4_colbind_v2_dual03_min2sq_abl5_sig0_gated_gap30_r12",
    "sig1": "v4_colbind_v2_dual03_min2sq_abl5_sig1_gated_gap30_r12",
    "e6": "v4_colbind_v2_dual03_min2sq_abl5_e6_reversed_bootstrap_gap30_r12",
}

HIT_DIFF_QIDS = ["72", "201", "407", "1094"]


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


def load_merged(stem: str) -> dict:
    d: dict = {}
    for i in range(4):
        p = OUT / f"{stem}_w{i}.json"
        if p.is_file():
            d.update(json.loads(p.read_text(encoding="utf-8")))
    if not d:
        p = OUT / f"{stem}.json"
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
    return d


def pick_r4(rss: List[dict]) -> str:
    with redirect_stdout(io.StringIO()):
        return (select_sql("R4_majority_then_reward", rss) or "").strip()


def pick_gated(rss: List[dict], margin: float = 0.7) -> str:
    return (_analyze_r4_gate(rss, margin).sql or "").strip()


def exec_rows_and_v2(conn, sql: str, cache: dict) -> Tuple[str, Optional[frozenset]]:
    key = _norm_sql(sql)
    if key in cache:
        return cache[key]
    rows_set: Optional[frozenset] = None
    try:
        df, err = conn.execute_query(sql)
        if err or df is None:
            sig = f"invalid_{err or 'none'}"
        else:
            res = {"valid": True, "query_result": df}
            rows, cols = MCTSUtils.execution_result_to_rows_columns(res)
            sig = MCTSUtils.create_result_signature_v2(rows, cols, use_columns=False, topk=None)
            tuples = []
            for row in rows:
                if isinstance(row, dict):
                    vals = tuple(row.values())
                else:
                    vals = tuple(row)
                tuples.append(vals)
            rows_set = frozenset(tuples)
    except Exception as e:
        sig = f"invalid_{e}"
    cache[key] = (sig, rows_set)
    return cache[key]


def remap_rollouts_signatures(rss: List[dict], sql_to_sig: Dict[str, str]) -> List[dict]:
    out: List[dict] = []
    for rs in rss:
        rs2 = deepcopy(rs)
        rb: Counter = Counter()
        vs2 = []
        for v in rs.get("all_sql_variants") or []:
            v2 = dict(v)
            sql = (v.get("sql") or "").strip()
            if sql and v.get("valid"):
                sig = sql_to_sig.get(_norm_sql(sql), "")
                if sig:
                    v2["result_signature"] = sig
                    rb[sig] += 1
            vs2.append(v2)
        rs2["all_sql_variants"] = vs2
        rs2["result_buckets"] = dict(rb) if rb else {}
        out.append(rs2)
    return out


def _tiebreak_pick_sql(clusters: dict, sig: str) -> str:
    c = clusters.get(sig)
    if not c or not c.variants:
        return ""
    from workflows.mcts_v4.utils.execution_tiebreak import tiebreak_pick_variants

    return tiebreak_pick_variants([(s, r, n) for s, r, n in c.variants])


def pick_mul_purity(rss: List[dict], legacy_to_v2_counts: Dict[str, Counter]) -> str:
    votes = collect_r4_cluster_votes(rss)
    if not votes:
        return pick_r4(rss)
    best_sig, best_score = "", -1.0
    for leg, v in votes.items():
        v2c = legacy_to_v2_counts.get(leg, Counter())
        if not v2c:
            purity = 1.0
        else:
            purity = max(v2c.values()) / max(sum(v2c.values()), 1)
        score = v * purity
        if score > best_score:
            best_score, best_sig = score, leg
    clusters = build_clusters(rss)
    return _tiebreak_pick_sql(clusters, best_sig) or pick_r4(rss)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _union_find_merge(n: int, edges: List[Tuple[int, int]]) -> List[int]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, j in edges:
        union(i, j)
    return [find(i) for i in range(n)]


def pick_jaccard_merge(
    rss: List[dict],
    sql_list: List[str],
    sql_rows: List[Optional[frozenset]],
    threshold: float,
) -> str:
    """Dir2-lite: merge final row-sets by Jaccard, rebuild buckets, R4 + tiebreak."""
    n = len(sql_list)
    if n == 0:
        return ""
    edges = []
    for i in range(n):
        if sql_rows[i] is None:
            continue
        for j in range(i + 1, n):
            if sql_rows[j] is None:
                continue
            if _jaccard(sql_rows[i], sql_rows[j]) >= threshold:
                edges.append((i, j))
    roots = _union_find_merge(n, edges)
    norm_to_super = {_norm_sql(sql_list[i]): f"super_{roots[i]}" for i in range(n)}

    rss_super: List[dict] = []
    for rs in rss:
        rs2 = deepcopy(rs)
        rb: Counter = Counter()
        vs2 = []
        for v in rs.get("all_sql_variants") or []:
            v2 = dict(v)
            sql = (v.get("sql") or "").strip()
            if sql and v.get("valid"):
                sup = norm_to_super.get(_norm_sql(sql))
                if sup:
                    v2["result_signature"] = sup
                    rb[sup] += 1
            vs2.append(v2)
        rs2["all_sql_variants"] = vs2
        rs2["result_buckets"] = dict(rb) if rb else {}
        rss_super.append(rs2)
    return pick_r4(rss_super)


def pick_struct_with_bias(rss: List[dict], margin: float = 1.5) -> str:
    votes = collect_r4_cluster_votes(rss)
    if not votes:
        return pick_r4(rss)
    ranked = votes.most_common()
    top_v = ranked[0][1]
    tied = [s for s, v in ranked if v == top_v]
    if len(tied) == 1 and (len(ranked) < 2 or ranked[1][1] * margin >= top_v):
        # close second — check WITH bias among top legacy clusters
        close = [ranked[0][0]]
        if len(ranked) >= 2 and ranked[1][1] * margin >= top_v:
            close.append(ranked[1][0])
        clusters = build_clusters(rss)
        with_sigs = []
        flat_sigs = []
        for sig in close:
            c = clusters.get(sig)
            if not c:
                continue
            has_with = any(re.search(r"\bWITH\b", sql, re.I) for sql, _, _ in c.variants)
            (with_sigs if has_with else flat_sigs).append(sig)
        pick_sig = with_sigs[0] if with_sigs else tied[0]
        return _tiebreak_pick_sql(clusters, pick_sig) or pick_r4(rss)
    return pick_r4(rss)


def build_exec_maps(rec: dict, conn) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, Counter], List[str], List[Optional[frozenset]]]:
    """sql→v2, sql→legacy, legacy→Counter(v2), unique sql list, row sets."""
    cache: dict = {}
    sql_to_v2: Dict[str, str] = {}
    sql_to_legacy: Dict[str, str] = {}
    legacy_to_v2: Dict[str, Counter] = defaultdict(Counter)
    sql_list: List[str] = []
    sql_rows: List[Optional[frozenset]] = []
    seen: Set[str] = set()

    for rs in rec.get("rollout_stats") or []:
        for v in rs.get("all_sql_variants") or []:
            sql = (v.get("sql") or "").strip()
            if not sql or not v.get("valid"):
                continue
            nk = _norm_sql(sql)
            leg = (v.get("result_signature") or "").strip()
            if nk not in seen:
                seen.add(nk)
                sql_list.append(sql)
                if conn:
                    v2sig, rows = exec_rows_and_v2(conn, sql, cache)
                    sql_to_v2[nk] = v2sig
                    sql_rows.append(rows)
                else:
                    m = re.search(r"([0-9a-f]{32})$", leg)
                    sql_to_v2[nk] = m.group(1) if m else leg
                    sql_rows.append(None)
            if leg:
                sql_to_legacy[nk] = leg
                v2 = sql_to_v2.get(nk, "")
                if v2:
                    legacy_to_v2[leg][v2] += 1
    return sql_to_v2, sql_to_legacy, legacy_to_v2, sql_list, sql_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", choices=list(INPUTS), default="sig0")
    ap.add_argument("--jaccard-threshold", type=float, default=0.85)
    ap.add_argument(
        "--sweep-jaccard",
        type=str,
        default="",
        help="comma-separated thresholds to report jaccard Hit@1 only (e.g. 0.75,0.85,0.95)",
    )
    ap.add_argument("--no-exec", action="store_true", help="skip DB re-exec (v2/jaccard approximated)")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    qids = [str(q) for q in manifest["qids"]]
    data = load_merged(INPUTS[args.input])
    qids = [q for q in qids if q in data]
    n = len(qids)

    qdb = {str(x["question_id"]): x.get("db", "") for x in json.loads(PPL.read_text(encoding="utf-8"))}
    from workflows.mcts_v1.test.test_mcts import build_db_connector  # noqa: E402

    strategies = [
        "runtime",
        "r4_legacy",
        "gated_legacy",
        "v2_final_r4",
        "mul_purity",
        "jaccard_merge",
        "struct_with_bias",
    ]
    hits = Counter()
    per_qid: Dict[str, Dict[str, bool]] = defaultdict(dict)

    for qid in qids:
        rec = data[qid]
        rss = rec.get("rollout_stats") or []
        rt_sql = (rec.get("sql") or "").strip()
        per_qid[qid]["runtime"] = hit_sql(rec, rt_sql)
        per_qid[qid]["r4_legacy"] = hit_sql(rec, pick_r4(rss))
        per_qid[qid]["gated_legacy"] = hit_sql(rec, pick_gated(rss))

        db = qdb.get(qid, "")
        conn = None
        if db and not args.no_exec:
            conn = build_db_connector(db)
        try:
            sql_to_v2, _, leg_to_v2, sql_list, sql_rows = build_exec_maps(rec, conn)
            rss_v2 = remap_rollouts_signatures(rss, sql_to_v2) if sql_to_v2 else rss
            per_qid[qid]["v2_final_r4"] = hit_sql(rec, pick_r4(rss_v2))
            per_qid[qid]["mul_purity"] = hit_sql(rec, pick_mul_purity(rss, leg_to_v2))
            if conn and sql_list:
                per_qid[qid]["jaccard_merge"] = hit_sql(
                    rec,
                    pick_jaccard_merge(rss, sql_list, sql_rows, args.jaccard_threshold),
                )
            else:
                per_qid[qid]["jaccard_merge"] = per_qid[qid]["r4_legacy"]
            per_qid[qid]["struct_with_bias"] = hit_sql(rec, pick_struct_with_bias(rss))
        finally:
            if conn:
                conn.disconnect()

        for s in strategies:
            if per_qid[qid].get(s):
                hits[s] += 1

    recall = sum(1 for q in qids if has_recall(data[q]))
    rt = hits["runtime"]

    print(f"=== clustering ablation gap30 ({args.input}, n={n}) ===")
    print(f"Recall (pool oracle) = {recall}/{n}\n")
    print(f"{'strategy':<22} {'Hit@1':>8}  {'Δ vs runtime':>12}")
    print("-" * 46)
    for s in strategies:
        h = hits[s]
        print(f"{s:<22} {h:>3}/{n:<4}  {h - rt:>+12d}")

    print(f"\n--- 4 hit-divergent qids (runtime vs strategies) ---")
    print(f"{'qid':>5} | {'rt':^4} {'r4':^4} {'v2f':^4} {'mul':^4} {'jac':^4} {'str':^4} | recall")
    for q in HIT_DIFF_QIDS:
        if q not in per_qid:
            continue
        pq = per_qid[q]
        print(
            f"{q:>5} | "
            f"{str(pq.get('runtime', False)):^4} "
            f"{str(pq.get('r4_legacy', False)):^4} "
            f"{str(pq.get('v2_final_r4', False)):^4} "
            f"{str(pq.get('mul_purity', False)):^4} "
            f"{str(pq.get('jaccard_merge', False)):^4} "
            f"{str(pq.get('struct_with_bias', False)):^4} | "
            f"{has_recall(data[q])}"
        )

    out = {
        "input": args.input,
        "n": n,
        "recall": recall,
        "hits": {s: hits[s] for s in strategies},
        "jaccard_threshold": args.jaccard_threshold,
        "hit_diff_qids": {q: per_qid[q] for q in HIT_DIFF_QIDS if q in per_qid},
    }
    out_path = HERE / f"replay_clustering_gap30_{args.input}.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten: {out_path}")

    if args.sweep_jaccard.strip():
        thresholds = [float(x.strip()) for x in args.sweep_jaccard.split(",") if x.strip()]
        print("\n--- jaccard threshold sweep ---")
        for tau in thresholds:
            jhits = 0
            for qid in qids:
                rec = data[qid]
                rss = rec.get("rollout_stats") or []
                db = qdb.get(qid, "")
                conn = build_db_connector(db) if db and not args.no_exec else None
                try:
                    _, _, _, sql_list, sql_rows = build_exec_maps(rec, conn)
                    if conn and sql_list:
                        if hit_sql(rec, pick_jaccard_merge(rss, sql_list, sql_rows, tau)):
                            jhits += 1
                finally:
                    if conn:
                        conn.disconnect()
            print(f"  tau={tau:.2f}  Hit@1={jhits}/{n}  Δ={jhits - rt:+d}")


if __name__ == "__main__":
    main()
