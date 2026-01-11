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
        策略：选择所有SQL中reward最高的SQL（改进版）
        
        选择逻辑：
        1. 从所有rollout的所有SQL变体中，找到reward最高的SQL
        2. 如果reward相同，选择sql_bucket_count最大的rollout中的SQL
        3. 如果都相同，优先选择CTE rollout（非快速路径）中的SQL
        
        Args:
            rollout_stats_list: 所有rollout的统计信息列表，包含reward、sql_bucket_count、selected_sql、all_sql_variants
            
        Returns:
            最佳 SQL 字符串，如果找不到则返回空字符串
        """
        if not rollout_stats_list:
            print("[Selection] ⚠️ 没有rollout_stats，无法选择SQL")
            return ""
        
        print("[Selection] 使用策略：选择所有SQL中reward最高的SQL（改进版）")
        
        # 【改进】从所有rollout的所有SQL变体中，找到reward最高的SQL
        # 候选SQL列表：[(sql, reward, rollout_stats, sql_info), ...]
        candidate_sqls = []
        
        for rollout_stats in rollout_stats_list:
            rollout_reward = rollout_stats.get('reward', 0.0)
            sql_bucket_count = rollout_stats.get('sql_bucket_count', 0)
            is_quick_path = rollout_stats.get('is_quick_path', False)
            rollout_id = rollout_stats.get('rollout_id', 0)
            
            # 1. 添加selected_sql
            selected_sql = rollout_stats.get('selected_sql')
            if selected_sql:
                candidate_sqls.append({
                    'sql': selected_sql,
                    'reward': rollout_reward,
                    'sql_bucket_count': sql_bucket_count,
                    'is_quick_path': is_quick_path,
                    'rollout_id': rollout_id,
                    'source': 'selected',
                    'rollout_stats': rollout_stats
                })
            
            # 2. 添加all_sql_variants中的所有有效SQL
            all_sql_variants = rollout_stats.get('all_sql_variants', [])
            result_buckets = rollout_stats.get('result_buckets', {})
            
            for sql_info in all_sql_variants:
                sql_text = sql_info.get('sql', '').strip()
                if not sql_text or not sql_info.get('valid', False):
                    continue
                
                # 计算该SQL的reward（基于它所属的bucket）
                sql_signature = sql_info.get('result_signature')
                if sql_signature and sql_signature in result_buckets:
                    # 该SQL的reward = 它所属bucket的计数 / 总变体数
                    total_variants = len(all_sql_variants)
                    bucket_count = result_buckets[sql_signature]
                    sql_reward = bucket_count / float(total_variants) if total_variants > 0 else 0.0
                else:
                    # 如果没有结果签名，使用rollout的reward
                    sql_reward = rollout_reward
                
                # 避免重复添加selected_sql
                if sql_text == selected_sql:
                    continue
                
                candidate_sqls.append({
                    'sql': sql_text,
                    'reward': sql_reward,
                    'sql_bucket_count': sql_bucket_count,
                    'is_quick_path': is_quick_path,
                    'rollout_id': rollout_id,
                    'source': 'variant',
                    'rollout_stats': rollout_stats
                })
        
        if not candidate_sqls:
            print("[Selection] ❌ 未找到有效的SQL，无法选择")
            return ""
        
        # 找到最高reward
        max_reward = max(c.get('reward', 0.0) for c in candidate_sqls)
        
        # 收集所有具有最高reward的SQL
        top_reward_sqls = [
            c for c in candidate_sqls 
            if abs(c.get('reward', 0.0) - max_reward) < 1e-6
        ]
        
        if not top_reward_sqls:
            print("[Selection] ❌ 未找到有效的SQL，无法选择")
            return ""
        
        # 如果只有一个最高reward的SQL，直接返回
        if len(top_reward_sqls) == 1:
            best_candidate = top_reward_sqls[0]
        else:
            # 多个SQL具有相同最高reward，使用tie-breaker
            print(f"[Selection] 发现 {len(top_reward_sqls)} 个SQL具有相同最高reward {max_reward:.4f}，使用tie-breaker...")
            best_candidate = None
            max_sql_bucket = -1
            
            for candidate in top_reward_sqls:
                sql_bucket_count = candidate.get('sql_bucket_count', 0)
                sql_text = candidate.get('sql', '')
                is_quick_path = candidate.get('is_quick_path', False)
                source = candidate.get('source', '')
                
                # 优先选择sql_bucket_count最大的
                if sql_bucket_count > max_sql_bucket:
                    max_sql_bucket = sql_bucket_count
                    best_candidate = candidate
                elif sql_bucket_count == max_sql_bucket:
                    # sql_bucket_count相同，比较SQL特征
                    current_best_sql = best_candidate.get('sql', '') if best_candidate else ''
                    
                    # 优先选择不使用不必要聚合函数的SQL（避免过度聚合）
                    current_has_unnecessary_agg = SQLSelector._has_unnecessary_aggregation(current_best_sql)
                    candidate_has_unnecessary_agg = SQLSelector._has_unnecessary_aggregation(sql_text)
                    
                    if current_has_unnecessary_agg and not candidate_has_unnecessary_agg:
                        best_candidate = candidate
                        print(f"[Selection] 💡 相同reward和一致性下，优先选择不使用不必要聚合函数的SQL")
                    elif not current_has_unnecessary_agg and candidate_has_unnecessary_agg:
                        # 保持当前最佳
                        pass
                    else:
                        # 都使用或都不使用聚合，优先选择selected_sql，然后优先选择CTE rollout（非快速路径）
                        current_best_source = best_candidate.get('source', '') if best_candidate else ''
                        current_best_is_quick = best_candidate.get('is_quick_path', False) if best_candidate else False
                        
                        # 优先选择selected_sql
                        if current_best_source != 'selected' and source == 'selected':
                            best_candidate = candidate
                            print(f"[Selection] 💡 相同reward和一致性下，优先选择selected_sql")
                        elif current_best_source == 'selected' and source != 'selected':
                            # 保持当前最佳
                            pass
                        elif current_best_is_quick and not is_quick_path:
                            best_candidate = candidate
                            print(f"[Selection] 💡 相同reward和一致性下，优先选择CTE rollout而非quick_path")
            
            if best_candidate:
                print(f"[Selection] 💡 基于tie-breaker选择：sql_bucket_count={max_sql_bucket}")
        
        # 返回最佳SQL
        if best_candidate:
            selected_sql = best_candidate.get('sql', '').strip()
            if selected_sql:
                rollout_stats = best_candidate.get('rollout_stats', {})
                is_quick_path = best_candidate.get('is_quick_path', False)
                rollout_id = best_candidate.get('rollout_id', '?')
                source = best_candidate.get('source', 'unknown')
                rollout_type = "快速路径" if is_quick_path else f"CTE Rollout {rollout_id}"
                max_sql_bucket = best_candidate.get('sql_bucket_count', 0)
                print(f"[Selection] ✅ 选择最高reward的SQL (reward={max_reward:.4f}, sql_bucket_count={max_sql_bucket}, 来源={source}, 类型={rollout_type})")
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

