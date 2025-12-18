#!/usr/bin/env python3
"""
整理ideal策略的SQL，如果ideal SQL不正确，则使用原始selected_sql作为fallback
"""

import json
import sys
from pathlib import Path

def load_ideal_sqls(ideal_txt_file: str, question_ids: list) -> dict:
    """加载ideal策略的SQL文件，按question_ids顺序对应"""
    ideal_sqls = {}
    with open(ideal_txt_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        # 只取前len(question_ids)行，对应question_ids
        for idx, qid in enumerate(question_ids):
            if idx < len(lines):
                sql = lines[idx].strip()
                ideal_sqls[qid] = sql
            else:
                ideal_sqls[qid] = ""
    return ideal_sqls

def load_evaluation_data(eval_json_file: str) -> dict:
    """加载评估结果JSON"""
    with open(eval_json_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_original_data(original_json_file: str) -> dict:
    """加载原始数据JSON"""
    with open(original_json_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_ideal_sql_correctness(qid: str, ideal_sql: str, eval_data: dict) -> bool:
    """检查ideal SQL是否正确"""
    if qid not in eval_data:
        return False
    
    if not ideal_sql:  # 空SQL视为不正确
        return False
    
    # 在所有rollout的SQL评估中查找ideal SQL
    for rollout_eval in eval_data[qid]['rollout_evaluations']:
        for sql_eval in rollout_eval['sql_evaluations']:
            # 标准化SQL比较（去除空白字符）
            eval_sql = ' '.join(sql_eval['sql'].split())
            ideal_sql_normalized = ' '.join(ideal_sql.split())
            if eval_sql == ideal_sql_normalized:
                return sql_eval.get('is_correct', False)
    
    return False

def find_selected_sql(qid: str, original_data: dict) -> str:
    """从原始数据中找到selected_sql"""
    if qid not in original_data:
        return ""
    
    item = original_data[qid]
    rollout_stats = item.get('rollout_stats', [])
    
    # 遍历所有rollout，找到第一个非空的selected_sql
    for rollout in rollout_stats:
        selected_sql = rollout.get('selected_sql')
        if selected_sql:
            return selected_sql
    
    # 如果没有selected_sql，尝试使用顶层的sql字段
    top_level_sql = item.get('sql', '')
    return top_level_sql

def main():
    # 文件路径
    ideal_txt_file = "workflows/mcts/test/out/strategy_evaluation/12_9_strategy8_ideal.txt"
    eval_json_file = "workflows/mcts/test/out/strategy_evaluation/all_sqls_evaluation.json"
    original_json_file = "workflows/mcts/test/out/12_9_updated_stat.json"
    output_txt_file = "workflows/mcts/test/out/strategy_evaluation/ideal_sql_with_fallback.txt"
    
    print("正在加载数据...")
    eval_data = load_evaluation_data(eval_json_file)
    original_data = load_original_data(original_json_file)
    
    # 获取所有question_id并排序（与评估脚本保持一致）
    all_qids = sorted(original_data.keys(), key=lambda x: int(x) if x.isdigit() else 0)
    
    # 加载ideal SQL，按question_ids顺序对应
    ideal_sqls = load_ideal_sqls(ideal_txt_file, all_qids)
    
    print(f"加载了 {len(ideal_sqls)} 条ideal SQL")
    print(f"加载了 {len(eval_data)} 条评估数据")
    print(f"加载了 {len(original_data)} 条原始数据")
    print(f"总question数量: {len(all_qids)}")
    
    print("\n正在整理SQL...")
    final_sqls = {}
    correct_count = 0
    fallback_count = 0
    empty_count = 0
    
    for qid in all_qids:
        ideal_sql = ideal_sqls.get(qid, "")
        
        if not ideal_sql:
            # 如果ideal SQL为空，使用selected_sql
            selected_sql = find_selected_sql(qid, original_data)
            final_sqls[qid] = selected_sql
            empty_count += 1
            if selected_sql:
                fallback_count += 1
        else:
            # 检查ideal SQL是否正确
            is_correct = find_ideal_sql_correctness(qid, ideal_sql, eval_data)
            
            if is_correct:
                final_sqls[qid] = ideal_sql
                correct_count += 1
            else:
                # 使用selected_sql作为fallback
                selected_sql = find_selected_sql(qid, original_data)
                final_sqls[qid] = selected_sql if selected_sql else ideal_sql  # 如果selected_sql也为空，保留ideal_sql
                fallback_count += 1
    
    # 保存结果
    print(f"\n正在保存结果到: {output_txt_file}")
    with open(output_txt_file, 'w', encoding='utf-8') as f:
        for qid in all_qids:
            sql = final_sqls.get(qid, "")
            # 标准化SQL（去除多余空白）
            if sql:
                sql = ' '.join(sql.split())
            f.write(sql + "\n")
        
        # 确保有147行
        current_lines = len(all_qids)
        if current_lines < 147:
            for _ in range(147 - current_lines):
                f.write("\n")
    
    print(f"\n✅ 完成！")
    print(f"   - 使用正确ideal SQL: {correct_count}")
    print(f"   - 使用fallback (selected_sql): {fallback_count}")
    print(f"   - 空SQL数量: {empty_count}")
    print(f"   - 总数量: {len(all_qids)}")
    print(f"   - 输出文件: {output_txt_file}")

if __name__ == "__main__":
    main()

