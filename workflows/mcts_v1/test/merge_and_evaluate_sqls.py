"""
合并两个结果文件中的SQL，按照分桶结果选择最佳SQL，并与gold SQL比较

功能：
1. 从每个文件中找出reward最高的rollout对应的all_sql_variants
2. 合并两个文件的SQL变体
3. 执行这些SQL，按照分桶结果选出最终的SQL
4. 与gold SQL比较
"""

import json
import sys
import argparse
import math
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter

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
from workflows.mcts_v1.utils.mcts_helpers import MCTSUtils
from workflows.mcts_v1.test.test_mcts import compare_with_gold, build_db_connector


def calculate_entropy(result_buckets: Dict[str, int]) -> float:
    """
    计算result_buckets的信息熵
    
    Args:
        result_buckets: {signature: count} 字典
        
    Returns:
        信息熵值（如果为空则返回0）
    """
    if not result_buckets:
        return 0.0
    
    total = sum(result_buckets.values())
    if total == 0:
        return 0.0
    
    entropy = 0.0
    for count in result_buckets.values():
        if count > 0:
            prob = count / total
            entropy -= prob * math.log2(prob)
    
    return entropy


def calculate_confidence(result_buckets: Dict[str, int]) -> float:
    """
    计算result_buckets的置信度（最高count占总数的比例）
    
    Args:
        result_buckets: {signature: count} 字典
        
    Returns:
        置信度值（0-1之间）
    """
    if not result_buckets:
        return 0.0
    
    total = sum(result_buckets.values())
    if total == 0:
        return 0.0
    
    max_count = max(result_buckets.values())
    return max_count / total


def calculate_diversity(result_buckets: Dict[str, int]) -> int:
    """
    计算result_buckets的多样性（不同signature的数量）
    
    Args:
        result_buckets: {signature: count} 字典
        
    Returns:
        不同signature的数量
    """
    # 排除无效的signature
    valid_signatures = [sig for sig in result_buckets.keys() 
                       if not sig.startswith('invalid_') and sig != 'empty_result']
    return len(valid_signatures)


def select_rollouts_by_strategy(rollout_stats: List[Dict[str, Any]], strategy: str = "max_reward") -> List[Dict[str, Any]]:
    """
    根据策略选择rollout
    
    Args:
        rollout_stats: rollout统计信息列表
        strategy: 选择策略
            - "max_reward": 选择reward最高的rollout（默认）
            - "max_entropy": 选择信息熵最高的rollout
            - "max_diversity": 选择多样性最高的rollout（不同signature数量最多）
            - "max_confidence": 选择置信度最高的rollout（最高count占比最高）
            - "reward_entropy_combined": 综合考虑reward和熵（reward * entropy）
            - "reward_confidence_combined": 综合考虑reward和置信度（reward * confidence）
    
    Returns:
        选中的rollout列表
    """
    if not rollout_stats:
        return []
    
    # 过滤掉没有result_buckets的rollout
    valid_rollouts = [r for r in rollout_stats if r.get('result_buckets')]
    
    if not valid_rollouts:
        return []
    
    if strategy == "max_reward":
        # 选择reward最高的rollout
        max_reward = max((r.get('reward', 0.0) for r in valid_rollouts), default=0.0)
        return [r for r in valid_rollouts if r.get('reward', 0.0) == max_reward]
    
    elif strategy == "max_entropy":
        # 选择信息熵最高的rollout
        rollout_entropies = []
        for r in valid_rollouts:
            result_buckets = r.get('result_buckets', {})
            entropy = calculate_entropy(result_buckets)
            rollout_entropies.append((r, entropy))
        
        max_entropy = max((ent for _, ent in rollout_entropies), default=0.0)
        return [r for r, ent in rollout_entropies if ent == max_entropy]
    
    elif strategy == "max_diversity":
        # 选择多样性最高的rollout
        rollout_diversities = []
        for r in valid_rollouts:
            result_buckets = r.get('result_buckets', {})
            diversity = calculate_diversity(result_buckets)
            rollout_diversities.append((r, diversity))
        
        max_diversity = max((div for _, div in rollout_diversities), default=0)
        return [r for r, div in rollout_diversities if div == max_diversity]
    
    elif strategy == "max_confidence":
        # 选择置信度最高的rollout
        rollout_confidences = []
        for r in valid_rollouts:
            result_buckets = r.get('result_buckets', {})
            confidence = calculate_confidence(result_buckets)
            rollout_confidences.append((r, confidence))
        
        max_confidence = max((conf for _, conf in rollout_confidences), default=0.0)
        return [r for r, conf in rollout_confidences if conf == max_confidence]
    
    elif strategy == "reward_entropy_combined":
        # 综合考虑reward和熵：reward * entropy
        rollout_scores = []
        for r in valid_rollouts:
            reward = r.get('reward', 0.0)
            result_buckets = r.get('result_buckets', {})
            entropy = calculate_entropy(result_buckets)
            score = reward * entropy if entropy > 0 else reward * 0.1  # 如果熵为0，给一个小的权重
            rollout_scores.append((r, score))
        
        max_score = max((score for _, score in rollout_scores), default=0.0)
        return [r for r, score in rollout_scores if score == max_score]
    
    elif strategy == "reward_confidence_combined":
        # 综合考虑reward和置信度：reward * confidence
        rollout_scores = []
        for r in valid_rollouts:
            reward = r.get('reward', 0.0)
            result_buckets = r.get('result_buckets', {})
            confidence = calculate_confidence(result_buckets)
            score = reward * confidence
            rollout_scores.append((r, score))
        
        max_score = max((score for _, score in rollout_scores), default=0.0)
        return [r for r, score in rollout_scores if score == max_score]
    
    else:
        # 默认使用max_reward策略
        max_reward = max((r.get('reward', 0.0) for r in valid_rollouts), default=0.0)
        return [r for r in valid_rollouts if r.get('reward', 0.0) == max_reward]


def find_best_rollout_sqls(data: Dict[str, Any], qid: str, file_label: str = "", strategy: str = "max_reward") -> List[str]:
    """
    找到指定question_id的最佳rollout，返回其result_buckets中count最高的signature对应的SQL
    
    Args:
        data: JSON数据
        qid: question_id
        file_label: 文件标签（用于打印）
        strategy: 选择策略（默认"max_reward"）
        
    Returns:
        SQL字符串列表（每个最佳rollout对应一个SQL）
    """
    if qid not in data:
        return []
    
    question_data = data[qid]
    rollout_stats = question_data.get('rollout_stats', [])
    
    if not rollout_stats:
        return []
    
    # 根据策略选择rollout
    best_rollouts = select_rollouts_by_strategy(rollout_stats, strategy)
    
    # 打印策略信息和选中的rollout信息
    strategy_names = {
        "max_reward": "最高reward",
        "max_entropy": "最高信息熵",
        "max_diversity": "最高多样性",
        "max_confidence": "最高置信度",
        "reward_entropy_combined": "reward×熵",
        "reward_confidence_combined": "reward×置信度"
    }
    strategy_name = strategy_names.get(strategy, strategy)
    
    if best_rollouts:
        first_rollout = best_rollouts[0]
        reward = first_rollout.get('reward', 0.0)
        result_buckets = first_rollout.get('result_buckets', {})
        entropy = calculate_entropy(result_buckets)
        confidence = calculate_confidence(result_buckets)
        diversity = calculate_diversity(result_buckets)
        print(f"  [{file_label}] question_id={qid}: 使用策略[{strategy_name}]找到{len(best_rollouts)}个rollout")
        print(f"    reward={reward:.4f}, entropy={entropy:.4f}, confidence={confidence:.4f}, diversity={diversity}")
    else:
        print(f"  [{file_label}] question_id={qid}: 使用策略[{strategy_name}]未找到有效rollout")
        return []
    
    selected_sqls = []
    
    for rollout in best_rollouts:
        # 获取result_buckets
        result_buckets = rollout.get('result_buckets', {})
        if not result_buckets:
            print(f"    [警告] 该rollout没有result_buckets，跳过")
            continue
        
        # 找到count最高的signature
        max_count = max(result_buckets.values())
        best_signatures = [sig for sig, count in result_buckets.items() if count == max_count]
        
        # 如果有多个平票，选择第一个
        best_signature = best_signatures[0] if best_signatures else None
        
        if not best_signature:
            print(f"    [警告] 无法找到最佳signature，跳过")
            continue
        
        # 从all_sql_variants中找到这个signature对应的SQL
        all_sql_variants = rollout.get('all_sql_variants', [])
        found_sql = None
        
        for sql_info in all_sql_variants:
            sql_signature = sql_info.get('result_signature')
            if sql_signature == best_signature:
                found_sql = sql_info.get('sql', '')
                break
        
        if found_sql:
            selected_sqls.append(found_sql)
            print(f"    [选择] signature={best_signature}, count={max_count}, SQL长度={len(found_sql)}")
        else:
            print(f"    [警告] 未找到signature={best_signature}对应的SQL")
    
    print(f"  [{file_label}] 共选择了{len(selected_sqls)}个SQL")
    
    return selected_sqls


def execute_sqls_and_bucketize(sql_variants: List[Dict[str, Any]], db_connector: DatabaseConnector) -> Tuple[Dict[str, int], str, Dict[str, str]]:
    """
    执行SQL变体，创建result_signature，分桶，返回(分桶字典, 最佳签名, 签名到SQL的映射)
    
    Args:
        sql_variants: SQL变体列表，每个元素包含'sql'字段
        db_connector: 数据库连接器
        
    Returns:
        (result_buckets, best_key, signature_to_sql)
    """
    execution_results = []
    
    # 执行所有SQL
    for sql_info in sql_variants:
        sql = sql_info.get('sql', '')
        if not sql:
            execution_results.append({
                'valid': False,
                'error': 'Empty SQL',
                'query_result': []
            })
            continue
        
        try:
            # 执行SQL
            result, error = db_connector.execute_query(sql, timeout_s=30.0)
            
            if error is not None:
                execution_results.append({
                    'valid': False,
                    'error': error,
                    'query_result': []
                })
            elif result is not None:
                # 转换为字典列表格式
                query_result = MCTSUtils.safe_to_dict(result)
                execution_results.append({
                    'valid': True,
                    'error': None,
                    'query_result': query_result
                })
            else:
                execution_results.append({
                    'valid': False,
                    'error': 'Result is None',
                    'query_result': []
                })
        except Exception as e:
            execution_results.append({
                'valid': False,
                'error': str(e),
                'query_result': []
            })
    
    # 分桶
    result_buckets, best_key = MCTSUtils.bucketize_valid_nonempty(execution_results)
    
    # 创建签名到SQL的映射（选择第一个匹配的SQL）
    signature_to_sql = {}
    for sql_info, exec_res in zip(sql_variants, execution_results):
        if exec_res.get('valid', False):
            sig = MCTSUtils.create_result_signature(exec_res)
            if sig not in signature_to_sql:
                signature_to_sql[sig] = sql_info.get('sql', '')
    
    print(f"  [执行结果] 执行了{len(sql_variants)}个SQL，有效{sum(1 for r in execution_results if r.get('valid', False))}个")
    print(f"  [分桶结果] 共有{len(result_buckets)}个不同的结果签名，最佳签名: {best_key}")
    if result_buckets:
        print(f"  [分桶详情] {dict(result_buckets)}")
    
    return result_buckets, best_key, signature_to_sql


def select_best_sql(result_buckets: Dict[str, int], best_key: str, signature_to_sql: Dict[str, str], 
                   signature_to_result: Dict[str, Any] = None) -> str:
    """
    根据分桶结果选择最佳SQL
    
    Args:
        result_buckets: 分桶字典
        best_key: 最佳签名
        signature_to_sql: 签名到SQL的映射
        signature_to_result: 签名到结果的映射（可选，用于平票时选择）
        
    Returns:
        最佳SQL
    """
    if not result_buckets or not best_key:
        return ""
    
    # 如果有平票，选择结果行数最少的，然后列数最少的，最后SQL最短的
    max_count = max(result_buckets.values())
    tied_keys = [k for k, v in result_buckets.items() if v == max_count]
    
    if len(tied_keys) > 1:
        print(f"  [平票处理] 有{len(tied_keys)}个签名平票，进行tiebreak")
        
        def get_tiebreak_score(sig: str) -> Tuple[int, int, int]:
            """返回(行数, 列数, SQL长度)，越小越好"""
            res = signature_to_result.get(sig, []) if signature_to_result else []
            sql = signature_to_sql.get(sig, "")
            num_rows = len(res) if isinstance(res, list) else 0
            num_cols = 0
            if res and isinstance(res, list) and len(res) > 0:
                first_row = res[0]
                if isinstance(first_row, dict):
                    num_cols = len(first_row.keys())
            sql_len = len(sql)
            return (num_rows, num_cols, sql_len)
        
        best_key = min(tied_keys, key=get_tiebreak_score)
        print(f"  [平票处理] 选择签名: {best_key}")
    
    best_sql = signature_to_sql.get(best_key, "")
    return best_sql


def process_question_single_file(qid: str, file_label: str, data: Dict[str, Any], 
                                 db_name: str, gold_sql: Optional[str] = None, 
                                 strategy: str = "max_reward") -> Dict[str, Any]:
    """
    处理单个question_id，只使用单个文件的数据
    
    Args:
        qid: question_id
        file_label: 文件标签
        data: 文件数据
        db_name: 数据库名称
        gold_sql: gold SQL（可选）
        strategy: 选择策略（默认"max_reward"）
        
    Returns:
        处理结果字典
    """
    # 从文件中找到最佳rollout对应的最高桶分布SQL
    sqls = find_best_rollout_sqls(data, qid, file_label=file_label, strategy=strategy)
    
    if not sqls:
        return {
            'qid': qid,
            'file_label': file_label,
            'error': '未找到数据',
            'best_sql': '',
            'gold_match': None
        }
    
    # 去重SQL
    all_sqls = []
    seen_sqls = set()
    for sql in sqls:
        sql = sql.strip()
        if sql and sql not in seen_sqls:
            seen_sqls.add(sql)
            all_sqls.append(sql)
    
    if not all_sqls:
        return {
            'qid': qid,
            'file_label': file_label,
            'error': '没有有效的SQL',
            'best_sql': '',
            'gold_match': None
        }
    
    # 执行SQL并分桶
    db_connector = build_db_connector(db_name)
    try:
        sql_variants = [{'sql': sql} for sql in all_sqls]
        result_buckets, best_key, signature_to_sql = execute_sqls_and_bucketize(sql_variants, db_connector)
        
        # 为了平票处理，需要创建签名到结果的映射
        signature_to_result = {}
        for sql in all_sqls:
            if not sql:
                continue
            try:
                result, error = db_connector.execute_query(sql, timeout_s=30.0)
                if error is None and result is not None:
                    query_result = MCTSUtils.safe_to_dict(result)
                    exec_res = {
                        'valid': True,
                        'error': None,
                        'query_result': query_result
                    }
                    sig = MCTSUtils.create_result_signature(exec_res)
                    if sig not in signature_to_result:
                        signature_to_result[sig] = query_result
            except Exception:
                pass
        
        # 选择最佳SQL
        best_sql = select_best_sql(result_buckets, best_key, signature_to_sql, signature_to_result)
        
        # 与gold SQL比较
        gold_match = None
        if gold_sql:
            gold_match = compare_with_gold(best_sql, gold_sql, db_connector=db_connector)
        
        return {
            'qid': qid,
            'file_label': file_label,
            'best_sql': best_sql,
            'result_buckets': dict(result_buckets),
            'best_signature': best_key,
            'total_sqls': len(all_sqls),
            'valid_sql_count': sum(1 for k in result_buckets.keys() if not k.startswith('invalid_') and k != 'empty_result'),
            'gold_match': gold_match,
            'gold_sql': gold_sql
        }
    finally:
        db_connector.disconnect()


def process_question(qid: str, file_data_list: List[Tuple[str, Dict[str, Any]]], 
                    db_name: str, gold_sql: Optional[str] = None, 
                    strategy: str = "max_reward") -> Dict[str, Any]:
    """
    处理单个question_id
    
    Args:
        qid: question_id
        file_data_list: 文件数据列表，每个元素是(file_label, data)的元组
        db_name: 数据库名称
        gold_sql: gold SQL（可选）
        strategy: 选择策略（默认"max_reward"）
        
    Returns:
        处理结果字典
    """
    print(f"\n{'='*80}")
    print(f">>> 处理 question_id={qid}")
    print(f"{'='*80}")
    
    # 1. 从所有文件中找到最佳rollout对应的最高桶分布SQL
    all_sqls_from_files = []
    files_with_data = []
    files_without_data = []
    
    for file_label, data in file_data_list:
        sqls = find_best_rollout_sqls(data, qid, file_label=file_label, strategy=strategy)
        if sqls:
            all_sqls_from_files.extend(sqls)
            files_with_data.append(file_label)
        else:
            files_without_data.append(file_label)
    
    if files_without_data:
        print(f"  [信息] 以下文件中未找到question_id={qid}的数据: {', '.join(files_without_data)}")
    
    if not all_sqls_from_files:
        print(f"  [错误] 所有文件中都没有找到question_id={qid}的数据")
        return {
            'qid': qid,
            'error': '未找到数据',
            'best_sql': '',
            'gold_match': None
        }
    
    print(f"  [信息] 从以下文件中找到了数据: {', '.join(files_with_data)}")
    
    # 2. 合并SQL（去重，基于SQL文本）
    all_sqls = []
    seen_sqls = set()
    
    for sql in all_sqls_from_files:
        sql = sql.strip()
        if sql and sql not in seen_sqls:
            seen_sqls.add(sql)
            all_sqls.append(sql)
    
    print(f"  [合并] 从{len(file_data_list)}个文件合并后共有{len(all_sqls)}个唯一的SQL")
    
    if not all_sqls:
        print(f"  [错误] 没有有效的SQL")
        return {
            'qid': qid,
            'error': '没有有效的SQL',
            'best_sql': '',
            'gold_match': None
        }
    
    # 3. 执行SQL并分桶
    db_connector = build_db_connector(db_name)
    try:
        # 转换为sql_variants格式
        sql_variants = [{'sql': sql} for sql in all_sqls]
        result_buckets, best_key, signature_to_sql = execute_sqls_and_bucketize(sql_variants, db_connector)
        
        # 为了平票处理，需要创建签名到结果的映射
        signature_to_result = {}
        for sql in all_sqls:
            if not sql:
                continue
            try:
                result, error = db_connector.execute_query(sql, timeout_s=30.0)
                if error is None and result is not None:
                    query_result = MCTSUtils.safe_to_dict(result)
                    exec_res = {
                        'valid': True,
                        'error': None,
                        'query_result': query_result
                    }
                    sig = MCTSUtils.create_result_signature(exec_res)
                    if sig not in signature_to_result:
                        signature_to_result[sig] = query_result
            except Exception:
                pass
        
        # 4. 选择最佳SQL
        best_sql = select_best_sql(result_buckets, best_key, signature_to_sql, signature_to_result)
        
        print(f"  [选择] 最终选择的SQL: {best_sql[:200]}..." if len(best_sql) > 200 else f"  [选择] 最终选择的SQL: {best_sql}")
        
        # 5. 与gold SQL比较
        gold_match = None
        if gold_sql:
            print(f"  [Gold验证] Gold SQL: {gold_sql[:200]}..." if len(gold_sql) > 200 else f"  [Gold验证] Gold SQL: {gold_sql}")
            gold_match = compare_with_gold(best_sql, gold_sql, db_connector=db_connector)
            if gold_match:
                print(f"  ✅ [Gold验证] 匹配成功！")
            else:
                print(f"  ❌ [Gold验证] 不匹配")
        else:
            print(f"  ⚠️ [Gold验证] 未提供gold SQL")
        
        return {
            'qid': qid,
            'best_sql': best_sql,
            'result_buckets': dict(result_buckets),
            'best_signature': best_key,
            'total_sqls': len(all_sqls),
            'valid_sql_count': sum(1 for k in result_buckets.keys() if not k.startswith('invalid_') and k != 'empty_result'),
            'gold_match': gold_match,
            'gold_sql': gold_sql
        }
    finally:
        db_connector.disconnect()


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


def process_question_with_top_sql(qid: str, file_label: str, data: Dict[str, Any], 
                                   db_name: str, gold_sql: Optional[str] = None) -> Dict[str, Any]:
    """
    使用顶层sql字段直接评估
    
    Args:
        qid: question_id
        file_label: 文件标签
        data: 文件数据
        db_name: 数据库名称
        gold_sql: gold SQL（可选）
        
    Returns:
        处理结果字典
    """
    if qid not in data:
        return {
            'qid': qid,
            'file_label': file_label,
            'error': '未找到数据',
            'best_sql': '',
            'gold_match': False  # 没有数据算作失败
        }
    
    question_data = data[qid]
    best_sql = question_data.get('sql', '').strip()
    
    if not best_sql:
        return {
            'qid': qid,
            'file_label': file_label,
            'error': '没有有效的SQL',
            'best_sql': '',
            'gold_match': False  # 没有SQL算作失败
        }
    
    # 与gold SQL比较
    gold_match = False
    if gold_sql:
        db_connector = build_db_connector(db_name)
        try:
            gold_match = compare_with_gold(best_sql, gold_sql, db_connector=db_connector)
            if gold_match:
                print(f"  [{file_label}] qid={qid}: ✅ 匹配成功")
            else:
                print(f"  [{file_label}] qid={qid}: ❌ 不匹配")
        finally:
            db_connector.disconnect()
    
    return {
        'qid': qid,
        'file_label': file_label,
        'best_sql': best_sql,
        'gold_match': gold_match,
        'gold_sql': gold_sql
    }


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


def main():
    parser = argparse.ArgumentParser(description="合并多个结果文件中的SQL，按照分桶结果选择最佳SQL，并与gold SQL比较")
    parser.add_argument("--files", type=str, required=True, help="结果JSON文件列表，用逗号分隔（如 'file1.json,file2.json,file3.json'）")
    parser.add_argument("--gold_file", type=str, default=None, help="Gold SQL文件路径（可选）")
    parser.add_argument("--ppl_file", type=str, default=None, help="样本文件路径（用于获取数据库名称，可选）")
    parser.add_argument("--qids", type=str, default=None, help="要处理的question_id列表，用逗号分隔（如 '158,240'），如果不指定则处理所有共同的question_id")
    parser.add_argument("--output", type=str, default=None, help="输出JSON文件路径（可选）")
    parser.add_argument("--strategy", type=str, default="max_reward", 
                       choices=["max_reward", "max_entropy", "max_diversity", "max_confidence", 
                               "reward_entropy_combined", "reward_confidence_combined"],
                       help="选择rollout的策略: max_reward(最高reward), max_entropy(最高信息熵), "
                            "max_diversity(最高多样性), max_confidence(最高置信度), "
                            "reward_entropy_combined(reward×熵), reward_confidence_combined(reward×置信度)")
    parser.add_argument("--use_top_sql", action="store_true",
                       help="直接使用结果文件中顶层的'sql'字段进行评估，而不是从rollout_stats中选择")
    args = parser.parse_args()
    
    strategy = args.strategy
    use_top_sql = args.use_top_sql
    print(f"[策略] 使用选择策略: {strategy}")
    if use_top_sql:
        print(f"[模式] 使用顶层'sql'字段直接评估")
    
    # 解析文件列表
    file_paths = [f.strip() for f in args.files.split(',') if f.strip()]
    if len(file_paths) < 1:
        print("错误: 至少需要提供一个文件")
        return
    
    print(f"将加载 {len(file_paths)} 个文件")
    
    # 加载所有JSON文件
    file_data_list = []
    all_keys = None
    
    for idx, file_path in enumerate(file_paths):
        print(f"加载文件{idx+1}: {file_path}")
        try:
            # 检查文件是否存在
            if not Path(file_path).exists():
                print(f"  [警告] 文件不存在，跳过: {file_path}")
                continue
            
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            file_label = f"文件{idx+1}"
            file_data_list.append((file_label, data))
            
            # 收集所有question_id（使用并集，然后找出至少在一个文件中存在的question_id）
            if all_keys is None:
                all_keys = set(data.keys())
            else:
                all_keys = all_keys | set(data.keys())
        except FileNotFoundError:
            print(f"  [警告] 文件不存在，跳过: {file_path}")
            continue
        except Exception as e:
            print(f"  [警告] 加载文件失败: {e}，跳过该文件")
            continue
    
    if not file_data_list:
        print("错误: 没有成功加载任何文件")
        return
    
    print(f"成功加载 {len(file_data_list)} 个文件（共尝试加载 {len(file_paths)} 个文件）")
    
    # 加载gold SQL（如果提供）
    gold_sqls = {}
    if args.gold_file:
        gold_sqls = load_gold_sqls(args.gold_file)
    
    # 确定要处理的question_id列表
    if args.qids:
        qid_list = [q.strip() for q in args.qids.split(',') if q.strip()]
    else:
        # 找出所有成功加载的文件中至少出现一次的question_id
        # 如果用户指定了qids，则使用指定的；否则使用所有文件中出现的question_id
        qid_list = sorted(list(all_keys)) if all_keys else []
        print(f"找到 {len(qid_list)} 个question_id（在所有成功加载的文件中出现）")
    
    if not qid_list:
        print("错误: 没有找到要处理的question_id")
        return
    
    # 处理每个question_id
    results = {}
    correct_count = 0
    total_count = 0
    
    # 每个文件的统计信息
    file_stats = {file_label: {'correct': 0, 'total': 0} for file_label, _ in file_data_list}
    
    # upper bound 和 reward-based selection 统计（仅use_top_sql模式）
    upper_bound_correct = 0
    upper_bound_total = 0
    reward_selection_correct = 0
    reward_selection_total = 0
    
    for qid in qid_list:
        # 获取数据库名称
        db_name = None
        if args.ppl_file:
            db_name = get_db_name_from_ppl_file(args.ppl_file, qid)
        
        if not db_name:
            # 尝试从JSON文件中获取（如果存在）
            # 这里假设JSON文件中可能包含数据库信息，但实际上可能没有
            # 如果都没有，需要用户提供
            print(f"  [警告] 无法获取question_id={qid}的数据库名称，跳过")
            continue
        
        gold_sql = gold_sqls.get(qid, None)
        
        if use_top_sql:
            # 使用顶层sql字段直接评估模式 + average_reward选择
            single_file_results = {}
            file_sqls = {}  # {file_label: sql}
            file_rewards = {}  # {file_label: average_reward}
            file_gold_matches = {}  # {file_label: gold_match}

            for file_label, data in file_data_list:
                single_result = process_question_with_top_sql(qid, file_label, data, db_name, gold_sql)
                single_file_results[file_label] = single_result
                file_sqls[file_label] = single_result.get('best_sql', '')
                file_rewards[file_label] = data.get(qid, {}).get('stats', {}).get('average_reward', 0.0)
                file_gold_matches[file_label] = single_result.get('gold_match', False)

                # 统计每个文件的gold验证结果（分母固定为所有question）
                file_stats[file_label]['total'] += 1
                if single_result.get('gold_match'):
                    file_stats[file_label]['correct'] += 1
            
            # Upper bound：任意一个文件对就算对
            upper_bound_total += 1
            if any(file_gold_matches.values()):
                upper_bound_correct += 1
            
            # Self-consistency：执行三个SQL，看结果是否一致，多数投票
            # 需要执行SQL获取结果签名
            db_connector = build_db_connector(db_name)
            try:
                sql_signatures = {}  # {file_label: signature}
                for file_label, sql in file_sqls.items():
                    if not sql:
                        sql_signatures[file_label] = 'empty_sql'
                        continue
                    try:
                        result, error = db_connector.execute_query(sql, timeout_s=30.0)
                        if error is not None:
                            sql_signatures[file_label] = f'error_{hash(str(error)) % 10000}'
                        elif result is not None:
                            query_result = MCTSUtils.safe_to_dict(result)
                            exec_res = {
                                'valid': True,
                                'error': None,
                                'query_result': query_result
                            }
                            sig = MCTSUtils.create_result_signature(exec_res)
                            sql_signatures[file_label] = sig
                        else:
                            sql_signatures[file_label] = 'none_result'
                    except Exception as e:
                        sql_signatures[file_label] = f'exception_{hash(str(e)) % 10000}'
                
                # 统计每个signature出现次数
                sig_counts = Counter(sql_signatures.values())
                # 找出出现次数最多的signature
                most_common_sig, most_common_count = sig_counts.most_common(1)[0] if sig_counts else (None, 0)

                # Reward-based Selection: 选择average_reward最高的SQL
                reward_selection_total += 1
                if file_rewards:
                    # 选择reward最高的策略
                    best_file = max(file_rewards.keys(), key=lambda f: file_rewards[f])
                    best_reward = file_rewards[best_file]
                    best_gold_match = file_gold_matches[best_file]

                    if best_gold_match:
                        reward_selection_correct += 1
                    print(f"  [Reward Selection] qid={qid}: 选择 {best_file} (reward={best_reward:.3f}), gold_match: {best_gold_match}")
                else:
                    # 没有reward信息，随机选第一个
                    first_file = list(file_gold_matches.keys())[0]
                    if file_gold_matches[first_file]:
                        reward_selection_correct += 1
                    print(f"  [Reward Selection] qid={qid}: 无reward信息，使用{first_file}, gold_match: {file_gold_matches[first_file]}")
            finally:
                db_connector.disconnect()
            
            results[qid] = {'qid': qid, 'single_file_results': single_file_results}
        else:
            # 原有模式：从rollout_stats中选择
            # 1. 先单独处理每个文件，计算每个文件的准确度
            single_file_results = {}
            for file_label, data in file_data_list:
                single_result = process_question_single_file(qid, file_label, data, db_name, gold_sql, strategy=strategy)
                single_file_results[file_label] = single_result
                
                # 统计每个文件的gold验证结果
                if single_result.get('gold_match') is not None:
                    file_stats[file_label]['total'] += 1
                    if single_result['gold_match']:
                        file_stats[file_label]['correct'] += 1
            
            # 2. 然后合并处理（总体）
            result = process_question(qid, file_data_list, db_name, gold_sql, strategy=strategy)
            result['single_file_results'] = single_file_results  # 保存每个文件的结果
            results[qid] = result
            
            # 统计gold验证结果（总体）
            if result.get('gold_match') is not None:
                total_count += 1
                if result['gold_match']:
                    correct_count += 1
    
    # 打印每个文件的统计
    print(f"\n{'='*80}")
    print(f"[各文件准确度统计]")
    for file_label, stats in file_stats.items():
        if stats['total'] > 0:
            accuracy = stats['correct'] / stats['total'] * 100
            print(f"  {file_label}: {stats['correct']}/{stats['total']} 正确 (准确率: {accuracy:.2f}%)")
        else:
            print(f"  {file_label}: 0/0 正确 (准确率: N/A)")
    print(f"{'='*80}")
    
    # 如果是use_top_sql模式，打印upper bound和self-consistency统计
    if use_top_sql:
        print(f"\n{'='*80}")
        print(f"[Upper Bound 统计] (任意一个文件对就算对)")
        if upper_bound_total > 0:
            accuracy = upper_bound_correct / upper_bound_total * 100
            print(f"  {upper_bound_correct}/{upper_bound_total} 正确 (准确率: {accuracy:.2f}%)")
        print(f"{'='*80}")
        
        print(f"\n{'='*80}")
        print(f"[Reward-based Selection 统计] (选择average_reward最高的SQL)")
        if reward_selection_total > 0:
            accuracy = reward_selection_correct / reward_selection_total * 100
            print(f"  {reward_selection_correct}/{reward_selection_total} 正确 (准确率: {accuracy:.2f}%)")
        print(f"{'='*80}")
    
    # 打印总体统计
    print(f"\n{'='*80}")
    print(f"[总体准确度统计]")
    print(f"  处理的问题数: {len(results)}")
    if total_count > 0:
        print(f"  Gold验证: {correct_count}/{total_count} 正确 (准确率: {correct_count/total_count*100:.2f}%)")
    print(f"{'='*80}\n")
    
    # 保存结果（如果指定）
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"[保存] 结果已保存到 {args.output}")


if __name__ == "__main__":
    main()

# cd /hpc2hdd/home/sshen190/wtao565 && python SelfCorrectionSQL/workflows/mcts_v1/test/merge_and_evaluate_sqls.py --files /hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/workflows/mcts_v1/test/out/1_10_test_5_strategy_s1_result.json,/hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/workflows/mcts_v1/test/out/1_13_strategy_force_s7_result.json,/hpc2hdd/home/sshen190/wtao565/SelfCorrectionSQL/workflows/mcts_v1/test/out/1_13_strategy_force_s2_result.json --ppl_file SelfCorrectionSQL/data/subset_ppl_dev_python.json --gold_file SelfCorrectionSQL/data/sub_sampled_bird_dev_set.json --output SelfCorrectionSQL/workflows/mcts_v1/test/out/merged_s1_s7_s2_result.json
