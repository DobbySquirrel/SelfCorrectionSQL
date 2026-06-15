#!/usr/bin/env python3
"""Deep analysis: Hit@1-9, rollout sufficiency, Mode-C path contribution (E6 full498 vs Alpha)."""

from __future__ import annotations

import json
import pickle
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workflows/mcts_v4/test/out/clarify_a0_a2_coder/analysis/parallel_during_rerun"))

from selector_replay import build_clusters  # noqa: E402

E6_JSON = ROOT / "workflows/mcts_v4/test/out/cte_diverse/v4_colbind_v2_dual03_min2sq_abl5_e6_bootstrap_full498_rollouts12.json"
ALPHA_EVAL = ROOT / "results/arcwise_eval_result.json"
GOLD = ROOT / "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json"
ALPHA_PKL_DIR = ROOT / "Alpha-SQL-2.2.4/results/hpc2hdd/home/sshen190/wtao565/models/Qwen3-Coder-30B/arcwise"
ALPHA_ROOT = ROOT / "Alpha-SQL-2.2.4"
DB_ROOT = Path("/hpc2hdd/home/sshen190/wtao565/datasets/dev_20240627/dev_databases")


def gold_sigs(rec: dict) -> Set[str]:
    sigs: Set[str] = set()
    for x in rec.get("all_sqls_with_attributes") or []:
        if not x.get("is_correct"):
            continue
        sql = (x.get("sql") or "").strip()
        for rs in rec.get("rollout_stats") or []:
            for v in rs.get("all_sql_variants") or []:
                if (v.get("sql") or "").strip() == sql:
                    sig = v.get("result_signature") or ""
                    if sig:
                        sigs.add(sig)
    return sigs


def ranked_r4_sigs(rss: List[dict]) -> List[str]:
    clusters = build_clusters(rss)
    if not clusters:
        return []
    votes: Counter = Counter()
    for r in rss:
        rb = r.get("result_buckets") or {}
        if not rb:
            continue
        mc = max(rb.values())
        for sig, c in rb.items():
            if c == mc:
                votes[sig] += 1
    if not votes:
        return list(clusters.keys())
    ranked = sorted(votes.keys(), key=lambda s: (-votes[s], -clusters[s].max_rollout_reward if s in clusters else 0))
    return ranked


def hit_at_k_r4(rec: dict, k: int) -> bool:
    gs = gold_sigs(rec)
    if not gs:
        return False
    top = set(ranked_r4_sigs(rec.get("rollout_stats") or [])[:k])
    return bool(gs & top)


def has_recall(rec: dict) -> bool:
    if any(x.get("is_correct") for x in (rec.get("all_sqls_with_attributes") or [])):
        return True
    return bool(gold_sigs(rec))


def acc_hit1(rec: dict) -> bool:
    if (rec.get("stats") or {}).get("timeout_fallback_failed"):
        return False
    return bool((rec.get("stats") or {}).get("gold_match"))


def gold_cluster_rank_r4(rec: dict) -> Optional[int]:
    gs = gold_sigs(rec)
    if not gs:
        return None
    for i, sig in enumerate(ranked_r4_sigs(rec.get("rollout_stats") or []), start=1):
        if sig in gs:
            return i
    return None


def path_label(spec: dict) -> str:
    if spec.get("bootstrap_direct_sql"):
        return "bootstrap"
    strat = spec.get("strategy") or ""
    if strat == "reversed_prior_rollout":
        t = spec.get("temp", MODE_C_REVERSED_TEMP if "reversed" in str(spec) else "")
        return f"reversed@{spec.get('temp', 0.6)}"
    temp = spec.get("temp")
    inner = spec.get("strategy") or "unknown"
    if temp is not None:
        return f"t{temp}_{inner}"
    return strat or "other"


MODE_C_REVERSED_TEMP = 0.6


def analyze_mode_c_paths(e6: dict, qids: List[str]) -> dict:
    path_kept = Counter()
    path_expands = Counter()
    path_questions = Counter()
    for q in qids:
        seen_paths: Set[str] = set()
        for tr in e6[q].get("decompose_expand_traces") or []:
            specs = tr.get("per_temp_schema_strategy") or []
            if not specs and tr.get("parallel_call_specs"):
                specs = tr.get("parallel_call_specs") or []
            kept = int(tr.get("n_candidates") or tr.get("diverse_kept") or 0)
            for spec in specs:
                if not isinstance(spec, dict):
                    continue
                lab = path_label(spec)
                path_expands[lab] += 1
                if kept > 0:
                    path_kept[lab] += 1
                if lab not in seen_paths:
                    seen_paths.add(lab)
                    path_questions[lab] += 1
    rows = []
    for lab in sorted(set(path_expands) | set(path_kept)):
        exp = path_expands[lab]
        rows.append(
            {
                "path": lab,
                "expands": exp,
                "kept_rate": round(path_kept[lab] / exp, 3) if exp else 0,
                "questions_with_path": path_questions[lab],
            }
        )
    rows.sort(key=lambda x: -x["expands"])
    return {"paths": rows}


def _alpha_ranked_path_indices(pkl_path: str, db_root: str) -> List[int]:
    sys.path.insert(0, str(ALPHA_ROOT))
    from alphasql.database.sql_execution import cached_execute_sql_with_timeout, is_valid_execution_result

    with open(pkl_path, "rb") as f:
        results = pickle.load(f)
    if not results:
        return []
    db_id = results[0][0].db_id
    db_path = f"{db_root}/{db_id}/{db_id}.sqlite"
    valid_groups: dict = defaultdict(list)
    invalid_groups: dict = defaultdict(list)
    for idx, path in enumerate(results):
        if not path:
            continue
        sql = (getattr(path[-1], "final_sql_query", None) or "").strip()
        if not sql:
            continue
        ans = cached_execute_sql_with_timeout(db_path, sql)
        if ans.result_type.value != "success":
            continue
        key = frozenset(ans.result) if ans.result else frozenset()
        if is_valid_execution_result(ans):
            valid_groups[key].append(idx)
        else:
            invalid_groups[key].append(idx)
    groups = valid_groups if valid_groups else invalid_groups
    if not groups:
        return []
    total = sum(len(v) for v in groups.values()) or 1
    scored = []
    for _ans, indices in groups.items():
        sc = len(indices) / total
        scored.append((indices[0], sc))
    scored.sort(key=lambda x: -x[1])
    return [i for i, _ in scored]


def _alpha_hit_at_k_task(args):
    qid, pkl_path, db_path, gold_sql, k_max = args
    sys.path.insert(0, str(ALPHA_ROOT))
    from alphasql.database.sql_execution import cached_execute_sql_with_timeout, SQLExecutionResultType

    def match(sql: str) -> bool:
        if not sql or not gold_sql:
            return False
        try:
            g = cached_execute_sql_with_timeout(db_path, gold_sql)
            p = cached_execute_sql_with_timeout(db_path, sql)
            if g.result_type != SQLExecutionResultType.SUCCESS:
                return False
            if p.result_type != SQLExecutionResultType.SUCCESS:
                return False
            gf = frozenset(tuple(r) for r in (g.result or []))
            pf = frozenset(tuple(r) for r in (p.result or []))
            return gf == pf
        except Exception:
            return False

    ranked = _alpha_ranked_path_indices(pkl_path, str(DB_ROOT))
    hits = {k: False for k in range(1, k_max + 1)}
    try:
        with open(pkl_path, "rb") as f:
            results = pickle.load(f)
        for k in range(1, k_max + 1):
            for pi in ranked[:k]:
                sql = (getattr(results[pi][-1], "final_sql_query", None) or "").strip()
                if match(sql):
                    hits[k] = True
                    break
    except Exception:
        pass
    return qid, hits


def main() -> None:
    e6 = json.loads(E6_JSON.read_text())
    alpha_pq = json.loads(ALPHA_EVAL.read_text())["per_question"]
    gold_rows = {str(r["question_id"]): r for r in json.loads(GOLD.read_text())}
    cohort = sorted(set(e6.keys()) & set(alpha_pq.keys()), key=int)

    print("=" * 60)
    print(f"E6 full498 deep analysis | cohort n={len(cohort)}")
    print("=" * 60)

    # --- 1. Hit@1-9 ---
    print("\n## 1. Hit@k (oracle cluster/path rank)")
    print("| k | E6 R4 | Alpha path | Δ(E6-α) |")
    print("|---:|---:|---:|---:|")
    e6_hk = {k: 0 for k in range(1, 10)}
    alpha_hk = {k: 0 for k in range(1, 10)}

    for q in cohort:
        for k in range(1, 10):
            if hit_at_k_r4(e6[q], k):
                e6_hk[k] += 1

    # Alpha hit@k (parallel, sample if too slow - run all 497)
    tasks = []
    for q in cohort:
        pkl = ALPHA_PKL_DIR / f"{q}.pkl"
        if not pkl.exists():
            continue
        row = gold_rows.get(q, {})
        db_id = row.get("db_id", "")
        db_path = str(DB_ROOT / db_id / f"{db_id}.sqlite")
        gold_sql = row.get("SQL", "")
        tasks.append((q, str(pkl), db_path, gold_sql, 9))

    alpha_hits_by_q: Dict[str, Dict[int, bool]] = {}
    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_alpha_hit_at_k_task, t) for t in tasks]
        for i, fut in enumerate(as_completed(futs)):
            qid, hits = fut.result()
            alpha_hits_by_q[qid] = hits
            if (i + 1) % 100 == 0:
                print(f"  alpha hit@k progress {i+1}/{len(tasks)}", file=sys.stderr)

    for q in cohort:
        h = alpha_hits_by_q.get(q, {})
        for k in range(1, 10):
            if h.get(k):
                alpha_hk[k] += 1

    n = len(cohort)
    for k in range(1, 10):
        print(f"| {k} | {e6_hk[k]}/{n} ({100*e6_hk[k]/n:.1f}%) | {alpha_hk[k]}/{n} ({100*alpha_hk[k]/n:.1f}%) | {e6_hk[k]-alpha_hk[k]:+d} |")

    print(f"\nActual selector Hit@1: E6 {sum(acc_hit1(e6[q]) for q in cohort)}/{n} | Alpha {sum(alpha_pq[q]['hit1_ok'] for q in cohort)}/{n}")

    # --- 2. Rollout sufficiency ---
    print("\n## 2. Rollout 次数是否足够")
    rc = Counter(len(r.get("rollout_stats") or []) for r in (e6[q] for q in cohort))
    print("rollout_stats 长度分布:", dict(sorted(rc.items())))
    full12 = sum(1 for q in cohort if len(e6[q].get("rollout_stats") or []) >= 12)
    print(f"≥12 rollout: {full12}/{n} ({100*full12/n:.1f}%)")

    recalled = [q for q in cohort if has_recall(e6[q])]
    ranks = [gold_cluster_rank_r4(e6[q]) for q in recalled]
    rank_buckets = Counter()
    for r in ranks:
        if r == 1:
            rank_buckets["1"] += 1
        elif r and r <= 3:
            rank_buckets["2-3"] += 1
        elif r and r <= 9:
            rank_buckets["4-9"] += 1
        elif r and r <= 12:
            rank_buckets["10-12"] += 1
        elif r:
            rank_buckets["13+"] += 1
        else:
            rank_buckets["not_in_pool"] += 1
    print(f"有 recall 题 n={len(recalled)}，gold cluster R4 rank 分布: {dict(rank_buckets)}")
    sel_loss = sum(1 for q in recalled if has_recall(e6[q]) and not acc_hit1(e6[q]))
    h9_cov = sum(1 for q in recalled if hit_at_k_r4(e6[q], 9))
    print(f"有 recall 但 Hit@1 失败（selector 损失）: {sel_loss}")
    print(f"有 recall 且 Hit@9 oracle 覆盖: {h9_cov}/{len(recalled)}")
    extra_rollout_gain = hit_at_k_r4  # reuse
    h6 = sum(1 for q in cohort if extra_rollout_gain(e6[q], 6))
    h9 = sum(1 for q in cohort if extra_rollout_gain(e6[q], 9))
    h12 = sum(1 for q in cohort if extra_rollout_gain(e6[q], 12))
    print(f"Hit@6/9/12 oracle: {h6}/{n}, {h9}/{n}, {h12}/{n}  (h12≈recall upper bound)")

    pool_sizes = [len(r.get("all_sqls_with_attributes") or []) for q in cohort for r in [e6[q]]]
    print(f"E6 pool size mean={sum(pool_sizes)/len(pool_sizes):.1f} p50={sorted(pool_sizes)[len(pool_sizes)//2]}")

    # --- 3. Mode C path contribution ---
    print("\n## 3. CTE Mode-C 各路径贡献（expand 次数 / kept 率）")
    path_stats = analyze_mode_c_paths(e6, cohort)
    print("| path | expands | kept_rate | questions |")
    print("|------|---:|---:|---:|")
    for row in path_stats["paths"]:
        print(f"| {row['path']} | {row['expands']} | {row['kept_rate']:.1%} | {row['questions_with_path']} |")

    # unique path kept rates differ?
    rates = [r["kept_rate"] for r in path_stats["paths"] if r["expands"] >= 100]
    if rates:
        print(f"高流量路径 kept_rate 范围: {min(rates):.1%} ~ {max(rates):.1%} （并不相同）")

    # --- 4. 如何超过 Alpha（差 6 题 Hit@1）---
    print("\n## 4. 追 Alpha Hit@1（当前差 6 题）")
    e6_ok = {q for q in cohort if acc_hit1(e6[q])}
    a_ok = {q for q in cohort if alpha_pq[q]["hit1_ok"]}
    a_only = sorted(a_ok - e6_ok, key=int)
    e6_only = sorted(e6_ok - a_ok, key=int)
    both_wrong = [q for q in cohort if q not in e6_ok and q not in a_ok]
    print(f"Alpha 独赢 Hit@1: {len(a_only)} 题 | E6 独赢: {len(e6_only)} 题 | 都错: {len(both_wrong)}")
    print(f"净差 Alpha−E6 = {len(a_only)-len(e6_only)} (=6)")

    buckets = Counter()
    for q in a_only:
        rec = e6[q]
        if not has_recall(rec):
            buckets["recall_miss"] += 1
        elif gold_cluster_rank_r4(rec) and gold_cluster_rank_r4(rec) <= 9:
            buckets["selector_miss_top9"] += 1
        elif has_recall(rec):
            buckets["selector_miss_low_rank"] += 1
        else:
            buckets["other"] += 1
    print(f"Alpha 独赢 E6 失败原因: {dict(buckets)}")

    timeout_qs = [q for q in a_only if (e6[q].get("stats") or {}).get("timeout_fallback") or (e6[q].get("stats") or {}).get("timeout_fallback_failed")]
    print(f"其中 timeout 相关: {len(timeout_qs)} {timeout_qs[:10]}")

    recoverable = sum(1 for q in a_only if has_recall(e6[q]) and gold_cluster_rank_r4(e6[q]) and gold_cluster_rank_r4(e6[q]) <= 3)
    print(f"Alpha 独赢里 E6 gold@R4 top-3 可救: {recoverable} 题（selector/tiebreak 改进）")
    recall_fix = sum(1 for q in a_only if not has_recall(e6[q]) and alpha_pq[q]["any_ok"])
    print(f"Alpha 独赢里 E6 recall 缺失: {sum(1 for q in a_only if not has_recall(e6[q]))} 题（其中 Alpha any_ok: {recall_fix}）")


if __name__ == "__main__":
    main()
