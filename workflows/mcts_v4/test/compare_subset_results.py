"""
比较子集结果与全集结果，分析子集是否有提升
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, Any, Optional

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

from workflows.mcts_v1.core.database_connector import DatabaseConnector
from workflows.mcts_v1.test.test_mcts import compare_with_gold, build_db_connector


def get_db_name_from_ppl_file(ppl_file: str, qid: str) -> Optional[str]:
    """从ppl文件中获取指定question_id的数据库名称"""
    try:
        with open(ppl_file, 'r', encoding='utf-8') as f:
            ppls = json.load(f)
        for item in ppls:
            if str(item.get('question_id', '')) == str(qid):
                return item.get('db', None)
    except Exception as e:
        print(f"[警告] 从ppl文件获取数据库名称失败: {e}")
    return None


def load_gold_sqls(gold_file: str) -> Dict[str, str]:
    """加载gold SQL文件，返回 {question_id: gold_sql} 的字典"""
    gold_sqls = {}
    try:
        with open(gold_file, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
        for item in gold_data:
            qid = item.get('question_id')
            sql = item.get('SQL', '')
            if qid is not None:
                gold_sqls[str(qid)] = sql
        print(f"[Gold] 从 {gold_file} 加载了 {len(gold_sqls)} 条gold SQL")
    except Exception as e:
        print(f"[警告] 加载gold文件失败: {e}")
    return gold_sqls


def compare_results(new_file: str, old_file: str, ppl_file: str, gold_file: str):
    """
    比较新版本(子集)和旧版本(全集)的结果
    
    Args:
        new_file: 新版本结果文件路径
        old_file: 旧版本结果文件路径
        ppl_file: ppl文件路径（用于获取数据库名称）
        gold_file: gold SQL文件路径
    """
    # 加载文件
    with open(new_file, 'r', encoding='utf-8') as f:
        new_data = json.load(f)
    
    with open(old_file, 'r', encoding='utf-8') as f:
        old_data = json.load(f)
    
    # 加载gold SQL
    gold_sqls = load_gold_sqls(gold_file)
    
    print(f"\n{'='*80}")
    print(f"新版本(子集)文件: {new_file}")
    print(f"  question_id数量: {len(new_data)}")
    print(f"旧版本(全集)文件: {old_file}")
    print(f"  question_id数量: {len(old_data)}")
    print(f"{'='*80}\n")
    
    # 只比较子集中存在的question_id
    common_qids = [qid for qid in new_data.keys() if qid in old_data]
    print(f"共同的question_id数量: {len(common_qids)}")
    
    # 统计
    new_correct = 0
    old_correct = 0
    both_correct = 0
    both_wrong = 0
    new_better = []  # 新版本对，旧版本错
    old_better = []  # 旧版本对，新版本错
    
    for qid in common_qids:
        new_sql = new_data[qid].get('sql', '').strip()
        old_sql = old_data[qid].get('sql', '').strip()
        gold_sql = gold_sqls.get(qid, None)
        
        if not gold_sql:
            print(f"  [警告] qid={qid}: 没有gold SQL，跳过")
            continue
        
        # 获取数据库名称
        db_name = get_db_name_from_ppl_file(ppl_file, qid)
        if not db_name:
            print(f"  [警告] qid={qid}: 无法获取数据库名称，跳过")
            continue
        
        # 比较
        db_connector = build_db_connector(db_name)
        try:
            new_match = compare_with_gold(new_sql, gold_sql, db_connector=db_connector) if new_sql else False
            old_match = compare_with_gold(old_sql, gold_sql, db_connector=db_connector) if old_sql else False
        finally:
            db_connector.disconnect()
        
        if new_match:
            new_correct += 1
        if old_match:
            old_correct += 1
        
        if new_match and old_match:
            both_correct += 1
            status = "✅✅ 都对"
        elif new_match and not old_match:
            new_better.append(qid)
            status = "✅❌ 新版本对，旧版本错 ⬆️"
        elif not new_match and old_match:
            old_better.append(qid)
            status = "❌✅ 新版本错，旧版本对 ⬇️"
        else:
            both_wrong += 1
            status = "❌❌ 都错"
        
        print(f"  qid={qid}: {status}")
    
    # 汇总
    total = len(common_qids)
    print(f"\n{'='*80}")
    print(f"[汇总统计] 共比较 {total} 个question")
    print(f"{'='*80}")
    print(f"  新版本正确: {new_correct}/{total} ({new_correct/total*100:.2f}%)")
    print(f"  旧版本正确: {old_correct}/{total} ({old_correct/total*100:.2f}%)")
    print(f"{'='*80}")
    print(f"  都对: {both_correct}")
    print(f"  都错: {both_wrong}")
    print(f"  新版本提升(新对旧错): {len(new_better)}")
    print(f"  新版本退步(新错旧对): {len(old_better)}")
    print(f"{'='*80}")
    
    if new_better:
        print(f"\n[新版本提升的question_id列表]")
        print(f"  {new_better}")
    
    if old_better:
        print(f"\n[新版本退步的question_id列表]")
        print(f"  {old_better}")
    
    # 净提升
    net_improvement = len(new_better) - len(old_better)
    print(f"\n{'='*80}")
    print(f"[净提升] {net_improvement:+d} 个 ({len(new_better)} 提升 - {len(old_better)} 退步)")
    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(description="比较子集结果与全集结果")
    parser.add_argument("--new_file", type=str, required=True, help="新版本(子集)结果文件路径")
    parser.add_argument("--old_file", type=str, required=True, help="旧版本(全集)结果文件路径")
    parser.add_argument("--ppl_file", type=str, required=True, help="ppl文件路径（用于获取数据库名称）")
    parser.add_argument("--gold_file", type=str, required=True, help="gold SQL文件路径")
    args = parser.parse_args()
    
    compare_results(args.new_file, args.old_file, args.ppl_file, args.gold_file)


if __name__ == "__main__":
    main()

# 使用示例：
# cd /hpc2hdd/home/sshen190/wtao565 && source .venv/bin/activate && python SelfCorrectionSQL/workflows/mcts_v1/test/compare_subset_results.py \
#   --new_file SelfCorrectionSQL/workflows/mcts_v1/test/out/test_cte_repair_strategy_force-s7_result.json \
#   --old_file SelfCorrectionSQL/workflows/mcts_v1/test/out/add_semantic_probe_strategy_force-s7_result.json \
#   --ppl_file SelfCorrectionSQL/data/subset_ppl_dev_python.json \
#   --gold_file SelfCorrectionSQL/data/sub_sampled_bird_dev_set.json
