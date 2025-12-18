#!/usr/bin/env python3
"""
评估SQL准确度并基于不同策略（包括immediate_score和confidence）找出最佳策略

适配新版JSON结构:
- Root -> QID -> all_sqls_with_attributes -> cte_path
- 策略新增: 针对 cte_path 中的 immediate_score 和 confidence 进行加权计算
"""

import json
import sys
import argparse
from pathlib import Path
from collections import Counter
from typing import Dict, List, Any, Optional, Callable

# 添加项目路径
sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from core.database_connector import DatabaseConnector
from workflows.mcts.test.test_mcts import compare_with_gold, build_db_connector


def load_gold_sqls(gold_file: str) -> Dict[str, str]:
    """加载gold SQL文件 (增强版: 自动识别字段名)"""
    print(f"📂 正在加载 Gold 文件: {gold_file}")
    gold_sqls = {}
    
    try:
        with open(gold_file, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
            
        # 统一转为列表处理
        items = []
        if isinstance(gold_data, list):
            items = gold_data
        elif isinstance(gold_data, dict):
            for k, v in gold_data.items():
                if isinstance(v, dict):
                    v['question_id'] = k
                    items.append(v)
        
        count = 0
        for item in items:
            # 1. 尝试获取 ID
            qid = item.get('question_id') or item.get('id')
            if qid is None:
                continue
            qid = str(qid).strip()
            
            # 2. 尝试获取 SQL (支持 SQL, sql, query 等多种写法)
            sql = item.get('SQL') or item.get('sql') or item.get('query') or item.get('question_sql')
            
            if qid and sql:
                gold_sqls[qid] = sql
                count += 1
                
        print(f"✅ 成功提取了 {count} 条标准 SQL")
        
    except Exception as e:
        print(f"❌ 加载 Gold 文件失败: {e}")
        
    return gold_sqls


def extract_path_metrics(cte_path: List[Dict[str, Any]]) -> Dict[str, float]:
    """从 CTE Path 中提取统计指标"""
    if not cte_path:
        return {
            "avg_immediate_score": 0.0,
            "weighted_immediate_score": 0.0,
            "last_immediate_score": 0.0,
            "min_confidence": 0.0,
            "avg_confidence": 0.0
        }

    valid_steps = [step for step in cte_path if isinstance(step, dict) and "immediate_score" in step]
    
    if not valid_steps:
        return {
            "avg_immediate_score": 0.0,
            "weighted_immediate_score": 0.0,
            "last_immediate_score": 0.0,
            "min_confidence": 0.0,
            "avg_confidence": 0.0
        }

    scores = [step.get("immediate_score", 0.0) for step in valid_steps]
    confidences = [step.get("confidence", 0.0) for step in valid_steps]

    avg_score = sum(scores) / len(scores)
    
    # 加权分: 越靠后权重越大
    total_weight = sum(range(1, len(scores) + 1))
    weighted_sum = sum(score * (i + 1) for i, score in enumerate(scores))
    weighted_score = weighted_sum / total_weight if total_weight > 0 else 0.0

    last_score = scores[-1]
    min_conf = min(confidences) if confidences else 0.0
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return {
        "avg_immediate_score": avg_score,
        "weighted_immediate_score": weighted_score,
        "last_immediate_score": last_score,
        "min_confidence": min_conf,
        "avg_confidence": avg_conf
    }


def evaluate_all_sqls(data: Dict[str, Any], gold_sqls: Dict[str, str], 
                      ppl_file: str = None) -> Dict[str, Any]:
    """评估所有候补SQL的准确度"""
    print("正在评估所有候补SQL的准确度并计算路径指标...")
    
    # 1. 加载数据库映射 (兼容 db 和 db_id)
    db_info_map = {}
    if ppl_file:
        try:
            with open(ppl_file, 'r', encoding='utf-8') as f:
                ppls = json.load(f)
            
            # 兼容处理 list 或 dict 格式的 PPL 文件
            items = ppls if isinstance(ppls, list) else list(ppls.values())
            
            for item in items:
                qid = item.get('question_id') or item.get('id')
                if qid is None: continue
                qid = str(qid).strip()
                
                # --- 关键修改：同时查找 db 和 db_id ---
                db_name = item.get('db') or item.get('db_id')
                
                if qid and db_name:
                    db_info_map[qid] = db_name
            print(f"✅ 加载了 {len(db_info_map)} 条数据库映射信息")
        except Exception as e:
            print(f"⚠️ 加载数据库信息失败: {e}")

    evaluated_data = {}
    total_sqls_processed = 0
    
    for qid, item in data.items():
        # 确保 qid 是字符串对比
        qid_str = str(qid).strip()
        gold_sql = gold_sqls.get(qid_str, '')
        
        if not gold_sql:
            continue
            
        db_name = db_info_map.get(qid_str, '')
        db_connector = None
        if db_name:
            try:
                db_connector = build_db_connector(db_name)
            except Exception as e:
                print(f"⚠️ 连接数据库 {db_name} 失败: {e}")

        candidates = item.get('all_sqls_with_attributes', [])
        evaluated_candidates = []

        for cand in candidates:
            sql = cand.get('sql', '')
            cte_path = cand.get('cte_path', [])
            
            reward = cand.get('reward', 0.0)
            bucket_count = cand.get('bucket_count', 0)
            visit_count = cand.get('visit_count', 0)
            
            path_metrics = extract_path_metrics(cte_path)

            is_correct = False
            if db_connector and sql:
                try:
                    is_correct = compare_with_gold(sql, gold_sql, db_connector=db_connector)
                except Exception:
                    pass 

            evaluated_candidates.append({
                'sql': sql,
                'is_correct': is_correct,
                'reward': reward,
                'bucket_count': bucket_count,
                'visit_count': visit_count,
                'metrics': path_metrics
            })
            total_sqls_processed += 1

        evaluated_data[qid_str] = {
            'candidates': evaluated_candidates,
            'gold_sql': gold_sql
        }

        if db_connector:
            db_connector.disconnect()

    print(f"✅ 预处理完成，共处理 {total_sqls_processed} 条候选SQL (涉及 {len(evaluated_data)} 个问题)")
    return evaluated_data


def run_strategies(evaluated_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """定义并运行所有策略"""
    all_qids = set(evaluated_data.keys())
    total_qids = len(all_qids)
    
    strategies_def = {
        "1_max_reward": lambda x: x['reward'],
        "2_max_bucket_count": lambda x: x['bucket_count'],
        "3_max_visit_count": lambda x: x['visit_count'],
        "4_avg_immediate_score": lambda x: x['metrics']['avg_immediate_score'],
        "5_weighted_immediate_score": lambda x: x['metrics']['weighted_immediate_score'],
        "6_last_step_score": lambda x: x['metrics']['last_immediate_score'],
        "7_min_confidence": lambda x: x['metrics']['min_confidence'],
        "8_avg_confidence": lambda x: x['metrics']['avg_confidence'],
        "9_hybrid_reward_weighted": lambda x: x['reward'] * x['metrics']['weighted_immediate_score'],
        "10_hybrid_weighted_conf": lambda x: x['metrics']['weighted_immediate_score'] * x['metrics']['min_confidence']
    }
    
    results_summary = {}

    for strategy_name, sort_key in strategies_def.items():
        strategy_results = {}
        correct_count = 0
        selected_count = 0

        for qid in all_qids:
            candidates = evaluated_data[qid]['candidates']
            if not candidates:
                strategy_results[qid] = ""
                continue
            
            try:
                # 排序优化：主指标 > bucket > sql长度(短的优先)
                best_cand = max(candidates, key=lambda x: (sort_key(x), x['bucket_count'], -len(x['sql'])))
            except Exception:
                best_cand = candidates[0]

            strategy_results[qid] = best_cand['sql']
            selected_count += 1
            if best_cand['is_correct']:
                correct_count += 1
        
        results_summary[strategy_name] = {
            'results': strategy_results,
            'accuracy': correct_count / total_qids if total_qids > 0 else 0.0,
            'correct': correct_count,
            'total': total_qids,
            'selected': selected_count
        }

    # Ideal Strategy
    ideal_results = {}
    ideal_correct = 0
    for qid in all_qids:
        candidates = evaluated_data[qid]['candidates']
        best_cand = next((c for c in candidates if c['is_correct']), None)
        if best_cand:
            ideal_results[qid] = best_cand['sql']
            ideal_correct += 1
        else:
            ideal_results[qid] = candidates[0]['sql'] if candidates else ""

    results_summary["0_ideal_upper_bound"] = {
        'results': ideal_results,
        'accuracy': ideal_correct / total_qids if total_qids > 0 else 0.0,
        'correct': ideal_correct,
        'total': total_qids
    }

    return results_summary


def save_results_to_file(strategies: Dict[str, Dict[str, Any]], output_dir: Path, sorted_qids: List[str]):
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in strategies.items():
        file_path = output_dir / f"eval_{name}.txt"
        with open(file_path, 'w', encoding='utf-8') as f:
            for qid in sorted_qids:
                sql = data['results'].get(qid, "")
                sql = ' '.join(sql.split())
                f.write(sql + "\n")


def main():
    parser = argparse.ArgumentParser(description="Evaluate SQL strategies")
    parser.add_argument("--json_file", type=str, required=True, help="Result JSON file")
    parser.add_argument("--gold_file", type=str, required=True, help="Gold SQL file (BIRD dev)")
    parser.add_argument("--ppl_file", type=str, default=None, help="DB info file")
    parser.add_argument("--output_dir", type=str, default="strategy_results", help="Output dir")
    args = parser.parse_args()

    print(f"📂 加载 JSON: {args.json_file}")
    with open(args.json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 使用相同的 gold 文件加载 Gold SQL 和 DB Info
    gold_file_path = args.gold_file
    ppl_file_path = args.ppl_file if args.ppl_file else args.gold_file

    gold_sqls = load_gold_sqls(gold_file_path)
    evaluated_data = evaluate_all_sqls(data, gold_sqls, ppl_file_path)

    print("🚀 正在计算各策略准确度...")
    strategies = run_strategies(evaluated_data)

    print("\n" + "="*80)
    print("📊 策略准确度排名:")
    print("="*80)
    
    sorted_strategies = sorted(strategies.items(), key=lambda x: x[1]['accuracy'], reverse=True)
    for idx, (name, res) in enumerate(sorted_strategies, 1):
        print(f"{idx:<2}. {name:<30}: {res['accuracy']:.2%} ({res['correct']}/{res['total']})")

    output_dir = Path(args.output_dir)
    # 按照ID数字排序
    sorted_qids = sorted(evaluated_data.keys(), key=lambda x: int(x) if x.isdigit() else 0)
    save_results_to_file(strategies, output_dir, sorted_qids)
    print(f"\n💾 结果已保存至: {output_dir}")


if __name__ == "__main__":
    main()