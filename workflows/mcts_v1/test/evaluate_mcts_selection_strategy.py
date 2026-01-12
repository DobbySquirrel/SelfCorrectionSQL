"""
评估MCTS选择策略的效果

分析结果文件中所有SQL与Gold SQL的一致性，评估MCTS最后选择SQL的策略是否可以优化。
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
import pandas as pd
import numpy as np
from tqdm import tqdm

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from workflows.mcts_v1.core.database_connector import DatabaseConnector


def load_result_file(result_file: str) -> Dict:
    """加载结果文件"""
    print(f"[加载] 正在加载结果文件: {result_file}")
    with open(result_file, 'r', encoding='utf-8') as f:
        results = json.load(f)
    print(f"[加载] 成功加载 {len(results)} 个问题的结果")
    return results


def load_database_mapping(ppl_file: str) -> Dict[int, str]:
    """从ppl文件中加载question_id到database的映射"""
    print(f"[加载] 正在加载数据库映射: {ppl_file}")
    with open(ppl_file, 'r', encoding='utf-8') as f:
        ppl_data = json.load(f)
    
    # ppl_data是一个列表，每个元素包含question_id和db字段
    db_mapping = {}
    for item in ppl_data:
        qid = item.get('question_id')
        db = item.get('db')
        if qid is not None and db:
            db_mapping[int(qid)] = db
    
    print(f"[加载] 成功加载 {len(db_mapping)} 个问题的数据库映射")
    return db_mapping


def normalize_result(result) -> List[Dict]:
    """将结果标准化为字典列表格式"""
    if result is None:
        return []
    if isinstance(result, pd.DataFrame):
        return result.to_dict('records')
    if isinstance(result, list):
        # 如果是字典列表，直接返回
        if result and isinstance(result[0], dict):
            return result
        # 如果是元组列表，转换为字典列表
        if result and isinstance(result[0], (tuple, list)):
            # 尝试获取列名
            if hasattr(result, 'columns'):
                columns = result.columns
            else:
                # 如果没有列名，使用索引
                columns = [f'col_{i}' for i in range(len(result[0]))]
            return [dict(zip(columns, row)) for row in result]
    return []


def compare_results(predicted_result: List[Dict], gold_result: List[Dict]) -> bool:
    """比较两个结果是否相同（忽略顺序）"""
    # 转换为集合进行比较（需要先转换为可哈希的格式）
    def row_to_tuple(row: Dict) -> Tuple:
        """将字典行转换为元组（用于集合比较）"""
        # 对键进行排序，确保顺序一致
        sorted_items = sorted(row.items())
        # 处理None值，转换为字符串"None"
        return tuple(
            str(v).lower().strip() if v is not None else "none"
            for k, v in sorted_items
        )
    
    pred_set = {row_to_tuple(row) for row in predicted_result}
    gold_set = {row_to_tuple(row) for row in gold_result}
    
    return pred_set == gold_set


def compare_with_gold_timeout(predicted_sql: str, gold_sql: str, db_connector: DatabaseConnector, timeout_s: float = 30.0) -> Tuple[bool, Optional[str]]:
    """
    带超时的SQL比较函数
    
    Returns:
        (是否匹配, 错误信息)
    """
    if not predicted_sql or not gold_sql:
        return False, "SQL为空"
    
    try:
        # 执行gold SQL
        gold_result, gold_error = db_connector.execute_query(gold_sql, timeout_s=timeout_s)
        if gold_error is not None:
            return False, f"Gold SQL执行失败: {gold_error}"
        
        if gold_result is None:
            return False, "Gold SQL返回None"
        
        # 执行predicted SQL
        predicted_result, predicted_error = db_connector.execute_query(predicted_sql, timeout_s=timeout_s)
        if predicted_error is not None:
            return False, f"Predicted SQL执行失败: {predicted_error}"
        
        if predicted_result is None:
            return False, "Predicted SQL返回None"
        
        # 标准化结果
        gold_normalized = normalize_result(gold_result)
        predicted_normalized = normalize_result(predicted_result)
        
        # 比较结果
        is_match = compare_results(predicted_normalized, gold_normalized)
        
        return is_match, None
        
    except FutureTimeoutError:
        return False, "执行超时"
    except Exception as e:
        return False, f"执行异常: {str(e)}"


def extract_all_sqls_from_result(result_data: Dict) -> List[Dict]:
    """
    从结果中提取所有SQL（包括selected_sql和所有rollout中的SQL）
    
    Returns:
        List[Dict]: 每个元素包含 {'sql': str, 'source': str, 'reward': float, 'rollout_id': int}
    """
    all_sqls = []
    
    # 先收集所有rollout的信息，用于查找selected_sql的来源
    rollout_stats = result_data.get('rollout_stats', [])
    selected_sql = result_data.get('sql')
    
    # 1. 提取selected_sql（MCTS最终选择的SQL）
    # 需要找到selected_sql来自哪个rollout，使用该rollout的reward
    if selected_sql:
        selected_reward = 0.0
        # 在所有rollout中查找selected_sql
        for rollout_idx, rollout in enumerate(rollout_stats):
            selected_sql_in_rollout = rollout.get('selected_sql')
            if selected_sql_in_rollout and selected_sql_in_rollout.strip() == selected_sql.strip():
                # 找到了selected_sql的来源rollout，使用该rollout的reward
                selected_reward = rollout.get('reward', 0.0)
                break
        
        # 如果没找到，使用stats中的average_reward作为fallback
        if selected_reward == 0.0:
            stats = result_data.get('stats', {})
            selected_reward = stats.get('average_reward', 0.0)
        
        all_sqls.append({
            'sql': selected_sql,
            'source': 'selected',
            'reward': selected_reward,
            'rollout_id': None
        })
    
    # 2. 提取所有rollout中的SQL
    for rollout_idx, rollout in enumerate(rollout_stats):
        rollout_reward = rollout.get('reward', 0.0)
        result_buckets = rollout.get('result_buckets', {})
        all_sql_variants = rollout.get('all_sql_variants', [])
        total_variants = len(all_sql_variants)
        
        # 2.1 提取rollout的selected_sql
        selected_sql_in_rollout = rollout.get('selected_sql')
        if selected_sql_in_rollout:
            all_sqls.append({
                'sql': selected_sql_in_rollout,
                'source': f'rollout_{rollout_idx + 1}',
                'reward': rollout_reward,  # rollout的selected_sql使用rollout的reward
                'rollout_id': rollout_idx + 1
            })
        
        # 2.2 提取所有SQL变体，计算每个SQL的实际reward（基于它所属的bucket）
        for sql_var in all_sql_variants:
            sql_text = sql_var.get('sql', '').strip()
            if not sql_text:
                continue
            
            # 计算该SQL的reward（基于它所属的bucket）
            sql_signature = sql_var.get('result_signature')
            if sql_signature and sql_signature in result_buckets and total_variants > 0:
                # 该SQL的reward = 它所属bucket的计数 / 总变体数
                bucket_count = result_buckets[sql_signature]
                sql_reward = bucket_count / float(total_variants)
            else:
                # 如果没有结果签名或不在buckets中，使用rollout的reward
                sql_reward = rollout_reward
            
            # 避免重复添加selected_sql（已经在上面添加了）
            if sql_text == selected_sql_in_rollout:
                continue
            
            all_sqls.append({
                'sql': sql_text,
                'source': f'rollout_{rollout_idx + 1}_variant',
                'reward': sql_reward,  # 使用基于bucket计算的reward
                'rollout_id': rollout_idx + 1
            })
    
    return all_sqls


def evaluate_single_question(question_id: str, result_data: Dict, gold_sql: str, db_name: str, timeout_s: float = 30.0) -> Dict:
    """
    评估单个问题的所有SQL
    
    Returns:
        Dict包含评估结果
    """
    # 提取所有SQL
    all_sqls = extract_all_sqls_from_result(result_data)
    
    # 去重SQL（基于SQL文本）
    unique_sqls = {}
    for sql_info in all_sqls:
        sql_text = sql_info['sql']
        if sql_text not in unique_sqls:
            unique_sqls[sql_text] = sql_info
        else:
            # 如果已存在，保留reward更高的
            if sql_info['reward'] > unique_sqls[sql_text]['reward']:
                unique_sqls[sql_text] = sql_info
    
    # 构建数据库连接器
    db_connector = DatabaseConnector(db_name)
    if not db_connector.connect():
        return {
            'question_id': question_id,
            'error': f'数据库连接失败: {db_name}',
            'total_sqls': len(unique_sqls),
            'matches': [],
            'selected_match': False
        }
    
    try:
        # 评估每个SQL
        evaluation_results = []
        selected_match = False
        
        for sql_text, sql_info in unique_sqls.items():
            is_match, error = compare_with_gold_timeout(
                sql_text, gold_sql, db_connector, timeout_s=timeout_s
            )
            
            evaluation_results.append({
                'sql': sql_text,
                'source': sql_info['source'],
                'reward': sql_info['reward'],
                'rollout_id': sql_info['rollout_id'],
                'matches_gold': is_match,
                'error': error
            })
            
            # 检查selected_sql是否匹配
            if sql_info['source'] == 'selected' and is_match:
                selected_match = True
        
        return {
            'question_id': question_id,
            'total_sqls': len(unique_sqls),
            'matches': evaluation_results,
            'selected_match': selected_match,
            'has_match': any(r['matches_gold'] for r in evaluation_results)
        }
        
    finally:
        db_connector.disconnect()


def process_single_question(args_tuple) -> Dict:
    """处理单个问题的包装函数（用于并行执行）"""
    question_id, result_data, gold_sql, db_name, timeout_s = args_tuple
    
    try:
        result = evaluate_single_question(question_id, result_data, gold_sql, db_name, timeout_s)
        return result
    except Exception as e:
        return {
            'question_id': question_id,
            'error': f'处理异常: {str(e)}',
            'total_sqls': 0,
            'matches': [],
            'selected_match': False
        }


def analyze_strategy_effectiveness(evaluation_results: List[Dict]) -> Dict:
    """分析MCTS选择策略的效果"""
    total_questions = len(evaluation_results)
    selected_matches = sum(1 for r in evaluation_results if r.get('selected_match', False))
    has_match = sum(1 for r in evaluation_results if r.get('has_match', False))
    
    # 统计：如果selected不匹配，但其他SQL匹配的情况
    missed_opportunities = []
    for r in evaluation_results:
        if not r.get('selected_match', False) and r.get('has_match', False):
            # 找到匹配的SQL
            matching_sqls = [m for m in r.get('matches', []) if m.get('matches_gold', False)]
            if matching_sqls:
                # 找到reward最高的匹配SQL
                best_match = max(matching_sqls, key=lambda x: x.get('reward', 0.0))
                selected_sql_info = next((m for m in r.get('matches', []) if m.get('source') == 'selected'), None)
                
                missed_opportunities.append({
                    'question_id': r['question_id'],
                    'selected_reward': selected_sql_info.get('reward', 0.0) if selected_sql_info else 0.0,
                    'best_match_reward': best_match.get('reward', 0.0),
                    'best_match_source': best_match.get('source', 'unknown')
                })
    
    # 按reward排序分析
    reward_analysis = {}
    for r in evaluation_results:
        matches = r.get('matches', [])
        if not matches:
            continue
        
        # 找到匹配的SQL
        matching_sqls = [m for m in matches if m.get('matches_gold', False)]
        if matching_sqls:
            # 按reward排序
            matching_sqls_sorted = sorted(matching_sqls, key=lambda x: x.get('reward', 0.0), reverse=True)
            best_reward = matching_sqls_sorted[0].get('reward', 0.0)
            
            # 检查selected是否是最高的
            selected_sql_info = next((m for m in matches if m.get('source') == 'selected'), None)
            if selected_sql_info:
                selected_reward = selected_sql_info.get('reward', 0.0)
                if selected_reward < best_reward:
                    reward_analysis[r['question_id']] = {
                        'selected_reward': selected_reward,
                        'best_match_reward': best_reward,
                        'gap': best_reward - selected_reward
                    }
    
    return {
        'total_questions': total_questions,
        'selected_matches': selected_matches,
        'selected_accuracy': selected_matches / total_questions if total_questions > 0 else 0.0,
        'has_match_count': has_match,
        'potential_accuracy': has_match / total_questions if total_questions > 0 else 0.0,
        'missed_opportunities': len(missed_opportunities),
        'missed_opportunities_details': missed_opportunities,
        'reward_analysis': reward_analysis
    }


def main():
    parser = argparse.ArgumentParser(description='评估MCTS选择策略的效果')
    parser.add_argument('--result_file', type=str, required=True,
                        help='结果文件路径')
    parser.add_argument('--ppl_file', type=str, 
                        default='/hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/data/subset_ppl_dev_python.json',
                        help='PPL文件路径（用于获取database映射）')
    parser.add_argument('--gold_file', type=str, default=None,
                        help='Gold SQL文件路径（可选，如果结果文件中已包含gold_sql）')
    parser.add_argument('--timeout_s', type=float, default=30.0,
                        help='SQL执行超时时间（秒）')
    parser.add_argument('--max_workers', type=int, default=10,
                        help='最大并行工作线程数')
    parser.add_argument('--output_file', type=str, default=None,
                        help='输出结果文件路径（JSON格式）')
    
    args = parser.parse_args()
    
    # 加载数据
    print("=" * 80)
    print("开始加载数据...")
    print("=" * 80)
    
    results = load_result_file(args.result_file)
    db_mapping = load_database_mapping(args.ppl_file)
    
    # 加载gold SQLs（如果提供了gold_file）
    gold_sqls = {}
    if args.gold_file:
        print(f"[加载] 正在加载Gold SQL文件: {args.gold_file}")
        with open(args.gold_file, 'r', encoding='utf-8') as f:
            gold_data = json.load(f)
            # 支持两种格式：列表格式和字典格式
            if isinstance(gold_data, list):
                # 列表格式：[{"question_id": 1, "SQL": "..."}, ...]
                for item in gold_data:
                    qid = item.get('question_id')
                    sql = item.get('SQL', '')
                    if qid is not None and sql:
                        gold_sqls[str(qid)] = sql
            elif isinstance(gold_data, dict):
                # 字典格式：{"1": "SQL", ...} 或 {"1": {"SQL": "..."}, ...}
                for qid_str, value in gold_data.items():
                    if isinstance(value, str):
                        gold_sqls[qid_str] = value
                    elif isinstance(value, dict):
                        sql = value.get('SQL', '')
                        if sql:
                            gold_sqls[qid_str] = sql
        print(f"[加载] 成功加载 {len(gold_sqls)} 个Gold SQL")
    else:
        print(f"[加载] 未提供Gold SQL文件，将从结果文件中读取gold_sql")
    
    # 准备任务列表
    tasks = []
    for qid_str, result_data in results.items():
        qid = int(qid_str)
        if qid not in db_mapping:
            print(f"[警告] 问题 {qid} 未找到数据库映射，跳过")
            continue
        
        # 优先从gold_sqls中获取，如果没有则从result_data的stats中获取
        gold_sql = None
        if str(qid) in gold_sqls:
            gold_sql = gold_sqls[str(qid)]
        elif result_data.get('stats', {}).get('gold_sql'):
            gold_sql = result_data['stats']['gold_sql']
        
        if not gold_sql:
            print(f"[警告] 问题 {qid} 未找到Gold SQL，跳过")
            continue
        
        db_name = db_mapping[qid]
        tasks.append((qid_str, result_data, gold_sql, db_name, args.timeout_s))
    
    print(f"\n[准备] 共 {len(tasks)} 个问题需要评估")
    
    # 并行执行评估
    print("\n" + "=" * 80)
    print("开始并行评估...")
    print("=" * 80)
    
    evaluation_results = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {executor.submit(process_single_question, task): task[0] for task in tasks}
        
        with tqdm(total=len(tasks), desc="评估进度") as pbar:
            for future in as_completed(futures):
                qid = futures[future]
                try:
                    result = future.result()
                    evaluation_results.append(result)
                except Exception as e:
                    print(f"\n[错误] 问题 {qid} 评估失败: {e}")
                    evaluation_results.append({
                        'question_id': qid,
                        'error': f'评估异常: {str(e)}',
                        'total_sqls': 0,
                        'matches': [],
                        'selected_match': False
                    })
                finally:
                    pbar.update(1)
    
    # 分析结果
    print("\n" + "=" * 80)
    print("分析结果...")
    print("=" * 80)
    
    analysis = analyze_strategy_effectiveness(evaluation_results)
    
    # 打印统计信息
    print(f"\n{'='*80}")
    print("评估结果统计")
    print(f"{'='*80}")
    print(f"总问题数: {analysis['total_questions']}")
    print(f"Selected SQL匹配数: {analysis['selected_matches']}")
    print(f"Selected SQL准确率: {analysis['selected_accuracy']:.2%}")
    print(f"存在匹配SQL的问题数: {analysis['has_match_count']}")
    print(f"潜在准确率: {analysis['potential_accuracy']:.2%}")
    print(f"错失机会数: {analysis['missed_opportunities']}")
    
    if analysis['missed_opportunities'] > 0:
        print(f"\n错失机会详情（前10个）:")
        for i, opp in enumerate(analysis['missed_opportunities_details'][:10]):
            print(f"  问题 {opp['question_id']}: Selected reward={opp['selected_reward']:.4f}, "
                  f"Best match reward={opp['best_match_reward']:.4f}, "
                  f"Source={opp['best_match_source']}")
    
    if analysis['reward_analysis']:
        print(f"\nReward分析（Selected不是最高reward的情况，共{len(analysis['reward_analysis'])}个）:")
        for qid, info in list(analysis['reward_analysis'].items())[:10]:
            print(f"  问题 {qid}: Selected reward={info['selected_reward']:.4f}, "
                  f"Best match reward={info['best_match_reward']:.4f}, "
                  f"Gap={info['gap']:.4f}")
    
    # 保存结果
    output = {
        'evaluation_results': evaluation_results,
        'analysis': analysis
    }
    
    if args.output_file:
        print(f"\n[保存] 正在保存结果到: {args.output_file}")
        with open(args.output_file, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[保存] 结果已保存")
    else:
        # 默认输出文件名
        default_output = args.result_file.replace('.json', '_evaluation.json')
        print(f"\n[保存] 正在保存结果到: {default_output}")
        with open(default_output, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[保存] 结果已保存")
    
    print("\n" + "=" * 80)
    print("评估完成！")
    print("=" * 80)


if __name__ == '__main__':
    main()
