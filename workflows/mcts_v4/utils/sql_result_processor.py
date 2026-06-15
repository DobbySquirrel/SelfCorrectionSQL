"""
SQL结果处理器

提取SQL执行和结果处理的通用逻辑，用于快速路径和模拟阶段
"""

from typing import Dict, List, Optional, Tuple, Any
from .mcts_helpers import MCTSUtils
from .sql_exec_helpers import execute_sqls_parallel
import time as _time_for_timing


class SQLResultProcessor:
    """SQL结果处理工具类"""
    
    @staticmethod
    def execute_and_process_sqls(
        db_connector,
        sql_variants: List[str],
        timeout_s: float,
        max_workers: int,
        context_prefix: str = "[SQL执行]"
    ) -> Tuple[List[Dict[str, Any]], float, int]:
        """
        执行SQL变体并处理结果
        
        Args:
            db_connector: 数据库连接器
            sql_variants: SQL变体列表
            timeout_s: 超时时间（秒）
            max_workers: 最大并行工作线程数（建议不超过5，避免数据库连接竞争）
            context_prefix: 日志前缀
            
        Returns:
            (execution_results, elapsed_time, timeout_count)
        """
        # 限制SQL执行的并行数，避免过多并发导致数据库连接竞争
        # 即使外层有多个问题并行处理，每个问题内部的SQL执行也应该限制在较小值
        sql_exec_max_workers = min(max_workers, 3)  # 最多3个SQL并行执行，避免数据库连接竞争
        
        print(f"{context_prefix} 正在并行执行 {len(sql_variants)} 个SQL（超时={timeout_s}s，最大并行数={sql_exec_max_workers}）...")
        
        execution_results = []
        exec_start = _time_for_timing.time()
        parallel_results = execute_sqls_parallel(db_connector, sql_variants, timeout_s=timeout_s, max_workers=sql_exec_max_workers)
        exec_elapsed = _time_for_timing.time() - exec_start
        
        timeout_count = 0
        for (result, error) in parallel_results:
            if result is not None and not error:
                execution_results.append({'valid': True, 'query_result': result})
            else:
                error_msg = str(error) if error else 'unknown error'
                execution_results.append({'valid': False, 'error': error_msg})
                # 检查是否为超时错误
                error_lower = error_msg.lower()
                if '超时' in error_msg or 'timeout' in error_lower or 'timed out' in error_lower:
                    timeout_count += 1
        
        if timeout_count > 0:
            print(f"{context_prefix} ⚠️ 警告：{timeout_count}/{len(sql_variants)} 个SQL执行超时（总耗时 {exec_elapsed:.2f}s）")
        else:
            print(f"{context_prefix} SQL执行完成（总耗时 {exec_elapsed:.2f}s）")
        
        return execution_results, exec_elapsed, timeout_count
    
    @staticmethod
    def build_sql_signature_mapping(
        sql_variants: List[str],
        execution_results: List[Dict[str, Any]]
    ) -> Tuple[
        List[Tuple[str, Optional[str]]],
        Dict[str, Any],
        Dict[str, Dict[tuple, List[Tuple[str, Any]]]],
        Dict[str, str]
    ]:
        """
        建立SQL与签名的映射关系
        
        Args:
            sql_variants: SQL变体列表
            execution_results: 执行结果列表
            
        Returns:
            (sql_with_signatures, signature_to_result, signature_to_column_order_sqls, signature_to_sql)
        """
        sql_with_signatures: List[Tuple[str, Optional[str]]] = []
        signature_to_result: Dict[str, Any] = {}
        signature_to_column_order_sqls: Dict[str, Dict[tuple, List[Tuple[str, Any]]]] = {}
        signature_to_sql: Dict[str, str] = {}
        
        for sql, res in zip(sql_variants, execution_results):
            if res.get('valid', False):
                query_result = res.get('query_result', [])
                try:
                    query_result = MCTSUtils.safe_to_dict(query_result)
                except Exception:
                    query_result = []
                if not isinstance(query_result, list):
                    try:
                        query_result = list(query_result)
                    except Exception:
                        query_result = []
                if query_result and len(query_result) > 0:
                    key = MCTSUtils.cluster_signature(res)
                    sql_with_signatures.append((sql, key))
                    
                    # 提取列顺序
                    column_order = tuple(query_result[0].keys()) if query_result and isinstance(query_result[0], dict) else tuple()
                    
                    if key not in signature_to_column_order_sqls:
                        signature_to_column_order_sqls[key] = {}
                    
                    if column_order not in signature_to_column_order_sqls[key]:
                        signature_to_column_order_sqls[key][column_order] = []
                    signature_to_column_order_sqls[key][column_order].append((sql, query_result))
                    
                    if key not in signature_to_result:
                        signature_to_result[key] = query_result
                        signature_to_sql[key] = sql
                else:
                    sql_with_signatures.append((sql, None))
            else:
                sql_with_signatures.append((sql, None))
        
        return sql_with_signatures, signature_to_result, signature_to_column_order_sqls, signature_to_sql
    
    @staticmethod
    def calculate_reward_and_select_sql(
        sql_variants: List[str],
        execution_results: List[Dict[str, Any]],
        result_buckets: Dict[str, int],
        best_key: str,
        signature_to_column_order_sqls: Dict[str, Dict[tuple, List[Tuple[str, Any]]]],
        signature_to_sql: Dict[str, str],
        sql_with_signatures: List[Tuple[str, Optional[str]]],
        context_prefix: str = "[SQL选择]"
    ) -> Tuple[float, Optional[str]]:
        """
        计算奖励并选择SQL
        
        Args:
            sql_variants: SQL变体列表
            execution_results: 执行结果列表
            result_buckets: 结果分桶
            best_key: 最佳签名
            signature_to_column_order_sqls: 签名到列顺序SQL的映射
            signature_to_sql: 签名到SQL的映射
            sql_with_signatures: SQL与签名的列表
            context_prefix: 日志前缀
            
        Returns:
            (reward, selected_sql)
        """
        # 计算最高一致性（最频繁结果/总变体）
        max_consistency = MCTSUtils.calculate_consistency_reward(result_buckets, len(sql_variants))
        reward = max_consistency
        
        # 如果最佳结果签名为单个0，则降低奖励（惩罚）
        if result_buckets and best_key:
            best_result = None
            for res in execution_results:
                if res.get('valid', False):
                    if MCTSUtils.cluster_signature(res) == best_key:
                        best_result = res.get('query_result', None)
                        break
            
            if best_result is not None and MCTSUtils.is_single_zero_result(best_result):
                original_reward = reward
                reward = reward * 0.5
                print(f"{context_prefix} ⚠️ 警告：最佳结果为单个0，降低奖励 {original_reward:.4f} → {reward:.4f} (惩罚50%)")
        
        # 选择本次rollout的代表SQL
        selected_sql: Optional[str] = None
        if result_buckets:
            if best_key in signature_to_column_order_sqls:
                column_order_sqls = signature_to_column_order_sqls[best_key]
                
                # 分离结果为单个0的SQL和非单个0的SQL
                non_zero_column_order_sqls = {}
                zero_column_order_sqls = {}
                
                for col_order, sqls in column_order_sqls.items():
                    non_zero_sqls = []
                    zero_sqls = []
                    for sql, query_result in sqls:
                        if MCTSUtils.is_single_zero_result(query_result):
                            zero_sqls.append((sql, query_result))
                        else:
                            non_zero_sqls.append((sql, query_result))
                    
                    if non_zero_sqls:
                        non_zero_column_order_sqls[col_order] = non_zero_sqls
                    if zero_sqls:
                        zero_column_order_sqls[col_order] = zero_sqls
                
                candidate_column_order_sqls = non_zero_column_order_sqls if non_zero_column_order_sqls else zero_column_order_sqls
                is_zero_result = not non_zero_column_order_sqls
                
                if candidate_column_order_sqls:
                    column_order_counts = {col_order: len(sqls) for col_order, sqls in candidate_column_order_sqls.items()}
                    max_count = max(column_order_counts.values()) if column_order_counts else 0
                    most_common_column_orders = [col_order for col_order, count in column_order_counts.items() if count == max_count]
                    
                    if most_common_column_orders:
                        best_column_order = most_common_column_orders[0]
                        if best_column_order in candidate_column_order_sqls and candidate_column_order_sqls[best_column_order]:
                            selected_sql = candidate_column_order_sqls[best_column_order][0][0]
                            if is_zero_result:
                                print(f"{context_prefix} ⚠️ 警告：从桶中选择列顺序出现次数最多的SQL，但结果为单个0")
            
            if selected_sql is None:
                selected_sql = signature_to_sql.get(best_key, None)
                if selected_sql is None:
                    for sql, sig in sql_with_signatures:
                        if sig == best_key:
                            selected_sql = sql
                            break
        
        # 如果所有SQL结果都为空，选择第一个有效的SQL
        if selected_sql is None and sql_variants:
            for sql, res in zip(sql_variants, execution_results):
                if res.get('valid', False):
                    selected_sql = sql
                    break
            # 如果所有SQL都无效，不选择任何SQL（返回None），避免选择语法错误的SQL
            if selected_sql is None:
                print(f"{context_prefix} ⚠️ 警告：所有SQL都无效，不选择任何SQL")
        
        return reward, selected_sql
    
    @staticmethod
    def build_all_sql_variants_info(
        sql_variants: List[str],
        execution_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        构建所有SQL变体的详细信息
        
        Args:
            sql_variants: SQL变体列表
            execution_results: 执行结果列表
            
        Returns:
            SQL变体信息列表
        """
        all_sql_variants = []
        for idx, (sql, res) in enumerate(zip(sql_variants, execution_results)):
            sql_info = {
                'sql': sql,
                'valid': res.get('valid', False),
                'error': res.get('error', None) if not res.get('valid', False) else None,
                'result_signature': None,
                'result_row_count': 0
            }
            if res.get('valid', False):
                query_result = res.get('query_result', [])
                try:
                    query_result = MCTSUtils.safe_to_dict(query_result)
                except Exception:
                    query_result = []
                if not isinstance(query_result, list):
                    try:
                        query_result = list(query_result)
                    except Exception:
                        query_result = []
                if query_result and len(query_result) > 0:
                    sql_info['result_signature'] = MCTSUtils.cluster_signature(res)
                    sql_info['result_row_count'] = len(query_result)
            all_sql_variants.append(sql_info)
        
        return all_sql_variants

