#!/usr/bin/env python3
"""
按「执行一置信」算准确率：按执行结果 signature 多数投票，再与 gold 的 signature 比较（只执行 gold 一次，不执行预测 SQL）。

signature 与 compare_with_gold 的集合比较一致：mcts_helpers.create_result_signature 对结果行排序后再哈希，
且本脚本对 gold 的 query_result 做 Python 原生类型归一化，保证与 workflow 内执行结果格式一致。

提供两种统计：
1) 基于 all_sqls_with_attributes 的 signature 多数投票；
2) 基于 rollout_stats 的 all_sql_variants 的 signature 多数投票。
每题只执行 1 次 gold 取 hash，与多数 signature 比较，更快。

用法：
  python workflows/mcts_v4/test/compute_arcwise_execution_confidence_acc.py \\
    --result_file workflows/mcts_v4/test/out/v4_arcwise_full_result_rollouts_20_decompose_S1_suggestions.json \\
    --gold_file workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json
"""
import argparse
import json
import sys
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, desc="", unit="题", **kwargs):
        return iterable

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if _ROOT not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_GOLD_FILE = Path(__file__).resolve().parent.parent.parent / "mcts_v3" / "data" / "arcwise_plat_sql_only_with_diff_withSchema.json"


def _load_gold_and_db_mapping(gold_file: Path):
    gold_sqls = {}
    qid_to_db = {}
    with open(gold_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    for item in data:
        qid = item.get("question_id")
        if qid is None:
            continue
        qid = str(qid)
        gold_sqls[qid] = item.get("SQL", "") or ""
        qid_to_db[qid] = (item.get("db_id") or item.get("db") or "").strip()
    return gold_sqls, qid_to_db


def _build_sql_to_signature_from_rollout_stats(rollout_stats: list) -> dict:
    """从 rollout_stats 建 sql文本 -> result_signature。selected_sql 的 signature 取该 rollout 的 result_buckets 中 count 最大的。"""
    sql_to_sig = {}
    for rs in rollout_stats:
        buckets = rs.get("result_buckets") or {}
        variants = rs.get("all_sql_variants") or []
        best_sig = None
        if buckets:
            best_sig = max(buckets, key=lambda k: buckets[k])
        for info in variants:
            if not info.get("valid"):
                continue
            s = (info.get("sql") or "").strip()
            sig = info.get("result_signature")
            if s and sig:
                sql_to_sig[s] = sig
        sel = (rs.get("selected_sql") or "").strip()
        if sel and best_sig and sel not in sql_to_sig:
            sql_to_sig[sel] = best_sig
    return sql_to_sig


def _normalize_query_result_for_signature(rows: list) -> list:
    """将 query_result 转为 Python 原生类型，与 MCTSUtils.create_result_signature 内部处理一致。"""
    if not rows:
        return rows
    try:
        import numpy as np
    except ImportError:
        np = None
    out = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        new_row = {}
        for k, v in row.items():
            if v is None:
                new_row[k] = None
            elif isinstance(v, float) and (v != v):  # NaN
                new_row[k] = None
            elif np is not None and hasattr(np, "integer") and isinstance(v, np.integer):
                new_row[k] = int(v)
            elif np is not None and hasattr(np, "floating") and isinstance(v, np.floating):
                new_row[k] = float(v) if (v == v) else None  # NaN -> None
            elif isinstance(v, (int, float, str)):
                new_row[k] = v
            else:
                new_row[k] = str(v) if v is not None else None
        out.append(new_row)
    return out


def _gold_result_signature(db_connector, gold_sql: str) -> Optional[str]:
    """执行 gold_sql 一次，返回与 MCTSUtils.create_result_signature 一致的签名（与 compare_with_gold 的集合比较一致）。"""
    try:
        from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils
        df, err = db_connector.execute_query(gold_sql)
        query_result = df.to_dict("records") if df is not None else []
        query_result = _normalize_query_result_for_signature(query_result)
        result = {
            "valid": err is None and df is not None,
            "query_result": query_result,
            "error": err,
        }
        return MCTSUtils.create_result_signature(result)
    except Exception:
        return None


def _majority_signature_from_all_sqls_with_attributes(rec: dict) -> Optional[str]:
    """基于 all_sqls_with_attributes 按 signature 多数投票，返回获胜的 signature（不执行 SQL）。"""
    all_sqls = rec.get("all_sqls_with_attributes") or []
    rollout_stats = rec.get("rollout_stats") or []
    if not all_sqls or not rollout_stats:
        return None
    sql_to_sig = _build_sql_to_signature_from_rollout_stats(rollout_stats)
    sig_to_count = Counter()
    for item in all_sqls:
        s = (item.get("sql") or "").strip()
        if not s:
            continue
        sig = sql_to_sig.get(s)
        if not sig:
            continue
        sig_to_count[sig] += 1
    if not sig_to_count:
        return None
    best_sig, _ = sig_to_count.most_common(1)[0]
    return best_sig


def _majority_signature_from_rollout_stats(rec: dict) -> Optional[str]:
    """基于 rollout_stats 的 all_sql_variants 按 result_signature 多数投票，返回获胜的 signature。"""
    rollout_stats = rec.get("rollout_stats") or []
    sig_to_count = Counter()
    for rs in rollout_stats:
        for info in rs.get("all_sql_variants") or []:
            if not info.get("valid"):
                continue
            sig = info.get("result_signature")
            if not sig:
                continue
            sig_to_count[sig] += 1
    if not sig_to_count:
        return None
    best_sig, _ = sig_to_count.most_common(1)[0]
    return best_sig


def main():
    parser = argparse.ArgumentParser(description="按执行结果多数投票（执行一置信）算准确率")
    parser.add_argument("--result_file", type=Path, required=True, help="结果 JSON")
    parser.add_argument("--gold_file", type=Path, default=DEFAULT_GOLD_FILE, help="Gold JSON（含 question_id, db_id, SQL）")
    parser.add_argument("--n_parallel", type=int, default=16, help="并行题数，默认 1 为串行；建议 8～32 加速")
    args = parser.parse_args()
    if not args.result_file.exists():
        print(f"文件不存在: {args.result_file}", file=sys.stderr)
        sys.exit(1)
    if not args.gold_file.exists():
        print(f"Gold 文件不存在: {args.gold_file}", file=sys.stderr)
        sys.exit(1)

    gold_sqls, qid_to_db = _load_gold_and_db_mapping(args.gold_file)
    try:
        from workflows.mcts_v4.test.test_mcts import build_db_connector
    except Exception as e:
        print(f"无法导入 build_db_connector: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.result_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    n_parallel = max(1, int(args.n_parallel))

    def _eval_one(qid: str, rec: dict, gold_sql: str, db_id: str, majority_sig_fn) -> Tuple[int, int, int]:
        """单题评估：返回 (correct, checked, no_majority)。"""
        if not isinstance(rec, dict) or not gold_sql or not db_id:
            return (0, 0, 0)
        majority_sig = majority_sig_fn(rec)
        if not majority_sig:
            return (0, 0, 1)
        try:
            db = build_db_connector(db_id)
            try:
                gold_sig = _gold_result_signature(db, gold_sql)
                ok = 1 if (gold_sig and majority_sig == gold_sig) else 0
                return (ok, 1, 0)
            finally:
                db.disconnect()
        except Exception:
            return (0, 1, 0)

    def run_majority_acc(majority_sig_fn, desc: str = "评估") -> Tuple[int, int, int]:
        """只执行 gold 一次得 gold_signature，与多数投票的 signature 比较。支持 n_parallel 并行。"""
        tasks = []
        for qid, rec in data.items():
            if not isinstance(rec, dict):
                continue
            gold_sql = gold_sqls.get(qid, "")
            db_id = qid_to_db.get(qid, "")
            if not gold_sql or not db_id:
                continue
            tasks.append((qid, rec, gold_sql, db_id, majority_sig_fn))
        if not tasks:
            return 0, 0, 0
        correct = checked = no_majority = 0
        if n_parallel <= 1:
            for (qid, rec, gold_sql, db_id, fn) in tqdm(tasks, desc=desc, unit="题"):
                c, ch, nm = _eval_one(qid, rec, gold_sql, db_id, fn)
                correct += c
                checked += ch
                no_majority += nm
        else:
            with ThreadPoolExecutor(max_workers=n_parallel) as ex:
                futures = {ex.submit(_eval_one, qid, rec, gs, di, fn): None for (qid, rec, gs, di, fn) in tasks}
                for fut in tqdm(as_completed(futures), total=len(futures), desc=desc, unit="题"):
                    c, ch, nm = fut.result()
                    correct += c
                    checked += ch
                    no_majority += nm
        return correct, checked, no_majority

    total = len([r for r in data.values() if isinstance(r, dict)])
    print("【执行一置信】按「执行结果」多数投票，取出现次数最多的结果对应的 SQL 与 gold 比较")
    if n_parallel > 1:
        print(f"  并行数: {n_parallel}")
    print()

    c1, n1, skip1 = run_majority_acc(_majority_signature_from_all_sqls_with_attributes, desc="1/2 all_sqls")
    acc1 = (c1 / n1 * 100) if n1 else 0.0
    print("1) 基于 all_sqls_with_attributes（signature 从 rollout_stats 查，gold 也只执行一次用 hash 比）")
    print(f"   正确: {c1}/{n1} (无多数: {skip1}, 总题数: {total}) -> {acc1:.2f}%")
    print()

    c2, n2, skip2 = run_majority_acc(_majority_signature_from_rollout_stats, desc="2/2 rollout_stats")
    acc2 = (c2 / n2 * 100) if n2 else 0.0
    print("2) 基于 rollout_stats（每条 rollout 的 all_sql_variants，gold 也只执行一次用 hash 比）")
    print(f"   正确: {c2}/{n2} (无多数: {skip2}, 总题数: {total}) -> {acc2:.2f}%")
    print()
    print("---")
    print("【任一路径对 recall】在 compute_arcwise_any_path_acc.py 中：")
    print("  优先用 all_sqls_with_attributes 里的 is_correct（若有任一 True 则该题算对）；")
    print("  若没有或未填 is_correct，则用 rollout_stats 里每条 rollout 的 selected_sql 现场执行与 gold 比较，任一对即算对。")


if __name__ == "__main__":
    main()
