#!/usr/bin/env python3
"""
从两个JSON文件中匹配对应的问题ID数据，输出简洁格式（包含所有ID，已排序）
"""

import json
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="匹配开发集问题ID和预测SQL数据")
    parser.add_argument("--dev_set", type=str, required=True,
                       help="开发集JSON文件路径")
    parser.add_argument("--pred_sqls", type=str, required=True,
                       help="预测SQL文件路径")
    parser.add_argument("--output", type=str, default="matched_sqls.json",
                       help="输出文件路径")
    
    args = parser.parse_args()
    
    # 检查文件
    dev_set_path = Path(args.dev_set)
    pred_sqls_path = Path(args.pred_sqls)
    
    if not dev_set_path.exists():
        print(f"❌ 开发集文件不存在: {dev_set_path}")
        return
    
    if not pred_sqls_path.exists():
        print(f"❌ 预测SQL文件不存在: {pred_sqls_path}")
        return
    
    # 1. 从开发集提取问题ID
    print(f"正在加载开发集: {dev_set_path}")
    with open(dev_set_path, 'r', encoding='utf-8') as f:
        dev_data = json.load(f)
    
    dev_ids = []
    if isinstance(dev_data, list):
        for item in dev_data:
            if 'question_id' in item:
                dev_ids.append(item['question_id'])
    elif isinstance(dev_data, dict):
        if 'data' in dev_data and isinstance(dev_data['data'], list):
            for item in dev_data['data']:
                if 'question_id' in item:
                    dev_ids.append(item['question_id'])
    
    # 去重并排序
    dev_ids = sorted(set(dev_ids))
    print(f"开发集中找到 {len(dev_ids)} 个唯一问题ID (已排序)")
    
    # 2. 加载预测SQL
    print(f"正在加载预测SQL: {pred_sqls_path}")
    with open(pred_sqls_path, 'r', encoding='utf-8') as f:
        pred_sqls = json.load(f)
    
    print(f"预测SQL中有 {len(pred_sqls)} 条记录")
    
    # 3. 匹配数据（保持顺序）
    matched_data = {}
    missing_count = 0
    
    for qid in dev_ids:
        str_qid = str(qid)
        if str_qid in pred_sqls:
            matched_data[str_qid] = pred_sqls[str_qid]
        else:
            matched_data[str_qid] = ""  # 为空的情况
            missing_count += 1
    
    # 4. 保存结果
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(matched_data, f, ensure_ascii=False, indent=2)
    
    # 显示统计信息
    print(f"\n========== 匹配结果 ==========")
    print(f"总ID数量: {len(matched_data)} 个")
    print(f"匹配到SQL: {len(matched_data) - missing_count} 条")
    print(f"SQL为空: {missing_count} 条")
    
    # 显示前几个ID作为示例
    print(f"\nID范围: {dev_ids[0]} ~ {dev_ids[-1]}")
    if missing_count > 0:
        # 找出哪些ID是空的
        empty_ids = []
        for qid in dev_ids[:20]:  # 只显示前20个空的ID
            if matched_data[str(qid)] == "":
                empty_ids.append(qid)
        if empty_ids:
            print(f"前{len(empty_ids)}个空的ID示例: {empty_ids}")
    
    print(f"\n结果已保存到: {output_path}")
    
    # 显示前5条匹配数据作为示例
    print(f"\n========== 前5条数据示例 ==========")
    count = 0
    for qid in dev_ids:
        if count >= 5:
            break
        sql = matched_data[str(qid)]
        status = "✓" if sql else "✗ (空)"
        sql_preview = sql[:50] + "..." if sql and len(sql) > 50 else sql
        print(f"ID: {qid:4d} {status:8} {sql_preview}")
        count += 1


if __name__ == "__main__":
    main()