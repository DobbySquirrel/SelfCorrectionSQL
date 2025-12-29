"""
SQL 执行缓存辅助模块（学习 Alpha-SQL）

提供 SQL 执行结果缓存，避免重复执行相同的 SQL 查询。
使用 sqlglot 标准化 SQL 以提升缓存命中率。
"""

from functools import lru_cache
from typing import Optional, Tuple, Any
import hashlib
import pickle

try:
    import sqlglot
    HAS_SQLGLOT = True
except ImportError:
    HAS_SQLGLOT = False
    print("⚠️ sqlglot 未安装，SQL 标准化功能将不可用。建议安装: pip install sqlglot")


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


def create_cache_key(db_path: str, sql: str) -> str:
    """
    创建缓存键
    
    Args:
        db_path: 数据库路径
        sql: SQL 查询（会被标准化）
        
    Returns:
        缓存键（hash）
    """
    normalized_sql = normalize_sql(sql)
    # 使用数据库路径和标准化 SQL 创建唯一键
    key_string = f"{db_path}|||{normalized_sql}"
    return hashlib.md5(key_string.encode('utf-8')).hexdigest()


# 全局缓存：存储 (db_path, normalized_sql) -> (result, error)
_SQL_CACHE: dict = {}
_CACHE_HITS = 0
_CACHE_MISSES = 0


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


def cached_execute_sql(
    db_path: str,
    sql: str,
    executor_func,
    timeout_s: Optional[float] = None
) -> Tuple[Any, Optional[str]]:
    """
    带缓存的 SQL 执行
    
    Args:
        db_path: 数据库路径
        sql: SQL 查询
        executor_func: 执行函数，签名: executor_func(sql, timeout_s) -> (result, error)
        timeout_s: 超时时间（秒）
        
    Returns:
        (result, error) 元组
    """
    global _CACHE_HITS, _CACHE_MISSES
    
    cache_key = create_cache_key(db_path, sql)
    
    # 检查缓存
    if cache_key in _SQL_CACHE:
        _CACHE_HITS += 1
        return _SQL_CACHE[cache_key]
    
    # 缓存未命中，执行查询
    _CACHE_MISSES += 1
    result, error = executor_func(sql, timeout_s)
    
    # 只缓存成功的查询（避免缓存错误结果）
    if result is not None and error is None:
        _SQL_CACHE[cache_key] = (result, error)
        # 限制缓存大小（LRU 策略，简单实现）
        if len(_SQL_CACHE) > 10000:
            # 删除最旧的 10% 条目
            keys_to_remove = list(_SQL_CACHE.keys())[:1000]
            for key in keys_to_remove:
                del _SQL_CACHE[key]
    
    return result, error


def print_cache_stats():
    """打印缓存统计信息"""
    stats = get_cache_stats()
    print(f"\n[SQL缓存统计] 命中: {stats['hits']}, 未命中: {stats['misses']}, "
          f"命中率: {stats['hit_rate']:.1f}%, 缓存大小: {stats['cache_size']}")

