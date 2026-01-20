"""
CTE处理器

提取CTE去重和分桶的通用逻辑
"""

import re
from typing import Dict, List, Any, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
from .mcts_helpers import MCTSUtils
from .cte_error_handler import CTEErrorHandler
from ..core.mcts_node import MCTSNode
import time as _time_for_timing


class CTEProcessor:
    """CTE处理工具类"""
    
    def __init__(self, sql_executor, cte_probe_timeout_s: float, max_workers: int, timing_dict: Optional[Dict[str, Any]] = None, cte_probe_limit: int = 15):
        """
        初始化CTE处理器
        
        Args:
            sql_executor: SQL执行器实例
            cte_probe_timeout_s: CTE探针执行超时时间（秒）
            max_workers: 最大并行工作线程数
            timing_dict: 可选的计时统计字典（用于记录执行耗时）
            cte_probe_limit: CTE探针查询的LIMIT值（默认15）
            cte_probe_limit: CTE探针查询的LIMIT值（默认15）
        """
        self.sql_executor = sql_executor
        self.cte_probe_timeout_s = cte_probe_timeout_s
        self.max_workers = max_workers
        self.timing_dict = timing_dict
        self.cte_probe_limit = cte_probe_limit
    
    def deduplicate_cte_variants(
        self, 
        cte_variants: List[str], 
        node: MCTSNode
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        """
        对CTE变体进行去重/分桶
        
        Args:
            cte_variants: CTE变体列表
            node: 当前节点
            
        Returns:
            (去重后的CTE列表, 失败信息列表)
            去重后的CTE列表：每个元素包含 {'cte': str, 'execution_result': dict, 'count': int, 'variants': List[str]}
            失败信息列表：每个元素包含 {'cte': str, 'error': str}，用于重试提示
        """
        if not cte_variants:
            return [], []
        
        # 过滤掉 None 值（生成失败的变体）
        cte_variants = [cte for cte in cte_variants if cte is not None]
        if not cte_variants:
            return [], []
        
        # 用于存储每个桶的代表CTE、执行结果、数量和所有变体
        buckets = {}  # {bucket_key: {'cte': str, 'execution_result': dict, 'count': int, 'variants': List[str]}}
        
        overall_start = _time_for_timing.time()
        
        # 先处理 <END>，其余放入并行
        non_end_ctes = []
        for cte in cte_variants:
            if cte == "<END>":
                if "<END>" not in buckets:
                    buckets["<END>"] = {
                        'cte': cte,
                        'execution_result': None,
                        'count': 1,
                        'variants': [cte]
                    }
                else:
                    buckets["<END>"]['count'] += 1
            else:
                non_end_ctes.append(cte)
        
        def worker(one_cte: str):
            # 1) 构建可执行SQL
            exec_sql = self.sql_executor.build_executable_cte_sql(node, one_cte)
            
            # 如果构建失败（返回空字符串），跳过此CTE
            if not exec_sql:
                # 提取CTE名称用于更具体的错误信息
                cte_name_match = re.match(r'(?:WITH\s+)?(\w+)\s+AS\s*\(', one_cte.strip(), re.IGNORECASE)
                cte_name = cte_name_match.group(1) if cte_name_match else 'unknown'
                error_msg = f"CTE '{cte_name}' 构建失败：无法生成可执行SQL"
                res = {'valid': False, 'error': error_msg}
                return one_cte, res, None, {'cte': one_cte, 'error': error_msg}, ""
            
            # 检查是否已经有LIMIT（包括LIMIT 5、LIMIT 10等）
            has_limit = re.search(r'\bLIMIT\s+\d+', exec_sql, re.IGNORECASE) is not None
            # 如果没有LIMIT，添加LIMIT用于探针执行（使用cte_probe_limit，与prompt中的建议一致）
            if not has_limit:
                # 在最后的SELECT语句末尾添加LIMIT（在分号之前，或如果没有分号则在末尾）
                # 匹配整个SELECT语句，包括WHERE、JOIN、GROUP BY、ORDER BY等子句
                # 使用更精确的正则：匹配SELECT到分号或字符串末尾之间的内容
                if re.search(r'\bSELECT\s+', exec_sql, re.IGNORECASE):
                    # 如果SQL以分号结尾，在分号前添加LIMIT
                    if exec_sql.rstrip().endswith(';'):
                        exec_sql = exec_sql.rstrip()[:-1].rstrip() + f' LIMIT {self.cte_probe_limit};'
                    else:
                        # 如果没有分号，直接在末尾添加LIMIT
                        exec_sql = exec_sql.rstrip() + f' LIMIT {self.cte_probe_limit}'
            # 2) 直接执行（不再进行自动修复）
            # 使用较短的超时时间进行探针执行（快速检测）
            # 注意：统计信息收集推迟到节点创建时进行，避免每次执行都收集
            res = self.sql_executor._execute_single_query(exec_sql, timeout_s=self.cte_probe_timeout_s)

            cte_used = one_cte
            
            # 3) 生成签名key
            bucket_key = MCTSUtils.create_result_signature(res)
            
            # 检查执行结果是否失败
            failed_item = None
            if not res.get('valid', False):
                # 执行失败：收集失败信息
                error_msg = res.get('error', '执行失败或超时')
                failed_item = {'cte': cte_used, 'error': error_msg}
            
            # 空结果/失败/超时处理：
            # - 允许空结果继续扩展，以便在下一层使用模糊匹配（Levenshtein/pg_trgm）
            # - 只有执行失败/超时才过滤掉
            if bucket_key == "empty_result":
                # 空结果允许继续，标记为特殊bucket以便后续处理
                bucket_key = "empty_result"
            elif bucket_key.startswith("invalid_"):
                # 执行失败/超时：返回失败信息
                if not failed_item:
                    error_msg = res.get('error', '执行失败或超时') if not res.get('valid', False) else "执行失败或超时"
                    failed_item = {'cte': cte_used, 'error': error_msg}
                return cte_used, res, None, failed_item, exec_sql
            
            return cte_used, res, bucket_key, failed_item, exec_sql
        
        # 并行执行所有非 <END> CTE
        _exec_t0 = _time_for_timing.time()
        failed_info = []  # 收集失败信息
        exec_sql_map = {}  # 存储每个CTE对应的可执行SQL，用于关系检查
        # 统计信息
        total_executed = 0
        empty_result_count = 0
        invalid_count = 0
        valid_count = 0
        
        # 限制CTE执行的并行数，避免过多并发导致数据库连接竞争
        # 即使外层有多个问题并行处理，每个问题内部的CTE执行也应该限制在较小值
        cte_exec_max_workers = min(self.max_workers, 3)  # 最多3个CTE并行执行，避免数据库连接竞争
        
        with ThreadPoolExecutor(max_workers=cte_exec_max_workers) as executor:
            futures = [executor.submit(worker, c) for c in non_end_ctes]
            for fut in as_completed(futures):
                total_executed += 1
                try:
                    # 为future.result()添加超时，避免单个CTE卡住导致整个流程卡住
                    # 超时时间 = CTE探针超时时间 + 5秒缓冲（减少缓冲时间，更快失败）
                    future_timeout = (self.cte_probe_timeout_s + 5.0) if self.cte_probe_timeout_s is not None else None
                    if future_timeout:
                        cte_used, cte_result, bucket_key, failed_item, exec_sql = fut.result(timeout=future_timeout)
                    else:
                        cte_used, cte_result, bucket_key, failed_item, exec_sql = fut.result()
                except FutureTimeoutError:
                    # 如果future本身超时，记录错误
                    error_msg = f"CTE执行future超时（>{future_timeout:.1f}秒）" if future_timeout else "CTE执行future超时"
                    # 创建一个失败的CTE结果
                    cte_used = "unknown"
                    cte_result = {'valid': False, 'error': error_msg}
                    bucket_key = None
                    failed_item = {'cte': cte_used, 'error': error_msg}
                    exec_sql = ""
                except Exception as e:
                    # 捕获其他异常（如线程异常等）
                    error_msg = f"CTE执行异常: {str(e)}"
                    cte_used = "unknown"
                    cte_result = {'valid': False, 'error': error_msg}
                    bucket_key = None
                    failed_item = {'cte': cte_used, 'error': error_msg}
                    exec_sql = ""
                # 保存可执行SQL
                exec_sql_map[cte_used] = exec_sql
                # 收集所有失败信息（不去重，每个失败都保存）
                if failed_item:
                    error = failed_item.get('error', '').strip()
                    if not error:
                        failed_item['error'] = '未知错误'
                    
                    # 使用CTEErrorHandler处理所有错误类型，添加修复提示
                    root_node = node
                    while root_node.parent is not None:
                        root_node = root_node.parent
                    failed_item = CTEErrorHandler.process_error(failed_item, node, root_node)
                    failed_info.append(failed_item)
                
                # 统计bucket_key类型
                if bucket_key is None:
                    invalid_count += 1
                    continue
                elif bucket_key == "empty_result":
                    empty_result_count += 1
                else:
                    valid_count += 1
                
                if bucket_key not in buckets:
                    buckets[bucket_key] = {
                        'cte': cte_used,
                        'execution_result': cte_result,
                        'count': 1,
                        'variants': [cte_used]
                    }
                else:
                    buckets[bucket_key]['count'] += 1
                    buckets[bucket_key]['variants'].append(cte_used)
                    if len(cte_used) < len(buckets[bucket_key]['cte']):
                        buckets[bucket_key]['cte'] = cte_used
                        buckets[bucket_key]['execution_result'] = cte_result
        
        # 打印执行统计
        if total_executed > 0:
            print(f"[执行统计] 共执行 {total_executed} 个CTE: 有效 {valid_count}, 空结果 {empty_result_count}, 失败 {invalid_count}")
        
        # 记录探针 SQL 执行耗时（视为 DB 执行）
        exec_elapsed = _time_for_timing.time() - _exec_t0
        if self.timing_dict is not None:
            self.timing_dict['db_exec_s'] = self.timing_dict.get('db_exec_s', 0.0) + exec_elapsed
        
        # 统计桶的类型
        total_buckets = len(buckets)
        end_buckets = 1 if "<END>" in buckets else 0
        empty_result_buckets = sum(1 for k in buckets.keys() if k == "empty_result")
        invalid_buckets = sum(1 for k in buckets.keys() if k.startswith("invalid_"))
        valid_buckets = total_buckets - end_buckets - empty_result_buckets - invalid_buckets
        
        # 总是打印统计信息（即使buckets为空，也要显示原因）
        print(f"[去重统计] 总桶数: {total_buckets} (有效: {valid_buckets}, 空结果: {empty_result_buckets}, 失败: {invalid_buckets}, <END>: {end_buckets})")
        if total_buckets == 0:
            print(f"[去重统计] ⚠️ 所有CTE都执行失败或被过滤，没有可用的CTE桶")
        
        overall_cost = _time_for_timing.time() - overall_start
        return list(buckets.values()), failed_info

    def _enhance_execution_result_with_stats(self, original_sql: str, original_result: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
        """
        使用额外的统计查询增强执行结果，提供更多上下文信息

        Args:
            original_sql: 原始SQL查询
            original_result: 原始执行结果
            timeout_s: 超时时间

        Returns:
            增强后的执行结果
        """
        enhanced_result = original_result.copy()

        # 如果原始查询无效，直接返回
        if not original_result.get('valid', False):
            return enhanced_result

        # 解析原始SQL，提取表名（简化版本）
        # 这里只处理简单的SELECT FROM table_name的情况
        import re
        table_match = re.search(r'\bFROM\s+([`\w]+)', original_sql, re.IGNORECASE)
        if not table_match:
            return enhanced_result

        table_name = table_match.group(1).strip('`')

        # 执行统计查询
        stats_info = {}

        try:
            # 查询1: COUNT(*) - 获取总行数
            count_sql = f"SELECT COUNT(*) as total_rows FROM {table_name}"
            count_result = self.sql_executor._execute_single_query(count_sql, timeout_s=min(timeout_s, 10))
            if count_result.get('valid', False) and count_result.get('query_result'):
                total_rows = count_result['query_result'][0].get('total_rows', 0)
                stats_info['total_rows'] = total_rows

                # 根据表大小决定采样策略
                if total_rows <= 10000:
                    # 小表：精确计算
                    distinct_sql = f"SELECT COUNT(*) as distinct_rows FROM (SELECT DISTINCT * FROM {table_name})"
                    distinct_timeout = min(timeout_s * 0.3, 15)
                else:
                    # 大表：采样估计 (使用更大的样本)
                    sample_size = min(total_rows // 100, 5000)  # 1%样本，最多5000行
                    distinct_sql = f"SELECT COUNT(*) as distinct_rows FROM (SELECT DISTINCT * FROM {table_name} LIMIT {sample_size})"
                    distinct_timeout = min(timeout_s * 0.2, 10)

                distinct_result = self.sql_executor._execute_single_query(distinct_sql, timeout_s=distinct_timeout)
                if distinct_result.get('valid', False) and distinct_result.get('query_result'):
                    distinct_count = distinct_result['query_result'][0].get('distinct_rows', 0)
                    if total_rows <= 10000:
                        stats_info['distinct_rows'] = distinct_count
                        stats_info['duplicate_ratio'] = round(1.0 - (distinct_count / total_rows) if total_rows > 0 else 0, 3)
                    else:
                        # 估计去重比例
                        estimated_distinct = distinct_count * (total_rows / sample_size)
                        stats_info['distinct_rows_estimated'] = int(estimated_distinct)
                        stats_info['duplicate_ratio_estimated'] = round(1.0 - (distinct_count / sample_size) if sample_size > 0 else 0, 3)

            # 查询3: 列统计信息 (只对前几列)
            if original_result.get('query_result') and len(original_result['query_result']) > 0:
                sample_row = original_result['query_result'][0]
                column_stats = {}

                for col_name in list(sample_row.keys())[:5]:  # 只统计前5列避免过多查询
                    try:
                        # NULL值统计
                        null_sql = f"SELECT COUNT(*) as null_count FROM {table_name} WHERE `{col_name}` IS NULL"
                        null_result = self.sql_executor._execute_single_query(null_sql, timeout_s=min(timeout_s * 0.1, 5))
                        if null_result.get('valid', False) and null_result.get('query_result'):
                            null_count = null_result['query_result'][0].get('null_count', 0)
                            total_rows = stats_info.get('total_rows', 0)
                            if total_rows > 0:
                                null_ratio = null_count / total_rows
                                col_info = {
                                    'null_ratio': round(null_ratio, 3),
                                    'has_nulls': null_count > 0
                                }

                                # 根据数据类型添加额外统计
                                sample_value = sample_row.get(col_name)
                                if sample_value is not None:
                                    if isinstance(sample_value, (int, float)):
                                        # 数值列：添加范围统计
                                        range_sql = f"SELECT MIN(`{col_name}`) as min_val, MAX(`{col_name}`) as max_val FROM {table_name} WHERE `{col_name}` IS NOT NULL"
                                        range_result = self.sql_executor._execute_single_query(range_sql, timeout_s=min(timeout_s * 0.1, 3))
                                        if range_result.get('valid', False) and range_result.get('query_result'):
                                            range_data = range_result['query_result'][0]
                                            col_info['range'] = {
                                                'min': range_data.get('min_val'),
                                                'max': range_data.get('max_val')
                                            }
                                    elif isinstance(sample_value, str):
                                        # 字符串列：添加长度统计和常见值
                                        length_sql = f"SELECT AVG(LENGTH(`{col_name}`)) as avg_length, COUNT(DISTINCT `{col_name}`) as unique_count FROM {table_name} WHERE `{col_name}` IS NOT NULL"
                                        length_result = self.sql_executor._execute_single_query(length_sql, timeout_s=min(timeout_s * 0.1, 3))
                                        if length_result.get('valid', False) and length_result.get('query_result'):
                                            length_data = length_result['query_result'][0]
                                            col_info['string_stats'] = {
                                                'avg_length': round(length_data.get('avg_length', 0), 1),
                                                'unique_count': length_data.get('unique_count', 0)
                                            }

                                        # 获取最常见的值 (如果唯一值不多)
                                        if col_info.get('string_stats', {}).get('unique_count', 1000) <= 20:
                                            common_sql = f"SELECT `{col_name}`, COUNT(*) as freq FROM {table_name} WHERE `{col_name}` IS NOT NULL GROUP BY `{col_name}` ORDER BY freq DESC LIMIT 5"
                                            common_result = self.sql_executor._execute_single_query(common_sql, timeout_s=min(timeout_s * 0.1, 3))
                                            if common_result.get('valid', False) and common_result.get('query_result'):
                                                common_values = []
                                                for row in common_result['query_result']:
                                                    common_values.append({
                                                        'value': row.get(col_name),
                                                        'count': row.get('freq', 0)
                                                    })
                                                col_info['common_values'] = common_values

                                        # 检测字符串模式 (基于样本数据)
                                        if original_result.get('query_result') and len(original_result['query_result']) > 0:
                                            sample_values = [str(row.get(col_name, '')) for row in original_result['query_result'] if row.get(col_name) is not None]
                                            if sample_values:
                                                patterns = self._detect_string_patterns(sample_values[:10])  # 只分析前10个样本
                                                if patterns:
                                                    col_info['string_stats']['patterns'] = patterns

                                column_stats[col_name] = col_info
                    except:
                        pass

                if column_stats:
                    stats_info['column_stats'] = column_stats

        except Exception as e:
            # 统计查询失败，不影响原始结果
            pass

        # 将统计信息添加到结果中
        if stats_info:
            enhanced_result['stats_info'] = stats_info

        return enhanced_result

    def collect_stats_for_node(self, cte_sql: str, execution_result: Dict[str, Any], timeout_s: float = None) -> Dict[str, Any]:
        """
        为MCTS节点收集统计信息（在节点创建时调用）

        Args:
            cte_sql: CTE SQL语句
            execution_result: CTE执行结果
            timeout_s: 超时时间

        Returns:
            包含统计信息的增强执行结果
        """
        if timeout_s is None:
            timeout_s = self.cte_probe_timeout_s

        # 如果执行结果无效或为空，直接返回原结果
        if not execution_result.get('valid', False) or not execution_result.get('query_result'):
            return execution_result

        try:
            # 调用现有的统计信息收集方法
            enhanced_result = self._enhance_execution_result_with_stats(cte_sql, execution_result, timeout_s)
            return enhanced_result
        except Exception as e:
            print(f"⚠️ 为节点收集统计信息失败: {e}")
            return execution_result

    def _detect_string_patterns(self, sample_values):
        """检测字符串值的模式"""
        if not sample_values:
            return None

        patterns = {}

        # 检查是否都是数字
        all_numeric = all(v.replace('.', '').replace('-', '').isdigit() for v in sample_values if v)
        if all_numeric:
            patterns['numeric_only'] = True

        # 检查是否都是字母
        all_alpha = all(v.replace(' ', '').isalpha() for v in sample_values if v)
        if all_alpha:
            patterns['alpha_only'] = True

        # 检查字母数字混合
        has_alphanumeric = any(any(c.isdigit() for c in v) and any(c.isalpha() for c in v) for v in sample_values if v)
        if has_alphanumeric and not all_numeric and not all_alpha:
            patterns['alphanumeric'] = True

        # 检查是否有连字符
        has_dash = any('-' in v for v in sample_values)
        if has_dash:
            patterns['has_dash'] = True

        # 检测固定格式 (如 CDSCode 的模式)
        if len(sample_values) >= 3:
            # 检查长度是否相同
            lengths = [len(v) for v in sample_values]
            if len(set(lengths)) == 1:  # 所有长度相同
                fixed_length = lengths[0]

                # 检查前缀模式
                prefixes = [v[:min(8, len(v))] for v in sample_values if len(v) >= 8]
                if len(set(prefixes)) == 1:  # 所有有相同前缀
                    patterns['fixed_format'] = True
                    patterns['format_example'] = f"{prefixes[0]}... ({fixed_length} chars)"

                # 检查数字分段模式 (如 01-100170-109835)
                first_val = sample_values[0]
                if len(first_val) > 10 and '-' in first_val:
                    segments = first_val.split('-')
                    if len(segments) >= 3 and all(seg.isdigit() for seg in segments):
                        patterns['segmented_numeric'] = True
                        patterns['segment_count'] = len(segments)

        return patterns if patterns else None

