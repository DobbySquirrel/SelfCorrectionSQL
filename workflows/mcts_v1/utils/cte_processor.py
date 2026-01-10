"""
CTE处理器

提取CTE去重和分桶的通用逻辑
"""

import re
from typing import Dict, List, Any, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from .mcts_helpers import MCTSUtils
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
            # 检查是否已经有LIMIT（包括LIMIT 5、LIMIT 10等）
            has_limit = re.search(r'\bLIMIT\s+\d+', exec_sql, re.IGNORECASE) is not None
            # 如果没有LIMIT，添加LIMIT用于探针执行（使用cte_probe_limit，与prompt中的建议一致）
            if exec_sql and not has_limit:
                # 在最后的SELECT语句前添加LIMIT
                if 'SELECT * FROM' in exec_sql.upper():
                    exec_sql = re.sub(r'(SELECT \* FROM[^;]+)(;?)$', rf'\1 LIMIT {self.cte_probe_limit}\2', exec_sql, flags=re.IGNORECASE | re.DOTALL)
            # 2) 直接执行（不再进行自动修复）
            # 使用较短的超时时间进行探针执行（快速检测）
            res = self.sql_executor._execute_single_query(exec_sql, timeout_s=self.cte_probe_timeout_s)
            cte_used = one_cte
            # 3) 生成签名key
            bucket_key = MCTSUtils.create_result_signature(res)
            # 空结果/失败/超时处理：
            # - 允许空结果继续扩展，以便在下一层使用模糊匹配（Levenshtein/pg_trgm）
            # - 只有执行失败/超时才过滤掉
            if bucket_key == "empty_result":
                # 空结果允许继续，标记为特殊bucket以便后续处理
                bucket_key = "empty_result"
            elif bucket_key.startswith("invalid_"):
                # 执行失败/超时：返回失败信息
                error_msg = ""
                if not res.get('valid', False):
                    error_msg = res.get('error', '执行失败或超时')
                else:
                    error_msg = "执行失败或超时"
                # 打印简化的错误信息
                print(f"[CTE执行失败] 错误: {error_msg}")
                return cte_used, res, None, {'cte': cte_used, 'error': error_msg}, exec_sql
            return cte_used, res, bucket_key, None, exec_sql
        
        # 并行执行所有非 <END> CTE
        _exec_t0 = _time_for_timing.time()
        failed_info = []  # 收集失败信息
        exec_sql_map = {}  # 存储每个CTE对应的可执行SQL，用于关系检查
        # 统计信息
        total_executed = 0
        empty_result_count = 0
        invalid_count = 0
        valid_count = 0
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(worker, c) for c in non_end_ctes]
            for fut in as_completed(futures):
                total_executed += 1
                cte_used, cte_result, bucket_key, failed_item, exec_sql = fut.result()
                # 保存可执行SQL
                exec_sql_map[cte_used] = exec_sql
                # 收集所有失败信息（进行去重）
                if failed_item:
                    # 基于错误信息去重：对于相同的错误信息，只保留一个代表性的CTE（最短的）
                    error = failed_item.get('error', '').strip()
                    cte = failed_item.get('cte', '').strip()
                    if not error:
                        error = '未知错误'
                    
                    # 检查是否已存在相同的错误信息
                    if error in {item.get('error', '').strip() for item in failed_info}:
                        # 如果已存在，比较CTE长度，保留较短的
                        for idx, existing_item in enumerate(failed_info):
                            if existing_item.get('error', '').strip() == error:
                                existing_cte = existing_item.get('cte', '').strip()
                                # 如果新的CTE更短，替换
                                if len(cte) < len(existing_cte):
                                    failed_info[idx] = failed_item
                                break
                    else:
                        # 如果错误信息不存在，直接添加
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

