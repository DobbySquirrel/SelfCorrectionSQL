#!/usr/bin/env python3
"""
将 matched_sqls.json 格式转换为与 12_17_wo_schemaFilter.json 相同的格式
每个 SQL 后面添加 \t----- bird -----\t<db_id>
"""

import json
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="格式化 matched_sqls.json，添加数据库ID后缀")
    parser.add_argument("--dev_set", type=str, 
                       default="data/sub_sampled_bird_dev_set.json",
                       help="开发集JSON文件路径（用于获取db_id）")
    parser.add_argument("--matched_sqls", type=str,
                       default="Alpha-SQL-2.2.4/results/matched_sqls.json",
                       help="输入的matched_sqls.json文件路径")
    parser.add_argument("--output", type=str,
                       default="Alpha-SQL-2.2.4/results/matched_sqls_formatted.json",
                       help="输出文件路径")
    
    args = parser.parse_args()
    
    # 检查文件
    dev_set_path = Path(args.dev_set)
    matched_sqls_path = Path(args.matched_sqls)
    
    if not dev_set_path.exists():
        print(f"❌ 开发集文件不存在: {dev_set_path}")
        return
    
    if not matched_sqls_path.exists():
        print(f"❌ matched_sqls文件不存在: {matched_sqls_path}")
        return
    
    # 1. 从开发集提取 question_id -> db_id 映射
    print(f"正在加载开发集: {dev_set_path}")
    with open(dev_set_path, 'r', encoding='utf-8') as f:
        dev_data = json.load(f)
    
    id_to_db = {}
    if isinstance(dev_data, list):
        for item in dev_data:
            if 'question_id' in item and 'db_id' in item:
                id_to_db[item['question_id']] = item['db_id']
    elif isinstance(dev_data, dict):
        if 'data' in dev_data and isinstance(dev_data['data'], list):
            for item in dev_data['data']:
                if 'question_id' in item and 'db_id' in item:
                    id_to_db[item['question_id']] = item['db_id']
    
    print(f"从开发集中提取了 {len(id_to_db)} 个 question_id -> db_id 映射")
    
    # 2. 加载 matched_sqls
    print(f"正在加载 matched_sqls: {matched_sqls_path}")
    with open(matched_sqls_path, 'r', encoding='utf-8') as f:
        matched_sqls = json.load(f)
    
    print(f"matched_sqls中有 {len(matched_sqls)} 条记录")
    
    # 3. 格式化数据：为每个SQL添加后缀
    formatted_data = {}
    missing_db_count = 0
    empty_sql_count = 0
    
    for qid_str, sql in matched_sqls.items():
        qid = int(qid_str)
        
        # 获取对应的 db_id
        if qid in id_to_db:
            db_id = id_to_db[qid]
        else:
            print(f"⚠️  警告: question_id {qid} 在开发集中找不到对应的 db_id")
            missing_db_count += 1
            db_id = "unknown"  # 使用默认值
        
        # 格式化SQL：添加后缀
        if sql and sql.strip():
            # SQL不为空，添加后缀
            formatted_sql = f"{sql}\t----- bird -----\t{db_id}"
        else:
            # SQL为空，也添加后缀（保持格式一致）
            formatted_sql = f"\t----- bird -----\t{db_id}"
            empty_sql_count += 1
        
        formatted_data[qid_str] = formatted_sql
    
    # 4. 保存结果
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(formatted_data, f, ensure_ascii=False, indent=4)
    
    # 显示统计信息
    print(f"\n========== 格式化结果 ==========")
    print(f"总记录数: {len(formatted_data)}")
    print(f"SQL不为空: {len(formatted_data) - empty_sql_count} 条")
    print(f"SQL为空: {empty_sql_count} 条")
    print(f"找不到db_id: {missing_db_count} 条")
    
    print(f"\n结果已保存到: {output_path}")
    
    # 显示前5条数据作为示例
    print(f"\n========== 前5条数据示例 ==========")
    count = 0
    for qid_str in sorted(formatted_data.keys(), key=int)[:5]:
        sql = formatted_data[qid_str]
        status = "✓" if sql and sql.strip() and not sql.startswith("\t----- bird") else "✗ (空)"
        sql_preview = sql[:80] + "..." if len(sql) > 80 else sql
        print(f"ID: {qid_str:4s} {status:8} {sql_preview}")


if __name__ == "__main__":
    main()

