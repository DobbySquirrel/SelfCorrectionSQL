"""
CoCTE-MCTS 批量测试脚本
适配新架构：Strategy + Probe + CoCTE
"""

import json
import sys
import argparse
import time
import threading
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import pandas as pd
import numpy as np

# 导入新架构的 Solver
from mcts.solver import CoCTEMCTSSolver
from env.db_connector import DatabaseConnector

# 日志设置
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def build_solver(db_name: str, llm_config: dict) -> CoCTEMCTSSolver:
    """构建新版 MCTS Solver"""
    # 这里你需要根据你的实际路径修改
    db_path = f"/ssd/shenshuyu/work/bird/dev_20240627/dev_databases/{db_name}/{db_name}.sqlite"
    
    # 如果文件不存在，尝试本地路径 (便于调试)
    if not os.path.exists(db_path):
        local_path = f"./data/dev_databases/{db_name}/{db_name}.sqlite"
        if os.path.exists(local_path):
            db_path = local_path
            
    return CoCTEMCTSSolver(llm_config, db_path, max_iterations=10)

def run_once(sample: dict, multi_base_urls: list = None) -> dict:
    """运行单个样本"""
    db_name = sample["db"]
    question = sample["question"]
    # evidence = sample.get("evidence", "") # 使用 evidence 作为 context
    evidence = sample.get("evidence", "") 

    # 构建 LLM 配置
    # 这里简化处理，如果你有多模型轮询逻辑，可以在 LLMClient 内部实现，或者在这里随机选一个 url
    base_url = multi_base_urls[0] if multi_base_urls else "http://localhost:8000/v1"
    api_key = os.environ.get("OPENAI_API_KEY", "dummy")
    
    llm_config = {
        "model": "deepseek-coder", # 或者你的模型名
        "api_key": api_key,
        "base_url": base_url,
        "timeout": 120  # LLM调用超时时间（秒），默认120秒
    }
    
    # 实例化 Solver
    try:
        solver = build_solver(db_name, llm_config)
        
        # 执行求解
        # 我们把 evidence 传入 additional_context
        result = solver.solve(question, additional_context=evidence)
        
        return {
            "sql": result["sql"],
            "stats": {
                "score": result["score"],
                "time": result["time"],
                "steps": result["steps"]
            },
            "error": None
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "sql": "",
            "stats": {},
            "error": str(e)
        }

# ==========================================
# 保留你原来的 Gold 验证逻辑 (因为写得很好)
# ==========================================
def compare_with_gold(predicted_sql: str, gold_sql: str, db_path: str) -> bool:
    """执行并比较结果 (增强版：出错时自动打印差异)"""
    if not predicted_sql or not gold_sql: return False
    
    conn = DatabaseConnector(db_path)
    try:
        # 1. 执行 Gold
        gold_res, gold_err = conn.execute_sql(gold_sql)
        if gold_err:
            print(f"⚠️ Gold Error: {gold_err}")
            return False

        # 2. 执行 Predict
        pred_res, pred_err = conn.execute_sql(predicted_sql)
        if pred_err:
            # --- 报错时打印 ---
            print(f"\n❌ [Exec Error] Execution failed!")
            print(f"  > My SQL:   {predicted_sql}")
            print(f"  > Error:    {pred_err}")
            return False
            
        # 3. 结果比较逻辑
        def normalize(rows):
            if not rows: return set()
            norm_rows = []
            for row in rows:
                cleaned = []
                for item in row:
                    if item is None: cleaned.append("none")
                    else: cleaned.append(str(item).strip().lower())
                norm_rows.append(tuple(cleaned))
            return set(norm_rows)
            
        norm_gold = normalize(gold_res)
        norm_pred = normalize(pred_res)
        
        is_match = (norm_gold == norm_pred)
        
        # --- 4. 关键：如果不匹配，在这里打印对比 ---
        if not is_match:
            print(f"\n❌ [Mismatch] Result differs from Gold!")
            print(f"  > My SQL:   {predicted_sql}")
            print(f"  > Gold SQL: {gold_sql}")
            print(f"  > My Rows (First 5):   {list(pred_res)[:5]}")
            print(f"  > Gold Rows (First 5): {list(gold_res)[:5]}")
            print("-" * 50)

        return is_match
        
    except Exception as e:
        print(f"Compare Error: {e}")
        return False

# ==========================================
# 任务处理包装器
# ==========================================
def process_single_task(args_tuple):
    idx, sample, gold_sqls, multi_base_urls = args_tuple
    qid = str(sample.get('question_id', idx))
    
    print(f">>> Processing #{qid} (DB: {sample['db']})")
    
    # 运行
    res = run_once(sample, multi_base_urls)
    
    # 验证
    gold_match = None
    if gold_sqls and qid in gold_sqls:
        db_path = f"/ssd/shenshuyu/work/bird/dev_20240627/dev_databases/{sample['db']}/{sample['db']}.sqlite"
        if not os.path.exists(db_path): # fallback
             db_path = f"./data/dev_databases/{sample['db']}/{sample['db']}.sqlite"
             
        gold_match = compare_with_gold(res['sql'], gold_sqls[qid], db_path)
        mark = "✅" if gold_match else "❌"
        print(f"{mark} Result #{qid}: {gold_match}")
    
    return {
        'idx': idx,
        'qid': qid,
        'sql': res['sql'],
        'stats': res['stats'],
        'gold_match': gold_match,
        'error': res['error']
    }

# ==========================================
# Main Entry
# ==========================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ppl_file", type=str, required=True)
    parser.add_argument("--gold_file", type=str, default=None)
    parser.add_argument("--output_dir", type=str, default="./results")
    parser.add_argument("--qid", type=str, default=None)
    parser.add_argument("--max_workers", type=int, default=5)
    parser.add_argument("--base_urls", type=str, default="http://localhost:8000/v1")
    args = parser.parse_args()
    
    # 1. 加载数据
    with open(args.ppl_file, 'r') as f:
        samples = json.load(f)
        
    gold_sqls = {}
    if args.gold_file:
        with open(args.gold_file, 'r') as f:
            gold_data = json.load(f)
            for x in gold_data:
                gold_sqls[str(x['question_id'])] = x['SQL']

    # 2. 筛选任务
    tasks = []
    base_urls = args.base_urls.split(',')
    
    target_qids = args.qid.split(',') if args.qid else None
    
    for i, s in enumerate(samples):
        s_qid = str(s.get('question_id', i))
        if target_idx := (target_qids and s_qid in target_qids):
            tasks.append((i, s, gold_sqls, base_urls))
        elif not target_qids:
             tasks.append((i, s, gold_sqls, base_urls))

    print(f"Total Tasks: {len(tasks)}")
    
    # 3. 并行执行
    results = {}
    correct = 0
    total = 0
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {executor.submit(process_single_task, t): t for t in tasks}
        
        for future in tqdm(as_completed(futures), total=len(tasks)):
            res = future.result()
            qid = res['qid']
            results[qid] = res
            
            if res['gold_match'] is not None:
                total += 1
                if res['gold_match']: correct += 1
                
            # 实时保存
            with open(f"{args.output_dir}/results.json", 'w') as f:
                json.dump(results, f, indent=2)

    print(f"\nFinal Accuracy: {correct}/{total} = {correct/total*100:.2f}%" if total > 0 else "No gold validation.")

if __name__ == "__main__":
    main()

 # 跑指定 ID 测试