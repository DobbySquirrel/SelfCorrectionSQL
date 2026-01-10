"""
简化版单Rollout工作流

核心功能：
1. CTE生成 -> 执行 -> 选择 -> 继续到END
2. 最终生成完整SQL
3. 计算自一致性奖励

"""

import re
import json
from typing import Dict, List, Optional, Tuple, Any
import time as _time_for_timing
from .agents.cte_generator import CTEGenerator
from .agents.complete_sql_generator import CompleteSQLGenerator
from .agents.sql_executor import SQLExecutor
from .agents.probe_generator import ProbeGenerator
from .agents.strategy import build_strategy_injection_text, GLOBAL_STRATEGY_CONFIG, StrategyMode
from .utils.mcts_helpers import MCTSUtils
from .utils.sql_exec_helpers import execute_sqls_parallel
from core.database_connector import DatabaseConnector


class SimpleRolloutWorkflow:
    """简化版单Rollout工作流"""
    
    def __init__(self, llm_config: Dict, db_connector: DatabaseConnector, max_workers: int = None, enable_probe: bool = False, strategy_mode: Optional[str] = None):
        """
        初始化简化工作流
        
        Args:
            llm_config: LLM配置
            db_connector: 数据库连接器
            max_workers: 最大并行工作线程数
            enable_probe: 是否启用Probe步骤（在生成CTE前先执行probe探测），默认为True
            strategy_mode: 策略模式（FORCE_S1/S2/S3, NONE, LLM_PICK_ONCE），如果提供则覆盖全局配置
        """
        self.llm_config = llm_config
        self.db_connector = db_connector
        self.max_depth = 8  # CTE链最大深度
        self.max_cte_nodes_per_iteration = 5  # 每次生成的CTE变体数量
        self.num_sql_variants = 5  # 最终生成的SQL变体数量
        self.max_workers = max_workers
        self.enable_probe = enable_probe  # Probe功能开关
        
        # 策略模式配置
        if strategy_mode:
            self.strategy_mode: StrategyMode = strategy_mode  # type: ignore
        else:
            self.strategy_mode: StrategyMode = GLOBAL_STRATEGY_CONFIG.mode  # type: ignore
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
        # 初始化组件
        # 只有在启用probe时才初始化probe_generator（节省资源）
        if self.enable_probe:
            self.probe_generator = ProbeGenerator(llm_config, db_connector, multi_model_configs=multi_model_configs)
        else:
            self.probe_generator = None
        self.cte_generator = CTEGenerator(llm_config, max_depth=self.max_depth, multi_model_configs=multi_model_configs)
        self.complete_sql_generator = CompleteSQLGenerator(llm_config, multi_model_configs=multi_model_configs)
        self.sql_executor = SQLExecutor(db_connector)
        # 超时配置
        self.sql_timeout_s = 40
        self.cte_probe_timeout_s = 30
        self.probe_timeout_s = 30  # Probe SQL执行超时时间
        
        # 计时统计
        self._timing = {
            'total_s': 0.0,
            'cte_gen_s': 0.0,
            'sql_gen_s': 0.0,
            'db_exec_s': 0.0,
        }

    def solve(self, question: str, schema_info: str, additional_context: str = "") -> Dict[str, Any]:
        """
        执行单条Rollout
        
        Args:
            question: 自然语言问题
            schema_info: 数据库模式信息
            additional_context: 额外上下文
            
        Returns:
            包含最优SQL和统计信息的字典
        """
        solve_start_ts = _time_for_timing.time()
        
        # 重置计时统计
        for k in list(self._timing.keys()):
            self._timing[k] = 0.0
        
        print("\n=== 开始单Rollout流程 ===")
        
        # 执行单条Rollout
        reward, selected_sql, rollout_stats = self._execute_single_rollout(
            question=question,
            schema_info=schema_info,
            additional_context=additional_context
        )
        
        self._timing['total_s'] = max(0.0, _time_for_timing.time() - solve_start_ts)
        
        return {
            'optimal_sql': selected_sql if selected_sql else "",
            'reward': reward,
            'statistics': {
                'timing': self._timing,
                'rollout_stats': rollout_stats
            },
            'rollout_stats': [rollout_stats]
        }
    
    def _execute_single_rollout(self, question: str, schema_info: str, additional_context: str = "") -> Tuple[float, Optional[str], Dict[str, Any]]:
        """
        执行单条Rollout：CTE生成 -> 执行 -> 选择 -> 继续到END -> 生成完整SQL
        
        Returns:
            (奖励值, 选择的SQL, 统计信息字典)
        """
        # 构建简单的节点结构（不使用MCTS树）
        current_context = {
            'question': question,
            'schema_info': schema_info,
            'additional_context': additional_context,
            'cte_chain': [],  # CTE链
            'cte_execution_results': {},  # 存储每个CTE的执行结果，格式: {cte_index: execution_result}
            'depth': 0
        }
        
        # 1. 逐步生成CTE链，直到遇到<END>
        cte_path = []
        cte_bucket_counts = []
        cte_depths = []
        probe_results_text = ""  # 存储probe结果文本
        
        while current_context['depth'] < self.max_depth:
            print(f"\n[CTE生成] 深度 {current_context['depth'] + 1}/{self.max_depth}")
            
            # 在第一次（depth=0）时执行Probe步骤（如果启用）
            if self.enable_probe and current_context['depth'] == 0 and not probe_results_text:
                print(f"\n[Probe步骤] 开始执行Probe探测...")
                _probe_t0 = _time_for_timing.time()
                probe_data = self.probe_generator.generate_and_execute_probes(
                    question=question,
                    schema_info=schema_info,
                    additional_context=additional_context,
                    timeout_s=self.probe_timeout_s
                )
                self._timing['cte_gen_s'] += (_time_for_timing.time() - _probe_t0)  # 将probe时间计入CTE生成时间
                
                # 格式化probe结果
                probe_results_text = self.probe_generator.format_probe_results_for_cte(probe_data)
                print(f"[Probe步骤] 完成，{probe_data.get('summary', '')}")
                if probe_results_text:
                    print(f"[Probe步骤] Probe结果已准备，将在CTE生成时使用")
            elif not self.enable_probe and current_context['depth'] == 0:
                print(f"\n[Probe步骤] Probe功能已禁用，跳过Probe探测步骤")
            
            # 生成CTE变体（传入probe结果）
            _cte_t0 = _time_for_timing.time()
            cte_variants = self._generate_cte_variants(current_context, probe_results_text=probe_results_text)
            self._timing['cte_gen_s'] += (_time_for_timing.time() - _cte_t0)
            
            # 处理 LLM_PICK_ONCE 模式的策略选择（仅在根节点且未选择策略时）
            # 先单独选择策略（JSON格式），然后再生成CTE
            if self.strategy_mode == "LLM_PICK_ONCE" and current_context['depth'] == 0 and not current_context.get("picked_strategy"):
                # 单独调用LLM选择策略（JSON格式）
                print(f"\n[策略选择] ========== 开始单独选择策略 ==========")
                from .agents.strategy import build_strategy_selection_prompt, extract_strategy_from_json
                
                strategy_prompt = build_strategy_selection_prompt(
                    question=current_context['question'],
                    schema_info=current_context['schema_info'],
                    additional_context=current_context.get('additional_context', '')
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
                
                # 从JSON响应中提取策略和thought
                picked_strategy, picked_strategy_thought = extract_strategy_from_json(strategy_response)
                
                if picked_strategy and picked_strategy in ("S1", "S2", "S3"):
                    current_context["picked_strategy"] = picked_strategy
                    current_context["picked_strategy_thought"] = picked_strategy_thought
                    if picked_strategy == "S4":
                        if picked_strategy_thought:
                    print(f"\n✅ [策略选择] 成功选择策略: {picked_strategy}")
                            print(f"[策略选择] 自定义策略规划: {picked_strategy_thought}")
                        else:
                            print(f"\n⚠️ [策略选择] 选择了S4但未提供thought，使用默认策略 S2")
                            current_context["picked_strategy"] = "S2"
                            current_context["picked_strategy_thought"] = None
                            picked_strategy = "S2"
                            picked_strategy_thought = None
                    else:
                        print(f"\n✅ [策略选择] 成功选择策略: {picked_strategy}")
                        if picked_strategy_thought:
                            print(f"[策略选择] 策略规划: {picked_strategy_thought}")
                else:
                    print(f"\n⚠️ [策略选择] 未能从JSON中提取到有效策略，使用默认策略 S2")
                    current_context["picked_strategy"] = "S2"
                    current_context["picked_strategy_thought"] = None
                    picked_strategy = "S2"
                    picked_strategy_thought = None
                
                print(f"[策略选择] ========== 策略选择完成 ==========\n")
            
            if not cte_variants:
                print(f"[CTE生成] 未生成任何CTE变体，停止扩展")
                break
            
            print(f"[CTE生成] 生成了 {len(cte_variants)} 个CTE变体")
            
            # 执行并去重CTE变体
            unique_cte_variants, failed_info = self._deduplicate_cte_variants(cte_variants, current_context)
            
            if not unique_cte_variants:
                print(f"[CTE生成] ⚠️ 所有CTE变体执行失败，停止扩展")
                break
            
            # 选择最佳CTE（基于桶计数）
            best_cte_info = self._select_best_cte(unique_cte_variants)
            
            if not best_cte_info:
                break
            
            selected_cte = best_cte_info['cte']
            bucket_count = best_cte_info['count']
            
            print(f"[CTE选择] 选择CTE (bucket_count={bucket_count}):")
            if selected_cte == "<END>":
                print(f"  <END>")
                cte_path.append("<END>")
                cte_bucket_counts.append(bucket_count)
                cte_depths.append(current_context['depth'] + 1)
                break
            else:
                print(f"  {selected_cte[:200]}...")
                cte_path.append(selected_cte)
                cte_bucket_counts.append(bucket_count)
                cte_depths.append(current_context['depth'] + 1)
                
                # 存储执行结果到context中（用于后续节点构建）
                execution_result = best_cte_info.get('execution_result', {})
                cte_index = len(current_context['cte_chain'])
                current_context['cte_execution_results'][cte_index] = execution_result
                
                # 更新context，继续下一层
                current_context['cte_chain'].append(selected_cte)
                current_context['depth'] += 1
        
        # 2. 基于CTE链生成完整SQL
        print(f"\n[SQL生成] 基于CTE链生成完整SQL (链长度={len(cte_path)})")
        
        # 构建叶节点结构（用于SQL生成）
        leaf_node = self._build_leaf_node(question, schema_info, additional_context, cte_path, current_context.get('cte_execution_results', {}))
        
        # 生成多个SQL变体
        _sqlgen_t0 = _time_for_timing.time()
        sql_variants = self.complete_sql_generator.generate_multiple_complete_sqls_parallel(
            leaf_node,
            num_variants=self.num_sql_variants,
            max_workers=self.max_workers
        )
        self._timing['sql_gen_s'] += (_time_for_timing.time() - _sqlgen_t0)
        
        if not sql_variants:
            return 0.0, None, {
                'cte_path': cte_path,
                'cte_bucket_counts': cte_bucket_counts,
                'cte_depths': cte_depths,
                'sql_bucket_count': 0,
                'sql_total_variants': 0,
                'all_sql_variants': [],
                'result_buckets': {},
                'valid_count': 0,
                'error_reason': 'SQL生成失败：未生成任何SQL变体'
            }
        
        print(f"[SQL生成] 生成了 {len(sql_variants)} 个SQL变体")
        
        # 3. 执行SQL并计算奖励
        reward, selected_sql, sql_stats = self._execute_and_evaluate_sqls(sql_variants)
        
        # 构建统计信息
        rollout_stats = {
            'reward': reward,
            'cte_path': cte_path,
            'cte_bucket_counts': cte_bucket_counts,
            'cte_depths': cte_depths,
            'sql_bucket_count': sql_stats.get('sql_bucket_count', 0),
            'sql_total_variants': len(sql_variants),
            'selected_sql': selected_sql,
            'all_sql_variants': sql_stats.get('all_sql_variants', []),
            'result_buckets': sql_stats.get('result_buckets', {}),
            'valid_count': sql_stats.get('valid_count', 0),
            'error_reason': sql_stats.get('error_reason', None)
        }
        
        return reward, selected_sql, rollout_stats
    
    def _generate_cte_variants(self, context: Dict[str, Any], probe_results_text: str = "") -> List[str]:
        """
        生成CTE变体
        
        Args:
            context: 当前上下文
            probe_results_text: Probe结果文本（用于传递给CTE生成器）
        """
        # 获取策略相关参数
        picked_strategy = context.get("picked_strategy", None)
        picked_strategy_thought = context.get("picked_strategy_thought", None)
        depth = len(context['cte_chain'])
        
        # 生成策略注入文本
        strategy_text = build_strategy_injection_text(
            mode=self.strategy_mode,
            picked_strategy=picked_strategy,
            picked_strategy_thought=picked_strategy_thought,
            depth=depth
        )
        
        # 构建简单的节点对象（仅用于CTE生成）
        class SimpleNode:
            def __init__(self, question, schema_info, additional_context, cte_chain, cte="", parent=None, depth=None, probe_results_text="", execution_result=None, strategy_text=""):
                self.question = question
                self.schema_info = schema_info
                # 通用拼接：additional_context + strategy_text + probe_results_text
                extra_blocks = []
                if additional_context:
                    extra_blocks.append(additional_context)
                if strategy_text:
                    extra_blocks.append(strategy_text)
                if probe_results_text:
                    extra_blocks.append(probe_results_text)
                self.additional_context = "\n\n".join(extra_blocks)
                self.cte_chain = cte_chain
                self.parent = parent
                # 添加CTE和执行结果属性（cte_generator需要）
                self.cte = cte  # 当前节点的CTE
                # 使用传入的执行结果，如果没有则使用默认值
                if execution_result is not None:
                    self.execution_results = {'cte_result': execution_result}
                else:
                    self.execution_results = {'cte_result': {'valid': True}} if cte else {}  # 执行结果字典
                # depth：如果有parent则基于parent计算，否则基于cte_chain长度
                if depth is not None:
                    self.depth = depth
                elif parent is not None:
                    self.depth = parent.depth + 1
                else:
                    self.depth = len(cte_chain)
        
        # 构建parent链（用于cte_generator获取前序CTE）
        # cte_generator会从当前节点开始，沿着parent链向上遍历收集CTE
        current_node = None
        cte_execution_results = context.get('cte_execution_results', {})
        for i, cte in enumerate(context['cte_chain']):
            # 获取该CTE的执行结果（如果存在）
            execution_result = cte_execution_results.get(i, {})
            current_node = SimpleNode(
                context['question'],
                context['schema_info'],
                context['additional_context'],
                context['cte_chain'][:i+1],  # 到当前位置的CTE链
                cte=cte,
                parent=current_node,
                depth=i,  # depth从0开始（根节点为0）
                probe_results_text="",  # parent节点不需要probe结果（只在第一次生成时使用）
                execution_result=execution_result,  # 传递真实的执行结果
                strategy_text=""  # parent节点不需要策略文本（只在当前节点需要）
            )
        
        # 当前节点（要生成下一个CTE的节点）
        # 只在第一次（depth=0）时传递probe结果
        use_probe = (len(context['cte_chain']) == 0) and probe_results_text
        node = SimpleNode(
            context['question'],
            context['schema_info'],
            context['additional_context'],
            context['cte_chain'],
            cte="",  # 当前节点还没有CTE（要生成）
            parent=current_node,  # 连接到parent链
            depth=len(context['cte_chain']),  # 当前深度
            probe_results_text=probe_results_text if use_probe else "",
            strategy_text=strategy_text  # 传递策略文本
        )
        
        return self.cte_generator.generate_multiple_cte_variants(
            node,
            num_variants=self.max_cte_nodes_per_iteration,
            failed_attempts=[]  # 简化版不处理失败重试
        )
    
    def _extract_strategy_and_clean_cte(self, cte: str) -> Tuple[Optional[str], str]:
        """
        从CTE文本中提取策略并清洗
        
        支持格式: <S1> ... ```sql ... ```
        
        Args:
            cte: CTE文本，可能包含策略标签和SQL代码块
        
        Returns:
            (策略字符串或None, 清洗后的CTE文本)
        """
        if not cte:
            return None, cte
        
        cte = cte.strip()
        
        # 解析格式: <S1> ... ```sql ... ```
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
        
        return None, cte
    
    def _deduplicate_cte_variants(self, cte_variants: List[str], context: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        """
        对CTE变体进行去重/分桶
        
        Returns:
            (去重后的CTE列表, 失败信息列表)
        """
        if not cte_variants:
            return [], []
        
        cte_variants = [cte for cte in cte_variants if cte is not None]
        if not cte_variants:
            return [], []
        
        buckets = {}
        failed_info = []
        
        # 先处理 <END>
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
                    buckets["<END>"]['variants'].append(cte)
        
        # 执行非<END>的CTE
        non_end_ctes = [cte for cte in cte_variants if cte != "<END>"]
        
        def worker(one_cte: str):
            # 构建一个简单的node对象用于build_executable_cte_sql
            # build_executable_cte_sql会从node.parent链中提取历史CTE
            class SimpleNodeForCTE:
                def __init__(self, cte_chain, current_cte):
                    self.cte = current_cte
                    # 构建parent链：从cte_chain构建
                    if cte_chain:
                        # 创建最后一个历史CTE的节点
                        last_cte = cte_chain[-1]
                        self.parent = SimpleNodeForCTE(cte_chain[:-1], last_cte)
                    else:
                        self.parent = None
            
            current_node = SimpleNodeForCTE(context['cte_chain'], one_cte)
            
            # 构建可执行SQL
            exec_sql = self.sql_executor.build_executable_cte_sql(current_node, one_cte)
            
            # 调试：打印构建的SQL（用于排查空结果问题）
            is_first_depth = (context['depth'] == 0)  # 只在第一次时打印详细调试信息
            if is_first_depth:
                print(f"[CTE执行] 原始CTE: {one_cte[:300]}...")
                print(f"[CTE执行] 构建的完整SQL: {exec_sql[:500]}...")
            
            # 调试：检查exec_sql是否为空
            if not exec_sql or not exec_sql.strip():
                error_msg = f"构建的可执行SQL为空。原始CTE: {one_cte[:200] if one_cte else 'None'}..."
                print(f"[CTE执行] ⚠️ {error_msg}")
                return one_cte, {'valid': False, 'query_result': [], 'error': error_msg}, None, {'cte': one_cte, 'error': error_msg}
            
            # 添加LIMIT 5用于探针执行
            if exec_sql and not re.search(r'\bLIMIT\s+\d+', exec_sql, re.IGNORECASE):
                if 'SELECT * FROM' in exec_sql.upper():
                    exec_sql = re.sub(r'(SELECT \* FROM[^;]+)(;?)$', r'\1 LIMIT 5\2', exec_sql, flags=re.IGNORECASE | re.DOTALL)
            
            # 执行
            res = self.sql_executor._execute_single_query(exec_sql, timeout_s=self.cte_probe_timeout_s)
            bucket_key = MCTSUtils.create_result_signature(res)
            
            # 在第一次执行时，总是打印详细调试信息（无论成功还是失败）
            if is_first_depth:
                query_result = res.get('query_result', [])
                result_count = len(query_result) if isinstance(query_result, list) else 0
                print(f"\n[CTE执行] ========== 深度0详细调试 ==========")
                print(f"[CTE执行] 原始CTE:\n{one_cte}")
                print(f"[CTE执行] 构建的完整SQL:\n{exec_sql}")
                print(f"[CTE执行] 执行结果: valid={res.get('valid', False)}, error={res.get('error', None)}")
                print(f"[CTE执行] 查询结果行数: {result_count}")
                print(f"[CTE执行] bucket_key: {bucket_key}")
                
                # 如果返回空结果，尝试直接执行测试SQL看看是否有数据
                if 'satscores' in exec_sql.lower() and (bucket_key == "empty_result" or result_count == 0):
                    print(f"[CTE执行] ⚠️ 检测到空结果，开始诊断测试...")
                    # 测试1: 直接查询表
                    test_sql1 = "SELECT COUNT(*) as cnt FROM satscores"
                    test_res1 = self.sql_executor._execute_single_query(test_sql1, timeout_s=5)
                    if test_res1.get('valid', False):
                        test_data1 = test_res1.get('query_result', [])
                        print(f"[CTE执行] 调试1: SELECT COUNT(*) FROM satscores, 结果: {test_data1}")
                    else:
                        print(f"[CTE执行] 调试1: 失败 - {test_res1.get('error', 'unknown')}")
                    
                    # 测试2: 查询列是否存在（不使用反引号）
                    test_sql2 = "SELECT cds, AvgScrMath FROM satscores LIMIT 5"
                    test_res2 = self.sql_executor._execute_single_query(test_sql2, timeout_s=5)
                    if test_res2.get('valid', False):
                        test_data2 = test_res2.get('query_result', [])
                        print(f"[CTE执行] 调试2: SELECT cds, AvgScrMath FROM satscores LIMIT 5, 结果行数: {len(test_data2) if isinstance(test_data2, list) else 0}")
                        if test_data2:
                            print(f"[CTE执行] 调试2: 前3行数据: {test_data2[:3]}")
                    else:
                        print(f"[CTE执行] 调试2: 失败 - {test_res2.get('error', 'unknown')}")
                    
                    # 测试3: 使用反引号（和CTE中一样）
                    test_sql3 = "SELECT cds, `AvgScrMath` FROM satscores LIMIT 5"
                    test_res3 = self.sql_executor._execute_single_query(test_sql3, timeout_s=5)
                    if test_res3.get('valid', False):
                        test_data3 = test_res3.get('query_result', [])
                        print(f"[CTE执行] 调试3: SELECT cds, `AvgScrMath` FROM satscores LIMIT 5, 结果行数: {len(test_data3) if isinstance(test_data3, list) else 0}")
                        if test_data3:
                            print(f"[CTE执行] 调试3: 前3行数据: {test_data3[:3]}")
                    else:
                        print(f"[CTE执行] 调试3: 失败 - {test_res3.get('error', 'unknown')}")
                    
                    # 测试4: 使用表别名（和CTE中一样）
                    test_sql4 = "SELECT ss.cds, ss.`AvgScrMath` FROM satscores AS ss LIMIT 5"
                    test_res4 = self.sql_executor._execute_single_query(test_sql4, timeout_s=5)
                    if test_res4.get('valid', False):
                        test_data4 = test_res4.get('query_result', [])
                        print(f"[CTE执行] 调试4: SELECT ss.cds, ss.`AvgScrMath` FROM satscores AS ss LIMIT 5, 结果行数: {len(test_data4) if isinstance(test_data4, list) else 0}")
                        if test_data4:
                            print(f"[CTE执行] 调试4: 前3行数据: {test_data4[:3]}")
                    else:
                        print(f"[CTE执行] 调试4: 失败 - {test_res4.get('error', 'unknown')}")
                    
                    # 测试5: 直接执行CTE的SQL（不使用WITH）
                    test_sql5 = "SELECT ss.cds, ss.`AvgScrMath` FROM satscores AS ss LIMIT 5"
                    test_res5 = self.sql_executor._execute_single_query(test_sql5, timeout_s=5)
                    if test_res5.get('valid', False):
                        test_data5 = test_res5.get('query_result', [])
                        print(f"[CTE执行] 调试5: 直接执行SELECT（无WITH）, 结果行数: {len(test_data5) if isinstance(test_data5, list) else 0}")
                        if test_data5:
                            print(f"[CTE执行] 调试5: 前3行数据: {test_data5[:3]}")
                    else:
                        print(f"[CTE执行] 调试5: 失败 - {test_res5.get('error', 'unknown')}")
                
                print(f"[CTE执行] ======================================\n")
            
            if bucket_key.startswith("invalid_"):
                error_msg = res.get('error', '执行失败或超时') if not res.get('valid', False) else "执行失败或超时"
                # 调试：打印详细的错误信息
                if not is_first_depth:  # 如果已经在上面打印了，这里就不重复打印
                    print(f"[CTE执行] ❌ CTE执行失败: {error_msg[:200]}")
                    print(f"[CTE执行] 原始CTE: {one_cte[:200] if one_cte else 'None'}...")
                    print(f"[CTE执行] 构建的SQL: {exec_sql[:500] if exec_sql else 'None'}...")
                return one_cte, res, None, {'cte': one_cte, 'error': error_msg}
            elif bucket_key == "empty_result":
                # 调试：打印空结果信息
                if not is_first_depth:  # 如果已经在上面打印了，这里就不重复打印
                    print(f"[CTE执行] ⚠️ CTE执行成功但返回空结果")
                    print(f"[CTE执行] 原始CTE: {one_cte[:200] if one_cte else 'None'}...")
            
            return one_cte, res, bucket_key, None
        
        # 并行执行
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        _exec_t0 = _time_for_timing.time()
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(worker, c) for c in non_end_ctes]
            for fut in as_completed(futures):
                cte_used, cte_result, bucket_key, failed_item = fut.result()
                
                if failed_item:
                    failed_info.append(failed_item)
                    continue
                
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
        
        self._timing['db_exec_s'] += (_time_for_timing.time() - _exec_t0)
        
        return list(buckets.values()), failed_info
    
    def _select_best_cte(self, unique_cte_variants: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """选择最佳CTE（基于桶计数）"""
        if not unique_cte_variants:
            return None
        
        # 优先选择非<END>的CTE
        non_end_variants = [v for v in unique_cte_variants if v['cte'] != "<END>"]
        end_variants = [v for v in unique_cte_variants if v['cte'] == "<END>"]
        
        if non_end_variants:
            # 选择桶计数最大的
            best = max(non_end_variants, key=lambda x: x.get('count', 0))
            return best
        elif end_variants:
            # 只有<END>可选
            return end_variants[0]
        
        return None
    
    def _build_leaf_node(self, question: str, schema_info: str, additional_context: str, cte_path: List[str], cte_execution_results: Dict[int, Dict] = None):
        """
        构建叶节点（用于SQL生成）
        
        Args:
            question: 问题
            schema_info: Schema信息
            additional_context: 额外上下文
            cte_path: CTE路径列表
            cte_execution_results: CTE执行结果字典，格式: {cte_index: execution_result}
        """
        if cte_execution_results is None:
            cte_execution_results = {}
        
        class SimpleLeafNode:
            def __init__(self, question, schema_info, additional_context, cte_path, cte="", parent=None, depth=None, execution_result=None):
                self.question = question
                self.schema_info = schema_info
                self.additional_context = additional_context
                self.cte_chain = [cte for cte in cte_path if cte != "<END>"]
                self.is_terminal = True
                self.parent = parent
                # 添加CTE和执行结果属性（complete_sql_generator需要）
                self.cte = cte  # 当前节点的CTE（叶节点通常为空或<END>）
                # 使用传入的执行结果，如果没有则使用默认值
                if execution_result is not None:
                    self.execution_results = {'cte_result': execution_result}
                else:
                    self.execution_results = {'cte_result': {'valid': True}} if cte else {}  # 执行结果字典
                # depth：如果有parent则基于parent计算，否则基于cte_chain长度
                if depth is not None:
                    self.depth = depth
                elif parent is not None:
                    self.depth = parent.depth + 1
                else:
                    self.depth = len(self.cte_chain)
        
        # 构建parent链（用于complete_sql_generator获取前序CTE）
        # complete_sql_generator会从当前节点开始，沿着parent链向上遍历收集CTE
        current_node = None
        cte_chain_filtered = [cte for cte in cte_path if cte != "<END>"]
        for i, cte in enumerate(cte_chain_filtered):
            # 获取该CTE的执行结果（如果存在）
            execution_result = cte_execution_results.get(i, None)
            current_node = SimpleLeafNode(
                question,
                schema_info,
                additional_context,
                cte_path,
                cte=cte,
                parent=current_node,
                depth=i,  # depth从0开始（根节点为0）
                execution_result=execution_result  # 传递真实的执行结果
            )
        
        # 叶节点（用于生成完整SQL）
        leaf_node = SimpleLeafNode(
            question,
            schema_info,
            additional_context,
            cte_path,
            cte="",  # 叶节点没有CTE（或可能是<END>）
            parent=current_node,  # 连接到parent链
            depth=len(cte_chain_filtered)  # 当前深度
        )
        
        return leaf_node
    
    def _execute_and_evaluate_sqls(self, sql_variants: List[str]) -> Tuple[float, Optional[str], Dict[str, Any]]:
        """执行SQL并计算奖励"""
        print(f"[SQL执行] 正在并行执行 {len(sql_variants)} 个SQL（超时={self.sql_timeout_s}s）...")
        # 并行执行
        exec_start = _time_for_timing.time()
        parallel_results = execute_sqls_parallel(
            self.db_connector, 
            sql_variants, 
            timeout_s=self.sql_timeout_s, 
            max_workers=self.max_workers
        )
        exec_elapsed = _time_for_timing.time() - exec_start
        self._timing['db_exec_s'] += exec_elapsed
        
        # 收集执行结果
        execution_results = []
        for (result, error) in parallel_results:
            if result is not None and not error:
                execution_results.append({'valid': True, 'query_result': result})
            else:
                error_msg = str(error) if error else 'unknown error'
                execution_results.append({'valid': False, 'error': error_msg})
        
        # 计算一致性奖励与分桶
        result_buckets, best_key = MCTSUtils.bucketize_valid_nonempty(execution_results)
        valid_count = sum(1 for r in execution_results if r.get('valid', False))
        sql_bucket_count = max(result_buckets.values()) if result_buckets else 0
        
        if valid_count == 0:
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
        
        # 建立sql与签名的映射
        sql_with_signatures = []
        signature_to_sql = {}
        signature_to_result = {}
        signature_to_column_order_sqls = {}
        
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
        
        # 处理平票
        if result_buckets:
            max_count = max(result_buckets.values())
            tied_keys = [k for k, v in result_buckets.items() if v == max_count]
            
            if len(tied_keys) > 1:
                def get_tiebreak_score(sig: str) -> Tuple[int, int, int]:
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
        
        # 计算奖励
        max_consistency = MCTSUtils.calculate_consistency_reward(result_buckets, len(sql_variants))
        reward = max_consistency
        
        # 选择SQL
        selected_sql = None
        if result_buckets and best_key:
            if best_key in signature_to_column_order_sqls:
                column_order_sqls = signature_to_column_order_sqls[best_key]
                column_order_counts = {col_order: len(sqls) for col_order, sqls in column_order_sqls.items()}
                max_count = max(column_order_counts.values()) if column_order_counts else 0
                most_common_column_orders = [col_order for col_order, count in column_order_counts.items() if count == max_count]
                
                if most_common_column_orders:
                    best_column_order = most_common_column_orders[0]
                    if best_column_order in column_order_sqls and column_order_sqls[best_column_order]:
                        selected_sql = column_order_sqls[best_column_order][0][0]
            
            if selected_sql is None:
                selected_sql = signature_to_sql.get(best_key, None)
                if selected_sql is None:
                    for sql, sig in sql_with_signatures:
                        if sig == best_key:
                            selected_sql = sql
                            break
        
        if selected_sql is None and sql_variants:
            for sql, res in zip(sql_variants, execution_results):
                if res.get('valid', False):
                    selected_sql = sql
                    break
            if selected_sql is None and len(sql_variants) > 0:
                selected_sql = sql_variants[0]
        
        print(f"[SQL执行] 奖励: {reward:.4f} (一致性: {sql_bucket_count}/{len(sql_variants)}, 通过率: {valid_count}/{len(sql_variants)})")
        
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
        
        if sql_bucket_count == 0:
            if not sql_variants:
                sql_stats['error_reason'] = 'SQL生成失败：未生成任何SQL变体'
            elif valid_count == 0:
                sql_stats['error_reason'] = f'所有SQL执行失败：{len(sql_variants)}个SQL变体全部执行失败'
            elif not result_buckets:
                sql_stats['error_reason'] = f'所有SQL返回空结果：{valid_count}个SQL执行成功但结果为空'
            else:
                sql_stats['error_reason'] = '未知原因'
        
        return reward, selected_sql, sql_stats
