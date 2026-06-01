#!/usr/bin/env python3
"""
用与 compute_arcwise_any_path_acc 相同的「执行 + 与 gold 比较」逻辑，
对 **Alpha-SQL 的 pkl 结果目录** 统计 Hit@1 与「任一路径对」准确率。

- 数据来源：Alpha-SQL 的 results 目录（每题一个 question_id.pkl）。
- Hit@1：用 Alpha-SQL 的 select_final_sql_query 选出一条 SQL，用 mcts_v4 的 compare_with_gold 与 gold 比较。
- 任一路径对：对 pkl 中所有路径的 final_sql_query 执行并与 gold 比较，任一对即算对。

用法（在 SelfCorrectionSQL 根目录）：
  export DB_ROOT_DIR=/path/to/dev_databases
  python workflows/mcts_v4/test/compute_arcwise_any_path_acc_from_pkl_dir.py \\
    --results_dir "Alpha-SQL-2.2.4/results/hpc2hdd/home/sshen190/wtao565/models/Qwen3-Coder-30B/arcwise" \\
    --gold_file "workflows/mcts_v3/data/arcwise_plat_sql_only_with_diff_withSchema.json" \\
    [--db_root_dir /path/to/dev_databases] [--output out.json]
"""
import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if _ROOT not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Alpha-SQL 的 select_final_sql_query
_ALPHA_SQL = _ROOT / "Alpha-SQL-2.2.4"
if _ALPHA_SQL.exists() and str(_ALPHA_SQL) not in sys.path:
    sys.path.insert(0, str(_ALPHA_SQL))

from workflows.mcts_v4.test.test_mcts import build_db_connector, compare_with_gold


def _load_gold(gold_file: Path):
    qid_to_gold = {}
    qid_to_db = {}
    with open(gold_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    for item in data:
        qid = item.get("question_id")
        if qid is None:
            continue
        qid = str(qid)
        qid_to_gold[qid] = (item.get("SQL") or "").strip()
        qid_to_db[qid] = (item.get("db_id") or item.get("db") or "").strip()
    return qid_to_gold, qid_to_db


def main():
    parser = argparse.ArgumentParser(
        description="对 Alpha-SQL pkl 结果目录用 mcts_v4 逻辑算 Hit@1 与任一路径对"
    )
    parser.add_argument(
        "--results_dir",
        type=Path,
        required=True,
        help="Alpha-SQL 结果目录（内含 question_id.pkl）",
    )
    parser.add_argument(
        "--gold_file",
        type=Path,
        required=True,
        help="Gold JSON（含 question_id, db_id, SQL）",
    )
    parser.add_argument(
        "--db_root_dir",
        type=str,
        default=None,
        help="数据库根目录；不指定则用环境变量 DB_ROOT_DIR",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="将汇总结果写入该 JSON 文件（可选）",
    )
    args = parser.parse_args()

    results_dir = args.results_dir.resolve() if not args.results_dir.is_absolute() else args.results_dir
    gold_file = args.gold_file.resolve() if not args.gold_file.is_absolute() else args.gold_file
    if not results_dir.exists():
        print(f"错误: 结果目录不存在: {results_dir}", file=sys.stderr)
        sys.exit(1)
    if not gold_file.exists():
        print(f"错误: gold 文件不存在: {gold_file}", file=sys.stderr)
        sys.exit(1)

    db_root_dir = args.db_root_dir or os.environ.get("DB_ROOT_DIR") or ""
    if args.db_root_dir:
        os.environ["DB_ROOT_DIR"] = args.db_root_dir
    elif db_root_dir:
        os.environ["DB_ROOT_DIR"] = db_root_dir

    qid_to_gold, qid_to_db = _load_gold(gold_file)
    import pickle
    from tqdm import tqdm
    try:
        from alphasql.runner.sql_selection import select_final_sql_query
        _use_alpha_sql_hit1 = True
    except Exception as e:
        print(f"[提示] 未导入 Alpha-SQL select_final_sql_query（{e}），Hit@1 使用首条路径 SQL 作为选中", file=sys.stderr)
        select_final_sql_query = None
        _use_alpha_sql_hit1 = False

    pkl_files = list(results_dir.glob("*.pkl"))
    task_ids = []
    for f in pkl_files:
        try:
            task_ids.append(int(f.stem))
        except ValueError:
            pass
    common = sorted(set(task_ids) & set(int(k) for k in qid_to_gold.keys()))
    # 过滤：存在 db 的题目（若有 db_root_dir 则检查路径存在）
    db_root_path = Path(db_root_dir) if db_root_dir else None
    tasks = []
    for qid in common:
        qid_s = str(qid)
        gold_sql = qid_to_gold.get(qid_s)
        db_id = qid_to_db.get(qid_s)
        if not gold_sql or not db_id:
            continue
        if db_root_path is not None:
            db_path = db_root_path / db_id / f"{db_id}.sqlite"
            if not db_path.exists():
                continue
        pkl_path = results_dir / f"{qid}.pkl"
        if not pkl_path.exists():
            continue
        tasks.append((qid, str(pkl_path), gold_sql, db_id))

    checked = len(tasks)
    if checked == 0:
        print("没有可评估的题目（请检查 --results_dir、--gold_file、--db_root_dir 及 DB 是否存在）")
        if db_root_dir:
            print(f"  db_root_dir: {db_root_dir}")
        sys.exit(1)

    print("指标说明：Hit@1 = 选中的一条 SQL 与 gold 执行结果一致；任一路径对 = 任一条路径 SQL 与 gold 一致")
    print("（使用 mcts_v4 test_mcts 的 compare_with_gold + build_db_connector）")
    print(f"  结果目录: {results_dir}")
    print(f"  gold: {gold_file}")
    print(f"  待评估题数: {checked}")
    print("-" * 60)

    hit1_correct = 0
    any_correct = 0
    first_error = None
    for qid, pkl_path, gold_sql, db_id in tqdm(tasks, desc="评估", unit="题"):
        hit1_ok = False
        any_ok = False
        try:
            conn = build_db_connector(db_id)
            try:
                with open(pkl_path, "rb") as f:
                    all_paths = pickle.load(f)
                if not all_paths:
                    continue
                for path in all_paths:
                    if not path:
                        continue
                    sql = (getattr(path[-1], "final_sql_query", None) or "").strip()
                    if sql and compare_with_gold(sql, gold_sql, conn):
                        any_ok = True
                        break
                if _use_alpha_sql_hit1 and select_final_sql_query:
                    sel = select_final_sql_query(pkl_path, db_root_dir)
                    pred_sql = (sel.get("sql") or "").strip()
                else:
                    pred_sql = ""
                    for path in all_paths:
                        if path:
                            pred_sql = (getattr(path[-1], "final_sql_query", None) or "").strip()
                            break
                if pred_sql and pred_sql != "ERROR" and compare_with_gold(pred_sql, gold_sql, conn):
                    hit1_ok = True
            finally:
                conn.disconnect()
        except Exception as e:
            if first_error is None:
                first_error = (qid, str(e))
        if hit1_ok:
            hit1_correct += 1
        if any_ok:
            any_correct += 1
    if first_error is not None and hit1_correct == 0 and any_correct == 0:
        print(f"[提示] 所有题均未命中，首题报错示例 qid={first_error[0]}: {first_error[1][:200]}", file=sys.stderr)

    hit1_acc = (hit1_correct / checked * 100) if checked else 0
    any_acc = (any_correct / checked * 100) if checked else 0
    print("")
    print("========== Arcwise 准确率（mcts_v4 执行逻辑）==========")
    print(f"  评估题目数: {checked}")
    print(f"  Hit@1:        {hit1_correct}/{checked} -> {hit1_acc:.2f}%")
    print(f"  任一路径对:   {any_correct}/{checked} -> {any_acc:.2f}%")
    print("======================================================")

    summary = {
        "hit1_correct": hit1_correct,
        "hit1_checked": checked,
        "hit1_accuracy_pct": round(hit1_acc, 2),
        "any_correct": any_correct,
        "any_checked": checked,
        "any_accuracy_pct": round(any_acc, 2),
        "results_dir": str(results_dir),
        "gold_file": str(gold_file),
        "db_root_dir": db_root_dir or os.environ.get("DB_ROOT_DIR", ""),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"  结果已写入: {args.output}")


if __name__ == "__main__":
    main()
