#!/usr/bin/env python3
"""
将 Alpha-SQL 预处理后的 tasks.pkl 转为 mcts_v1 可用的 ppl JSON。

这样 mcts_v1 就能用上 Alpha-SQL 的 schema linking（relevant values 已灌进 schema_context）。

用法:
  python alpha_sql_tasks_to_ppl.py \\
    --tasks_pkl /path/to/Alpha-SQL-2.2.4/data/preprocessed/arcwise/dev/tasks.pkl \\
    --output_ppl workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl.json

然后:
  python test_mcts.py --ppl_file workflows/mcts_v1/test/out/arcwise_alpha_sql_ppl.json ...
"""
import argparse
import json
import pickle
import sys
from pathlib import Path

# 项目根
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if _ROOT not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main():
    parser = argparse.ArgumentParser(description="Alpha-SQL tasks.pkl -> mcts_v1 ppl JSON")
    parser.add_argument("--tasks_pkl", type=str, required=True, help="Alpha-SQL 的 tasks.pkl 路径")
    parser.add_argument("--output_ppl", type=str, required=True, help="输出的 ppl JSON 路径")
    args = parser.parse_args()

    pkl_path = Path(args.tasks_pkl)
    if not pkl_path.exists():
        print(f"错误: 文件不存在 {pkl_path}", file=sys.stderr)
        sys.exit(1)

    with open(pkl_path, "rb") as f:
        tasks = pickle.load(f)

    # Alpha-SQL Task: question_id, db_id, question, evidence, sql, schema_context
    # mcts_v1 sample: db, question, ddl_data, foreign_key, evidence, question_id, SQL
    # schema_context 是完整 DDL 字符串（含 CREATE TABLE 与 value examples），直接作 ddl_data
    # mcts_v1 会拼成 "db_name:{db}\n{ddl_data}\nforeign_key:{foreign_key}"；Alpha 的 DDL 里已有 REFERENCES，foreign_key 段可留空
    samples = []
    for t in tasks:
        # 兼容 pydantic Task 或普通对象
        qid = getattr(t, "question_id", None)
        db_id = getattr(t, "db_id", "")
        question = getattr(t, "question", "")
        evidence = getattr(t, "evidence", "") or ""
        sql = getattr(t, "sql", None) or ""
        schema_context = getattr(t, "schema_context", "") or ""
        if not schema_context:
            print(f"警告: question_id={qid} 无 schema_context，跳过", file=sys.stderr)
            continue
        # mcts_v1 的 ddl_data 不需要带 "db_name:"，run_once 会拼
        # foreign_key 留空即可（Alpha 的 DDL 里已有 FOREIGN KEY 子句）
        samples.append({
            "question_id": qid,
            "db_id": db_id,
            "db": db_id,
            "question": question,
            "evidence": evidence,
            "SQL": sql,
            "ddl_data": schema_context,
            "foreign_key": "",
        })

    out_path = Path(args.output_ppl)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    print(f"已写入 {len(samples)} 条 -> {out_path}")
    print("接着可用: python test_mcts.py --ppl_file {} --gold_file <同一或原arcwise> --json_out ...".format(out_path))


if __name__ == "__main__":
    main()
