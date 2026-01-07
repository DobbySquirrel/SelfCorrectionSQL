"""
MCTS辅助工具

包含MCTS算法中使用的各种辅助函数
"""

from typing import List, Dict, Any, Optional, Tuple
import re


class MCTSUtils:
    """MCTS工具类 - 仅保留实际使用的函数"""
    
    @staticmethod
    def safe_to_dict(query_result: Any) -> List[Dict[str, Any]]:
        """
        安全地将查询结果（可能是DataFrame）转换为字典列表
        处理重复列名问题：如果DataFrame有重复列名，重命名为唯一名称
        
        Args:
            query_result: 可能是DataFrame或已经是列表的结果
            
        Returns:
            字典列表
        """
        # 如果已经是列表，直接返回
        if isinstance(query_result, list):
            return query_result
        
        # 如果是DataFrame，处理重复列名后转换
        if hasattr(query_result, 'to_dict'):
            import pandas as pd
            if isinstance(query_result, pd.DataFrame):
                # 检查是否有重复列名，如果有则重命名
                if query_result.columns.duplicated().any():
                    # 创建新列名：重复的列名添加后缀 _1, _2, ...
                    new_columns = []
                    seen = {}
                    for col in query_result.columns:
                        if col in seen:
                            seen[col] += 1
                            new_columns.append(f"{col}_{seen[col]}")
                        else:
                            seen[col] = 0
                            new_columns.append(col)
                    # 创建副本并重命名列（避免修改原DataFrame）
                    df_copy = query_result.copy()
                    df_copy.columns = new_columns
                    return df_copy.to_dict(orient='records')
                else:
                    return query_result.to_dict(orient='records')
            else:
                # 其他有 to_dict 方法的对象，直接调用
                return query_result.to_dict(orient='records')
        
        # 尝试转换为列表
        try:
            return list(query_result)
        except Exception:
            return []
    
    @staticmethod
    def normalize_result_rows(rows: List[Dict]) -> str:
        """
        规范化结果用于分组：将行列表转为稳定字符串。
        """
        try:
            import json
            # 排序字段+行，尽量稳定
            norm = [
                {k: row[k] for k in sorted(row.keys())}
                for row in rows
            ]
            return json.dumps(norm, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(rows)

    @staticmethod
    def normalize_columns(query_result: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将结果行的列名规范为小写、去空格，忽略别名差异。"""
        normalized_result: List[Dict[str, Any]] = []
        for row in query_result:
            normalized_row: Dict[str, Any] = {}
            for col_name, value in row.items():
                normalized_col_name = col_name.strip().lower()
                normalized_row[normalized_col_name] = value
            normalized_result.append(normalized_row)
        return normalized_result

    @staticmethod
    def create_result_signature(result: Dict[str, Any]) -> str:
        """基于执行结果创建唯一标识符（忽略列名差异，只基于数据值哈希）。
        
        注意：此方法会忽略列名的大小写、空格和别名差异，只基于数据值创建签名。
        例如：
        - [{"answer": 5}] 和 [{"count": 5}] 会被认为是相同的结果
        - [{"a": 1, "b": 2}] 和 [{"A": 1, "B": 2}] 会被认为是相同的结果
        """
        import hashlib
        import json

        if not result.get('valid', False):
            return f"invalid_{result.get('error', 'unknown_error')}"

        query_result = result.get('query_result', [])
        try:
            query_result = MCTSUtils.safe_to_dict(query_result)
        except Exception:
            query_result = []
        if not isinstance(query_result, list):
            try:
                query_result = list(query_result)
            except Exception:
                query_result = []
        if not query_result:
            return "empty_result"

        sample = query_result[:5]
        row_count = len(sample)
        
        # 提取每行的值（忽略列名），按值排序以确保稳定性
        # 这样即使列名不同，只要数据值相同，签名就相同
        # 注意：此方法假设相同的数据值应该被认为是相同的结果，即使列名不同
        value_rows = []  # 每行是一个值列表
        for row in sample:
            row_values = []
            for col_name, value in row.items():
                # 处理None和NaN
                if value is None:
                    row_values.append(None)
                else:
                    try:
                        import pandas as pd
                        if pd.isna(value):
                            row_values.append(None)
                        else:
                            # 统一数据类型：数字转为float/int，字符串规范化
                            if isinstance(value, (int, float)):
                                row_values.append(float(value) if isinstance(value, float) else int(value))
                            else:
                                row_values.append(str(value).strip().lower() if isinstance(value, str) else value)
                    except:
                        row_values.append(str(value).strip().lower() if isinstance(value, str) else value)
            # 对每行的值排序（忽略列名和列的顺序）
            row_values.sort(key=lambda x: (
                0 if x is None else 1,  # None在前
                str(type(x).__name__),  # 按类型名排序
                str(x) if x is not None else ''  # 按值排序
            ))
            value_rows.append(row_values)
        
        # 如果列数不同，在签名中包含列数信息
        if sample:
            col_count = len(sample[0])
            # 序列化值行列表（不包含列名信息，但保持行的结构）
            data_str = json.dumps(value_rows, sort_keys=True, ensure_ascii=False, default=str)
            data_sig = hashlib.md5(data_str.encode('utf-8')).hexdigest()
            return f"{row_count}_{col_count}_{data_sig}"
        else:
            return "empty_result"

    @staticmethod
    def bucketize_valid_nonempty(execution_results: List[Dict[str, Any]]) -> (Dict[str, int], str):
        """将有效且非空的执行结果按签名分桶，返回(桶计数字典, 最佳签名)。"""
        from collections import Counter
        buckets: Dict[str, int] = Counter()
        for res in execution_results:
            if not res.get('valid', False):
                continue
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
            if not query_result:
                continue
            key = MCTSUtils.create_result_signature(res)
            buckets[key] += 1
        best_key = max(buckets, key=buckets.get) if buckets else ""
        return dict(buckets), best_key

    @staticmethod
    def calculate_consistency_reward(result_buckets: Dict[str, int], total_variants: int) -> float:
        """基于最多票结果出现比例计算一致性奖励。"""
        if not result_buckets or total_variants <= 0:
            return 0.0
        top = max(result_buckets.values()) if result_buckets else 0
        return top / float(total_variants)

    @staticmethod
    def calculate_consensus(results_list: List[Dict[str, Any]]) -> float:
        """基于有效、非空结果的多数投票共识度。"""
        if not results_list:
            return 0.0
        valid_results = [r for r in results_list if r.get('valid') and r.get('query_result')]
        if not valid_results:
            return 0.0
        from collections import Counter
        buckets = Counter()
        for r in valid_results:
            key = MCTSUtils.normalize_result_rows(r['query_result'])
            buckets[key] += 1
        top_votes = max(buckets.values()) if buckets else 0
        return top_votes / max(1, len(valid_results))

    @staticmethod
    def calculate_sc(pred_result: List[Dict], gold_result: List[Dict]) -> float:
        """计算 Set Containment (SC)。"""
        if not pred_result or not gold_result:
            return 0.0
        if pred_result == gold_result:
            return 1.0
        pred_set = set(str(item) for item in pred_result)
        gold_set = set(str(item) for item in gold_result)
        if not gold_set:
            return 1.0 if not pred_set else 0.0
        intersection = pred_set.intersection(gold_set)
        union = pred_set.union(gold_set)
        if not union:
            return 1.0
        return len(intersection) / len(union)
    
    @staticmethod
    def is_single_zero_result(query_result: Any) -> bool:
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
    
    @staticmethod
    def has_where_clause(cte: str) -> bool:
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
    
    @staticmethod
    def extract_strategy_and_clean_cte(cte: str) -> Tuple[Optional[str], str]:
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
    
    @staticmethod
    def get_best_result_signature(rollout_stats: Dict[str, Any]) -> Optional[str]:
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
