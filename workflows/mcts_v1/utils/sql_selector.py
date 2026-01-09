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
        策略：选择最高奖励的rollout的SQL
        
        选择逻辑：
        1. 优先选择reward最高的rollout的selected_sql
        2. 如果reward相同，选择sql_bucket_count最大的
        3. 如果都相同，优先选择CTE rollout（非快速路径）
        
        Args:
            rollout_stats_list: 所有rollout的统计信息列表，包含reward、sql_bucket_count、selected_sql
            
        Returns:
            最佳 SQL 字符串，如果找不到则返回空字符串
        """
        if not rollout_stats_list:
            print("[Selection] ⚠️ 没有rollout_stats，无法选择SQL")
            return ""
        
        print("[Selection] 使用策略：选择最高奖励的rollout的SQL")
        
        # 第一步：找到最高reward
        max_reward = max((r.get('reward', 0.0) for r in rollout_stats_list if r.get('selected_sql')), default=-1.0)
        
        # 第二步：收集所有具有最高reward的rollout
        top_reward_rollouts = [
            r for r in rollout_stats_list 
            if r.get('selected_sql') and abs(r.get('reward', 0.0) - max_reward) < 1e-6
        ]
        
        if not top_reward_rollouts:
            print("[Selection] ❌ 未找到有效的rollout（没有selected_sql），无法选择SQL")
            return ""
        
        # 如果只有一个最高reward的rollout，直接返回
        if len(top_reward_rollouts) == 1:
            best_rollout = top_reward_rollouts[0]
        else:
            # 多个rollout具有相同最高reward，计算平均奖励
            print(f"[Selection] 发现 {len(top_reward_rollouts)} 个rollout具有相同最高reward {max_reward:.4f}，计算平均奖励...")
            best_rollout = None
            max_avg_reward = -1.0
            max_sql_bucket = -1
            
            for rollout_stats in top_reward_rollouts:
                avg_reward = SQLSelector._calculate_avg_reward(rollout_stats)
                sql_bucket_count = rollout_stats.get('sql_bucket_count', 0)
                selected_sql = rollout_stats.get('selected_sql')
                is_quick_path = rollout_stats.get('is_quick_path', False)
                
                # 优先选择平均奖励最高的
                if avg_reward > max_avg_reward:
                    max_avg_reward = avg_reward
                    max_sql_bucket = sql_bucket_count
                    best_rollout = rollout_stats
                elif abs(avg_reward - max_avg_reward) < 1e-6:
                    # 平均奖励相同，选择sql_bucket_count最大的
                    if sql_bucket_count > max_sql_bucket:
                        max_sql_bucket = sql_bucket_count
                        best_rollout = rollout_stats
                    elif sql_bucket_count == max_sql_bucket:
                        # 平均奖励和sql_bucket_count都相同，比较SQL特征
                        current_best_sql = best_rollout.get('selected_sql', '') if best_rollout else ''
                        current_sql = selected_sql or ''
                        
                        # 优先选择不使用不必要聚合函数的SQL（避免过度聚合）
                        current_has_unnecessary_agg = SQLSelector._has_unnecessary_aggregation(current_best_sql)
                        candidate_has_unnecessary_agg = SQLSelector._has_unnecessary_aggregation(current_sql)
                        
                        if current_has_unnecessary_agg and not candidate_has_unnecessary_agg:
                            best_rollout = rollout_stats
                            print(f"[Selection] 💡 相同平均奖励和一致性下，优先选择不使用不必要聚合函数的SQL")
                        elif not current_has_unnecessary_agg and candidate_has_unnecessary_agg:
                            # 保持当前最佳
                            pass
                        else:
                            # 都使用或都不使用聚合，优先选择CTE rollout（非快速路径）
                            current_best_is_quick = best_rollout.get('is_quick_path', False) if best_rollout else False
                            if current_best_is_quick and not is_quick_path:
                                best_rollout = rollout_stats
                                print(f"[Selection] 💡 相同平均奖励和一致性下，优先选择CTE rollout而非quick_path")
            
            if best_rollout:
                print(f"[Selection] 💡 基于平均奖励选择：avg_reward={max_avg_reward:.4f}, sql_bucket_count={max_sql_bucket}")
        
        # 返回最佳rollout的SQL
        if best_rollout:
            selected_sql = best_rollout.get('selected_sql')
            if selected_sql:
                # 验证选择的SQL是否有效（检查all_sql_variants中是否有对应的有效SQL）
                all_sql_variants = best_rollout.get('all_sql_variants', [])
                is_valid_sql = False
                if all_sql_variants:
                    for sql_info in all_sql_variants:
                        if sql_info.get('sql', '').strip() == selected_sql.strip() and sql_info.get('valid', False):
                            is_valid_sql = True
                            break
                else:
                    # 如果没有all_sql_variants信息，假设SQL有效（向后兼容）
                    is_valid_sql = True
                
                if not is_valid_sql:
                    print(f"[Selection] ⚠️ 警告：选择的SQL无效（语法错误），尝试从其他rollout中选择有效的SQL")
                    # 尝试从其他rollout中选择有效的SQL
                    for rollout_stats in rollout_stats_list:
                        all_sql_variants_alt = rollout_stats.get('all_sql_variants', [])
                        if all_sql_variants_alt:
                            for sql_info in all_sql_variants_alt:
                                if sql_info.get('valid', False):
                                    valid_sql = sql_info.get('sql', '').strip()
                                    if valid_sql:
                                        print(f"[Selection] ✅ 找到有效的SQL替代方案")
                                        return valid_sql
                    print(f"[Selection] ❌ 所有rollout都没有有效的SQL")
                    return ""
                
                is_quick_path = best_rollout.get('is_quick_path', False)
                rollout_id = best_rollout.get('rollout_id', '?')
                rollout_type = "快速路径" if is_quick_path else f"CTE Rollout {rollout_id}"
                max_sql_bucket = best_rollout.get('sql_bucket_count', 0)
                print(f"[Selection] ✅ 选择最高奖励的rollout的SQL (reward={max_reward:.4f}, sql_bucket_count={max_sql_bucket}, 类型={rollout_type})")
                return selected_sql.strip()
        
        print("[Selection] ❌ 未找到有效的rollout（没有selected_sql），无法选择SQL")
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

