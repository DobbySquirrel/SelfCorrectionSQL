#!/usr/bin/env python3
"""
统计 Hit@1 与「任一路径对」准确率：
- Hit@1：每道题选一条 SQL（结果文件中已选好的 sql），在 DB 执行后与 gold 比对，正确即算对。
- 任一路径对：对每道题所有路径的 SQL 都执行一遍，只要有一条与 gold 一致则该题算对。

数据来源：结果 JSON（如 v4_arcwise_full_result_rollouts_20.json 或 arcwise_FORCE_S*_result.json）。
任一路径对：优先看 all_sqls_with_attributes 里是否有 is_correct True；
若没有，再在 rollout_stats 里用「执行结果签名」比较：只执行一次 gold 得 gold_signature，与各 rollout 的 result_buckets 最佳 signature 比较，无需对每条 selected_sql 再执行。

可后台运行：nohup python compute_arcwise_any_path_acc.py --result_file out/v4_arcwise_full_result_rollouts_20.json > acc.log 2>&1 &
"""
import argparse
import json
import sys
from pathlib import Path

# 保证从项目根目录可导入 workflows（与 test_mcts.py 一致）
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if _ROOT not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 默认结果目录与 arcwise 数据路径（与 run_mcts_v1_arcwise.sh 一致）
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "out"
MCTS_V3_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_GOLD_FILE = MCTS_V3_ROOT / "mcts_v3" / "data" / "arcwise_plat_sql_only_with_diff_withSchema.json"
STRATEGIES = ["FORCE_S1", "FORCE_S2", "FORCE_S7"]


def _load_gold_and_db_mapping(gold_file: Path):
    """加载 gold 文件，返回 (qid -> gold_sql, qid -> db_id)。"""
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


def _gold_result_signature(db_connector, gold_sql: str):
    """执行 gold_sql 一次，返回与 MCTSUtils.create_result_signature 一致的签名（不重复执行 selected_sql）。"""
    try:
        from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils
        df, err = db_connector.execute_query(gold_sql)
        result = {
            "valid": err is None and df is not None,
            "query_result": df.to_dict("records") if df is not None else [],
            "error": err,
        }
        return MCTSUtils.create_result_signature(result)
    except Exception:
        return None


def _any_path_correct_from_rollout_stats(rec, gold_sql: str, db_id: str, build_connector, compare_with_gold_fn=None):
    """
    任一路径对：用「执行结果签名」比较，不重复执行 selected_sql。
    只执行一次 gold_sql 得到 gold_signature，再看各 rollout 的 result_buckets 里最佳 signature 是否等于 gold_signature。
    compare_with_gold_fn 保留兼容，本实现不再使用。
    """
    rollout_stats = rec.get("rollout_stats") or []
    if not rollout_stats or not gold_sql or not db_id:
        return False
    try:
        from workflows.mcts_v4.utils.mcts_helpers import MCTSUtils
        db_connector = build_connector(db_id)
        try:
            gold_sig = _gold_result_signature(db_connector, gold_sql)
            if not gold_sig:
                return False
            for rs in rollout_stats:
                best_sig = MCTSUtils.get_best_result_signature(rs)
                if best_sig and best_sig == gold_sig:
                    return True
        finally:
            if db_connector:
                db_connector.disconnect()
    except Exception:
        pass
    return False


def main():
    parser = argparse.ArgumentParser(description="统计 arcwise 任一路径对即算对的准确率")
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"结果目录，默认 {DEFAULT_OUT_DIR}",
    )
    parser.add_argument(
        "--gold_file",
        type=Path,
        default=DEFAULT_GOLD_FILE,
        help="Gold/ppl JSON（含 question_id, db_id, SQL），用于从 rollout_stats 算任一路径对",
    )
    parser.add_argument(
        "--json_out",
        type=str,
        default=None,
        help="将汇总结果写入该 JSON 文件（可选）",
    )
    parser.add_argument(
        "--no_db",
        action="store_true",
        help="不连 DB：若没有 all_sqls_with_attributes 则只报 Hit@1，不根据 rollout_stats 现场比较",
    )
    parser.add_argument(
        "--result_file",
        type=Path,
        default=None,
        help="单个结果 JSON（如 v4_arcwise_full_result_rollouts_20.json）；指定时只算该文件的 Hit@1 与任一路径对",
    )
    args = parser.parse_args()
    out_dir = args.out_dir

    if args.result_file is None and not out_dir.exists():
        print(f"目录不存在: {out_dir}", file=sys.stderr)
        sys.exit(1)
    if args.result_file is not None and not args.result_file.exists():
        print(f"结果文件不存在: {args.result_file}", file=sys.stderr)
        sys.exit(1)

    gold_sqls = {}
    qid_to_db = {}
    compare_with_gold_fn = None
    build_db_connector_fn = None
    if not args.no_db and args.gold_file.exists():
        gold_sqls, qid_to_db = _load_gold_and_db_mapping(args.gold_file)
        try:
            from workflows.mcts_v4.test.test_mcts import compare_with_gold, build_db_connector
            compare_with_gold_fn = compare_with_gold
            build_db_connector_fn = build_db_connector
        except Exception:
            try:
                from workflows.mcts_v1.test.test_mcts import compare_with_gold, build_db_connector
                compare_with_gold_fn = compare_with_gold
                build_db_connector_fn = build_db_connector
            except Exception as e:
                print(f"[警告] 无法导入 compare_with_gold/build_db_connector: {e}，仅用已保存的 all_sqls_with_attributes", file=sys.stderr)
                gold_sqls = {}
                qid_to_db = {}

    print("指标说明：")
    print("  Hit@1 = 选中的一条 SQL 与 gold 执行结果一致即算对")
    print("  任一路径对 = 所有路径的 SQL 都执行一遍，任一条与 gold 一致则该题算对")
    print("（任一路径对：先看 all_sqls 的 is_correct；若无 True 再用 rollout_stats 的 result_signature 与 gold 签名比较，只执行 gold 一次）")
    print("-" * 65)

    summary = {}

    def _compute_one_file(data: dict, label: str) -> dict:
        """对一份 result JSON 计算 Hit@1 与任一路径对。返回 summary 条目。"""
        total = len(data)
        hit1_correct = 0
        any_correct = 0
        checked = 0
        for qid, rec in data.items():
            if not isinstance(rec, dict):
                continue
            stats = rec.get("stats") or {}
            gm = stats.get("gold_match")
            if gm is None:
                continue
            checked += 1
            if gm is True:
                hit1_correct += 1
                any_correct += 1
            else:
                all_sqls = rec.get("all_sqls_with_attributes") or []
                any_from_all_sqls = bool(all_sqls) and any(s.get("is_correct") is True for s in all_sqls)
                if any_from_all_sqls:
                    any_correct += 1
                else:
                    # 当 all_sqls 里没有 is_correct True 时，再去 rollout_stats 里用每条 selected_sql 与 gold 比较
                    if build_db_connector_fn and gold_sqls and qid_to_db:
                        gold_sql = gold_sqls.get(qid, "")
                        db_id = qid_to_db.get(qid, "")
                        if _any_path_correct_from_rollout_stats(
                            rec, gold_sql, db_id, build_db_connector_fn, compare_with_gold_fn
                        ):
                            any_correct += 1
        hit1_acc = (hit1_correct / checked * 100) if checked else 0
        any_acc = (any_correct / checked * 100) if checked else 0
        print(f"{label}:")
        print(f"  Hit@1:        {hit1_correct}/{checked} (已处理 {total} 条) -> {hit1_acc:.2f}%")
        print(f"  任一路径对:   {any_correct}/{checked} -> {any_acc:.2f}%")
        return {
            "hit1_correct": hit1_correct,
            "hit1_checked": checked,
            "hit1_accuracy_pct": round(hit1_acc, 2),
            "any_correct": any_correct,
            "any_checked": checked,
            "any_accuracy_pct": round(any_acc, 2),
            "total_processed": total,
        }

    if args.result_file is not None:
        with open(args.result_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        label = args.result_file.stem
        summary[label] = _compute_one_file(data, label)
        print("-" * 65)
        print(summary)
        if args.json_out:
            out_path = Path(args.json_out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            print(f"汇总已写入: {out_path}")
        return

    for strat in STRATEGIES:
        p = out_dir / f"arcwise_{strat}_result.json"
        if not p.exists():
            print(f"{strat}: 文件不存在 -> 跳过")
            summary[strat] = {"error": "文件不存在", "path": str(p)}
            continue
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        total = len(data)
        hit1_correct = 0
        any_correct = 0
        checked = 0
        for qid, rec in data.items():
            if not isinstance(rec, dict):
                continue
            stats = rec.get("stats") or {}
            gm = stats.get("gold_match")
            if gm is None:
                continue
            checked += 1
            if gm is True:
                hit1_correct += 1
                any_correct += 1
            else:
                all_sqls = rec.get("all_sqls_with_attributes") or []
                any_from_all_sqls = bool(all_sqls) and any(s.get("is_correct") is True for s in all_sqls)
                if any_from_all_sqls:
                    any_correct += 1
                else:
                    # 当 all_sqls 里没有 is_correct True 时，再去 rollout_stats 里用每条 selected_sql 与 gold 比较
                    if build_db_connector_fn and gold_sqls and qid_to_db:
                        gold_sql = gold_sqls.get(qid, "")
                        db_id = qid_to_db.get(qid, "")
                        if _any_path_correct_from_rollout_stats(
                            rec, gold_sql, db_id, build_db_connector_fn, compare_with_gold_fn
                        ):
                            any_correct += 1
        hit1_acc = (hit1_correct / checked * 100) if checked else 0
        any_acc = (any_correct / checked * 100) if checked else 0
        print(f"{strat}: Hit@1 {hit1_correct}/{checked} -> {hit1_acc:.2f}%; 任一路径对 {any_correct}/{checked} -> {any_acc:.2f}% (已处理 {total} 条)")
        summary[strat] = {
            "hit1_correct": hit1_correct,
            "hit1_accuracy_pct": round(hit1_acc, 2),
            "any_correct": any_correct,
            "any_checked": checked,
            "total_processed": total,
            "any_accuracy_pct": round(any_acc, 2),
        }
    print("-" * 65)
    print(summary)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"汇总已写入: {out_path}")


if __name__ == "__main__":
    main()
