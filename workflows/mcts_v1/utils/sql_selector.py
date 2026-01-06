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

