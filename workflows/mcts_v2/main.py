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
import random
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

def build_solver(db_name: str, llm_config: dict, max_rollouts: int = 10) -> CoCTEMCTSSolver:
    """构建新版 MCTS Solver"""
    # 这里你需要根据你的实际路径修改
    db_path = f"/ssd/shenshuyu/work/bird/dev_20240627/dev_databases/{db_name}/{db_name}.sqlite"
    
    # 如果文件不存在，尝试本地路径 (便于调试)
    if not os.path.exists(db_path):
        local_path = f"./data/dev_databases/{db_name}/{db_name}.sqlite"
        if os.path.exists(local_path):
            db_path = local_path
            
    return CoCTEMCTSSolver(llm_config, db_path, max_rollouts=max_rollouts)

# 线程安全的 URL 轮询计数器
_url_counter_lock = threading.Lock()
_url_counter = 0

def get_next_base_url(multi_base_urls: list) -> str:
    """线程安全地轮询选择下一个 base_url"""
    if not multi_base_urls:
        return "http://localhost:8000/v1"
    
    global _url_counter
    with _url_counter_lock:
        url = multi_base_urls[_url_counter % len(multi_base_urls)]
        _url_counter += 1
        return url

def run_single_strategy(sample: dict, strategy: str, multi_base_urls: list = None, max_rollouts: int = 10) -> dict:
    """使用固定策略运行单个样本"""
    db_name = sample["db"]
    question = sample["question"]
    evidence = sample.get("evidence", "") 

    # 构建 LLM 配置
    # 使用轮询方式选择 base_url，实现负载均衡
    base_url = get_next_base_url(multi_base_urls)
    api_key = os.environ.get("OPENAI_API_KEY", "dummy")
    
    llm_config = {
        "model": "deepseek-coder",
        "api_key": api_key,
        "base_url": base_url,
        "timeout": 120
    }
    
    try:
        solver = build_solver(db_name, llm_config, max_rollouts=max_rollouts)
        result = solver.solve(question, additional_context=evidence, fixed_strategy=strategy)
        
        return {
            "strategy": strategy,
            "sql": result["sql"],
            "stats": {
                "score": result["score"],
                "time": result["time"],
                "rollouts": result.get("rollouts", result.get("steps", 0)),
                "total_iterations": result.get("total_iterations", result.get("steps", 0))
            },
            "error": None
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "strategy": strategy,
            "sql": "",
            "stats": {},
            "error": str(e)
        }

def select_best_result(strategy_results: list) -> dict:
    """
    从多个策略结果中选择最优的
    
    优先级：
    1. 无 error
    2. SQL 非空
    3. score 最高
    """
    # 过滤掉有 error 的结果
    valid_results = [r for r in strategy_results if r.get("error") is None]
    
    if not valid_results:
        # 如果所有结果都有 error，返回第一个（至少记录错误信息）
        return strategy_results[0] if strategy_results else {}
    
    # 过滤掉 SQL 为空的结果
    non_empty_results = [r for r in valid_results if r.get("sql") and r["sql"].strip()]
    
    if not non_empty_results:
        # 如果所有结果 SQL 都为空，返回第一个有效结果
        return valid_results[0]
    
    # 按 score 排序，选择最高的
    best = max(non_empty_results, key=lambda r: r.get("stats", {}).get("score", -float('inf')))
    return best

def run_once(sample: dict, multi_base_urls: list = None, total_max_rollouts: int = 40) -> dict:
    """
    运行单个样本，使用 Multi-root/Multi-rollout 方案
    
    Args:
        sample: 样本数据
        multi_base_urls: 多个 base_url 列表
        total_max_rollouts: 总 rollout 预算，会平均分配给 4 个策略
    """
    # 将总预算平均分配给 4 个策略
    rollouts_per_strategy = max(1, total_max_rollouts // 4)
    
    strategies = ["S1", "S2", "S3", "S4"]
    
    # 并行运行 4 个策略
    strategy_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(run_single_strategy, sample, strategy, multi_base_urls, rollouts_per_strategy): strategy
            for strategy in strategies
        }
        
        for future in as_completed(futures):
            strategy = futures[future]
            try:
                result = future.result()
                strategy_results.append(result)
                print(f"  ✓ Strategy {strategy} completed: score={result.get('stats', {}).get('score', 'N/A'):.2f}, "
                      f"error={'Yes' if result.get('error') else 'No'}")
            except Exception as e:
                print(f"  ✗ Strategy {strategy} failed: {str(e)}")
                strategy_results.append({
                    "strategy": strategy,
                    "sql": "",
                    "stats": {},
                    "error": str(e)
                })
    
    # 选择最优结果
    best_result = select_best_result(strategy_results)
    
    # 添加所有策略的结果信息（用于调试和分析）
    best_result["all_strategies"] = {
        r["strategy"]: {
            "score": r.get("stats", {}).get("score", "N/A"),
            "error": r.get("error"),
            "sql_length": len(r.get("sql", ""))
        }
        for r in strategy_results
    }
    
    print(f"  🏆 Best strategy: {best_result.get('strategy', 'N/A')}, "
          f"score={best_result.get('stats', {}).get('score', 'N/A'):.2f}")
    
    # 返回格式与原来兼容
    return {
        "sql": best_result.get("sql", ""),
        "stats": best_result.get("stats", {}),
        "error": best_result.get("error"),
        "selected_strategy": best_result.get("strategy"),
        "all_strategies": best_result.get("all_strategies", {})
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
        gold_res, gold_cols, gold_err = conn.execute_sql(gold_sql)
        if gold_err:
            print(f"⚠️ Gold Error: {gold_err}")
            return False

        # 2. 执行 Predict
        pred_res, pred_cols, pred_err = conn.execute_sql(predicted_sql)
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
    idx, sample, gold_sqls, multi_base_urls, total_max_rollouts = args_tuple
    qid = str(sample.get('question_id', idx))
    
    print(f">>> Processing #{qid} (DB: {sample['db']})")
    
    # 运行
    res = run_once(sample, multi_base_urls, total_max_rollouts=total_max_rollouts)
    
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
    parser.add_argument("--total_max_rollouts", type=int, default=40, 
                        help="总 rollout 预算，会平均分配给 4 个策略（S1/S2/S3/S4）")
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
    # 解析 base_urls，支持逗号分隔的多个 URL
    base_urls = [url.strip() for url in args.base_urls.split(',')]
    
    target_qids = args.qid.split(',') if args.qid else None
    
    for i, s in enumerate(samples):
        s_qid = str(s.get('question_id', i))
        # 如果指定了 qid，只处理匹配的；否则处理所有
        if target_qids:
            if s_qid in target_qids:
                tasks.append((i, s, gold_sqls, base_urls, args.total_max_rollouts))
        else:
            # 不指定 qid 时，处理所有任务
            tasks.append((i, s, gold_sqls, base_urls, args.total_max_rollouts))

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