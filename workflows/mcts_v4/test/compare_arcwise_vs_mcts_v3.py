#!/usr/bin/env python3
"""
找出：在 arcwise FORCE_S1 / FORCE_S2 / FORCE_S7 里「任一路径对」都算错的题目，
但在 mcts_v3 的 run_mcts_sql_*.json 里 hit=true（即框架做对了）。
这类题目可能是 mcts_v1 框架/连接等导致无法跑通的 bug case。

用法:
  python compare_arcwise_vs_mcts_v3.py --mcts_v3_json path/to/run_mcts_sql_20260312_111905.json
  python compare_arcwise_vs_mcts_v3.py --mcts_v3_json path/to/run_mcts_sql_20260312_111905.json --no_db --out_list bug_candidates.json
"""
import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if _ROOT not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workflows.mcts_v1.test.compute_arcwise_any_path_acc import (
    STRATEGIES,
    DEFAULT_OUT_DIR,
    DEFAULT_GOLD_FILE,
    _load_gold_and_db_mapping,
    _any_path_correct_from_rollout_stats,
)


def _any_path_correct_for_record(
    rec,
    qid: str,
    gold_sqls: dict,
    qid_to_db: dict,
    build_connector_fn,
    compare_with_gold_fn,
) -> bool:
    """对单条 arcwise 记录判断「任一路径对即对」。与 compute_arcwise_any_path_acc 逻辑一致。"""
    if not isinstance(rec, dict):
        return False
    stats = rec.get("stats") or {}
    gm = stats.get("gold_match")
    if gm is True:
        return True
    all_sqls = rec.get("all_sqls_with_attributes") or []
    if all_sqls and any(s.get("is_correct") is True for s in all_sqls):
        return True
    if build_connector_fn and gold_sqls and qid_to_db:
        gold_sql = gold_sqls.get(qid, "")
        db_id = qid_to_db.get(qid, "")
        if _any_path_correct_from_rollout_stats(
            rec, gold_sql, db_id, build_connector_fn, compare_with_gold_fn
        ):
            return True
    return False


def load_mcts_v3_by_qid(mcts_v3_path: Path) -> dict:
    """mcts_v3 结果是 JSON 数组，每项 question_id, hit, question, db_id, gold_sql。返回 qid -> 该项（含 hit）。"""
    with open(mcts_v3_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return {}
    by_qid = {}
    for item in data:
        qid = item.get("question_id")
        if qid is None:
            continue
        qid = str(qid)
        by_qid[qid] = {
            "hit": item.get("hit") is True,
            "question": item.get("question", ""),
            "db_id": item.get("db_id", ""),
            "gold_sql": item.get("gold_sql", ""),
        }
    return by_qid


def main():
    parser = argparse.ArgumentParser(
        description="找出 S1/S2/S7 都错但 mcts_v3 对的题目（疑似框架 bug）"
    )
    parser.add_argument(
        "--mcts_v3_json",
        type=Path,
        required=True,
        help="mcts_v3 结果 JSON，如 run_mcts_sql_20260312_111905.json",
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"arcwise 结果目录，默认 {DEFAULT_OUT_DIR}",
    )
    parser.add_argument(
        "--gold_file",
        type=Path,
        default=DEFAULT_GOLD_FILE,
        help="Gold 文件（与 compute_arcwise_any_path_acc 一致）",
    )
    parser.add_argument(
        "--no_db",
        action="store_true",
        help="不连 DB：仅用 stats.gold_match 与 all_sqls_with_attributes 判断 arcwise 对错",
    )
    parser.add_argument(
        "--out_list",
        type=str,
        default=None,
        help="将候选 question_id 列表及详情写入该 JSON 文件",
    )
    args = parser.parse_args()

    if not args.mcts_v3_json.exists():
        print(f"mcts_v3 文件不存在: {args.mcts_v3_json}", file=sys.stderr)
        sys.exit(1)
    if not args.out_dir.exists():
        print(f"out_dir 不存在: {args.out_dir}", file=sys.stderr)
        sys.exit(1)

    gold_sqls = {}
    qid_to_db = {}
    build_db_connector_fn = None
    compare_with_gold_fn = None
    if not args.no_db and args.gold_file.exists():
        gold_sqls, qid_to_db = _load_gold_and_db_mapping(args.gold_file)
        try:
            from workflows.mcts_v1.test.test_mcts import compare_with_gold, build_db_connector
            build_db_connector_fn = build_db_connector
            compare_with_gold_fn = compare_with_gold
        except Exception as e:
            print(f"[警告] 无法导入 compare_with_gold/build_db_connector: {e}，仅用已保存字段", file=sys.stderr)
            gold_sqls = {}
            qid_to_db = {}

    # 加载 arcwise 三策略
    arcwise = {}
    for strat in STRATEGIES:
        p = args.out_dir / f"arcwise_{strat}_result.json"
        if not p.exists():
            print(f"缺少 {strat} 文件: {p}", file=sys.stderr)
            sys.exit(1)
        with open(p, "r", encoding="utf-8") as f:
            arcwise[strat] = json.load(f)

    mcts_v3_by_qid = load_mcts_v3_by_qid(args.mcts_v3_json)
    print(f"mcts_v3 题目数: {len(mcts_v3_by_qid)}")

    # 仅在「三个 arcwise 文件都有且 mcts_v3 也有」的 qid 上比较
    all_qids = set(arcwise["FORCE_S1"].keys()) & set(arcwise["FORCE_S2"].keys()) & set(arcwise["FORCE_S7"].keys()) & set(mcts_v3_by_qid.keys())
    print(f"三者与 mcts_v3 交集题目数: {len(all_qids)}")

    bug_candidates = []
    for qid in sorted(all_qids, key=lambda x: int(x) if x.isdigit() else 0):
        s1_ok = _any_path_correct_for_record(
            arcwise["FORCE_S1"][qid], qid, gold_sqls, qid_to_db, build_db_connector_fn, compare_with_gold_fn
        )
        s2_ok = _any_path_correct_for_record(
            arcwise["FORCE_S2"][qid], qid, gold_sqls, qid_to_db, build_db_connector_fn, compare_with_gold_fn
        )
        s7_ok = _any_path_correct_for_record(
            arcwise["FORCE_S7"][qid], qid, gold_sqls, qid_to_db, build_db_connector_fn, compare_with_gold_fn
        )
        mcts_ok = mcts_v3_by_qid[qid]["hit"]
        if not s1_ok and not s2_ok and not s7_ok and mcts_ok:
            info = {
                "question_id": qid,
                "db_id": mcts_v3_by_qid[qid]["db_id"],
                "question": mcts_v3_by_qid[qid]["question"],
                "gold_sql": mcts_v3_by_qid[qid]["gold_sql"],
            }
            bug_candidates.append(info)
            print(f"  [候选] qid={qid} db={info['db_id']} | {info['question'][:60]}...")

    print("-" * 65)
    print(f"S1/S2/S7 都错但 mcts_v3 对的题目数: {len(bug_candidates)}（疑似框架无法跑通的 bug case）")

    if args.out_list:
        out_path = Path(args.out_list)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(bug_candidates, f, ensure_ascii=False, indent=2)
        print(f"已写入: {out_path}")


if __name__ == "__main__":
    main()
