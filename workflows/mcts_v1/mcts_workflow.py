"""
MCTS Workflow主控制器

实现MCTS算法的完整工作流：
1. 选择节点 (Node Selection)
2. 生成CTE (Common Table Expression)
3. 执行CTE和完整SQL
4. 统计执行结果
5. 回溯更新
6. 重复直到结束
7. 选择最优完整SQL
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import random
import numpy as np
from typing import Dict, List, Optional, Tuple, Any, Set
from pathlib import Path
from .core.mcts_tree import MCTSTree
from .core.mcts_node import MCTSNode
from .agents.cte_generator import CTEGenerator
from .agents.complete_sql_generator import CompleteSQLGenerator
from .agents.sql_executor import SQLExecutor
from .utils.mcts_helpers import MCTSUtils
from .utils.sql_exec_helpers import execute_sqls_parallel
from .agents.strategy import build_strategy_injection_text, GLOBAL_STRATEGY_CONFIG, StrategyMode, build_strategy_selection_prompt, extract_strategy_from_json
from core.database_connector import DatabaseConnector
from .utils.agent_helpers import AgentHelpers
import time as _time_for_timing

class MCTSWorkflow:
    """MCTS工作流主控制器"""
    
    def __init__(self, llm_config: Dict, db_connector: DatabaseConnector, max_workers: int = None, strategy_mode: Optional[str] = None):
        """
        初始化MCTS工作流
        
        Args:
            llm_config: LLM配置
            db_connector: 数据库连接器
            max_workers: 最大并行工作线程数（默认根据实际需求动态计算）
            strategy_mode: 策略模式（FORCE_S1/S2/S3/S4, NONE, LLM_PICK_ONCE），如果提供则覆盖全局配置
        """
        self.llm_config = llm_config
        self.db_connector = db_connector
        self.helpers = AgentHelpers()
        self.rollouts_per_iteration = 6  # 从6增加到10，让visit_count更好地反映节点质量
        self.exploration_constant = 1.414  # sqrt(2)
        # 基于分析结果优化：超过7层成功率显著下降（r=-0.329）
        self.max_depth = 8  # MCTS树最大深度（对于有CTE的节点，depth = CTE路径长度）
        self.max_cte_nodes_per_iteration = 5  # 每次扩展节点时生成的CTE变体数量
        # SQL变体数量配置：每个rollout末尾生成的SQL变体数量（用于计算sql_bucket_count）
        # 范围：5-8个，根据rollouts_per_iteration动态调整
        self.num_sql_variants = 5  # 每个rollout末尾生成的SQL变体数量
        
        # 基于分析结果的优化参数
        self.bucket_count_threshold = 4  # bucket_count>=4时成功率显著提高（成功案例平均4.43 vs 失败3.15）
        self.depth_penalty_start = 6  # 深度超过6层时开始应用惩罚

        # 从llm_config中提取multi_model_configs（如果存在config_list）
        multi_model_configs = None
        if 'config_list' in llm_config and len(llm_config['config_list']) > 1:
            # 如果有多个配置项，提取为multi_model_configs列表
            multi_model_configs = []
            for config in llm_config['config_list']:
                multi_model_configs.append({
                    'model': config.get('model', 'unknown'),
                    'base_url': config.get('base_url', ''),
                    'api_key': config.get('api_key', '')
                })
        
        # 初始化MCTS组件
        self.mcts_tree = MCTSTree()
        self.cte_generator = CTEGenerator(llm_config, max_depth=self.max_depth, multi_model_configs=multi_model_configs)
        self.complete_sql_generator = CompleteSQLGenerator(llm_config, multi_model_configs=multi_model_configs)
        self.sql_executor = SQLExecutor(db_connector)
        # 设置sql_executor的cte_generator（用于错误恢复）
        self.sql_executor.set_cte_generator(self.cte_generator)
        
        # 并行配置
        self.use_parallel = True  # 是否使用并行生成（CTE/SQL生成）
        # 根据实际并发需求动态计算max_workers：CTE最多5个，SQL最多8个，留一些余量
        if max_workers is None:
            # 实际最大并发需求：max(CTE变体数, SQL变体数) = max(5, 8) = 8
            # 留一些余量，设为10
            self.max_workers = 10
        else:
            self.max_workers = max_workers
        
        # 统一 SQL 超时配置（秒）
        self.sql_timeout_s = 40
        self.cte_probe_timeout_s = 10  # CTE探针执行超时（较短，用于快速检测）
        self.root_dirichlet_alpha = 0.3  # Dirichlet 分布的 alpha 参数（越小噪声越大）
        self.root_noise_weight = 0.1  # 噪声权重（与 UCB 混合）
        # 分阶段计时统计
        self._timing = {
            'total_s': 0.0,
            'rollout_s': 0.0,
            'cte_gen_s': 0.0,
            'sql_gen_s': 0.0,
            'db_exec_s': 0.0,
            'rollout_count': 0,
        }
        
        # 策略模式配置
        if strategy_mode:
            self.strategy_mode: StrategyMode = strategy_mode  # type: ignore
        else:
            self.strategy_mode: StrategyMode = GLOBAL_STRATEGY_CONFIG.mode  # type: ignore
        
    def solve(self, question: str, schema_info: str, additional_context: str = "", 
              original_schema: Optional[str] = None, _retry: bool = False) -> Dict[str, Any]:
        """
        Args:
            question: 自然语言问题
            schema_info: 数据库模式信息（可能是经过静态剪枝后的简化schema）
            additional_context: 额外上下文
            original_schema: 原始完整schema（用于动态扩充，如果为None则使用schema_info）
            
        Returns:
            包含最优SQL和统计信息的字典
        """
        # 每次 solve 调用时重置节点全局计数器，使新的问题节点编号从0开始
        MCTSNode._global_node_counter = 0
        # 重置计时统计
        for k in list(self._timing.keys()):
            self._timing[k] = 0.0 if k.endswith('_s') else 0
        
        # 初始化根节点
        root_node = MCTSNode(
            question=question,
            schema_info=schema_info,
            additional_context=additional_context,
            parent=None
        )
        # 在根节点存储 picked_strategy（用于 LLM_PICK_ONCE 模式）
        root_node.picked_strategy = None
        self.mcts_tree.set_root(root_node)
        
        # MCTS主循环：执行多个rollout
        solve_start_ts = _time_for_timing.time()
        
        # 快速路径：先执行一条深度为1的rollout（直接生成完整SQL，不经过CTE）
        # 注意：即使快速路径奖励为1，也至少执行一次CTE rollout来验证结果
        print("\n[快速路径] 执行深度为1的快速rollout（直接生成完整SQL，跳过CTE）...")
        quick_reward, quick_sql, quick_stats = self._quick_path_rollout(root_node)
        quick_stats['rollout_id'] = 0  # 标记为快速路径
        quick_stats['is_quick_path'] = True
        
        # 打印快速路径结果，但不直接返回（即使奖励为1.0，也要执行至少一次CTE rollout验证）
        if quick_reward >= 1.0:
            print(f"[快速路径] ✅ 快速路径奖励为1.0 (reward={quick_reward:.4f})")
            print(f"[快速路径] 💡 将执行至少一次CTE rollout来验证结果（防止一致但错误的答案）")
        else:
            print(f"[快速路径] ⚠️ 快速路径奖励为 {quick_reward:.4f} < 1.0，继续执行完整MCTS流程")
        
        # 从根到叶的完整探索过程
        # 包含多个rollout来探索和评估不同的路径
        
        # 串行执行 rollout，收集每个rollout的统计信息
        rollout_stats_list = [quick_stats]  # 包含快速路径的统计信息
        high_reward_threshold = 1  # 高奖励阈值，达到此值可提前终止
        
        # 获取evidence信息（从additional_context或question中提取）
        evidence = additional_context if additional_context else ""
        
        for rollout in range(self.rollouts_per_iteration):
            print(f"\n--- Rollout [{rollout + 1}/{self.rollouts_per_iteration}] ---")
            
            reward, selected_sql, rollout_stats = self._execute_mcts_rollout()
            rollout_stats['rollout_id'] = rollout + 1
            rollout_stats['is_quick_path'] = False
            rollout_stats_list.append(rollout_stats)
            
            # 提前终止策略
            # 条件1: 如果quick_rollout奖励为1，且CTE rollout_1的结果与quick_rollout一致，则选择quick_rollout结果
            if rollout == 0 and quick_reward >= 1.0:  # 第一个CTE rollout完成后检查
                print(f"\n[提前终止检查] quick_rollout奖励为1.0，检查与rollout_1的结果一致性...")
                # 比较quick_rollout和rollout_1的执行结果签名
                quick_best_sig = self._get_best_result_signature(quick_stats)
                rollout_1_best_sig = self._get_best_result_signature(rollout_stats)
                
                print(f"[提前终止检查] quick_rollout最佳签名: {quick_best_sig[:50] if quick_best_sig else 'None'}...")
                print(f"[提前终止检查] rollout_1最佳签名:    {rollout_1_best_sig[:50] if rollout_1_best_sig else 'None'}...")
                
                if quick_best_sig and rollout_1_best_sig and quick_best_sig == rollout_1_best_sig:
                    print(f"[提前终止] ✅ 条件1满足：quick_rollout奖励为1.0且与rollout_1结果一致")
                    print(f"[提前终止]    quick_rollout奖励: {quick_reward:.4f}")
                    print(f"[提前终止]    rollout_1奖励: {reward:.4f}")
                    print(f"[提前终止]    选择quick_rollout的结果（更简洁，无CTE），提前终止")
                    self._timing['total_s'] = max(0.0, _time_for_timing.time() - solve_start_ts)
                    return {
                        'optimal_sql': quick_sql if quick_sql else "",
                        'statistics': self._get_final_statistics(),
                        'tree_info': self.mcts_tree.get_tree_info(),
                        'rollout_stats': rollout_stats_list,
                        'used_quick_path': True,  # 标记使用了快速路径
                        'early_termination': 'quick_path_consistent'  # 标记提前终止原因
                    }
                else:
                    print(f"[提前终止检查] ❌ 结果签名不一致或有空值，继续执行后续rollout")
            
            # 条件2: 如果CTE rollout奖励为1，提前终止
            if reward >= high_reward_threshold:
                print(f"[提前终止] ✅ 条件2满足：CTE rollout奖励为1.0 (reward={reward:.4f})")
                print(f"[提前终止]    提前终止剩余rollout")
                break


        # 使用奖励优先策略选择最佳 SQL（优先选择最高奖励的rollout的SQL）
        print("\n[Strategy] 使用奖励优先策略选择最佳 SQL（优先选择最高奖励的rollout）")
        optimal_sql = self._select_sql_by_robust_path(rollout_stats_list=rollout_stats_list)
        
        if not optimal_sql:
            print("[Strategy] ⚠️ 混合策略返回空")

        self._timing['total_s'] = max(0.0, _time_for_timing.time() - solve_start_ts)
        return {
            'optimal_sql': optimal_sql,
            'statistics': self._get_final_statistics(),
            'tree_info': self.mcts_tree.get_tree_info(),
            'rollout_stats': rollout_stats_list,  # 每个rollout的详细统计信息
            'used_quick_path': False  # 标记未使用快速路径（因为继续执行了完整MCTS）
        }
    
    def _select_node(self) -> MCTSNode:
        """选择节点（UCB1算法）"""
        return self.mcts_tree.select_node(self.exploration_constant)
    
    def _get_best_result_signature(self, rollout_stats: Dict[str, Any]) -> Optional[str]:
        """
        从rollout统计信息中提取最佳结果签名（出现次数最多的签名）
        
        Args:
            rollout_stats: rollout的统计信息字典
            
        Returns:
            最佳结果签名，如果没有有效结果则返回None
        """
        result_buckets = rollout_stats.get('result_buckets', {})
        if not result_buckets:
            return None
        
        # 找到出现次数最多的签名
        best_signature = max(result_buckets.keys(), key=lambda k: result_buckets[k])
        return best_signature
    
    def _quick_path_rollout(self, root_node: MCTSNode) -> Tuple[float, Optional[str], Dict[str, Any]]:
        """
        快速路径rollout：直接基于根节点生成完整SQL（不经过CTE），用于处理简单SQL
        
        Args:
            root_node: 根节点
            
        Returns:
            (奖励值, 选择的SQL, 统计信息字典)
        """
        print(f"[快速路径] 开始快速路径rollout（深度=1，直接生成完整SQL）...")
        
        # 直接基于根节点生成多个SQL变体（不经过CTE链）
        _sqlgen_t0 = _time_for_timing.time()
        sql_variants = self.complete_sql_generator.generate_multiple_complete_sqls_parallel(
            root_node,
            num_variants=self.num_sql_variants,
            max_workers=self.max_workers
        )
        self._timing['sql_gen_s'] += (_time_for_timing.time() - _sqlgen_t0)
        
        if not sql_variants:
            print(f"[快速路径] ⚠️ 未生成任何SQL变体")
            return 0.0, None, {
                'sql_bucket_count': 0, 
                'sql_total_variants': 0,
                'all_sql_variants': [],
                'result_buckets': {},
                'valid_count': 0,
                'error_reason': 'SQL生成失败：未生成任何SQL变体',
                'cte_path': [],  # 快速路径没有CTE
                'cte_bucket_counts': [],
                'cte_depths': [],
                'visit_counts': [],
                'cte_buckets_per_node': [],
                'leaf_depth': 0,
                'leaf_visit_count': 0
            }
        
        print(f"[快速路径] 生成了 {len(sql_variants)} 个SQL变体")
        print(f"[快速路径] 正在并行执行 {len(sql_variants)} 个SQL（超时={self.sql_timeout_s}s，最大并行数={self.max_workers}）...")
        
        # 并行执行所有SQL并收集结果
        execution_results = []
        exec_start = _time_for_timing.time()
        parallel_results = execute_sqls_parallel(self.db_connector, sql_variants, timeout_s=self.sql_timeout_s, max_workers=self.max_workers)
        exec_elapsed = _time_for_timing.time() - exec_start
        # 记录数据库执行耗时
        self._timing['db_exec_s'] += exec_elapsed
        
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
            print(f"[快速路径] ⚠️ 警告：{timeout_count}/{len(sql_variants)} 个SQL执行超时（总耗时 {exec_elapsed:.2f}s）")
        else:
            print(f"[快速路径] SQL执行完成（总耗时 {exec_elapsed:.2f}s）")
        
        # 计算一致性奖励与分桶
        result_buckets, best_key = MCTSUtils.bucketize_valid_nonempty(execution_results)
        valid_count = sum(1 for r in execution_results if r.get('valid', False))
        # 获取SQL桶数量（最大桶的计数）
        sql_bucket_count = max(result_buckets.values()) if result_buckets else 0
        
        if valid_count == 0:
            # 所有SQL执行失败
            all_sql_variants = []
            for idx, (sql, res) in enumerate(zip(sql_variants, execution_results)):
                sql_info = {
                    'sql': sql,
                    'valid': False,
                    'error': res.get('error', 'unknown error') if res else 'unknown error',
                    'result_signature': None,
                    'result_row_count': 0
                }
                all_sql_variants.append(sql_info)
            
            return 0.0, None, {
                'sql_bucket_count': 0, 
                'sql_total_variants': len(sql_variants),
                'all_sql_variants': all_sql_variants,
                'result_buckets': {},
                'valid_count': 0,
                'error_reason': f'所有SQL执行失败：{len(sql_variants)}个SQL变体全部执行失败',
                'cte_path': [],
                'cte_bucket_counts': [],
                'cte_depths': [],
                'visit_counts': [],
                'cte_buckets_per_node': [],
                'leaf_depth': 0,
                'leaf_visit_count': 0
            }
        
        # 建立sql与签名的映射
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
                    key = MCTSUtils.create_result_signature(res)
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
        
        # 处理平票情况
        if result_buckets:
            max_count = max(result_buckets.values())
            tied_keys = [k for k, v in result_buckets.items() if v == max_count]
            
            if len(tied_keys) > 1:
                def get_tiebreak_score(sig: str) -> Tuple[int, int, int]:
                    """返回(行数, 列数, SQL长度)，越小越好"""
                    res = signature_to_result.get(sig, [])
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
        
        # 计算最高一致性（最频繁结果/总变体）
        max_consistency = MCTSUtils.calculate_consistency_reward(result_buckets, len(sql_variants))
        
        # 自一致性奖励：最高一致性比例
        reward = max_consistency
        
        # 如果最佳结果签名为单个0，则降低奖励（惩罚）
        if result_buckets and best_key:
            best_result = None
            for res in execution_results:
                if res.get('valid', False):
                    if MCTSUtils.create_result_signature(res) == best_key:
                        best_result = res.get('query_result', None)
                        break
            
            if best_result is not None and self._is_single_zero_result(best_result):
                original_reward = reward
                reward = reward * 0.5
                print(f"[快速路径] ⚠️ 警告：最佳结果为单个0，降低奖励 {original_reward:.4f} → {reward:.4f} (惩罚50%)")
        
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
                        if self._is_single_zero_result(query_result):
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
                                print(f"[快速路径] ⚠️ 警告：从桶中选择列顺序出现次数最多的SQL，但结果为单个0")
            
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
            if selected_sql is None and len(sql_variants) > 0:
                selected_sql = sql_variants[0]
        
        # 计算最频繁结果的出现次数
        max_bucket_count = max(result_buckets.values()) if result_buckets else 0
        print(f"[快速路径] 奖励: {reward:.4f} (一致性: {max_bucket_count}/{len(sql_variants)}, 通过率: {valid_count}/{len(sql_variants)})")
        
        # 打印一致性最高的数据库执行结果
        best_result = None
        if result_buckets and best_key:
            # 从本次 execution_results 里找与 best_key 匹配的那条结果
            for res in execution_results:
                if res.get('valid', False):
                    if MCTSUtils.create_result_signature(res) == best_key:
                        best_result = res.get('query_result', None)
                        break

        if best_result is not None:
            print("[快速路径] 一致性最高的数据库执行结果:")
            print(best_result)
        
        # 构建所有SQL变体的详细信息
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
                    sql_info['result_signature'] = MCTSUtils.create_result_signature(res)
                    sql_info['result_row_count'] = len(query_result)
            all_sql_variants.append(sql_info)
        
        sql_stats = {
            'sql_bucket_count': sql_bucket_count,
            'sql_total_variants': len(sql_variants),
            'all_sql_variants': all_sql_variants,
            'result_buckets': dict(result_buckets) if result_buckets else {},
            'valid_count': valid_count,
            'error_reason': None
        }
        
        
        # 构建统计信息（快速路径没有CTE，所以相关字段为空）
        rollout_stats = {
            'reward': reward,
            'cte_path': [],  # 快速路径没有CTE
            'cte_bucket_counts': [],
            'cte_depths': [],
            'visit_counts': [],
            'cte_buckets_per_node': [],
            'leaf_depth': 0,  # 根节点深度为0
            'leaf_visit_count': 0,
            'sql_bucket_count': sql_bucket_count,
            'sql_total_variants': len(sql_variants),
            'selected_sql': selected_sql,
            'all_sql_variants': all_sql_variants,
            'result_buckets': sql_stats.get('result_buckets', {}),
            'valid_count': valid_count,
            'error_reason': sql_stats.get('error_reason', None)
        }
        
        return reward, selected_sql, rollout_stats
    
    def _execute_mcts_rollout(self) -> Tuple[float, Optional[str], Dict[str, Any]]:
        """
        执行完整的MCTS rollout
        
        Returns:
            (奖励值, 选择的SQL, 统计信息字典)
        """
        
        rollout_start_ts = _time_for_timing.time()
        # 1. Selection: 从根节点开始，使用UCT选择最优子节点，直到叶节点
        path = self._mcts_selection()
        
        # 2. Expansion: 如果叶节点未展开，生成CTE子节点
        # 扩展：使用自一致性（桶计数）选择策略，逐步选择一个CTE前进到<END>
        leaf_node, added_nodes = self._mcts_expansion(path[-1])
        if added_nodes:
            path.extend(added_nodes)
        
        # 收集CTE路径信息
        cte_path = []
        cte_bucket_counts = []
        cte_depths = []
        visit_counts = []
        cte_buckets_per_node = []  # 每个节点生成的所有CTE桶信息（用于计算信息熵）
        
        for node in path:
            if node.cte and node.cte != "<END>":
                cte_path.append(node.cte)  # 保存完整的CTE，不截断
                bucket_count = node.execution_results.get('bucket_count', 0)
                cte_bucket_counts.append(bucket_count)
                cte_depths.append(node.depth)
                visit_counts.append(node.visit_count)
                
                # 收集该节点生成的所有CTE桶信息
                all_buckets = node.execution_results.get('all_cte_buckets', [])
                if all_buckets:
                    # 保存每个桶的CTE和桶数
                    node_buckets_info = []
                    for bucket_info in all_buckets:
                        node_buckets_info.append({
                            'cte': bucket_info.get('cte', ''),
                            'count': bucket_info.get('count', 0),
                            'result_signature': bucket_info.get('result_signature', '')
                        })
                    cte_buckets_per_node.append(node_buckets_info)
                else:
                    # 如果没有桶信息（可能是根节点或旧节点），记录空列表
                    cte_buckets_per_node.append([])
        
        # 3. Simulation: 对叶节点进行模拟（生成多个SQL并计算自一致性奖励）
        reward, selected_sql, sql_stats = self._mcts_simulation(leaf_node)
        
        # 4. Backpropagation: 将奖励回溯更新到路径上的所有节点
        self._mcts_backpropagation(path, reward)
        
        self._timing['rollout_s'] += (_time_for_timing.time() - rollout_start_ts)
        self._timing['rollout_count'] += 1
        
        # 构建统计信息
        rollout_stats = {
            'reward': reward,
            'cte_path': cte_path,  # 完整的CTE路径（不截断）
            'cte_bucket_counts': cte_bucket_counts,  # 路径上每个节点选中CTE的桶数
            'cte_depths': cte_depths,
            'visit_counts': visit_counts,
            'cte_buckets_per_node': cte_buckets_per_node,  # 每个节点生成的所有CTE桶信息（用于计算信息熵）
            # 格式：[[{'cte': str, 'count': int, 'result_signature': str}, ...], ...]
            # 每个元素对应路径上的一个节点，包含该节点生成的所有CTE桶
            'leaf_depth': leaf_node.depth,
            'leaf_visit_count': leaf_node.visit_count,  # 叶节点的访问次数（该节点被rollout访问的次数）
            'sql_bucket_count': sql_stats.get('sql_bucket_count', 0),  # 最大桶计数（有多少个SQL产生相同结果）
            'sql_total_variants': sql_stats.get('sql_total_variants', 0),  # 总共生成的SQL变体数量
            'selected_sql': selected_sql,  # 选择的SQL（对应最大桶的SQL）
            'all_sql_variants': sql_stats.get('all_sql_variants', []),  # 所有SQL变体及其执行结果
            'result_buckets': sql_stats.get('result_buckets', {}),  # SQL结果分桶信息
            'valid_count': sql_stats.get('valid_count', 0),  # 有效SQL数量
            'error_reason': sql_stats.get('error_reason', None)  # 如果sql_bucket_count为0，记录原因
        }
        
        return reward, selected_sql, rollout_stats
    
    def _mcts_selection(self) -> List[MCTSNode]:
        """
        MCTS Selection阶段：从根节点开始，使用UCT选择最优子节点，直到叶节点
        Returns:
            从根到叶的路径
        """
        path = [self.mcts_tree.root]
        current_node = self.mcts_tree.root
        
        # 使用UCT选择，直到到达叶节点
        while current_node.children and not current_node.is_terminal:
            # 只在"非终止子节点"中挑选；若全为终止节点，则停止选择
            non_terminal_children = [ch for ch in current_node.children if not ch.is_terminal]
            if not non_terminal_children:
                break
            
            best_child = max(
                non_terminal_children,
                key=lambda child: child.get_ucb1_value(self.exploration_constant)
            )
            
            current_node = best_child
            path.append(current_node)
        
        return path

    def _mcts_expansion(self, leaf_node: MCTSNode) -> Tuple[MCTSNode, List[MCTSNode]]:
        """
        MCTS Expansion阶段：如果叶节点未展开，生成CTE子节点。
        逐步选择一个CTE前进，直到遇到<END>或达到最大深度，形成一条更长的链。
        
        Args:
            leaf_node: 叶节点
            
        Returns:
            (最终叶节点, 扩展过程中新增的节点列表)
        """
        added_nodes: List[MCTSNode] = []
        current = leaf_node
        
        # 如果初始就是终端，直接返回
        if current.is_terminal:
            return current, added_nodes
        
        # 循环渐进扩展，直至<END>或达到深度限制
        # 如果连续三次模糊匹配都返回空结果，才会停止扩展（consecutive_empty_count >= 3）
        # 第二次空结果时允许继续扩展，但会提示检查其他列
        # 添加CTE路径长度限制：基于分析结果，路径越长成功率越低（r=-0.2933）
        while (not current.is_terminal) and (current.depth < self.max_depth) and \
              (getattr(current, 'consecutive_empty_count', 0) < 3):
            if current.is_expanded:
                if current.children:
                    # 所有孩子都参与 UCB 竞争，包括 terminal
                    next_child = max(
                        current.children,
                        key=lambda child: child.get_ucb1_value(self.exploration_constant)
                    )
                    added_nodes.append(next_child)
                    current = next_child

                    # 如果选出来的就是 terminal，就可以跳出扩展环节
                    if next_child.is_terminal:
                        break

                    # 否则继续 while 往下走
                    continue
                else:
                    # 这个节点标记成"已展开"，却没有children（可能之前生成CTE失败/全是非法）
                    current.is_expanded = False
                    # 再继续走到下面的"生成CTE逻辑"
                    continue
            
            # 检查节点是否已扩展（rollout是串行执行的，不需要并行检查）
            if current.is_expanded:
                # 已扩展，跳过
                continue
            
            # 处理 LLM_PICK_ONCE 模式的策略选择（仅在根节点且未选择策略时）
            # 先单独选择策略（JSON格式），然后再生成CTE
            if self.strategy_mode == "LLM_PICK_ONCE" and current.depth == 0:
                root_node = self.mcts_tree.root if self.mcts_tree else None
                if root_node and not getattr(root_node, 'picked_strategy', None):
                    # 单独调用LLM选择策略（JSON格式）
                    print(f"\n[策略选择] ========== 开始单独选择策略 ==========")
                    from .agents.strategy import build_strategy_selection_prompt, extract_strategy_from_json
                    
                    strategy_prompt = build_strategy_selection_prompt(
                        question=current.question,
                        schema_info=current.schema_info,
                        additional_context=current.additional_context
                    )
                    
                    print(f"[策略选择] Strategy Selection Prompt:")
                    print(f"{'='*80}")
                    print(strategy_prompt)
                    print(f"{'='*80}")
                    
                    # 调用LLM选择策略
                    strategy_messages = [
                        {
                            "role": "system",
                            "content": "You are a strategy selection assistant. Your task is to analyze the SQL generation task and select the most appropriate strategy."
                        },
                        {
                            "role": "user",
                            "content": strategy_prompt
                        }
                    ]
                    
                    # 使用CTE生成器的agent来调用（复用LLM配置）
                    strategy_response = self.cte_generator.cte_agent.generate_reply(strategy_messages)
                    
                    print(f"\n[策略选择] LLM响应:")
                    print(f"{'='*80}")
                    print(strategy_response)
                    print(f"{'='*80}")
                    
                    # 从JSON响应中提取策略
                    picked_strategy = extract_strategy_from_json(strategy_response)
                    
                    if picked_strategy and picked_strategy in ("S1", "S2", "S3", "S4"):
                        root_node.picked_strategy = picked_strategy
                        print(f"\n✅ [策略选择] 成功选择策略: {picked_strategy}")
                    else:
                        print(f"\n⚠️ [策略选择] 未能从JSON中提取到有效策略，使用默认策略 S2")
                        root_node.picked_strategy = "S2"
                        picked_strategy = "S2"
                    
                    print(f"[策略选择] ========== 策略选择完成 ==========\n")
            
            # 1) 生成多个CTE变体（计时：CTE 生成）
            # 注意：此时如果已选择策略，会在_generate_cte_variants中使用已选择的策略

            _cte_t0 = _time_for_timing.time()
            cte_variants = self._generate_cte_variants(current)
            self._timing['cte_gen_s'] += (_time_for_timing.time() - _cte_t0)
            
            # 注意：不再需要从CTE中提取策略，因为策略已经单独选择
            # 旧的策略提取逻辑已移除
            
            # 2) 去重并统计执行结果桶
            unique_cte_variants, failed_info = self._deduplicate_cte_variants(cte_variants, current)
            
            # 保存当前节点生成的所有CTE桶信息（用于后续计算信息熵）
            # 格式：每个桶包含 {'cte': str, 'count': int, 'result_signature': str}
            current_node_buckets = []
            for info in unique_cte_variants:
                cte_text = info.get('cte', '')
                count = info.get('count', 0)
                exec_res = info.get('execution_result', {})
                # 生成结果签名（用于标识桶）
                if exec_res:
                    result_signature = MCTSUtils.create_result_signature(exec_res)
                else:
                    result_signature = "<END>" if cte_text == "<END>" else "unknown"
                current_node_buckets.append({
                    'cte': cte_text,
                    'count': count,
                    'result_signature': result_signature
                })
            # 保存到节点的execution_results中
            if not hasattr(current, 'execution_results'):
                current.execution_results = {}
            current.execution_results['all_cte_buckets'] = current_node_buckets
            
            # 检测前序CTE是否返回空结果（用于判断是否提示了模糊匹配）
            parent_has_empty_result = False
            if current.parent and hasattr(current.parent, 'execution_results'):
                parent_exec_res = current.parent.execution_results.get('cte_result', {})
                if parent_exec_res.get('valid', False):
                    parent_query_result = parent_exec_res.get('query_result', [])
                    try:
                        parent_query_result = MCTSUtils.safe_to_dict(parent_query_result)
                    except Exception:
                        parent_query_result = []
                    if not isinstance(parent_query_result, list):
                        try:
                            parent_query_result = list(parent_query_result)
                        except Exception:
                            parent_query_result = []
                    parent_has_empty_result = (not parent_query_result or len(parent_query_result) == 0)
            
            all_cte_scores = []  # 记录所有CTE的评分（即使被剪枝）
            
            if not unique_cte_variants:
                # 无可用变体，检查是否已经重试过
                if not hasattr(current, '_cte_retry_count'):
                    current._cte_retry_count = 0
                    current._failed_cte_attempts = []  # 记录失败的CTE尝试（包含CTE和错误信息）
                
                # 检测前序CTE是否返回空结果（用于判断是否提示了模糊匹配）
                parent_has_empty_result = False
                if current.parent and hasattr(current.parent, 'execution_results'):
                    parent_exec_res = current.parent.execution_results.get('cte_result', {})
                    if parent_exec_res.get('valid', False):
                        parent_query_result = parent_exec_res.get('query_result', [])
                        try:
                            parent_query_result = MCTSUtils.safe_to_dict(parent_query_result)
                        except Exception:
                            parent_query_result = []
                        if not isinstance(parent_query_result, list):
                            try:
                                parent_query_result = list(parent_query_result)
                            except Exception:
                                parent_query_result = []
                        parent_has_empty_result = (not parent_query_result or len(parent_query_result) == 0)
                
                # 记录本次失败的CTE和执行错误（从去重函数返回的失败信息中获取）
                if failed_info:
                        # 基于错误信息去重：对于相同的错误信息，只保留一个代表性的CTE（最短的）
                        existing_errors = {
                            item.get('error', '').strip() 
                            for item in getattr(current, '_failed_cte_attempts', [])
                        }
                        deduplicated_failed_info = []  # 去重后的失败信息列表
                        for failed_item in failed_info:
                            error = failed_item.get('error', '').strip()
                            # 过滤掉无效的重复命名错误
                            if error.lower().find('duplicate with table name') != -1:
                                continue
                            if not error:
                                error = '未知错误'
                            cte = failed_item.get('cte', '').strip()
                            
                            # 基于错误信息去重：如果错误信息已存在，比较CTE长度，保留较短的
                            if error in existing_errors:
                                # 查找已存在的相同错误信息的项
                                for idx, existing_item in enumerate(current._failed_cte_attempts):
                                    if existing_item.get('error', '').strip() == error:
                                        existing_cte = existing_item.get('cte', '').strip()
                                        # 如果新的CTE更短，替换
                                        if len(cte) < len(existing_cte):
                                            current._failed_cte_attempts[idx] = failed_item
                                            # 更新deduplicated_failed_info中对应的项
                                            for d_idx, d_item in enumerate(deduplicated_failed_info):
                                                if d_item.get('error', '').strip() == error:
                                                    deduplicated_failed_info[d_idx] = failed_item
                                                    break   
                                        break
                            else:
                                # 如果错误信息不存在，直接添加
                                current._failed_cte_attempts.append(failed_item)
                                existing_errors.add(error)
                                deduplicated_failed_info.append(failed_item)
                        
                        # 处理重试后的失败信息（对重试后的失败信息也进行去重）
                        deduplicated_failed_info_retry = []  # 在外部定义，供后续使用
                        if failed_info:
                            # 对重试后的失败信息进行去重：基于错误信息去重，对于相同的错误信息，只保留一个代表性的CTE（最短的）
                            existing_errors_retry = {
                                item.get('error', '').strip() 
                                for item in getattr(current, '_failed_cte_attempts', [])
                            }
                            for failed_item in failed_info:
                                error = failed_item.get('error', '').strip()
                                if error.lower().find('duplicate with table name') != -1:
                                    continue
                                if not error:
                                    error = '未知错误'
                                cte = failed_item.get('cte', '').strip()
                                
                                # 基于错误信息去重：如果错误信息已存在，比较CTE长度，保留较短的
                                if error in existing_errors_retry:
                                    # 查找已存在的相同错误信息的项
                                    for idx, existing_item in enumerate(deduplicated_failed_info_retry):
                                        if existing_item.get('error', '').strip() == error:
                                            existing_cte = existing_item.get('cte', '').strip()
                                            # 如果新的CTE更短，替换
                                            if len(cte) < len(existing_cte):
                                                deduplicated_failed_info_retry[idx] = failed_item
                                            break
                                else:
                                    # 如果错误信息不存在，直接添加
                                    deduplicated_failed_info_retry.append(failed_item)
                                    existing_errors_retry.add(error)
                            # 将重试后的失败信息也添加到当前节点的_failed_cte_attempts中（如果还没有添加）
                            # 注意：重试后的失败信息应该已经在新的循环中通过第583-603行的逻辑被记录了
                            # 但为了保险起见，这里也添加一次（会去重）
                            if deduplicated_failed_info_retry:
                                existing_combinations_in_current = {
                                    (item.get('error', '').strip(), item.get('cte', '').strip()) 
                                    for item in getattr(current, '_failed_cte_attempts', [])
                                }
                                for failed_item in deduplicated_failed_info_retry:
                                    error = failed_item.get('error', '').strip()
                                    cte = failed_item.get('cte', '').strip()
                                    combination = (error, cte)
                                    if error and combination not in existing_combinations_in_current:
                                        current._failed_cte_attempts.append(failed_item)
                                        existing_combinations_in_current.add(combination)
                        # 创建失败节点，保存错误信息
                        with self.mcts_tree.lock:
                            # 再次检查是否已扩展（rollout是串行执行的，不需要并行检查）
                            if current.is_expanded:
                                continue
                            
                            # 按照错误信息分桶，为每种错误类型创建失败节点
                            # {错误信息: [失败项列表]}
                            error_buckets = {}
                            if failed_info:
                                for failed_item in failed_info:
                                    error_msg = failed_item.get('error', '未知错误').strip()
                                    # 过滤掉无效的重复命名错误
                                    if error_msg and error_msg.lower().find('duplicate with table name') != -1:
                                        continue
                                    # 规范化错误信息（用于分桶）
                                    if not error_msg:
                                        error_msg = '未知错误'
                                    if error_msg not in error_buckets:
                                        error_buckets[error_msg] = []
                                    error_buckets[error_msg].append(failed_item)
                            
                            # 如果没有失败信息，创建一个通用的失败节点
                            if not error_buckets:
                                error_buckets['所有CTE变体都执行失败'] = []
                            
                            # 为每种错误类型创建一个失败节点
                            # 使用根节点的最新schema_info，确保使用LLM选择的schema
                            root_schema = self.mcts_tree.root.schema_info if self.mcts_tree.root else current.schema_info
                            created_failed_nodes = []
                            for error_msg, failed_items in error_buckets.items():
                                failed_child = MCTSNode(
                                    question=current.question,
                                    schema_info=root_schema,  # 使用根节点的最新schema
                                    additional_context=current.additional_context,
                                    parent=current
                                )
                                failed_child.cte = ""  # 失败节点没有CTE
                                
                                # 构建详细的错误信息
                                if len(failed_items) > 0:
                                    detailed_error = f"{error_msg} (共{len(failed_items)}个CTE变体失败)"
                                else:
                                    detailed_error = error_msg
                                
                                failed_child.execution_results['cte_result'] = {
                                    'valid': False,
                                    'error': detailed_error
                                }
                                failed_child.execution_results['is_failed'] = True
                                failed_child.execution_results['error_type'] = error_msg  # 保存错误类型，用于区分
                                
                                # 保存失败尝试信息，供下一轮使用
                                failed_child._failed_cte_attempts = current._failed_cte_attempts.copy() if hasattr(current, '_failed_cte_attempts') else []
                                
                                # 将当前错误类型的失败信息添加到失败节点中（去重后的）
                                if failed_items:
                                    existing_combinations_in_failed_node = {
                                        (item.get('error', '').strip(), item.get('cte', '').strip()) 
                                        for item in failed_child._failed_cte_attempts
                                    }
                                    for failed_item in failed_items:
                                        error = failed_item.get('error', '').strip()
                                        cte = failed_item.get('cte', '').strip()
                                        combination = (error, cte)
                                        if error and combination not in existing_combinations_in_failed_node:
                                            failed_child._failed_cte_attempts.append(failed_item)
                                            existing_combinations_in_failed_node.add(combination)
                                
                                # 将重试后的失败信息也添加到失败节点中（如果错误类型匹配）
                                if deduplicated_failed_info_retry:
                                    for failed_item in deduplicated_failed_info_retry:
                                        retry_error = failed_item.get('error', '').strip()
                                        if retry_error == error_msg:
                                            cte = failed_item.get('cte', '').strip()
                                            combination = (retry_error, cte)
                                            if retry_error and combination not in existing_combinations_in_failed_node:
                                                failed_child._failed_cte_attempts.append(failed_item)
                                                existing_combinations_in_failed_node.add(combination)
                                
                                # 失败节点也增加深度（深度+1）
                                current.add_child(failed_child)
                                created_failed_nodes.append(failed_child)
                            
                            current.is_expanded = True
                        
                        # 选择第一个失败节点继续扩展（如果有多个失败节点，后续可以通过UCB选择不同的错误路径）
                        if created_failed_nodes:
                            failed_child = created_failed_nodes[0]  # 选择第一个失败节点
                            added_nodes.append(failed_child)
                            current = failed_child
                        # 继续while循环，尝试在失败节点上生成新的CTE
                        continue

            # 3) 仅为"有效且非空"的变体创建子节点；<END> 根据策略保留
            # 如果成功生成了CTE变体，重置重试计数和失败记录
            if hasattr(current, '_cte_retry_count'):
                current._cte_retry_count = 0
            if hasattr(current, '_failed_cte_attempts'):
                current._failed_cte_attempts = []  # 清空失败记录
            
            # 优化：将排序和评估移到锁外，减少锁持有时间
            created_map = {}  # cte文本 -> 子节点
            first_step = (len(added_nodes) == 0)
            children_to_create = []  # 准备创建的子节点信息 [(child, cte_text, info), ...]
            for info in unique_cte_variants:
                cte_text = info['cte']
                exec_res = info.get('execution_result')
                # 判定是否有效且非空
                is_valid_nonempty = False
                if exec_res and exec_res.get('valid', False):
                    qr = exec_res.get('query_result', [])
                    try:
                        qr = MCTSUtils.safe_to_dict(qr)
                    except Exception:
                        qr = []
                    if not isinstance(qr, list):
                        try:
                            qr = list(qr)
                        except Exception:
                            qr = []
                    is_valid_nonempty = bool(qr)

                # 在锁外创建节点对象（不添加到树中）
                # 使用根节点的最新schema_info，而不是当前节点的（确保使用LLM选择的schema）
                root_schema = self.mcts_tree.root.schema_info if self.mcts_tree.root else current.schema_info
                
                if cte_text == "<END>":
                    child = MCTSNode(
                        question=current.question,
                        schema_info=root_schema,  # 使用根节点的最新schema
                        additional_context=current.additional_context,
                        parent=None  # 暂时不设置parent，在锁内添加时再设置
                    )
                    child.cte = "<END>"
                    child.is_terminal = True
                    children_to_create.append((child, cte_text, info, None))
                elif is_valid_nonempty:
                    child = MCTSNode(
                        question=current.question,
                        schema_info=root_schema,  # 使用根节点的最新schema
                        additional_context=current.additional_context,
                        parent=None
                    )
                    child.cte = cte_text
                    child.execution_results['cte_result'] = exec_res
                    child.execution_results['bucket_count'] = info.get('count', 0)
                    child.execution_results['bucket_variants'] = info.get('variants', [])
                    children_to_create.append((child, cte_text, info, None))
                elif exec_res and exec_res.get('valid', False):
                    # 允许基于"有效但结果为空"的候选创建子节点
                    has_where = self._has_where_clause(cte_text)
                    
                    if not has_where:
                        # 没有WHERE子句，即使结果为空也不触发模糊匹配，直接跳过
                        continue
                    
                    # 有WHERE子句且结果为空，创建子节点
                    child = MCTSNode(
                        question=current.question,
                        schema_info=root_schema,  # 使用根节点的最新schema
                        additional_context=current.additional_context,
                        parent=None
                    )
                    child.cte = cte_text
                    child.execution_results['cte_result'] = exec_res
                    child.execution_results['bucket_count'] = info.get('count', 0)
                    child.execution_results['bucket_variants'] = info.get('variants', [])
                    child.execution_results['is_empty_result'] = True
                    
                    # 检查父节点是否也是空结果节点
                    parent_is_empty = False
                    if current.parent and hasattr(current.parent, 'execution_results'):
                        parent_is_empty = current.parent.execution_results.get('is_empty_result', False)
                    
                    # 跟踪连续空结果的次数
                    if parent_is_empty:
                        parent_consecutive_empty = getattr(current.parent, 'consecutive_empty_count', 0)
                        if parent_consecutive_empty >= 2:
                            child.consecutive_empty_count = 3
                            child.is_terminal = True
                        else:
                            child.consecutive_empty_count = 2
                    else:
                        child.consecutive_empty_count = 1
                    
                    children_to_create.append((child, cte_text, info, None))
                # else: 执行失败/超时：不创建子节点，直接过滤掉

            # 只在锁内批量添加子节点和更新状态（最小化锁持有时间）
            with self.mcts_tree.lock:
                # 再次检查是否已扩展（rollout是串行执行的，不需要并行检查）
                if current.is_expanded:
                    continue
                
                # 批量添加所有准备好的子节点
                for child, cte_text, info, _ in children_to_create:
                    child.parent = current  # 设置parent
                    current.add_child(child)  # 这会自动设置child.depth
                    created_map[cte_text] = child
                
                current.is_expanded = True

            # 4) 使用自一致性（桶计数）选择最佳子节点
            # 获取所有已创建的子节点
            created_children = list(created_map.values())
            
            if not created_children:
                # 没有创建任何子节点，停止扩展
                if first_step:
                    current.is_expanded = True
                break
            
            # 计算是否允许选择 <END>
            end_child = created_map.get('<END>')
            non_end_children = [ch for ch in created_children if ch.cte != '<END>']
            allow_choose_end = (end_child is not None and len(non_end_children) == 0) or \
                              (end_child is not None and not first_step)
            
            # 使用自一致性（桶计数）选择最佳子节点
            # bucket_count 表示有多少个CTE变体产生了相同的结果（自一致性）
            next_child = None
            
            if non_end_children:
                # 优先从非 <END> 节点中选择
                # 使用 bucket_count（自一致性）选择：选择产生最多相同结果的CTE
                # 但在选择时，优先考虑非空且非单0的CTE
                def get_bucket_count(child):
                    """获取子节点的桶计数（自一致性）"""
                    bucket_count = child.execution_results.get('bucket_count', 0)
                    return bucket_count
                
                def is_valid_nonempty_nonzero(child):
                    """检查子节点是否有效、非空且非单0"""
                    exec_res = child.execution_results.get('cte_result', {})
                    if not exec_res or not exec_res.get('valid', False):
                        return False
                    qr = exec_res.get('query_result', [])
                    try:
                        qr = MCTSUtils.safe_to_dict(qr)
                    except Exception:
                        qr = []
                    if not isinstance(qr, list):
                        try:
                            qr = list(qr)
                        except Exception:
                            qr = []
                    if not qr or len(qr) == 0:
                        return False
                    # 检查是否为单0结果
                    return not self._is_single_zero_result(qr)
                
                # 找出所有子节点的 bucket_count
                children_with_counts = [(ch, get_bucket_count(ch)) for ch in non_end_children]
                max_bucket_count = max(count for _, count in children_with_counts)
                
                # 找出所有具有最大 bucket_count 的子节点（处理平票）
                tied_children = [ch for ch, count in children_with_counts if count == max_bucket_count]
                
                # 在选择时，优先考虑非空且非单0的CTE
                # 1. 首先筛选出非空且非单0的CTE
                nonempty_nonzero_children = [ch for ch in tied_children if is_valid_nonempty_nonzero(ch)]
                
                if nonempty_nonzero_children:
                    # 如果有非空且非单0的CTE，从中选择
                    candidates = nonempty_nonzero_children
                else:
                    # 如果没有非空且非单0的CTE，从所有平票的CTE中选择
                    candidates = tied_children
                
                if len(candidates) > 1:
                    # 平票时，综合考虑多个因素：
                    # 1. LIKE CTE非空优先（在非空CTE中）
                    # 2. bucket_count阈值（>=4时成功率显著提高）
                    # 3. 深度和路径长度（越短越好）
                    # 4. Q值
                    def get_tiebreak_score(child):
                        """平票时的综合评分"""
                        exec_res = child.execution_results.get('cte_result', {})
                        is_valid_nonempty = False
                        if exec_res and exec_res.get('valid', False):
                            qr = exec_res.get('query_result', [])
                            try:
                                qr = MCTSUtils.safe_to_dict(qr)
                            except Exception:
                                qr = []
                            if not isinstance(qr, list):
                                try:
                                    qr = list(qr)
                                except Exception:
                                    qr = []
                            is_valid_nonempty = bool(qr and len(qr) > 0)
                        
                        # 检查是否是LIKE CTE
                        cte_text = child.cte or ""
                        is_like_cte = 'LIKE' in cte_text.upper() or 'fuzzy_match' in cte_text.lower()
                        
                        # 检查是否非单0
                        is_nonzero = is_valid_nonempty and not self._is_single_zero_result(exec_res.get('query_result', []))
                        
                        # 综合评分：(非空且非单0, LIKE CTE非空优先, bucket_count>=阈值, 深度惩罚, 路径长度惩罚, Q值)
                        bucket_count = get_bucket_count(child)
                        depth_score = 1.0 if child.depth <= self.depth_penalty_start else 0.8
                        # 对于有CTE的节点，depth = CTE路径长度，所以直接用depth判断
                        path_score = 1.0 if child.depth < 5 else (1.0 - 0.1 * (child.depth - 4))
                        
                        return (
                            1 if is_nonzero else 0,  # 非空且非单0优先
                            1 if (is_valid_nonempty and is_like_cte) else 0,  # 在非空CTE中，优先选择LIKE CTE
                            1 if bucket_count >= self.bucket_count_threshold else 0,  # bucket_count>=4优先
                            depth_score,  # 深度越浅越好
                            path_score,  # 路径越短越好
                            child.q_value  # Q值
                        )
                    
                    next_child = max(candidates, key=get_tiebreak_score)
                else:
                    next_child = candidates[0]
            
            # 若没有非 <END> 可选，且策略允许选择 <END>，则选择 <END>
            if next_child is None and allow_choose_end and end_child is not None:
                next_child = end_child

            if next_child is None:
                # 没有可继续的有效子节点，则停止在此
                if first_step:
                    # 第一步且没有非<END>的有效子节点，直接终止此次扩展
                    current.is_expanded = True
                break

            added_nodes.append(next_child)
            current = next_child
            # 继续下一轮扩展
        
        return current, added_nodes
    
    def _mcts_simulation(self, leaf_node: MCTSNode) -> Tuple[float, Optional[str], Dict[str, Any]]:
        """
        MCTS Simulation阶段：对叶节点进行模拟（生成多个SQL并计算自一致性奖励）
        
        Args:
            leaf_node: 叶节点
            
        Returns:
            (奖励值, 选择的SQL, 统计信息字典)
        """
        # 在以下情况生成SQL并评估：
        # 1. 到达<END>（完整链）
        # 2. 达到最大深度但未到达<END>（超过深度限制）
        if not leaf_node.is_terminal and leaf_node.depth < self.max_depth:
            return 0.0, None, {'sql_bucket_count': 0, 'sql_total_variants': 0}
        
        # 生成多个SQL（通过temperature和表随机化增加多样性）
        _sqlgen_t0 = _time_for_timing.time()
        # 使用配置的SQL变体数量
        sql_variants = self.complete_sql_generator.generate_multiple_complete_sqls_parallel(
            leaf_node,
            num_variants=self.num_sql_variants,
            max_workers=self.max_workers
        )
        self._timing['sql_gen_s'] += (_time_for_timing.time() - _sqlgen_t0)
        
        if not sql_variants:
            return 0.0, None, {
                'sql_bucket_count': 0, 
                'sql_total_variants': 0,
                'all_sql_variants': [],
                'result_buckets': {},
                'valid_count': 0,
                'error_reason': 'SQL生成失败：未生成任何SQL变体'
            }
        
        print(f"[模拟] 生成了 {len(sql_variants)} 个SQL变体")
        print(f"[模拟] 正在并行执行 {len(sql_variants)} 个SQL（超时={self.sql_timeout_s}s，最大并行数={self.max_workers}）...")
        
        # 并行执行所有SQL并收集结果
        execution_results = []
        exec_start = _time_for_timing.time()
        parallel_results = execute_sqls_parallel(self.db_connector, sql_variants, timeout_s=self.sql_timeout_s, max_workers=self.max_workers)
        exec_elapsed = _time_for_timing.time() - exec_start
        # 记录数据库执行耗时
        self._timing['db_exec_s'] += exec_elapsed
        
        timeout_count = 0
        for (result, error) in parallel_results:
            if result is not None and not error:
                execution_results.append({'valid': True, 'query_result': result})
            else:
                error_msg = str(error) if error else 'unknown error'
                execution_results.append({'valid': False, 'error': error_msg})
                # 检查是否为超时错误（数据库连接器返回格式：查询执行超时(XXs)）
                error_lower = error_msg.lower()
                if '超时' in error_msg or 'timeout' in error_lower or 'timed out' in error_lower:
                    timeout_count += 1
        
        if timeout_count > 0:
            print(f"[模拟] ⚠️ 警告：{timeout_count}/{len(sql_variants)} 个SQL执行超时（总耗时 {exec_elapsed:.2f}s）")
        else:
            print(f"[模拟] SQL执行完成（总耗时 {exec_elapsed:.2f}s）")
        
        # 计算一致性奖励与分桶（下沉到工具类）
        result_buckets, best_key = MCTSUtils.bucketize_valid_nonempty(execution_results)
        valid_count = sum(1 for r in execution_results if r.get('valid', False))
        # 获取SQL桶数量（最大桶的计数）
        sql_bucket_count = max(result_buckets.values()) if result_buckets else 0
        if valid_count == 0:
            # 所有SQL执行失败，但仍然记录所有SQL变体信息
            all_sql_variants = []
            for idx, (sql, res) in enumerate(zip(sql_variants, execution_results)):
                sql_info = {
                    'sql': sql,
                    'valid': False,
                    'error': res.get('error', 'unknown error') if res else 'unknown error',
                    'result_signature': None,
                    'result_row_count': 0
                }
                all_sql_variants.append(sql_info)
            
            return 0.0, None, {
                'sql_bucket_count': 0, 
                'sql_total_variants': len(sql_variants),
                'all_sql_variants': all_sql_variants,
                'result_buckets': {},
                'valid_count': 0,
                'error_reason': f'所有SQL执行失败：{len(sql_variants)}个SQL变体全部执行失败'
            }

        # 建立sql与签名的映射（与execution_results保持顺序一致）
        # 生成 sql_with_signatures，并排除结果为空的情况（与分桶逻辑保持一致）
        sql_with_signatures: List[Tuple[str, Optional[str]]] = []
        signature_to_result: Dict[str, Any] = {}  # 签名 -> 对应的结果样本
        # 修改：为每个签名维护列顺序到SQL的映射，并统计每个列顺序的出现次数
        signature_to_column_order_sqls: Dict[str, Dict[tuple, List[Tuple[str, Any]]]] = {}  # 签名 -> {列顺序元组: [(SQL, 查询结果), ...]}
        # 保留旧的signature_to_sql用于向后兼容（平票时比较）
        signature_to_sql: Dict[str, str] = {}  # 签名 -> 对应的SQL（第一个遇到的）
        for sql, res in zip(sql_variants, execution_results):
            if res.get('valid', False):
                # 检查结果是否为空（与分桶逻辑保持一致）
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
                    key = MCTSUtils.create_result_signature(res)
                    sql_with_signatures.append((sql, key))
                    
                    # 提取列顺序（从查询结果的第一行获取列名顺序）
                    column_order = tuple(query_result[0].keys()) if query_result and isinstance(query_result[0], dict) else tuple()
                    
                    # 初始化签名对应的列顺序映射
                    if key not in signature_to_column_order_sqls:
                        signature_to_column_order_sqls[key] = {}
                    
                    # 将SQL和查询结果按列顺序分组
                    if column_order not in signature_to_column_order_sqls[key]:
                        signature_to_column_order_sqls[key][column_order] = []
                    signature_to_column_order_sqls[key][column_order].append((sql, query_result))
                    
                    # 保存签名对应的结果和SQL（只保存第一个，用于平票时比较）
                    if key not in signature_to_result:
                        signature_to_result[key] = query_result
                        signature_to_sql[key] = sql
                else:
                    sql_with_signatures.append((sql, None))  # 结果为空，不参与选择
            else:
                sql_with_signatures.append((sql, None))  # 执行失败，不参与选择
        
        # 如果有平票（多个分桶的count相同），选择"更好"的分桶
        if result_buckets:
            max_count = max(result_buckets.values())
            tied_keys = [k for k, v in result_buckets.items() if v == max_count]
            
            if len(tied_keys) > 1:
                # 平票时，选择结果行数最少的，然后列数最少的，最后SQL最短的
                def get_tiebreak_score(sig: str) -> Tuple[int, int, int]:
                    """返回(行数, 列数, SQL长度)，越小越好"""
                    res = signature_to_result.get(sig, [])
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
        # 计算最高一致性（最频繁结果/总变体）
        max_consistency = MCTSUtils.calculate_consistency_reward(result_buckets, len(sql_variants))
        
        # 自一致性奖励：最高一致性比例
        reward = max_consistency
        
        # 如果最佳结果签名为单个0，则降低奖励（惩罚）
        if result_buckets and best_key:
            # 从execution_results中找到对应的结果
            best_result = None
            for res in execution_results:
                if res.get('valid', False):
                    if MCTSUtils.create_result_signature(res) == best_key:
                        best_result = res.get('query_result', None)
                        break
            
            if best_result is not None and self._is_single_zero_result(best_result):
                # 单个0结果的惩罚：将奖励降低到原来的50%
                original_reward = reward
                reward = reward * 0.5
                print(f"[模拟] ⚠️ 警告：最佳结果为单个0，降低奖励 {original_reward:.4f} → {reward:.4f} (惩罚50%)")

        # 选择本次rollout的代表SQL：对应max计数的签名（或平票时的最佳签名）
        # 修改：在桶内选择列顺序出现次数最多的SQL，但优先排除结果为单个0的SQL
        selected_sql: Optional[str] = None
        if result_buckets:
            # 从最佳签名的桶中选择列顺序出现次数最多的SQL
            if best_key in signature_to_column_order_sqls:
                column_order_sqls = signature_to_column_order_sqls[best_key]
                
                # 分离结果为单个0的SQL和非单个0的SQL
                non_zero_column_order_sqls = {}  # 非单个0的SQL
                zero_column_order_sqls = {}  # 单个0的SQL
                
                for col_order, sqls in column_order_sqls.items():
                    non_zero_sqls = []
                    zero_sqls = []
                    for sql, query_result in sqls:
                        if self._is_single_zero_result(query_result):
                            zero_sqls.append((sql, query_result))
                        else:
                            non_zero_sqls.append((sql, query_result))
                    
                    if non_zero_sqls:
                        non_zero_column_order_sqls[col_order] = non_zero_sqls
                    if zero_sqls:
                        zero_column_order_sqls[col_order] = zero_sqls
                
                # 优先从非单个0的SQL中选择
                candidate_column_order_sqls = non_zero_column_order_sqls if non_zero_column_order_sqls else zero_column_order_sqls
                is_zero_result = not non_zero_column_order_sqls
                
                if candidate_column_order_sqls:
                    # 统计每个列顺序的出现次数
                    column_order_counts = {col_order: len(sqls) for col_order, sqls in candidate_column_order_sqls.items()}
                    # 找出出现次数最多的列顺序
                    max_count = max(column_order_counts.values()) if column_order_counts else 0
                    most_common_column_orders = [col_order for col_order, count in column_order_counts.items() if count == max_count]
                    
                    # 如果有多个列顺序出现次数相同，选择第一个（或可以添加其他平票策略）
                    if most_common_column_orders:
                        best_column_order = most_common_column_orders[0]
                        # 从该列顺序对应的SQL列表中选择第一个（它们都有相同的列顺序）
                        if best_column_order in candidate_column_order_sqls and candidate_column_order_sqls[best_column_order]:
                            selected_sql = candidate_column_order_sqls[best_column_order][0][0]  # (sql, query_result) -> sql
                            if is_zero_result:
                                print(f"[模拟] ⚠️ 警告：从桶中选择列顺序出现次数最多的SQL，但结果为单个0 (列顺序出现{max_count}次，共{len(column_order_counts)}种列顺序)")
                            else:
                                print(f"[模拟] 从桶中选择列顺序出现次数最多的SQL (列顺序出现{max_count}次，共{len(column_order_counts)}种列顺序，已排除{sum(len(sqls) for sqls in zero_column_order_sqls.values())}个结果为单个0的SQL)")
            
            # 如果上面的逻辑没有找到SQL，回退到原来的逻辑
            if selected_sql is None:
                selected_sql = signature_to_sql.get(best_key, None)
                # 如果还是找不到，回退到原来的逻辑
                if selected_sql is None:
                    for sql, sig in sql_with_signatures:
                        if sig == best_key:
                            selected_sql = sql
                            break
        
        # 如果所有SQL结果都为空（result_buckets为空），但仍然需要选择一个SQL用于revision
        # 选择第一个有效的SQL（即使结果为空）
        if selected_sql is None and sql_variants:
            # 优先选择执行成功但结果为空的SQL
            for sql, res in zip(sql_variants, execution_results):
                if res.get('valid', False):
                    selected_sql = sql
                    break
            # 如果所有SQL都执行失败，至少选择一个SQL用于revision
            if selected_sql is None and len(sql_variants) > 0:
                selected_sql = sql_variants[0]
        
        # 计算最频繁结果的出现次数
        max_bucket_count = max(result_buckets.values()) if result_buckets else 0
        print(f"[模拟] 奖励: {reward:.4f} (一致性: {max_bucket_count}/{len(sql_variants)}, 通过率: {valid_count}/{len(sql_variants)})")
        # —— 在已有的 selected_sql 确定后，紧接着加上这段 —— 
        best_result = None
        if result_buckets:
            # 从本次 execution_results 里找与 best_key 匹配的那条结果
            for res in execution_results:
                if res.get('valid', False):
                    if MCTSUtils.create_result_signature(res) == best_key:
                        best_result = res.get('query_result', None)
                        break

        if best_result is not None:
            print("[模拟] 对应结果内容如下:")
            print(best_result)
        
        # [修改点]：保存最佳 SQL 到节点上，用于后续 Max Visit Path 策略
        if selected_sql:
            # 如果该节点已经有记录，且新的 reward 更高，则更新（或者保留最近一次）
            # 这里简单起见，我们保留最后一次模拟的结果，或者逻辑上保留 best_reward 的那次
            if not hasattr(leaf_node, 'best_simulation_sql') or reward > getattr(leaf_node, 'best_simulation_reward', -1.0):
                leaf_node.best_simulation_sql = selected_sql
                leaf_node.best_simulation_reward = reward
        
        # 构建所有SQL变体的详细信息
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
                    sql_info['result_signature'] = MCTSUtils.create_result_signature(res)
                    sql_info['result_row_count'] = len(query_result)
            all_sql_variants.append(sql_info)
        
        sql_stats = {
            'sql_bucket_count': sql_bucket_count,
            'sql_total_variants': len(sql_variants),
            'all_sql_variants': all_sql_variants,  # 保存所有SQL变体及其执行结果
            'result_buckets': dict(result_buckets) if result_buckets else {},  # 保存分桶信息
            'valid_count': valid_count,  # 有效SQL数量
            'error_reason': None  # 如果sql_bucket_count为0，记录原因
        }
        
        
        return reward, selected_sql, sql_stats
    
    def _mcts_backpropagation(self, path: List[MCTSNode], reward: float):
        """
        MCTS Backpropagation阶段：将奖励回溯更新到路径上的所有节点
        
        Args:
            path: 从根到叶的路径
            reward: 奖励值
        """
        if not path:
            return

        # 回传逻辑：沿路径向上，对每个节点更新统计信息
        print(f"\n[回传] 开始回传奖励 {reward:.4f} 到路径上的 {len(path)} 个节点:")
        for node in path:
            old_visits = node.visit_count
            old_q = node.q_value
            old_backup_sum = node.backup_reward_sum
            old_backup_visits = node.backup_visits
            
            # 更新 backup 统计和 visit_count
            node.visit_count += 1
            node.backup_reward_sum += reward
            node.backup_visits += 1
            # 保持向后兼容：同时更新 total_reward 和 average_reward
            node.total_reward += reward
            node.average_reward = node.total_reward / node.visit_count
            new_q = node.q_value
            q_backup = node.q_backup
            
            print(
                f"  [回传] 节点#{getattr(node, 'node_id', -1)} 深度={node.depth}:"
            )
            print(
                f"    访问: {old_visits} → {node.visit_count}, "
                f"BackupRewardSum: {old_backup_sum:.3f} → {node.backup_reward_sum:.3f}, "
                f"BackupVisits: {old_backup_visits} → {node.backup_visits}"
            )
            print(
                f"    Q值: {old_q:.4f} → {new_q:.4f} "
                f"(Q_backup={q_backup:.3f})"
            )
    
    
    def _is_node_execution_failed(self, node: MCTSNode) -> bool:
        """检查节点是否执行失败"""
        if not hasattr(node, 'execution_results') or not node.execution_results:
            return False
        
        cte_result = node.execution_results.get('cte_result', {})
        return not cte_result.get('valid', True)
    
    def _is_single_zero_result(self, query_result: Any) -> bool:
        """
        检查查询结果是否为单个0值
        
        Args:
            query_result: 查询结果（可能是DataFrame、列表或字典）
            
        Returns:
            如果结果是单个0值返回True，否则返回False
        """
        try:
            # 转换为字典列表
            if isinstance(query_result, list):
                result_list = query_result
            else:
                result_list = MCTSUtils.safe_to_dict(query_result)
            
            # 检查是否只有一行
            if not result_list or len(result_list) != 1:
                return False
            
            # 获取第一行
            first_row = result_list[0]
            if not isinstance(first_row, dict):
                return False
            
            # 检查是否只有一个列
            if len(first_row) != 1:
                return False
            
            # 获取唯一的值
            value = list(first_row.values())[0]
            
            # 检查值是否为0（支持int、float、字符串"0"等）
            if value is None:
                return False
            
            # 尝试转换为数字
            try:
                num_value = float(value)
                # 检查是否为0（允许小的浮点误差）
                return abs(num_value) < 1e-10
            except (ValueError, TypeError):
                # 如果不能转换为数字，检查字符串是否为"0"
                return str(value).strip() == "0"
        except Exception:
            return False

    def _has_where_clause(self, cte: str) -> bool:
        """
        检查CTE中是否包含WHERE子句
        
        Args:
            cte: CTE文本
            
        Returns:
            如果包含WHERE子句返回True，否则返回False
        """
        if not cte or cte == "<END>":
            return False

        # 提取CTE定义部分（去除WITH和CTE名称）
        match = re.search(r'WITH\s+\w+\s+AS\s*\((.*?)\)', cte, re.DOTALL | re.IGNORECASE)
        if match:
            select_part = match.group(1).strip()
        else:
            # 如果没有WITH，尝试直接提取SELECT
            select_part = cte
        
        # 检查是否包含WHERE关键字（需要排除字符串中的WHERE）
        # 使用正则表达式匹配WHERE关键字，但要避免匹配字符串中的WHERE
        # 简单方法：查找 WHERE 关键字，但要确保不在引号内
        where_pattern = r'\bWHERE\b'
        # 检查是否有WHERE关键字（忽略大小写）
        if re.search(where_pattern, select_part, re.IGNORECASE):
            # 进一步验证：确保WHERE后面有内容（不是空WHERE）
            where_match = re.search(r'\bWHERE\s+', select_part, re.IGNORECASE)
            if where_match:
                # 找到WHERE后的内容，检查是否有实际条件
                after_where = select_part[where_match.end():].strip()
                # 移除可能的注释和空白
                after_where = re.sub(r'--.*$', '', after_where, flags=re.MULTILINE)  # 移除单行注释
                after_where = re.sub(r'/\*.*?\*/', '', after_where, flags=re.DOTALL)  # 移除多行注释
                after_where = after_where.strip()
                # 如果WHERE后有内容（不是空），返回True
                if after_where and not after_where.startswith(';') and not after_where.startswith(')'):
                    return True
        return False
    
    def _deduplicate_cte_variants(self, cte_variants: List[str], node: MCTSNode) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
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
        from concurrent.futures import ThreadPoolExecutor, as_completed

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
            # 如果没有LIMIT，添加LIMIT 5用于探针执行（快速检测，与prompt中的建议一致）
            if exec_sql and not has_limit:
                # 在最后的SELECT语句前添加LIMIT 5
                if 'SELECT * FROM' in exec_sql.upper():
                    exec_sql = re.sub(r'(SELECT \* FROM[^;]+)(;?)$', r'\1 LIMIT 5\2', exec_sql, flags=re.IGNORECASE | re.DOTALL)
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
        self._timing['db_exec_s'] += (_time_for_timing.time() - _exec_t0)
        
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
    
    def _generate_cte_variants(self, node: MCTSNode) -> List[str]:
        """生成多个CTE变体（根据配置选择串行或并行）"""
        # 获取策略相关参数
        # 从根节点获取 picked_strategy（如果已选择）
        root_node = self.mcts_tree.root if self.mcts_tree else None
        picked_strategy = getattr(root_node, 'picked_strategy', None) if root_node else None
        depth = node.depth
        
        # 生成策略注入文本
        strategy_text = build_strategy_injection_text(
            mode=self.strategy_mode,
            picked_strategy=picked_strategy,
            depth=depth
        )
        
        # 临时修改节点的 additional_context（注入策略文本）
        original_additional_context = node.additional_context
        if strategy_text:
            extra_blocks = []
            if original_additional_context:
                extra_blocks.append(original_additional_context)
            extra_blocks.append(strategy_text)
            node.additional_context = "\n\n".join(extra_blocks)
        
        # 从当前节点及其父节点链中收集所有失败信息
        failed_attempts = []
        current = node
        visited_nodes = set()  # 避免重复收集同一节点的失败信息
        
        # 向上遍历父节点链，收集所有失败尝试
        while current is not None and current.node_id not in visited_nodes:
            visited_nodes.add(current.node_id)
            # 收集当前节点的失败尝试
            if hasattr(current, '_failed_cte_attempts') and current._failed_cte_attempts:
                # 基于错误信息去重：对于相同的错误信息，只保留一个代表性的CTE（最短的）
                existing_errors = {
                    item.get('error', '').strip() 
                    for item in failed_attempts
                }
                for attempt in current._failed_cte_attempts:
                    error = attempt.get('error', '').strip()
                    if not error:
                        error = '未知错误'
                    cte = attempt.get('cte', '').strip()
                    
                    
                    # 基于错误信息去重：如果错误信息已存在，比较CTE长度，保留较短的
                    if error in existing_errors:
                        # 查找已存在的相同错误信息的项
                        for idx, existing_item in enumerate(failed_attempts):
                            if existing_item.get('error', '').strip() == error:
                                existing_cte = existing_item.get('cte', '').strip()
                                # 如果新的CTE更短，替换
                                if len(cte) < len(existing_cte):
                                    failed_attempts[idx] = attempt
                                break
                    else:
                        # 如果错误信息不存在，直接添加
                        failed_attempts.append(attempt)
                        existing_errors.add(error)
            
            # 如果当前节点是失败节点，也收集其execution_results中的错误信息
            # 但优先使用_failed_cte_attempts中的详细信息（包含具体CTE和错误）
            # 如果_failed_cte_attempts为空，才使用execution_results中的汇总错误
            if current.execution_results.get('is_failed', False):
                # 如果已经有_failed_cte_attempts，就不需要再添加汇总错误了
                if not (hasattr(current, '_failed_cte_attempts') and current._failed_cte_attempts):
                    cte_result = current.execution_results.get('cte_result', {})
                    if not cte_result.get('valid', True):
                        error_msg = cte_result.get('error', '所有CTE变体都执行失败')
                        # 检查是否已存在相同的错误信息
                        if not any(item.get('error', '') == error_msg for item in failed_attempts):
                            failed_attempts.append({
                                'cte': '',
                                'error': error_msg
                            })
            
            current = current.parent
        
        # 限制失败信息数量，避免prompt过长
        failed_attempts = failed_attempts[:5]  # 最多保留5个失败尝试
        
        if failed_attempts:
            print(f"[扩展] 收集到 {len(failed_attempts)} 个历史失败尝试（来自当前节点及父节点链）")
        
        # generate_multiple_cte_variants 方法内部已经支持并行生成
        # 统一使用配置的CTE变体数量
        num_cte_variants = self.max_cte_nodes_per_iteration
        
        result = self.cte_generator.generate_multiple_cte_variants(
            node, 
            num_variants=num_cte_variants,
            failed_attempts=failed_attempts  # 传递失败信息
        )
        
        # 恢复节点的 original_additional_context（避免影响后续使用）
        node.additional_context = original_additional_context
        
        return result
    
   
    def _extract_strategy_and_clean_cte(self, cte: str) -> Tuple[Optional[str], str]:
        """
        从CTE文本中提取策略并清洗
        
        支持两种格式：
        1. 新格式: <S1> ... ```sql ... ```
        2. 旧格式: -- STRATEGY: S1 ... (向后兼容)
        
        Args:
            cte: CTE文本，可能包含策略标签和SQL代码块
        
        Returns:
            (策略字符串或None, 清洗后的CTE文本)
        """
        if not cte:
            return None, cte
        
        cte = cte.strip()
        
        # 尝试解析新格式: <S1> ... ```sql ... ```
        import re
        strategy_pattern = r'<(S[1-4])>'
        match = re.search(strategy_pattern, cte, re.IGNORECASE)
        if match:
            strategy = match.group(1).upper()
            # 提取SQL代码块内容
            sql_block_pattern = r'```sql\s*(.*?)\s*```'
            sql_match = re.search(sql_block_pattern, cte, re.DOTALL | re.IGNORECASE)
            if sql_match:
                cleaned_cte = sql_match.group(1).strip()
            else:
                # 如果没有代码块，尝试提取 <S1> 之后的内容
                cleaned_cte = cte[match.end():].strip()
                # 移除可能的代码块标记
                cleaned_cte = re.sub(r'^```sql\s*', '', cleaned_cte, flags=re.IGNORECASE)
                cleaned_cte = re.sub(r'\s*```$', '', cleaned_cte, flags=re.IGNORECASE)
            return strategy, cleaned_cte
        
        # 向后兼容旧格式: -- STRATEGY: S1
        lines = cte.splitlines()
        if lines and lines[0].strip().startswith("-- STRATEGY:"):
            first = lines[0].strip()
            s = first.replace("-- STRATEGY:", "").strip()
            cleaned = "\n".join(lines[1:]).strip()
            # 移除可能的代码块标记
            cleaned = re.sub(r'^```sql\s*', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\s*```$', '', cleaned, flags=re.IGNORECASE)
            return s, cleaned
        
        return None, cte
    
    def _get_final_statistics(self) -> Dict[str, Any]:
        """获取最终统计信息"""
        tree_stats = self.mcts_tree.get_final_statistics()
        
        # 已移除全局桶统计，如需可在外部自行统计
        # 合并计时信息
        try:
            timing_copy = {
                'total_s': float(self._timing.get('total_s', 0.0)),
                'rollout_s': float(self._timing.get('rollout_s', 0.0)),
                'cte_gen_s': float(self._timing.get('cte_gen_s', 0.0)),
                'sql_gen_s': float(self._timing.get('sql_gen_s', 0.0)),
                'db_exec_s': float(self._timing.get('db_exec_s', 0.0)),
                'rollout_count': int(self._timing.get('rollout_count', 0)),
            }
            tree_stats['timing'] = timing_copy
        except Exception:
            pass
        return tree_stats


    def _count_nodes(self) -> int:
            """计算树中的总节点数"""
            if not self.mcts_tree.root:
                return 0
            return self._count_nodes_recursive(self.mcts_tree.root)
    
    def _count_nodes_recursive(self, node: MCTSNode) -> int:
        """递归计算节点数"""
        count = 1
        for child in node.children:
            count += self._count_nodes_recursive(child)
        return count
    
    
    def _select_sql_by_robust_path(self, rollout_stats_list: List[Dict[str, Any]] = None) -> str:
        """
        策略：选择最高奖励的rollout的SQL
        
        选择逻辑：
        1. 优先选择reward最高的rollout的selected_sql
        2. 如果reward相同，选择sql_bucket_count最大的
        3. 如果都相同，选择第一个
        
        Args:
            rollout_stats_list: 所有rollout的统计信息列表，包含reward、sql_bucket_count、selected_sql
        
        Returns:
            最佳 SQL 字符串，如果找不到则返回空字符串
        """
        if not rollout_stats_list:
            print("[Selection] ⚠️ 没有rollout_stats，无法选择SQL")
            return ""
        
        # 使用新策略：最高奖励
        print("[Selection] 使用策略：选择最高奖励的rollout的SQL")
        best_rollout = None
        max_reward = -1.0
        max_sql_bucket = -1
        
        for rollout_stats in rollout_stats_list:
            reward = rollout_stats.get('reward', 0.0)
            sql_bucket_count = rollout_stats.get('sql_bucket_count', 0)
            selected_sql = rollout_stats.get('selected_sql')
            is_quick_path = rollout_stats.get('is_quick_path', False)
            
            # 只考虑有selected_sql的rollout
            if not selected_sql:
                continue
            
            # 优先选择reward最高的
            if reward > max_reward:
                max_reward = reward
                max_sql_bucket = sql_bucket_count
                best_rollout = rollout_stats
            elif reward == max_reward:
                # reward相同，选择sql_bucket_count最大的
                if sql_bucket_count > max_sql_bucket:
                    max_sql_bucket = sql_bucket_count
                    best_rollout = rollout_stats
                elif sql_bucket_count == max_sql_bucket:
                    # reward和sql_bucket_count都相同，优先选择CTE rollout（非快速路径）
                    current_best_is_quick = best_rollout.get('is_quick_path', False) if best_rollout else False
                    # 如果当前最佳是quick_path，但这个不是，则替换
                    if current_best_is_quick and not is_quick_path:
                        best_rollout = rollout_stats
                        print(f"[Selection] 💡 相同奖励和一致性下，优先选择CTE rollout而非quick_path")
        
        if best_rollout:
            selected_sql = best_rollout.get('selected_sql')
            if selected_sql:
                is_quick_path = best_rollout.get('is_quick_path', False)
                rollout_id = best_rollout.get('rollout_id', '?')
                rollout_type = "快速路径" if is_quick_path else f"CTE Rollout {rollout_id}"
                print(f"[Selection] ✅ 选择最高奖励的rollout的SQL (reward={max_reward:.4f}, sql_bucket_count={max_sql_bucket}, 类型={rollout_type})")
                return selected_sql.strip()
        
        print("[Selection] ❌ 未找到有效的rollout（没有selected_sql），无法选择SQL")
        return ""
    
    def set_mcts_parameters(self, rollouts_per_iteration: int = None, 
                           max_cte_nodes_per_iteration: int = None, exploration_constant: float = None, 
                           sql_timeout_s: float = None, strike_threshold: int = None):
        """
        设置MCTS超参数
        
        Args:
            rollouts_per_iteration: 每次迭代的rollout数量
            max_cte_nodes_per_iteration: 每次迭代生成的CTE变体数量
            exploration_constant: UCB探索常数
            sql_timeout_s: SQL执行超时时间（秒）
            strike_threshold: 动态剪枝的strike阈值（默认3）
        """
        if rollouts_per_iteration is not None:
            self.rollouts_per_iteration = rollouts_per_iteration
        if max_cte_nodes_per_iteration is not None:
            self.max_cte_nodes_per_iteration = max_cte_nodes_per_iteration
        if exploration_constant is not None:
            self.exploration_constant = exploration_constant
        if sql_timeout_s is not None:
            self.sql_timeout_s = sql_timeout_s
    
