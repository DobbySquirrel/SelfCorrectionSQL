#!/usr/bin/env python3
"""
将“每行一个SQL”的txt，转换为 {qid(str): "SQL\t----- bird -----\t<db_id>"} 的json
对齐规则：按 dev_set 中样本出现顺序逐行对齐 txt 的 SQL
"""

import json
import argparse
from pathlib import Path


def load_dev_order_and_db(dev_set_path: Path):
    with open(dev_set_path, "r", encoding="utf-8") as f:
        dev_data = json.load(f)

    items = None
    if isinstance(dev_data, list):
        items = dev_data
    elif isinstance(dev_data, dict) and isinstance(dev_data.get("data"), list):
        items = dev_data["data"]
    else:
        raise ValueError("dev_set 格式不支持：应为 list 或 {'data': list}")

    qid_order = []
    qid_to_db = {}
    for it in items:
        if "question_id" in it and "db_id" in it:
            qid = it["question_id"]
            qid_order.append(qid)
            qid_to_db[qid] = it["db_id"]

    return qid_order, qid_to_db

def load_txt_sqls(txt_path):
    """
    每一行都保留：
    - 非空行：SQL
    - 空行：空字符串，占位用
    """
    sqls = []
    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            # 只去掉换行符，不做 strip
            sqls.append(line.rstrip("\n"))
    return sqls



def main():
    parser = argparse.ArgumentParser(description="将txt SQL列表转成 matched_sqls_formatted.json 格式")
    parser.add_argument("--dev_set", type=str, required=True,
                        help="开发集JSON文件路径（用于获取question_id顺序与db_id）")
    parser.add_argument("--txt_sqls", type=str, required=True,
                        help="输入txt：每行一个SQL")
    parser.add_argument("--output", type=str, required=True,
                        help="输出json路径")
    parser.add_argument("--start_idx", type=int, default=0,
                        help="从dev_set第几个样本开始对齐（默认0）")
    parser.add_argument("--limit", type=int, default=None,
                        help="只处理limit条（默认处理txt全部）")

    args = parser.parse_args()

    dev_set_path = Path(args.dev_set)
    txt_path = Path(args.txt_sqls)
    out_path = Path(args.output)

    if not dev_set_path.exists():
        raise FileNotFoundError(f"❌ dev_set 不存在: {dev_set_path}")
    if not txt_path.exists():
        raise FileNotFoundError(f"❌ txt_sqls 不存在: {txt_path}")

    qid_order, qid_to_db = load_dev_order_and_db(dev_set_path)
    sqls = load_txt_sqls(txt_path)

    if args.start_idx < 0 or args.start_idx >= len(qid_order):
        raise ValueError(f"start_idx 超出范围: {args.start_idx} (dev_set size={len(qid_order)})")

    if args.limit is not None:
        sqls = sqls[:args.limit]

    needed = len(sqls)
    available = len(qid_order) - args.start_idx
    if needed > available:
        raise ValueError(
            f"❌ txt 有 {needed} 条SQL，但从 dev_set.start_idx={args.start_idx} 起只剩 {available} 条可对齐。\n"
            f"   你可以：1) 调小 --limit  2) 调整 --start_idx  3) 确认txt是不是dev子集"
        )

    formatted = {}
    empty_sql = 0

    for i, sql in enumerate(sqls):
        qid = qid_order[args.start_idx + i]
        db_id = qid_to_db.get(qid, "unknown")

        if sql.strip():
            formatted_sql = f"{sql}\t----- bird -----\t{db_id}"
        else:
            formatted_sql = f"\t----- bird -----\t{db_id}"
            empty_sql += 1

        formatted[str(qid)] = formatted_sql

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, ensure_ascii=False, indent=4)

    print("========== 转换完成 ==========")
    print(f"dev_set: {dev_set_path}")
    print(f"txt_sqls: {txt_path}")
    print(f"output: {out_path}")
    print(f"写入记录数: {len(formatted)}")
    print(f"空SQL数: {empty_sql}")
    print("前3条示例：")
    for k in sorted(formatted.keys(), key=int)[:3]:
        v = formatted[k]
        preview = v[:120] + "..." if len(v) > 120 else v
        print(f"  {k}: {preview}")


if __name__ == "__main__":
    main()

# python3 /home/shenshuyu/SQL_tool_multiAgent/score_caluation/txt2json.py \
#   --dev_set data/sub_sampled_bird_dev_set.json \
#   --txt_sqls workflows/mcts_v1/test/out/12_25_single_rollout.txt \
#   --output workflows/mcts_v1/test/out/12_25_single_rollout.json
