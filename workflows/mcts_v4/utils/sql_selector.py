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
        策略：选择最高奖励的rollout的SQL（与merge_and_evaluate_sqls.py的max_reward策略一致）
        
        选择逻辑：
        1. 选择reward最高的rollout（如果有多个，收集所有）
        2. 从每个rollout的result_buckets中找到count最高的signature
        3. 如果有多个平票，选择第一个
        4. 从all_sql_variants中找到对应的SQL
        5. 如果有多个rollout具有相同最高reward，合并它们的SQL，然后选择结果行数最少 → SQL最短的
        
        Args:
            rollout_stats_list: 所有rollout的统计信息列表，包含reward、result_buckets、all_sql_variants
            
        Returns:
            最佳 SQL 字符串，如果找不到则返回空字符串
        """
        if not rollout_stats_list:
            print("[Selection] ⚠️ 没有rollout_stats，无法选择SQL")
            return ""
        
        print("[Selection] 使用策略：选择最高奖励的rollout的SQL（max_reward策略）")
        
        # 过滤掉没有result_buckets的rollout
        valid_rollouts = [r for r in rollout_stats_list if r.get('result_buckets')]
        
        if not valid_rollouts:
            print("[Selection] ❌ 未找到有效的rollout（没有result_buckets），无法选择SQL")
            return ""
        
        # 第一步：找到最高reward
        max_reward = max((r.get('reward', 0.0) for r in valid_rollouts), default=-1.0)
        
        # 第二步：收集所有具有最高reward的rollout
        top_reward_rollouts = [
            r for r in valid_rollouts 
            if abs(r.get('reward', 0.0) - max_reward) < 1e-6
        ]
        
        if not top_reward_rollouts:
            print("[Selection] ❌ 未找到有效的rollout")
            return ""
        
        print(f"[Selection] 找到 {len(top_reward_rollouts)} 个rollout具有最高reward {max_reward:.4f}")
        
        # 第三步：从每个rollout中提取SQL（找到result_buckets中count最高的signature对应的SQL）
        candidate_sqls = []  # 存储 (sql, result_buckets, signature, row_count) 元组
        
        for rollout in top_reward_rollouts:
            result_buckets = rollout.get('result_buckets', {})
            if not result_buckets:
                continue
            
            # 找到count最高的signature
            max_count = max(result_buckets.values())
            best_signatures = [sig for sig, count in result_buckets.items() if count == max_count]
            
            # 如果有多个平票，选择第一个
            best_signature = best_signatures[0] if best_signatures else None
            
            if not best_signature:
                continue
            
            # 从all_sql_variants中找到这个signature对应的SQL
            all_sql_variants = rollout.get('all_sql_variants', [])
            found_sql = None
            found_row_count = 0
            
            for sql_info in all_sql_variants:
                sql_signature = sql_info.get('result_signature')
                if sql_signature == best_signature:
                    found_sql = sql_info.get('sql', '')
                    if sql_info.get('valid', False):
                        found_row_count = sql_info.get('result_row_count', 0)
                    break
            
            if found_sql:
                candidate_sqls.append((found_sql, result_buckets, best_signature, found_row_count))
                print(f"[Selection] 从rollout {rollout.get('rollout_id', '?')} 提取SQL: signature={best_signature}, count={max_count}, row_count={found_row_count}")
        
        if not candidate_sqls:
            print("[Selection] ❌ 未找到有效的SQL")
            return ""
        
        # 第四步：如果有多个候选SQL，使用tiebreak逻辑选择最佳SQL
        if len(candidate_sqls) == 1:
            best_sql = candidate_sqls[0][0]
            print(f"[Selection] ✅ 选择唯一候选SQL")
        else:
            # 多个候选SQL，使用tiebreak：结果行数最少 → 列数最少 → SQL最短
            print(f"[Selection] 有 {len(candidate_sqls)} 个候选SQL，使用tiebreak逻辑")
            
            def get_tiebreak_score(item: tuple) -> tuple:
                """返回(行数, SQL长度)，越小越好"""
                sql, _, _, row_count = item
                num_rows = row_count if row_count else 0
                sql_len = len(sql) if sql else 0
                return (num_rows, sql_len)
            
            best_item = min(candidate_sqls, key=get_tiebreak_score)
            best_sql = best_item[0]
            best_score = get_tiebreak_score(best_item)
            print(f"[Selection] ✅ 选择最佳SQL (行数={best_score[0]}, SQL长度={best_score[1]})")
        
        return best_sql.strip() if best_sql else ""
    
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

