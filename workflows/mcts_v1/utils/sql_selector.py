"""
SQL选择策略

包含不同的SQL选择策略
"""

from typing import Dict, List, Any, Optional


class SQLSelector:
    """SQL选择策略工具类"""
    
    @staticmethod
    def select_by_highest_reward(rollout_stats_list: List[Dict[str, Any]]) -> str:
        """
        策略：选择reward最高的rollout的selected_sql（原始版本）
        
        选择逻辑：
        1. 只从每个rollout的selected_sql中选择
        2. 选择reward最高的rollout的selected_sql
        3. 如果reward相同（很少发生），选择sql_bucket_count最大的rollout的selected_sql
        
        Args:
            rollout_stats_list: 所有rollout的统计信息列表，包含reward、sql_bucket_count、selected_sql
            
        Returns:
            最佳 SQL 字符串，如果找不到则返回空字符串
        """
        if not rollout_stats_list:
            print("[Selection] ⚠️ 没有rollout_stats，无法选择SQL")
            return ""
        
        print("[Selection] 使用策略：选择reward最高的rollout的selected_sql（原始版本）")
        
        # 只从每个rollout的selected_sql中选择
        candidate_rollouts = []
        
        for rollout_stats in rollout_stats_list:
            selected_sql = rollout_stats.get('selected_sql')
            if not selected_sql:
                continue
            
            rollout_reward = rollout_stats.get('reward', 0.0)
            sql_bucket_count = rollout_stats.get('sql_bucket_count', 0)
            rollout_id = rollout_stats.get('rollout_id', 0)
            
            candidate_rollouts.append({
                'sql': selected_sql,
                'reward': rollout_reward,
                'sql_bucket_count': sql_bucket_count,
                'rollout_id': rollout_id,
                'rollout_stats': rollout_stats
            })
        
        if not candidate_rollouts:
            print("[Selection] ❌ 未找到有效的selected_sql，无法选择")
            return ""
        
        # 找到最高reward
        max_reward = max(c.get('reward', 0.0) for c in candidate_rollouts)
        
        # 收集所有具有最高reward的rollout
        top_reward_rollouts = [
            c for c in candidate_rollouts 
            if abs(c.get('reward', 0.0) - max_reward) < 1e-6
        ]
        
        if not top_reward_rollouts:
            print("[Selection] ❌ 未找到有效的SQL，无法选择")
            return ""
        
        # 如果只有一个最高reward的rollout，直接返回
        # 如果有多个rollout具有相同最高reward（很少发生），选择第一个（或sql_bucket_count最大的）
        if len(top_reward_rollouts) == 1:
            best_rollout = top_reward_rollouts[0]
        else:
            # 多个rollout具有相同最高reward，选择sql_bucket_count最大的
            # 注意：如果total_variants相同，sql_bucket_count也会相同，这个判断通常不会改变结果
            print(f"[Selection] 发现 {len(top_reward_rollouts)} 个rollout具有相同最高reward {max_reward:.4f}，选择sql_bucket_count最大的...")
            best_rollout = max(top_reward_rollouts, key=lambda x: x.get('sql_bucket_count', 0))
            max_sql_bucket = best_rollout.get('sql_bucket_count', 0)
            print(f"[Selection] 💡 基于sql_bucket_count选择：sql_bucket_count={max_sql_bucket}")
        
        # 返回最佳SQL
        selected_sql = best_rollout.get('sql', '').strip()
        if selected_sql:
            rollout_id = best_rollout.get('rollout_id', '?')
            max_sql_bucket = best_rollout.get('sql_bucket_count', 0)
            print(f"[Selection] ✅ 选择最高reward的rollout的selected_sql (reward={max_reward:.4f}, sql_bucket_count={max_sql_bucket}, rollout_id={rollout_id})")
            return selected_sql
        
        print("[Selection] ❌ 未找到有效的SQL，无法选择")
        return ""
    
    @staticmethod
    def _has_unnecessary_aggregation(sql: str) -> bool:
        """
        检查SQL是否包含可能不必要的聚合函数（MAX/MIN），
        用于在相同reward和sql_bucket_count时，优先选择不使用聚合的SQL
        
        Args:
            sql: SQL字符串
            
        Returns:
            如果包含可能不必要的聚合函数返回True
        """
        if not sql:
            return False
        
        sql_upper = sql.upper()
        # 检查是否包含MAX或MIN聚合函数
        # 简单检查：如果包含MAX(...)或MIN(...)模式
        import re
        # 匹配 MAX( 或 MIN( 模式
        has_max_min = bool(re.search(r'\b(MAX|MIN)\s*\(', sql_upper))
        
        return has_max_min
    
    @staticmethod
    def _calculate_avg_reward(rollout_stats: Dict[str, Any]) -> float:
        """
        计算rollout中所有SQL变体的平均奖励（基于所有bucket的加权平均）
        
        对于每个SQL变体，如果它的结果属于某个bucket，给予该bucket的权重分数
        （bucket计数/总变体数）。然后对所有SQL变体的分数求平均。
        
        这样，如果一个rollout的所有SQL变体都返回相同结果（都在最佳bucket），
        平均奖励就是1.0。如果SQL变体分散在不同bucket，平均奖励会较低。
        
        Args:
            rollout_stats: rollout统计信息，包含all_sql_variants和result_buckets
            
        Returns:
            平均奖励值（0.0到1.0之间）
        """
        all_sql_variants = rollout_stats.get('all_sql_variants', [])
        result_buckets = rollout_stats.get('result_buckets', {})
        
        if not all_sql_variants or not result_buckets:
            # 如果没有SQL变体或结果分桶，返回rollout的总体reward
            return rollout_stats.get('reward', 0.0)
        
        total_variants = len(all_sql_variants)
        if total_variants == 0:
            return 0.0
        
        # 计算每个bucket的权重分数 = bucket计数 / 总变体数
        bucket_weights = {}
        for bucket_signature, bucket_count in result_buckets.items():
            bucket_weights[bucket_signature] = bucket_count / float(total_variants)
        
        # 对于每个SQL变体，计算它的奖励分数
        total_score = 0.0
        valid_count = 0
        
        for sql_info in all_sql_variants:
            if sql_info.get('valid', False):
                sql_signature = sql_info.get('result_signature')
                if sql_signature and sql_signature in bucket_weights:
                    # 该SQL变体的奖励 = 它所属bucket的权重分数
                    total_score += bucket_weights[sql_signature]
                else:
                    # 如果SQL变体有效但没有结果签名，给予0分
                    total_score += 0.0
                valid_count += 1
            else:
                # 无效的SQL变体给予0分
                total_score += 0.0
                valid_count += 1
        
        # 平均奖励 = 所有SQL变体的分数总和 / 总变体数
        if valid_count == 0:
            return 0.0
        
        avg_reward = total_score / float(total_variants)
        
        return avg_reward

