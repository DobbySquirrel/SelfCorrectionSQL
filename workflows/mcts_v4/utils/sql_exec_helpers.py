"""
并行 SQL 执行辅助（带缓存优化）

提供统一的线程池并行执行接口，返回 (result, error) 列表，
其中 result 为 DataFrame 或 None，error 为错误消息或 None。

优化：
1. SQL 执行结果缓存（避免重复执行相同 SQL）
2. SQL 标准化（提升缓存命中率）
"""

from typing import List, Tuple, Optional, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
import hashlib

# 尝试导入 sqlglot 用于 SQL 标准化
try:
    import sqlglot
    HAS_SQLGLOT = True
except ImportError:
    HAS_SQLGLOT = False
    # print("⚠️ sqlglot 未安装，SQL 标准化功能将降级使用简单方法。建议安装: pip install sqlglot")


def normalize_sql(sql: str) -> str:
    """
    标准化 SQL 查询，去除格式差异，提升缓存命中率
    
    Args:
        sql: 原始 SQL 查询
        
    Returns:
        标准化后的 SQL 查询
    """
    sql = sql.strip()
    # 移除代码块标记
    if sql.startswith("```sql"):
        sql = sql[6:]
    if sql.endswith("```"):
        sql = sql[:-3]
    sql = sql.strip()
    
    if not HAS_SQLGLOT:
        # 简单标准化：移除多余空白和分号
        sql = sql.replace('\n', ' ').replace('\t', ' ')
        while '  ' in sql:
            sql = sql.replace('  ', ' ')
        return sql.rstrip(';').strip()
    
    try:
        # 使用 sqlglot 进行标准化
        parsed = sqlglot.parse_one(sql, dialect="sqlite")
        normalized = parsed.sql(
            dialect="sqlite",
            normalize=True,
            pretty=False,
            comments=False
        )
        return normalized
    except Exception:
        # 解析失败时回退到简单标准化
        sql = sql.replace('\n', ' ').replace('\t', ' ')
        while '  ' in sql:
            sql = sql.replace('  ', ' ')
        return sql.rstrip(';').strip()


def create_cache_key(db_path: str, sql: str, timeout_s: Optional[float] = None) -> str:
    """
    创建缓存键
    
    Args:
        db_path: 数据库路径
        sql: SQL 查询（会被标准化）
        timeout_s: 超时时间（作为缓存键的一部分，因为不同超时可能影响结果）
        
    Returns:
        缓存键（hash）
    """
    normalized_sql = normalize_sql(sql)
    # 使用数据库路径、标准化SQL和超时时间创建唯一键
    timeout_str = f"{timeout_s:.2f}" if timeout_s is not None else "None"
    key_string = f"{db_path}|||{normalized_sql}|||{timeout_str}"
    return hashlib.md5(key_string.encode('utf-8')).hexdigest()


# 全局缓存：存储 cache_key -> (result_df, error)
_SQL_CACHE: Dict[str, Tuple[object, Optional[str]]] = {}
_CACHE_HITS = 0
_CACHE_MISSES = 0
_MAX_CACHE_SIZE = 10000


def get_cache_stats() -> dict:
    """获取缓存统计信息"""
    total = _CACHE_HITS + _CACHE_MISSES
    hit_rate = (_CACHE_HITS / total * 100) if total > 0 else 0.0
    return {
        'hits': _CACHE_HITS,
        'misses': _CACHE_MISSES,
        'hit_rate': hit_rate,
        'cache_size': len(_SQL_CACHE)
    }


def clear_cache():
    """清空缓存"""
    global _SQL_CACHE, _CACHE_HITS, _CACHE_MISSES
    _SQL_CACHE.clear()
    _CACHE_HITS = 0
    _CACHE_MISSES = 0


def _execute_with_cache(
    db_connector,
    sql: str,
    timeout_s: Optional[float] = None
) -> Tuple[object, Optional[str]]:
    """
    带缓存的 SQL 执行
    
    Args:
        db_connector: 数据库连接器
        sql: SQL 查询
        timeout_s: 超时时间（秒）
        
    Returns:
        (result, error) 元组
    """
    global _CACHE_HITS, _CACHE_MISSES, _SQL_CACHE
    
    db_path = db_connector.db_path
    cache_key = create_cache_key(db_path, sql, timeout_s)
    
    # 检查缓存
    if cache_key in _SQL_CACHE:
        _CACHE_HITS += 1
        cached_result, cached_error = _SQL_CACHE[cache_key]
        # 返回缓存的副本（避免修改缓存内容）
        if cached_result is not None:
            import pandas as pd
            if isinstance(cached_result, pd.DataFrame):
                return cached_result.copy(), cached_error
        return cached_result, cached_error
    
    # 缓存未命中，执行查询
    _CACHE_MISSES += 1
    result, error = db_connector.execute_query_parallel_safe(sql, timeout_s=timeout_s)
    
    # 只缓存成功的查询（避免缓存错误结果）
    if result is not None and error is None:
        # 深拷贝结果以避免后续修改影响缓存
        import pandas as pd
        if isinstance(result, pd.DataFrame):
            cached_result = result.copy()
        else:
            cached_result = result
        _SQL_CACHE[cache_key] = (cached_result, error)
        
        # 限制缓存大小（LRU 策略，简单实现：删除最旧的 10% 条目）
        if len(_SQL_CACHE) > _MAX_CACHE_SIZE:
            keys_to_remove = list(_SQL_CACHE.keys())[:1000]
            for key in keys_to_remove:
                del _SQL_CACHE[key]
    
    return result, error


def execute_sqls_parallel(
    db_connector, 
    sqls: List[str], 
    timeout_s: Optional[float] = None, 
    max_workers: int = 5,
    use_cache: bool = True
) -> List[Tuple[object, Optional[str]]]:
    """
    并行执行多个 SQL 查询（带缓存优化）
    
    Args:
        db_connector: 数据库连接器
        sqls: SQL 查询列表
        timeout_s: 超时时间（秒）
        max_workers: 最大并行工作线程数
        use_cache: 是否使用缓存（默认 True）
        
    Returns:
        (result, error) 元组列表
    """
    if not sqls:
        return []

    results: List[Tuple[object, Optional[str]]] = [(None, None)] * len(sqls)

    def run_once(idx: int, sql: str) -> Tuple[int, Tuple[object, Optional[str]]]:
        if use_cache:
            df, err = _execute_with_cache(db_connector, sql, timeout_s=timeout_s)
        else:
            df, err = db_connector.execute_query_parallel_safe(sql, timeout_s=timeout_s)
        return idx, (df, err)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 创建future到索引的映射
        future_to_idx = {}
        futures = []
        for i, sql in enumerate(sqls):
            fut = executor.submit(run_once, i, sql)
            futures.append(fut)
            future_to_idx[fut] = i
        
        for fut in as_completed(futures):
            try:
                # 为future.result()添加超时，避免单个SQL卡住导致整个流程卡住
                # 超时时间 = SQL超时时间 + 10秒缓冲（用于处理线程调度等开销）
                future_timeout = (timeout_s + 10.0) if timeout_s is not None else None
                if future_timeout:
                    idx, pair = fut.result(timeout=future_timeout)
                else:
                    idx, pair = fut.result()
                results[idx] = pair
            except FutureTimeoutError:
                # 如果future本身超时，记录错误
                sql_idx = future_to_idx.get(fut, None)
                error_msg = f"SQL执行future超时（>{future_timeout:.1f}秒）" if future_timeout else "SQL执行future超时"
                if sql_idx is not None:
                    results[sql_idx] = (None, error_msg)
                else:
                    # 如果无法确定索引，找一个空位
                    for i, r in enumerate(results):
                        if r == (None, None):
                            results[i] = (None, error_msg)
                            break

    return results


def print_cache_stats():
    """打印缓存统计信息"""
    stats = get_cache_stats()
    if stats['hits'] + stats['misses'] > 0:
        print(f"\n[SQL缓存统计] 命中: {stats['hits']}, 未命中: {stats['misses']}, "
              f"命中率: {stats['hit_rate']:.1f}%, 缓存大小: {stats['cache_size']}")


