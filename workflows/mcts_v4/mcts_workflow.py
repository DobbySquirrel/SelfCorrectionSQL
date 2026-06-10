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
from .utils.cte_error_handler import CTEErrorHandler
from .agents.strategy import build_strategy_injection_text, GLOBAL_STRATEGY_CONFIG, StrategyMode
import os
import logging
import time as _time_for_timing

logger = logging.getLogger(__name__)

# mcts_v4: 问题拆分 + 子问题验证
from .agents.question_decomposer import QuestionDecomposer
from .agents.cte_sufficient_checker import CTESufficientChecker
from .utils.cte_diverse import (
    diverse_prompt_enabled,
    diverse_n,
    diverse_temps,
    generate_diverse_mode_c,
    skip_m_verify_enabled,
)
from .utils.plan_decomposer import (
    PlanDecomposer,
    build_rollout_schedule,
    multi_plan_enabled,
    plan_to_sub_questions,
)


class MCTSWorkflow:
    """MCTS工作流主控制器"""
    
    def __init__(self, llm_config: Dict, db_connector: DatabaseConnector, max_workers: int = None, strategy_mode: Optional[str] = None, collect_stats_on_node_creation: bool = True, use_decompose_flow: bool = False, decompose_strategy: str = "S2"):
        """
        初始化MCTS工作流

        Args:
            llm_config: LLM配置
            db_connector: 数据库连接器
            max_workers: 最大并行工作线程数（默认根据实际需求动态计算）
            strategy_mode: 策略模式（FORCE_S1/S2/S3, NONE, LLM_PICK_ONCE），如果提供则覆盖全局配置
            collect_stats_on_node_creation: 是否在节点创建时收集统计信息（默认True）
            use_decompose_flow: 是否使用 mcts_v4 流程（问题拆分 + 子问题验证 + 类 Alpha-SQL Select/Expand/Simulate）
            decompose_strategy: 问题拆分策略 S1/S2/S7（与 mcts_v1 一致），仅 use_decompose_flow 时生效
        """
        self.llm_config = llm_config
        self.use_decompose_flow = use_decompose_flow
        self.decompose_strategy = decompose_strategy
        self.db_connector = db_connector
        self.collect_stats_on_node_creation = collect_stats_on_node_creation
        # Opt defaults: rollouts=8, sql_variants=5 (overridable via test_mcts / env)
        self.rollouts_per_iteration = 8
        self.exploration_constant = 2.5  # 增加探索常数，从1.414增加到2.0，鼓励更多探索
        # 注意：UCB1的exploration项本身就会鼓励探索访问较少的节点，增加exploration_constant即可增强探索
        self.max_depth = 8  # MCTS树最大深度（对于有CTE的节点，depth = CTE路径长度）
        self.max_cte_nodes_per_iteration = 10  # 每次扩展节点时生成的CTE变体数量
        self.num_sql_variants = 5  # 每个rollout末尾生成的SQL变体数量（opt 默认 5）

        # 统一 SQL 超时配置（秒）
        self.sql_timeout_s = 40
        self.cte_probe_timeout_s = 30  # CTE探针执行超时（从40秒减到30秒，更快失败，避免卡住）
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
            except Exception:
                pass
        
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
        self._current_rollout_expansion_records: List[Dict[str, Any]] = []
        self._v4_decompose_expand_traces: List[Dict[str, Any]] = []
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

        # mcts_v4: 问题拆分与子问题验证
        self.question_decomposer: Optional[QuestionDecomposer] = None
        self.plan_decomposer: Optional[PlanDecomposer] = None
        self.cte_sufficient_checker: Optional[CTESufficientChecker] = None
        if use_decompose_flow:
            if multi_plan_enabled():
                self.plan_decomposer = PlanDecomposer(llm_config)
            else:
                self.question_decomposer = QuestionDecomposer(llm_config, strategy=decompose_strategy)
            self.cte_sufficient_checker = CTESufficientChecker(llm_config)
        
    def solve(self, question: str, schema_info: str, additional_context: str = "", 
              original_schema: Optional[str] = None, _retry: bool = False,
              qid: int = 0) -> Dict[str, Any]:
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
        self._v4_decompose_expand_traces = []
        self._pending_diverse_expand_trace: Optional[Dict[str, Any]] = None
        _orig_max_depth = self.max_depth
        self._solve_max_depth_cap = _orig_max_depth
        
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

        plan_proposals: List[Dict[str, Any]] = []
        plan_deduped: List[Dict[str, Any]] = []
        per_plan_rollout_stats: Dict[str, List[Dict[str, Any]]] = {}
        use_multi_plan = bool(
            self.use_decompose_flow
            and self.plan_decomposer is not None
            and multi_plan_enabled()
        )

        # mcts_v4: 问题拆分，设 max_depth 与 root.sub_questions
        if use_multi_plan:
            plan_proposals, plan_deduped = self.plan_decomposer.propose_all(
                question, schema_info, additional_context
            )
            for p in plan_deduped:
                per_plan_rollout_stats[p["plan_id"]] = []
            first_sq = plan_to_sub_questions(plan_deduped[0]) if plan_deduped else [question]
            root_node.sub_questions = first_sq  # type: ignore
            root_node.sub_question_index = -1
            self.max_depth = min(len(first_sq), _orig_max_depth)
        elif self.use_decompose_flow and self.question_decomposer:
            sub_questions = self.question_decomposer.decompose(question, schema_info, additional_context)
            root_node.sub_questions = sub_questions  # type: ignore
            root_node.sub_question_index = -1
            self.max_depth = min(len(sub_questions), _orig_max_depth)
        
        # MCTS主循环：执行多个rollout
        solve_start_ts = _time_for_timing.time()
        
        # 从根到叶的完整探索过程
        # 包含多个rollout来探索和评估不同的路径
        
        # 串行执行 rollout，收集每个rollout的统计信息
        rollout_stats_list = []
        
        # 获取evidence信息（从additional_context或question中提取）
        evidence = additional_context if additional_context else ""
        
        if use_multi_plan and plan_deduped:
            schedule = build_rollout_schedule(plan_deduped, self.rollouts_per_iteration)
            for rollout_idx, active_plan in enumerate(schedule):
                sq = plan_to_sub_questions(active_plan)
                self._reset_tree_for_plan(
                    question, schema_info, additional_context, sq
                )
                reward, selected_sql, rollout_stats = self._execute_mcts_rollout_v4()
                rollout_stats["rollout_id"] = rollout_idx + 1
                rollout_stats["plan_id"] = active_plan.get("plan_id")
                rollout_stats["plan_strategy"] = active_plan.get("strategy")
                rollout_stats["plan_hash"] = active_plan.get("plan_hash")
                rollout_stats["is_quick_path"] = False
                rollout_stats_list.append(rollout_stats)
                pid = active_plan.get("plan_id") or "unknown"
                per_plan_rollout_stats.setdefault(pid, []).append(rollout_stats)
        else:
            for rollout in range(self.rollouts_per_iteration):
                if self.use_decompose_flow:
                    reward, selected_sql, rollout_stats = self._execute_mcts_rollout_v4()
                else:
                    reward, selected_sql, rollout_stats = self._execute_mcts_rollout()
                rollout_stats['rollout_id'] = rollout + 1
                rollout_stats['is_quick_path'] = False
                rollout_stats_list.append(rollout_stats)

        from .query_clarifier.config import ENV_TRACE_PATH, clarify_enabled
        from .query_clarifier.integration import maybe_apply_clarify
        from .query_clarifier.logging_utils import set_trace_path

        clarify_record = None
        if clarify_enabled():
            trace = os.environ.get(ENV_TRACE_PATH)
            if trace:
                set_trace_path(trace)
            rollout_stats_list, clarify_record = maybe_apply_clarify(
                rollout_stats_list,
                qid=qid,
                nl_question=question,
                schema_ddl=schema_info,
            )

        optimal_sql = SQLSelector.select(rollout_stats_list)

        # 收集所有路径的 SQL，供 test_mcts 做「任一路径对即算对」统计（每条 rollout 的 selected_sql + 各 rollout 的 all_sql_variants）
        all_sqls_with_attributes = []
        seen_sql = set()
        for rs in rollout_stats_list:
            sel = rs.get('selected_sql')
            if sel and sel.strip() and sel not in seen_sql:
                seen_sql.add(sel)
                all_sqls_with_attributes.append({'sql': sel})
            for info in rs.get('all_sql_variants', []):
                s = (info.get('sql') or '').strip()
                if s and s not in seen_sql:
                    seen_sql.add(s)
                    all_sqls_with_attributes.append({'sql': s})
        self._timing['total_s'] = max(0.0, _time_for_timing.time() - solve_start_ts)
        out = {
            'optimal_sql': optimal_sql,
            'statistics': self._get_final_statistics(),
            'tree_info': self.mcts_tree.get_tree_info(),
            'rollout_stats': rollout_stats_list,  # 每个rollout的详细统计信息
            'all_sqls_with_attributes': all_sqls_with_attributes,  # 所有路径/变体的 SQL，供 gold 验证与任一路径对统计
        }
        if clarify_record is not None:
            out['clarify_trace'] = clarify_record.to_dict()
        # mcts_v4: 问题拆分结果写入输出，供下游保存到 JSON
        if self.use_decompose_flow and self.mcts_tree.root is not None:
            out['sub_questions'] = getattr(self.mcts_tree.root, 'sub_questions', [])
            if self._v4_decompose_expand_traces:
                out['decompose_expand_traces'] = list(self._v4_decompose_expand_traces)
        if use_multi_plan:
            out['plan_proposals'] = plan_proposals
            out['plan_dedup_count'] = len(plan_deduped)
            out['per_plan_rollout_stats'] = per_plan_rollout_stats
            out['union_rollout_stats'] = list(rollout_stats_list)
        return out

    def _reset_tree_for_plan(
        self,
        question: str,
        schema_info: str,
        additional_context: str,
        sub_questions: List[str],
    ) -> MCTSNode:
        """Fresh MCTS root per plan rollout (isolate visit stats across plans)."""
        MCTSNode._global_node_counter = 0
        root = MCTSNode(
            question=question,
            schema_info=schema_info,
            additional_context=additional_context,
            parent=None,
        )
        root.picked_strategy = None
        root.picked_strategy_thought = None
        root.sub_questions = sub_questions  # type: ignore
        root.sub_question_index = -1
        self.mcts_tree.set_root(root)
        cap = getattr(self, "_solve_max_depth_cap", self.max_depth)
        self.max_depth = min(len(sub_questions), cap)
        return root
    
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
        self._current_rollout_expansion_records = []
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
        cte_buckets_per_node = list(self._current_rollout_expansion_records)
        repair_info = []  # 记录修正信息：哪些CTE是修正后的，以及修正原因
        
        for node in path:
            if node.cte and node.cte != "<END>":
                cte_path.append(node.cte)  # 保存完整的CTE，不截断
                bucket_count = node.execution_results.get('bucket_count', 0)
                cte_bucket_counts.append(bucket_count)
                cte_depths.append(node.depth)
                visit_counts.append(node.visit_count)
                
                # 检查是否是修正后的CTE
                is_repaired = getattr(node, 'is_repaired', False)
                repair_reason = getattr(node, 'repair_reason', None)
                if is_repaired:
                    repair_info.append({
                        'cte_index': len(cte_path) - 1,  # CTE在路径中的索引
                        'depth': node.depth,
                        'repair_reason': repair_reason or 'CTE列名引用错误，需要重新生成完整CTE链'
                    })
        
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
            'error_reason': sql_stats.get('error_reason', None),  # 如果sql_bucket_count为0，记录原因
            'repair_info': repair_info  # 修正信息：记录哪些CTE是修正后的，以及修正原因
            # 格式：[{'cte_index': int, 'depth': int, 'repair_reason': str}, ...]
        }
        
        return reward, selected_sql, rollout_stats
    
    def _stamp_child_instrumentation(
        self,
        child: MCTSNode,
        cte_text: str,
        exec_res: Dict[str, Any],
        expansion_step_id: str,
        v2_to_cluster: Dict[str, int],
    ) -> None:
        """Record-only fields for clarification instrumentation (does not affect search)."""
        if not exec_res:
            return
        legacy, v2 = MCTSUtils.dual_signatures_from_execution(exec_res)
        child.result_signature_legacy = legacy
        child.result_signature_v2 = v2
        child.cluster_id = v2_to_cluster.get(v2)
        child.expansion_step_id = expansion_step_id
        child.cte_text_repr = (cte_text or "")[:2000]
        child.exec_rows_preview = MCTSUtils.build_exec_rows_preview(exec_res)

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
            
            # 【改进】优先选择未访问的节点（参考Alpha-SQL的实现）
            # 检查是否有未访问的子节点
            unvisited_children = [ch for ch in non_terminal_children if ch.visit_count == 0]
            if unvisited_children:
                # 如果有未访问的节点，选择第一个（子节点顺序已在扩展时被打乱）
                # 这样既保证了每个未访问节点都有机会被探索，又避免了完全随机的选择
                best_child = unvisited_children[0]
            else:
                # 所有子节点都已访问过，使用UCB1公式选择
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

            expansion_step_id = f"{getattr(current, 'node_id', -1)}::{current.depth}"
            v2_to_cluster: Dict[str, int] = {}

            cte_variants = self._generate_cte_variants(current)
            
            # 注意：不再需要从CTE中提取策略，因为策略已经单独选择
            # 旧的策略提取逻辑已移除
            
            # 2) 去重并统计执行结果桶（使用CTE处理器）
            unique_cte_variants, failed_info = self.cte_processor.deduplicate_cte_variants(cte_variants, current)
            
            current_node_buckets = MCTSUtils.build_instrumented_bucket_list(unique_cte_variants)
            self._current_rollout_expansion_records.append({
                "expansion_step_id": expansion_step_id,
                "depth": current.depth,
                "parent_node_id": getattr(current, "node_id", -1),
                "buckets": current_node_buckets,
            })
            if not hasattr(current, 'execution_results'):
                current.execution_results = {}
            current.execution_results['all_cte_buckets'] = current_node_buckets
            v2_to_cluster = {
                b["result_signature_v2"]: b["cluster_id"] for b in current_node_buckets
            }
            
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
                
                # 记录本次失败的CTE和执行错误
                if failed_info:
                    # 确保节点有_failed_cte_attempts属性
                    if not hasattr(current, '_failed_cte_attempts'):
                        current._failed_cte_attempts = []
                    
                    # 处理失败信息并分桶（不去重，每个失败都保存）
                    error_buckets = {}
                    for failed_item in failed_info:
                        error_msg = failed_item.get('error', '').strip()
                        if not error_msg:
                            error_msg = '未知错误'
                        
                        # 处理错误类型
                        failed_item = CTEErrorHandler.process_error(
                            failed_item, current, self.mcts_tree.root
                        )
                        
                        # 保存到当前节点
                        current._failed_cte_attempts.append(failed_item)
                        
                        # 按错误类型分桶
                        if error_msg not in error_buckets:
                            error_buckets[error_msg] = []
                        error_buckets[error_msg].append(failed_item)
                    
                    # 如果没有有效的失败信息，创建一个通用的失败节点
                    if not error_buckets:
                        error_buckets['所有CTE变体都执行失败'] = []
                    
                    # 创建失败节点
                    with self.mcts_tree.lock:
                        if current.is_expanded:
                            continue
                        
                        root_schema = self.mcts_tree.root.schema_info if self.mcts_tree.root else current.schema_info
                        created_failed_nodes = []
                        
                        for error_msg, failed_items in error_buckets.items():
                            failed_child = MCTSNode(
                                question=current.question,
                                schema_info=root_schema,
                                additional_context=current.additional_context,
                                parent=current
                            )
                            failed_child.cte = ""
                            failed_child.execution_results['cte_result'] = {
                                'valid': False,
                                'error': f"{error_msg} (共{len(failed_items)}个CTE变体失败)" if failed_items else error_msg
                            }
                            failed_child.execution_results['is_failed'] = True
                            failed_child.execution_results['error_type'] = error_msg
                            failed_child._failed_cte_attempts = failed_items.copy() if failed_items else []
                            
                            
                            current.add_child(failed_child)
                            created_failed_nodes.append(failed_child)
                        
                        current.is_expanded = True
                    
                    # 选择第一个失败节点继续扩展
                    if created_failed_nodes:
                        failed_child = created_failed_nodes[0]
                        added_nodes.append(failed_child)
                        current = failed_child
                    continue

            # 3) 仅为"有效且非空"的变体创建子节点；<END> 根据策略保留
            # 如果成功生成了CTE变体，重置重试计数（但保留失败记录，供后续rollout使用）
            if hasattr(current, '_cte_retry_count'):
                current._cte_retry_count = 0
            # 注意：不清空失败记录，即使有成功的CTE，失败信息也应该被保留
            # 这样后续rollout可以选择失败节点或使用失败信息生成修正CTE
            # 失败信息会被传递到子节点，或者在rollout到当前节点时使用
            # if hasattr(current, '_failed_cte_attempts'):
            #     current._failed_cte_attempts = []  # 不清空失败记录
            
            # 即使有成功的CTE，如果有失败信息，也应该处理失败信息并创建失败节点供rollout探索
            # 这样rollout可以探索不同的路径（成功的CTE路径和失败的CTE路径）
            if failed_info and unique_cte_variants:
                
                # 确保节点有_failed_cte_attempts属性
                if not hasattr(current, '_failed_cte_attempts'):
                    current._failed_cte_attempts = []
                
                # 处理失败信息并分桶（不再基于错误信息去重，每个失败都保存）
                error_buckets = {}
                for failed_item in failed_info:
                    error_msg = failed_item.get('error', '').strip()
                    if not error_msg:
                        error_msg = '未知错误'
                    
                    # 处理所有错误类型（使用CTEErrorHandler.process_error）
                    failed_item = CTEErrorHandler.process_error(
                        failed_item, current, self.mcts_tree.root
                    )
                    
                    # 保存到当前节点的失败记录
                    current._failed_cte_attempts.append(failed_item)
                    
                    # 按错误类型分桶
                    if error_msg not in error_buckets:
                        error_buckets[error_msg] = []
                    error_buckets[error_msg].append(failed_item)
                
                
                # 为每种错误类型创建一个失败节点
                root_schema = self.mcts_tree.root.schema_info if self.mcts_tree.root else current.schema_info
                created_failed_nodes_with_success = []
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
                    
                    # 失败节点只保存自己的失败信息（不复制父节点的失败信息）
                    # 每个失败节点对应一个错误类型，只包含该错误类型的失败信息
                    failed_child._failed_cte_attempts = []
                    
                    # 将当前错误类型的失败信息添加到失败节点中
                    if failed_items:
                        existing_combinations_in_failed_node = set()
                        for failed_item in failed_items:
                            error = failed_item.get('error', '').strip()
                            cte = failed_item.get('cte', '').strip()
                            combination = (error, cte)
                            if error and combination not in existing_combinations_in_failed_node:
                                failed_child._failed_cte_attempts.append(failed_item)
                                existing_combinations_in_failed_node.add(combination)
                    
                    # 失败节点也增加深度（深度+1）
                    # 注意：这里不立即添加到树中，而是在锁内批量添加
                    created_failed_nodes_with_success.append(failed_child)
                
                # 在锁内批量添加失败节点
                with self.mcts_tree.lock:
                    if not current.is_expanded:  # 再次检查，避免重复创建
                        for failed_child in created_failed_nodes_with_success:
                            current.add_child(failed_child)
                    else:
                        pass

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
                    # 根据配置决定是否在节点创建时收集统计信息
                    if self.collect_stats_on_node_creation:
                        enhanced_exec_res = self.cte_processor.collect_stats_for_node(cte_text, exec_res)
                        child.execution_results['cte_result'] = enhanced_exec_res
                    else:
                        child.execution_results['cte_result'] = exec_res
                    child.execution_results['bucket_count'] = info.get('count', 0)
                    child.execution_results['bucket_variants'] = info.get('variants', [])
                    
                    # 检查是否是修正后的CTE（父节点需要重新生成完整CTE链）
                    if hasattr(current, '_requires_full_cte_chain') and current._requires_full_cte_chain:
                        child.is_repaired = True
                        child.repair_reason = getattr(current, '_repair_reason', 'CTE列名引用错误，需要重新生成完整CTE链')
                    
                    self._stamp_child_instrumentation(
                        child, cte_text, exec_res, expansion_step_id, v2_to_cluster
                    )
                    children_to_create.append((child, cte_text, info, None))
                elif exec_res and exec_res.get('valid', False):
                    # 允许基于"有效但结果为空"的候选创建子节点（不管有没有WHERE子句）
                    child = MCTSNode(
                        question=current.question,
                        schema_info=root_schema,  # 使用根节点的最新schema
                        additional_context=current.additional_context,
                        parent=None
                    )
                    child.cte = cte_text
                    # 根据配置决定是否在节点创建时收集统计信息（即使结果为空也收集表统计信息）
                    if self.collect_stats_on_node_creation:
                        enhanced_exec_res = self.cte_processor.collect_stats_for_node(cte_text, exec_res)
                        child.execution_results['cte_result'] = enhanced_exec_res
                    else:
                        child.execution_results['cte_result'] = exec_res
                    child.execution_results['bucket_count'] = info.get('count', 0)
                    child.execution_results['bucket_variants'] = info.get('variants', [])
                    child.execution_results['is_empty_result'] = True
                    
                    # 检查是否是修正后的CTE（父节点需要重新生成完整CTE链）
                    if hasattr(current, '_requires_full_cte_chain') and current._requires_full_cte_chain:
                        child.is_repaired = True
                        child.repair_reason = getattr(current, '_repair_reason', 'CTE列名引用错误，需要重新生成完整CTE链')
                    
                    self._stamp_child_instrumentation(
                        child, cte_text, exec_res, expansion_step_id, v2_to_cluster
                    )
                    # 空结果节点允许继续扩展
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
                
                # 【改进】打乱子节点顺序（参考Alpha-SQL的实现）
                # 这样在优先选择未访问节点时，选择顺序是随机的，但选择过程是有序的
                import random
                random.shuffle(current.children)
                # 更新created_map以反映新的顺序（虽然顺序变了，但映射关系不变）
                
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
                                
                                # 失败节点只保存自己的失败信息（不复制父节点的失败信息）
                                # 每个失败节点对应一个错误类型，只包含该错误类型的失败信息
                                failed_child._failed_cte_attempts = []
                                
                                # 将当前错误类型的失败信息添加到失败节点中（去重后的）
                                existing_combinations_in_failed_node = set()
                                if failed_items:
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
            
            # 使用加权随机选择子节点（MCTS Simulation风格）
            # 权重基于bucket_count（自一致性），但保留探索性
            next_child = None
            
            if non_end_children:
                # 优先从非 <END> 节点中选择
                def get_bucket_count(child):
                    """获取子节点的桶计数（自一致性）"""
                    return child.execution_results.get('bucket_count', 0)
                
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
                
                # 筛选候选节点：优先非空且非单0的CTE
                nonempty_nonzero_children = [ch for ch in non_end_children if is_valid_nonempty_nonzero(ch)]
                
                if nonempty_nonzero_children:
                    candidates = nonempty_nonzero_children
                else:
                    candidates = non_end_children
                
                if len(candidates) == 1:
                    next_child = candidates[0]
                elif len(candidates) > 1:
                    # 使用加权随机选择（权重 = bucket_count + 1，避免0权重）
                    # bucket_count高的CTE更可能被选中，但低的也有机会（探索性）
                    weights = [get_bucket_count(ch) + 1 for ch in candidates]
                    next_child = random.choices(candidates, weights=weights, k=1)[0]
                    
                    # 打印选择信息（调试用）
                    selected_bucket = get_bucket_count(next_child)
                    max_bucket = max(weights) - 1  # 减1还原原始bucket_count
                    if selected_bucket < max_bucket:
                        pass

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
        
        【优化】Reward 复用：如果节点已被访问过，复用已有的平均 reward，减少重复计算
        参考 Alpha-SQL 的实现：如果节点已被访问过（visit_count > 0），直接使用已有的平均 reward
        
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
        
        # 【优化】Reward 复用：如果节点已被访问过，复用已有的平均 reward
        # 参考 Alpha-SQL：如果节点已被访问过（visit_count > 0），直接使用已有的平均 reward
        if leaf_node.visit_count > 0:
            # 节点已被访问过，复用已有的平均 reward
            reused_reward = leaf_node.average_reward
            reused_sql = getattr(leaf_node, 'best_simulation_sql', None)
            
            # 尝试从节点中获取之前保存的统计信息
            if hasattr(leaf_node, 'last_simulation_stats'):
                stats = leaf_node.last_simulation_stats
                return reused_reward, reused_sql, stats
            else:
                # 如果没有保存统计信息，构建基本的统计信息
                return reused_reward, reused_sql, {
                    'sql_bucket_count': getattr(leaf_node, 'last_sql_bucket_count', 0),
                    'sql_total_variants': getattr(leaf_node, 'last_sql_total_variants', 0),
                    'all_sql_variants': getattr(leaf_node, 'last_all_sql_variants', []),
                    'result_buckets': getattr(leaf_node, 'last_result_buckets', {}),
                    'valid_count': getattr(leaf_node, 'last_valid_count', 0),
                    'error_reason': None
                }
        
        # 节点未被访问过，需要计算新的 reward
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
            pass

        # [修改点]：保存最佳 SQL 到节点上，用于后续 Max Visit Path 策略
        # 【优化】同时保存统计信息，用于 Reward 复用
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
        
        # 【优化】保存统计信息到节点，用于后续 Reward 复用
        leaf_node.last_simulation_stats = sql_stats
        leaf_node.last_sql_bucket_count = sql_bucket_count
        leaf_node.last_sql_total_variants = len(sql_variants)
        leaf_node.last_all_sql_variants = all_sql_variants
        leaf_node.last_result_buckets = dict(result_buckets) if result_buckets else {}
        leaf_node.last_valid_count = valid_count
        
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

    # ---------- mcts_v4: 问题拆分 + 类 Alpha-SQL Select/Expand/Simulate ----------
    def _path_from_root_to_node(self, node: Optional[MCTSNode]) -> List[MCTSNode]:
        """从根到 node 的路径（含 node）。"""
        path = []
        cur = node
        while cur is not None:
            path.append(cur)
            cur = cur.parent
        path.reverse()
        return path

    def _select_to_leaf_v4(self) -> List[MCTSNode]:
        """从根出发 UCB（未访问优先）选到当前树的叶，返回路径。"""
        root = self.mcts_tree.root
        if not root:
            return []
        path = [root]
        current = root
        sub_questions = getattr(root, 'sub_questions', [])
        n_subs = len(sub_questions)

        while current.children and not current.is_terminal:
            # 未访问子节点优先（Alpha-SQL）
            unvisited = [c for c in current.children if c.visit_count == 0]
            if unvisited:
                next_node = unvisited[0]
            else:
                next_node = max(
                    current.children,
                    key=lambda c: c.get_ucb1_value(self.exploration_constant)
                )
            path.append(next_node)
            current = next_node

        return path

    def _is_terminal_v4(self, path: List[MCTSNode]) -> bool:
        """路径是否已覆盖所有子问题（可生成完整 SQL）。"""
        root = self.mcts_tree.root
        sub_questions = getattr(root, 'sub_questions', []) if root else []
        n_subs = len(sub_questions)
        if n_subs == 0:
            return getattr(path[-1], 'is_terminal', False)
        # 路径上除根外有 n_subs 个“已解决子问题”的节点
        nodes_with_cte = [n for n in path[1:] if getattr(n, 'cte', None) and n.cte != '<END>']
        valid_count = sum(1 for n in nodes_with_cte if n.execution_results.get('cte_result', {}).get('valid', False))
        return valid_count >= n_subs or getattr(path[-1], 'is_terminal', False)

    def _format_execution_preview(self, exec_res: Dict[str, Any], row_limit: int = 3, val_length_limit: int = 100) -> str:
        """将执行结果格式化为简短预览（前几行），供 refine 时传入 CTE 生成器。对齐 Alpha-SQL 的 format_execution_result。"""
        if not exec_res or not exec_res.get("valid") or exec_res.get("query_result") is None:
            return ""
        try:
            qr = MCTSUtils.safe_to_dict(exec_res.get("query_result", []))
        except Exception:
            return ""
        if not isinstance(qr, list) or len(qr) == 0:
            return "(empty result)"
        rows = qr[:row_limit]
        cols = list(rows[0].keys()) if rows else []
        lines = [" | ".join(str(c) for c in cols)]
        for row in rows:
            vals = []
            for c in cols:
                v = row.get(c)
                s = str(v) if v is not None else "NULL"
                if len(s) > val_length_limit:
                    s = s[:val_length_limit] + "..."
                vals.append(s)
            lines.append(" | ".join(vals))
        return "\n".join(lines)

    def _expand_leaf_v4(self, leaf: MCTSNode) -> None:
        """在叶节点上扩展：生成当前子问题 CTE → 执行 → M_verify（最多 3 次）→ 分桶建子节点。"""
        root = self.mcts_tree.root
        sub_questions = getattr(root, 'sub_questions', []) if root else []
        if not sub_questions:
            return
        idx = getattr(leaf, 'sub_question_index', 0)
        if idx < 0:
            idx = 0
        if idx >= len(sub_questions):
            leaf.is_terminal = True
            return
        sub_q = sub_questions[idx]
        leaf.sub_questions_total = len(sub_questions)  # 供 CTE 生成器按子问题展示 prompt
        path_to_leaf = self._path_from_root_to_node(leaf)
        h_prefix = []
        for n in path_to_leaf[1:]:
            if getattr(n, 'cte', None) and n.cte != '<END>':
                res = n.execution_results.get('cte_result', {})
                summary = "Execution OK" if res.get('valid') else res.get('error', '')
                if res.get('valid') and res.get('query_result'):
                    qr = res['query_result']
                    try:
                        qr = MCTSUtils.safe_to_dict(qr)
                    except Exception:
                        qr = []
                    summary = f"row count {len(qr) if isinstance(qr, list) else 0}"
                h_prefix.append({"q": getattr(n, 'sub_question', ''), "cte": n.cte, "result_summary": summary})

        # 节点内最多 3 次迭代：生成 → 执行 → M_verify；若无 valid 则用 (cte, reason) 作为 failed_attempts 再生成
        orig_question = leaf.question
        leaf.question = sub_q
        leaf.sub_question = sub_q
        leaf.sub_question_index = idx
        max_iterations = 3
        valid_results = []
        failed_attempts_for_gen = []  # 传给 CTE 生成器的失败列表：[{"cte": str, "error": str}, ...]
        unique_cte_variants = []  # 最后一轮去重后的结果，用于兜底

        for iteration in range(max_iterations):
            leaf.question = sub_q
            leaf.sub_question = sub_q
            leaf.sub_question_index = idx
            leaf._original_question = orig_question  # 供 CTE 生成器显示原始问题
            try:
                cte_variants = self._generate_cte_variants(leaf, failed_attempts_v4=failed_attempts_for_gen)
            finally:
                leaf.question = orig_question

            if self._pending_diverse_expand_trace is not None:
                tr = dict(self._pending_diverse_expand_trace)
                tr["sub_question_index"] = idx
                tr["iteration"] = iteration
                tr["m_verify_skipped"] = skip_m_verify_enabled()
                self._v4_decompose_expand_traces.append(tr)
                self._pending_diverse_expand_trace = None

            if not cte_variants:
                break
            if self.use_decompose_flow and diverse_prompt_enabled():
                cte_pool = cte_variants
            else:
                cte_pool = cte_variants[: self.max_cte_nodes_per_iteration]
            unique_cte_variants, _ = self.cte_processor.deduplicate_cte_variants(
                cte_pool, leaf
            )
            failed_attempts_for_gen = []
            skip_verify = skip_m_verify_enabled()
            for info in unique_cte_variants:
                cte_text = info.get("cte", "")
                if cte_text == "<END>":
                    continue
                exec_res = info.get("execution_result") or {}
                valid_exec = exec_res.get("valid", False)
                result_summary = "Execution OK" if valid_exec else exec_res.get("error", "")
                if valid_exec and exec_res.get("query_result") is not None:
                    try:
                        qr = MCTSUtils.safe_to_dict(exec_res.get("query_result", []))
                        result_summary = f"row count {len(qr) if isinstance(qr, list) else 0}"
                    except Exception:
                        pass
                if skip_verify:
                    if valid_exec:
                        sig = MCTSUtils.create_result_signature(exec_res) if exec_res else ""
                        valid_results.append({"cte": cte_text, "exec_res": exec_res, "signature": sig})
                    continue
                valid, reason = self.cte_sufficient_checker.verify(
                    original_question=root.question,
                    current_sub_question=sub_q,
                    h_prefix=h_prefix,
                    current_cte_or_sql=cte_text,
                    execution_result_summary=result_summary,
                    execution_valid=valid_exec,
                )
                if valid:
                    sig = MCTSUtils.create_result_signature(exec_res) if exec_res else ""
                    valid_results.append({"cte": cte_text, "exec_res": exec_res, "signature": sig})
                else:
                    # 对齐 Alpha-SQL refine：传入执行结果摘要与预览，便于模型根据「跑出了什么」修正
                    item = {
                        "cte": cte_text,
                        "error": reason or "M_verify: CTE does not sufficiently answer the current sub-question",
                        "result_summary": result_summary,
                    }
                    preview = self._format_execution_preview(exec_res, row_limit=3, val_length_limit=100)
                    if preview:
                        item["execution_preview"] = preview
                    failed_attempts_for_gen.append(item)
            if valid_results:
                break

        # 若 3 轮后仍无 M_verify 通过的结果，但最后一轮有执行成功的 CTE，则兜底接受第一个桶，避免整条 rollout 无子节点
        if not valid_results and unique_cte_variants:
            for info in unique_cte_variants:
                cte_text = info.get("cte", "")
                if cte_text == "<END>":
                    continue
                exec_res = info.get("execution_result") or {}
                if exec_res.get("valid", False):
                    sig = MCTSUtils.create_result_signature(exec_res) if exec_res else ""
                    valid_results.append({"cte": cte_text, "exec_res": exec_res, "signature": sig})
                    break

        # 按 result 签名分桶，创建子节点
        sig_to_info = {}
        for v in valid_results:
            sig = v.get("signature") or ""
            if sig not in sig_to_info:
                sig_to_info[sig] = []
            sig_to_info[sig].append(v)
        root_schema = root.schema_info if root else leaf.schema_info
        next_idx = idx + 1
        next_sub = sub_questions[next_idx] if next_idx < len(sub_questions) else ""
        with self.mcts_tree.lock:
            if leaf.is_expanded:
                return
            for sig, infos in sig_to_info.items():
                info = infos[0]
                child = MCTSNode(
                    question=root.question,
                    schema_info=root_schema,
                    additional_context=leaf.additional_context,
                    parent=leaf,
                )
                child.sub_question_index = next_idx
                child.sub_question = next_sub
                child.cte = info.get("cte", "")
                child.execution_results["cte_result"] = info.get("exec_res", {})
                child.execution_results["bucket_count"] = len(infos)
                if next_idx >= len(sub_questions):
                    child.is_terminal = True
                leaf.add_child(child)
            random.shuffle(leaf.children)
            leaf.is_expanded = True

    def _simulate_v4(self, start_node: MCTSNode) -> List[MCTSNode]:
        """从 start_node 起反复 expand → random choice 直到终态，返回从根到终节点的路径。"""
        path = self._path_from_root_to_node(start_node)
        current = start_node
        root = self.mcts_tree.root
        sub_questions = getattr(root, 'sub_questions', []) if root else []
        n_subs = len(sub_questions)

        while not current.is_terminal and (not sub_questions or getattr(current, 'sub_question_index', 0) < n_subs):
            if not current.is_expanded:
                self._expand_leaf_v4(current)
            if not current.children:
                break
            next_node = random.choice(current.children)
            path.append(next_node)
            current = next_node
        return path

    def _reward_from_path_v4(self, path: List[MCTSNode]) -> Tuple[float, Optional[str], Dict[str, Any]]:
        """根据路径生成完整 SQL，执行并分桶得 reward。"""
        if not path:
            return 0.0, None, {'sql_bucket_count': 0, 'sql_total_variants': 0, 'all_sql_variants': []}
        leaf = path[-1]
        # 用路径上各节点 CTE 链生成完整 SQL（复用 complete_sql_generator 的“从节点生成”逻辑，节点链即路径）
        _t0 = _time_for_timing.time()
        # v4 模拟阶段使用配置的 SQL 变体数量（与 _execute_mcts_rollout 一致）
        sql_variants = self.complete_sql_generator.generate_multiple_complete_sqls_parallel(
            leaf, num_variants=self.num_sql_variants, max_workers=self.max_workers
        )
        self._timing['sql_gen_s'] += (_time_for_timing.time() - _t0)
        if not sql_variants:
            return 0.0, None, {'sql_bucket_count': 0, 'sql_total_variants': 0, 'all_sql_variants': []}
        exec_results, _, _ = SQLResultProcessor.execute_and_process_sqls(
            self.db_connector, sql_variants, self.sql_timeout_s, self.max_workers, context_prefix="[v4模拟]"
        )
        result_buckets, best_key = MCTSUtils.bucketize_valid_nonempty(exec_results)
        sql_bucket_count = max(result_buckets.values()) if result_buckets else 0
        all_sql_variants = SQLResultProcessor.build_all_sql_variants_info(sql_variants, exec_results)
        selected_sql = None
        if best_key is not None:
            _, _, _, sig_to_sql = SQLResultProcessor.build_sql_signature_mapping(sql_variants, exec_results)
            selected_sql = sig_to_sql.get(best_key)
        if selected_sql is None and sql_variants:
            selected_sql = sql_variants[0]
        reward = (sql_bucket_count / len(sql_variants)) if sql_variants else 0.0
        stats = {
            'sql_bucket_count': sql_bucket_count,
            'sql_total_variants': len(sql_variants),
            'all_sql_variants': all_sql_variants,
            'result_buckets': result_buckets,
            'valid_count': sum(1 for r in exec_results if r.get('valid', False)),
        }
        return reward, selected_sql, stats

    def _execute_mcts_rollout_v4(self) -> Tuple[float, Optional[str], Dict[str, Any]]:
        """mcts_v4 单次 rollout：Select → Expand → Simulate（随机选）→ Reward → Backprop。"""
        rollout_start_ts = _time_for_timing.time()
        path = self._select_to_leaf_v4()
        if not path:
            self._timing['rollout_s'] += (_time_for_timing.time() - rollout_start_ts)
            self._timing['rollout_count'] += 1
            return 0.0, None, {'reward': 0.0, 'cte_path': [], 'sub_question_path': [], 'selected_sql': None, 'all_sql_variants': [], 'result_buckets': {}, 'sql_bucket_count': 0}

        leaf = path[-1]
        if self._is_terminal_v4(path):
            reward, selected_sql, sql_stats = self._reward_from_path_v4(path)
            self._mcts_backpropagation(path, reward)
            self._timing['rollout_s'] += (_time_for_timing.time() - rollout_start_ts)
            self._timing['rollout_count'] += 1
            root = self.mcts_tree.root
            sq_list = getattr(root, 'sub_questions', []) if root else []
            cte_path = [n.cte for n in path if getattr(n, 'cte', None)]
            # Each node's CTE was generated for the parent's sub-question (idx); we store next idx on child, so use sq_list[node.sub_question_index - 1]
            sub_question_path = []
            for n in path:
                if not getattr(n, 'cte', None):
                    continue
                idx = getattr(n, 'sub_question_index', 0)
                if idx >= 1 and (idx - 1) < len(sq_list):
                    sub_question_path.append(sq_list[idx - 1] or '')
                else:
                    sub_question_path.append(getattr(n, 'sub_question', '') or '')
            return reward, selected_sql, {
                'reward': reward, 'cte_path': cte_path, 'sub_question_path': sub_question_path,
                'selected_sql': selected_sql,
                'all_sql_variants': sql_stats.get('all_sql_variants', []),
                'result_buckets': sql_stats.get('result_buckets', {}), 'sql_bucket_count': sql_stats.get('sql_bucket_count', 0),
            }

        self._expand_leaf_v4(leaf)
        if not leaf.children:
            self._timing['rollout_s'] += (_time_for_timing.time() - rollout_start_ts)
            self._timing['rollout_count'] += 1
            return 0.0, None, {'reward': 0.0, 'cte_path': [], 'sub_question_path': [], 'selected_sql': None, 'all_sql_variants': [], 'result_buckets': {}, 'sql_bucket_count': 0}

        child = random.choice(leaf.children)
        sim_path = self._simulate_v4(child)
        reward, selected_sql, sql_stats = self._reward_from_path_v4(sim_path)
        self._mcts_backpropagation(sim_path, reward)
        self._timing['rollout_s'] += (_time_for_timing.time() - rollout_start_ts)
        self._timing['rollout_count'] += 1
        root = self.mcts_tree.root
        sq_list = getattr(root, 'sub_questions', []) if root else []
        cte_path = [n.cte for n in sim_path if getattr(n, 'cte', None)]
        sub_question_path = []
        for n in sim_path:
            if not getattr(n, 'cte', None):
                continue
            idx = getattr(n, 'sub_question_index', 0)
            if idx >= 1 and (idx - 1) < len(sq_list):
                sub_question_path.append(sq_list[idx - 1] or '')
            else:
                sub_question_path.append(getattr(n, 'sub_question', '') or '')
        return reward, selected_sql, {
            'reward': reward, 'cte_path': cte_path, 'sub_question_path': sub_question_path,
            'selected_sql': selected_sql,
            'all_sql_variants': sql_stats.get('all_sql_variants', []),
            'result_buckets': sql_stats.get('result_buckets', {}), 'sql_bucket_count': sql_stats.get('sql_bucket_count', 0),
        }
    
    
    def _is_node_execution_failed(self, node: MCTSNode) -> bool:
        """检查节点是否执行失败"""
        if not hasattr(node, 'execution_results') or not node.execution_results:
            return False
        
        cte_result = node.execution_results.get('cte_result', {})
        return not cte_result.get('valid', True)
    
    
    
    def _generate_cte_variants(self, node: MCTSNode, failed_attempts_v4: Optional[List[Dict[str, Any]]] = None) -> List[str]:
        """生成多个CTE变体（根据配置选择串行或并行）。failed_attempts_v4 为 mcts_v4 节点内迭代时的 M_verify 反馈。"""
        _cte_t0 = _time_for_timing.time()
        try:
            return self._generate_cte_variants_body(node, failed_attempts_v4)
        finally:
            self._timing['cte_gen_s'] += (_time_for_timing.time() - _cte_t0)

    def _generate_cte_variants_body(self, node: MCTSNode, failed_attempts_v4: Optional[List[Dict[str, Any]]] = None) -> List[str]:
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
        
        # 检查是否需要重新生成完整CTE链（用于标记修正）
        requires_full_cte_chain = False
        repair_reason = None
        
        # 使用CTEErrorHandler检查列名错误是否已经被前序CTE修正
        
        # 如果当前节点是失败节点，只使用该失败节点自己的失败信息，不向上遍历父节点链
        if node.execution_results.get('is_failed', False):
            # 失败节点只使用自己的失败信息
            if hasattr(node, '_failed_cte_attempts') and node._failed_cte_attempts:
                failed_attempts = []
                existing_errors = set()
                for attempt in node._failed_cte_attempts:
                    error = attempt.get('error', '').strip()
                    if not error:
                        error = '未知错误'
                    if error not in existing_errors:
                        failed_attempts.append(attempt)
                        existing_errors.add(error)
                        
                        # 检查是否需要重新生成完整CTE链
                        if attempt.get('requires_full_cte_chain', False):
                            requires_full_cte_chain = True
                            if not repair_reason:
                                error_msg = attempt.get('error', '').strip()
                                column_name = attempt.get('column_name', '')
                                if column_name:
                                    repair_reason = f"CTE列名引用错误: {column_name} ({error_msg})"
                                else:
                                    repair_reason = f"CTE列名引用错误: {error_msg}"
            else:
                # 如果没有_failed_cte_attempts，使用execution_results中的汇总错误
                failed_attempts = []
                cte_result = node.execution_results.get('cte_result', {})
                if not cte_result.get('valid', True):
                    error_msg = cte_result.get('error', '所有CTE变体都执行失败')
                    failed_attempts.append({
                        'cte': '',
                        'error': error_msg
                    })
        else:
            # 正常节点：向上遍历父节点链，收集所有失败尝试
            failed_attempts = []
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
                        
                        # 如果是列名错误（no such column），需要传递给后续节点用于修复
                        # 检测到 no such column 错误时就应该跳转到repair的部分，保留失败记录
                        
                        # 基于错误信息去重：如果错误信息已存在，比较CTE长度，保留较短的
                        if error in existing_errors:
                            # 查找已存在的相同错误信息的项
                            for idx, existing_item in enumerate(failed_attempts):
                                if existing_item.get('error', '').strip() == error:
                                    existing_cte = existing_item.get('cte', '').strip()
                                    # 如果新的CTE更短，替换（同时保留列名映射信息和修正标记）
                                    if len(cte) < len(existing_cte):
                                        # 如果新项有column_hint但旧项没有，保留新项；如果旧项有但新项没有，保留旧项的column_hint
                                        if 'column_hint' in attempt and 'column_hint' not in existing_item:
                                            # 保留旧项的requires_full_cte_chain标记（如果存在）
                                            if existing_item.get('requires_full_cte_chain', False):
                                                attempt['requires_full_cte_chain'] = True
                                                attempt['is_cte_column_error'] = existing_item.get('is_cte_column_error', False)
                                            failed_attempts[idx] = attempt
                                        elif 'column_hint' not in attempt and 'column_hint' in existing_item:
                                            # 保留旧项的column_hint，但更新其他字段
                                            attempt['column_hint'] = existing_item['column_hint']
                                            attempt['column_name'] = existing_item.get('column_name')
                                            attempt['column_tables'] = existing_item.get('column_tables')
                                            # 保留旧项的requires_full_cte_chain标记（如果存在）
                                            if existing_item.get('requires_full_cte_chain', False):
                                                attempt['requires_full_cte_chain'] = True
                                                attempt['is_cte_column_error'] = existing_item.get('is_cte_column_error', False)
                                            failed_attempts[idx] = attempt
                                        else:
                                            # 保留旧项的requires_full_cte_chain标记（如果存在）
                                            if existing_item.get('requires_full_cte_chain', False):
                                                attempt['requires_full_cte_chain'] = True
                                                attempt['is_cte_column_error'] = existing_item.get('is_cte_column_error', False)
                                            failed_attempts[idx] = attempt
                                    else:
                                        # 如果新CTE更长，但新项有requires_full_cte_chain标记，保留新项
                                        if attempt.get('requires_full_cte_chain', False) and not existing_item.get('requires_full_cte_chain', False):
                                            failed_attempts[idx] = attempt
                                    break
                        else:
                            # 如果错误信息不存在，直接添加
                            failed_attempts.append(attempt)
                            existing_errors.add(error)
                        
                        # 检查是否需要重新生成完整CTE链
                        if attempt.get('requires_full_cte_chain', False):
                            requires_full_cte_chain = True
                            if not repair_reason:
                                error_msg = attempt.get('error', '').strip()
                                column_name = attempt.get('column_name', '')
                                if column_name:
                                    repair_reason = f"CTE列名引用错误: {column_name} ({error_msg})"
                                else:
                                    repair_reason = f"CTE列名引用错误: {error_msg}"
                
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

        # mcts_v4 节点内迭代：优先使用 M_verify 反馈作为失败提示
        if failed_attempts_v4:
            failed_attempts = failed_attempts_v4[:5]
        
        # 限制失败信息数量，避免prompt过长
        failed_attempts = failed_attempts[:5]  # 最多保留5个失败尝试
        
        
        # 将修正信息存储到节点中，用于后续标记修正后的CTE
        # 如果检测到CTE列名引用错误，标记需要完整修复（失败节点被rollout到时会触发修复prompt）
        if requires_full_cte_chain:
            node._requires_full_cte_chain = True
            node._repair_reason = repair_reason
        else:
            node._requires_full_cte_chain = False
            node._repair_reason = None

        self._pending_diverse_expand_trace = None
        if self.use_decompose_flow and diverse_prompt_enabled():
            preceding = self.cte_generator._get_preceding_cte_info(node)
            used = self.cte_generator._get_used_cte_names(node)
            ctes, trace = generate_diverse_mode_c(
                node=node,
                llm_config=self.llm_config,
                extract_fn=self.cte_generator._extract_cte_from_response,
                n_per_call=diverse_n(),
                preceding_cte_info=preceding,
                used_cte_names=used,
                temperatures=diverse_temps(),
            )
            self._pending_diverse_expand_trace = trace
            if len(ctes) >= 2:
                node.additional_context = original_additional_context
                return ctes
            trace["diverse_fallback"] = True
            trace["diverse_fallback_reason"] = (
                trace.get("diverse_fallback_reason") or f"revert_to_temp:too_few_ctes:{len(ctes)}"
            )
            logger.info("diverse_mode_c revert_to_temp: %s", trace["diverse_fallback_reason"])
           
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
    
