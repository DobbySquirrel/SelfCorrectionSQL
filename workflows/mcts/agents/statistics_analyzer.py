"""
统计分析器智能体（轻量实现）
"""
from typing import Dict, List, Any
from ..utils.mcts_helpers import MCTSUtils


class StatisticsAnalyzer:
    def __init__(self):
        self.utils = MCTSUtils()

    def analyze(self, execution_results: Dict[str, Any], gold_result: List[Dict] = None,
                cte_ensemble: List[Dict[str, Any]] = None,
                final_sql_ensemble: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        cte_result = execution_results.get('cte_result', {})
        final_sql_result = execution_results.get('final_sql_result', {})
        reward = self.utils.calculate_reward(cte_result, final_sql_result, gold_result)

        # 多数投票共识度（不依赖gold）
        cte_consensus = 0.0
        final_consensus = 0.0
        if cte_ensemble:
            cte_consensus = self.utils.calculate_consensus(cte_ensemble)
        if final_sql_ensemble:
            final_consensus = self.utils.calculate_consensus(final_sql_ensemble)
        cte_sc = 0.0
        final_sc = 0.0
        if gold_result:
            if cte_result.get('valid') and cte_result.get('query_result'):
                cte_sc = self.utils.calculate_sc(cte_result['query_result'], gold_result)
            if final_sql_result.get('valid') and final_sql_result.get('query_result'):
                final_sc = self.utils.calculate_sc(final_sql_result['query_result'], gold_result)
        return {
            'reward': reward,
            'cte_sc': cte_sc,
            'final_sc': final_sc,
            'cte_consensus': cte_consensus,
            'final_consensus': final_consensus,
            'cte_valid': cte_result.get('valid', False),
            'final_sql_valid': final_sql_result.get('valid', False),
            'cte_result_count': len(cte_result.get('query_result', [])),
            'final_sql_result_count': len(final_sql_result.get('query_result', [])),
            'has_errors': bool(cte_result.get('error') or final_sql_result.get('error')),
            'execution_time': execution_results.get('execution_time', 0.0)
        }
