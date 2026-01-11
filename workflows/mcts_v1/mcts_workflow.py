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
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
import random
import numpy as np
from typing import Dict, List, Optional, Tuple, Any, Set
from pathlib import Path
import threading
from .core.mcts_tree import MCTSTree
from .core.mcts_node import MCTSNode
from .core.database_connector import DatabaseConnector
from .agents.cte_generator import CTEGenerator
import json
from pathlib import Path
from .agents.complete_sql_generator import CompleteSQLGenerator
from .agents.sql_executor import SQLExecutor
from .utils.mcts_helpers import MCTSUtils
from .utils.sql_result_processor import SQLResultProcessor
from .utils.sql_selector import SQLSelector
from .utils.cte_processor import CTEProcessor
from .agents.strategy import build_strategy_injection_text, GLOBAL_STRATEGY_CONFIG, StrategyMode
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
            strategy_mode: 策略模式（FORCE_S1/S2/S3, NONE, LLM_PICK_ONCE），如果提供则覆盖全局配置
        """
        self.llm_config = llm_config
        self.db_connector = db_connector
        # using rollouts_per_iteration=1 to test 
        self.rollouts_per_iteration =1  # 从6增加到10，让visit_count更好地反映节点质量
        self.exploration_constant = 2.5  # 增加探索常数，从1.414增加到2.0，鼓励更多探索
        # 注意：UCB1的exploration项本身就会鼓励探索访问较少的节点，增加exploration_constant即可增强探索
        self.max_depth = 8  # MCTS树最大深度（对于有CTE的节点，depth = CTE路径长度）
        self.max_cte_nodes_per_iteration = 8  # 每次扩展节点时生成的CTE变体数量
        # SQL变体数量配置：每个rollout末尾生成的SQL变体数量（用于计算sql_bucket_count）
        # 范围：5-8个，根据rollouts_per_iteration动态调整
        self.num_sql_variants = 10  # 每个rollout末尾生成的SQL变体数量

        # 统一 SQL 超时配置（秒）
        self.sql_timeout_s = 40
        self.cte_probe_timeout_s = 40  # CTE探针执行超时（较短，用于快速检测）
        self.cte_probe_limit = 15  # CTE探针查询的LIMIT值

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
        
        # 加载relationships.json
        relationships_data = {}
        relationships_file = Path(__file__).parent / "data" / "relationships.json"
        if relationships_file.exists():
            try:
                with open(relationships_file, 'r', encoding='utf-8') as f:
                    relationships_data = json.load(f)
                print(f"✅ 加载了 {len(relationships_data)} 个数据库的关系信息")
            except Exception as e:
                print(f"⚠️ 加载relationships.json失败: {e}")
        
        # CTE探针配置（需要在初始化CTEGenerator之前定义）
        self.cte_probe_limit = 15  # CTE探针查询的LIMIT值
        
        # 初始化MCTS组件
        self.mcts_tree = MCTSTree()
        self.cte_generator = CTEGenerator(llm_config, max_depth=self.max_depth, multi_model_configs=multi_model_configs, relationships_data=relationships_data, cte_probe_limit=self.cte_probe_limit)
        self.complete_sql_generator = CompleteSQLGenerator(llm_config, multi_model_configs=multi_model_configs, cte_probe_limit=self.cte_probe_limit)
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
        
        # 初始化CTE处理器（在超时配置和计时统计设置之后）
        self.cte_processor = CTEProcessor(
            sql_executor=self.sql_executor,
            cte_probe_timeout_s=self.cte_probe_timeout_s,
            max_workers=self.max_workers,
            timing_dict=self._timing,
            cte_probe_limit=self.cte_probe_limit
        )
        
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
        # 在根节点存储 picked_strategy 和 picked_strategy_thought（用于 LLM_PICK_ONCE 模式）
        root_node.picked_strategy = None
        root_node.picked_strategy_thought = None
        self.mcts_tree.set_root(root_node)
        
        # MCTS主循环：执行多个rollout
        solve_start_ts = _time_for_timing.time()
        
        # 从根到叶的完整探索过程
        # 包含多个rollout来探索和评估不同的路径
        
        # 串行执行 rollout，收集每个rollout的统计信息
        rollout_stats_list = []
        
        # 获取evidence信息（从additional_context或question中提取）
        evidence = additional_context if additional_context else ""
        
        for rollout in range(self.rollouts_per_iteration):
            print(f"\n--- Rollout [{rollout + 1}/{self.rollouts_per_iteration}] ---")
            
            reward, selected_sql, rollout_stats = self._execute_mcts_rollout()
            rollout_stats['rollout_id'] = rollout + 1
            rollout_stats['is_quick_path'] = False
            rollout_stats_list.append(rollout_stats)


        # 使用奖励优先策略选择最佳 SQL（优先选择最高奖励的rollout的SQL）
        print("\n[Strategy] 使用奖励优先策略选择最佳 SQL（优先选择最高奖励的rollout）")
        optimal_sql = SQLSelector.select_by_highest_reward(rollout_stats_list)
        
        if not optimal_sql:
            print("[Strategy] ⚠️ 混合策略返回空")

        self._timing['total_s'] = max(0.0, _time_for_timing.time() - solve_start_ts)
        return {
            'optimal_sql': optimal_sql,
            'statistics': self._get_final_statistics(),
            'tree_info': self.mcts_tree.get_tree_info(),
            'rollout_stats': rollout_stats_list  # 每个rollout的详细统计信息
        }
    
    def _select_node(self) -> MCTSNode:
        """选择节点（UCB1算法）"""
        return self.mcts_tree.select_node(self.exploration_constant)
    
    
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
        def _should_continue_expansion(node):
            """判断是否应该继续扩展节点"""
            # 如果是失败节点，允许继续扩展
            if getattr(node, 'execution_results', {}).get('is_failed', False):
                return True
            # 如果节点执行失败（但不是失败节点），不允许继续扩展
            if self._is_node_execution_failed(node):
                return False
            return True
        
        while (not current.is_terminal) and (current.depth < self.max_depth) and \
              _should_continue_expansion(current):
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
                    # 使用策略选择函数（封装在strategy.py中）
                    from .agents.strategy import select_strategy_with_llm
                    
                    picked_strategy, picked_strategy_thought = select_strategy_with_llm(
                        cte_agent=self.cte_generator.cte_agent,
                        question=current.question,
                        schema_info=current.schema_info,
                        additional_context=current.additional_context,
                        timeout_s=120.0,
                        default_strategy="S2"
                    )
                    
                    # 保存选择的策略到根节点
                    root_node.picked_strategy = picked_strategy
                    root_node.picked_strategy_thought = picked_strategy_thought
            
            # 1) 生成多个CTE变体（计时：CTE 生成）
            # 注意：此时如果已选择策略，会在_generate_cte_variants中使用已选择的策略

            _cte_t0 = _time_for_timing.time()
            cte_variants = self._generate_cte_variants(current)
            self._timing['cte_gen_s'] += (_time_for_timing.time() - _cte_t0)
            
            # 注意：不再需要从CTE中提取策略，因为策略已经单独选择
            # 旧的策略提取逻辑已移除
            
            # 2) 去重并统计执行结果桶（使用CTE处理器）
            unique_cte_variants, failed_info = self.cte_processor.deduplicate_cte_variants(cte_variants, current)
            
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
                            
                            # 尝试从错误信息中提取列名，并查找该列在schema中的实际位置
                            # 检查是否是列名错误（支持多种错误格式）
                            is_column_error = (
                                'no such column' in error.lower() or 
                                ('column' in error.lower() and ('not found' in error.lower() or 'unknown' in error.lower()))
                            )
                            
                            if is_column_error:
                                # 使用根节点的schema_info（确保使用最新的schema）
                                schema_info = None
                                if hasattr(current, 'schema_info') and current.schema_info:
                                    schema_info = current.schema_info
                                elif self.mcts_tree.root and hasattr(self.mcts_tree.root, 'schema_info'):
                                    schema_info = self.mcts_tree.root.schema_info
                                
                                if schema_info:
                                    # 传入CTE以便生成更准确的提示（检测是否在CTE上下文中）
                                    column_mapping = MCTSUtils.find_column_table_mapping(error, schema_info, cte=cte)
                                    if column_mapping:
                                        # 在失败信息中添加列名到表的映射提示
                                        if 'column_hint' not in failed_item:
                                            failed_item['column_hint'] = column_mapping['hint']
                                            failed_item['column_name'] = column_mapping['column']
                                            failed_item['column_tables'] = column_mapping['tables']
                                            print(f"[错误处理] ✅ 找到列名映射: {column_mapping['column']} -> {column_mapping['tables']}")
                                    # else: 未找到映射，可能是列名确实不存在或schema格式问题
                                # else: 无schema_info可用
                            
                            # 基于错误信息去重：如果错误信息已存在，比较CTE长度，保留较短的
                            if error in existing_errors:
                                # 查找已存在的相同错误信息的项
                                for idx, existing_item in enumerate(current._failed_cte_attempts):
                                    if existing_item.get('error', '').strip() == error:
                                        existing_cte = existing_item.get('cte', '').strip()
                                        # 如果新的CTE更短，替换（同时保留列名映射信息）
                                        if len(cte) < len(existing_cte):
                                            # 如果新项有column_hint但旧项没有，保留新项；如果旧项有但新项没有，保留旧项的column_hint
                                            if 'column_hint' in failed_item and 'column_hint' not in existing_item:
                                                current._failed_cte_attempts[idx] = failed_item
                                            elif 'column_hint' not in failed_item and 'column_hint' in existing_item:
                                                # 保留旧项的column_hint，但更新其他字段
                                                failed_item['column_hint'] = existing_item['column_hint']
                                                failed_item['column_name'] = existing_item.get('column_name')
                                                failed_item['column_tables'] = existing_item.get('column_tables')
                                                current._failed_cte_attempts[idx] = failed_item
                                            else:
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
                    has_where = MCTSUtils.has_where_clause(cte_text)
                    
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
                    
                    # 空结果节点允许继续扩展（不再基于连续空结果次数停止）
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
                # 没有创建任何子节点（所有CTE变体都失败或为空且无WHERE子句）
                # 如果有失败信息，创建失败节点继续扩展
                if failed_info:
                    # 按照错误信息分桶，为每种错误类型创建失败节点
                    error_buckets = {}
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
                    root_schema = self.mcts_tree.root.schema_info if self.mcts_tree.root else current.schema_info
                    created_failed_nodes = []
                    with self.mcts_tree.lock:
                        if not current.is_expanded:  # 再次检查，避免重复创建
                            for error_msg, failed_items in error_buckets.items():
                                failed_child = MCTSNode(
                                    question=current.question,
                                    schema_info=root_schema,
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
                                failed_child.execution_results['error_type'] = error_msg
                                
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
                                
                                # 失败节点也增加深度（深度+1）
                                current.add_child(failed_child)
                                created_failed_nodes.append(failed_child)
                            
                            if created_failed_nodes:
                                current.is_expanded = True
                    
                    # 选择第一个失败节点继续扩展（如果有多个失败节点，后续可以通过UCB选择不同的错误路径）
                    if created_failed_nodes:
                        failed_child = created_failed_nodes[0]  # 选择第一个失败节点
                        added_nodes.append(failed_child)
                        current = failed_child
                        # 继续while循环，尝试在失败节点上生成新的CTE
                        continue
                
                # 如果没有失败信息，停止扩展
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
                    return not MCTSUtils.is_single_zero_result(qr)
                
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
                        is_nonzero = is_valid_nonempty and not MCTSUtils.is_single_zero_result(exec_res.get('query_result', []))
                        
                        # 综合评分：(非空且非单0, LIKE CTE非空优先, 深度惩罚, 路径长度惩罚, Q值)
                        # 对于有CTE的节点，depth = CTE路径长度，所以直接用depth判断
                        path_score = 1.0 if child.depth < 5 else (1.0 - 0.1 * (child.depth - 4))
                        
                        return (
                            1 if is_nonzero else 0,  # 非空且非单0优先
                            1 if (is_valid_nonempty and is_like_cte) else 0,  # 在非空CTE中，优先选择LIKE CTE
                            1.0 if child.depth <= 6 else 0.8,  # 深度越浅越好
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
        
        # 使用工具类执行SQL并处理结果
        execution_results, exec_elapsed, timeout_count = SQLResultProcessor.execute_and_process_sqls(
            self.db_connector,
            sql_variants,
            self.sql_timeout_s,
            self.max_workers,
            context_prefix="[模拟]"
        )
        # 记录数据库执行耗时
        self._timing['db_exec_s'] += exec_elapsed
        
        # 计算一致性奖励与分桶（下沉到工具类）
        result_buckets, best_key = MCTSUtils.bucketize_valid_nonempty(execution_results)
        valid_count = sum(1 for r in execution_results if r.get('valid', False))
        # 获取SQL桶数量（最大桶的计数）
        sql_bucket_count = max(result_buckets.values()) if result_buckets else 0
        if valid_count == 0:
            # 所有SQL执行失败，但仍然记录所有SQL变体信息
            all_sql_variants = SQLResultProcessor.build_all_sql_variants_info(sql_variants, execution_results)
            
            return 0.0, None, {
                'sql_bucket_count': 0, 
                'sql_total_variants': len(sql_variants),
                'all_sql_variants': all_sql_variants,
                'result_buckets': {},
                'valid_count': 0,
                'error_reason': f'所有SQL执行失败：{len(sql_variants)}个SQL变体全部执行失败'
            }

        # 建立sql与签名的映射（使用工具类）
        sql_with_signatures, signature_to_result, signature_to_column_order_sqls, signature_to_sql = \
            SQLResultProcessor.build_sql_signature_mapping(sql_variants, execution_results)
        
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
        
        # 计算奖励并选择SQL（使用工具类）
        reward, selected_sql = SQLResultProcessor.calculate_reward_and_select_sql(
            sql_variants,
            execution_results,
            result_buckets,
            best_key,
            signature_to_column_order_sqls,
            signature_to_sql,
            sql_with_signatures,
            context_prefix="[模拟]"
        )
        
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
        
        # 构建所有SQL变体的详细信息（使用工具类）
        all_sql_variants = SQLResultProcessor.build_all_sql_variants_info(sql_variants, execution_results)
        
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
            
    
    
    def _is_node_execution_failed(self, node: MCTSNode) -> bool:
        """检查节点是否执行失败"""
        if not hasattr(node, 'execution_results') or not node.execution_results:
            return False
        
        cte_result = node.execution_results.get('cte_result', {})
        return not cte_result.get('valid', True)
    
    
    
    def _generate_cte_variants(self, node: MCTSNode) -> List[str]:
        """生成多个CTE变体（根据配置选择串行或并行）"""
        # 获取策略相关参数
        # 从根节点获取 picked_strategy 和 picked_strategy_thought（如果已选择）
        root_node = self.mcts_tree.root if self.mcts_tree else None
        picked_strategy = getattr(root_node, 'picked_strategy', None) if root_node else None
        picked_strategy_thought = getattr(root_node, 'picked_strategy_thought', None) if root_node else None
        depth = node.depth
        
        # 生成策略注入文本
        strategy_text = build_strategy_injection_text(
            mode=self.strategy_mode,
            picked_strategy=picked_strategy,
            picked_strategy_thought=picked_strategy_thought,
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
        
        # 辅助函数：检查列名错误是否已经被前序CTE修正
        def is_column_error_fixed(column_name: str, error_msg: str) -> bool:
            """检查列名错误是否已经被前序CTE修正"""
            if not column_name:
                return False
            
            # 从当前节点向上遍历，检查是否有成功的CTE包含了这个列名
            check_node = node
            while check_node is not None:
                # 检查当前节点的CTE是否成功执行
                if hasattr(check_node, 'cte') and check_node.cte and check_node.cte != "<END>":
                    exec_results = check_node.execution_results.get('cte_result', {})
                    if exec_results.get('valid', False):
                        # CTE执行成功，检查是否包含该列名
                        cte_text = check_node.cte
                        # 检查列名是否在CTE的SELECT子句中（使用反引号包裹或直接使用）
                        column_patterns = [
                            f"`{column_name}`",
                            f"`{column_name.replace(' ', '')}`",  # 处理空格
                            f"{column_name}",  # 直接使用（可能被反引号包裹）
                        ]
                        for pattern in column_patterns:
                            if pattern.lower() in cte_text.lower():
                                # 找到了列名，说明错误已经被修正
                                return True
                check_node = check_node.parent
            return False
        
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
                    
                    # 检查是否是列名错误，并且是否已经被修正
                    column_name = attempt.get('column_name')
                    if column_name and is_column_error_fixed(column_name, error):
                        # 错误已经被修正，完全跳过这个失败尝试
                        continue
                    
                    # 基于错误信息去重：如果错误信息已存在，比较CTE长度，保留较短的
                    if error in existing_errors:
                        # 查找已存在的相同错误信息的项
                        for idx, existing_item in enumerate(failed_attempts):
                            if existing_item.get('error', '').strip() == error:
                                existing_cte = existing_item.get('cte', '').strip()
                                # 如果新的CTE更短，替换（同时保留列名映射信息）
                                if len(cte) < len(existing_cte):
                                    # 如果新项有column_hint但旧项没有，保留新项；如果旧项有但新项没有，保留旧项的column_hint
                                    if 'column_hint' in attempt and 'column_hint' not in existing_item:
                                        failed_attempts[idx] = attempt
                                    elif 'column_hint' not in attempt and 'column_hint' in existing_item:
                                        # 保留旧项的column_hint，但更新其他字段
                                        attempt['column_hint'] = existing_item['column_hint']
                                        attempt['column_name'] = existing_item.get('column_name')
                                        attempt['column_tables'] = existing_item.get('column_tables')
                                        failed_attempts[idx] = attempt
                                    else:
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
    
