#!/usr/bin/env python3
"""
分析 execution_logs.json 中的 timeout 错误原因

分析维度：
1. SQL 复杂度（JOIN 数量、CTE 数量、子查询数量）
2. 查询模式（LIKE 操作、正则表达式、全表扫描）
3. 数据量相关（是否有 LIMIT、是否有 WHERE 条件）
4. 数据库名称分布
5. 问题 ID 分布
"""

import json
import re
from collections import defaultdict, Counter
from pathlib import Path
from typing import Dict, List, Any, Tuple
import argparse


def count_joins(sql: str) -> int:
    """统计 SQL 中的 JOIN 数量"""
    # 匹配各种 JOIN 类型（不区分大小写）
    join_pattern = r'\b(?:INNER|LEFT|RIGHT|FULL|OUTER|CROSS)\s+JOIN\b'
    return len(re.findall(join_pattern, sql, re.IGNORECASE))


def count_ctes(sql: str) -> int:
    """统计 CTE 数量"""
    # 匹配 WITH ... AS 模式
    cte_pattern = r'\bWITH\s+\w+\s+AS\s*\('
    return len(re.findall(cte_pattern, sql, re.IGNORECASE))


def count_like_operations(sql: str) -> int:
    """统计 LIKE 操作数量"""
    like_pattern = r'\bLIKE\s+'
    return len(re.findall(like_pattern, sql, re.IGNORECASE))


def count_subqueries(sql: str) -> int:
    """统计子查询数量（SELECT 在括号内）"""
    # 简化统计：计算 SELECT 后面跟着括号的情况
    # 注意：这个统计可能不够精确，但可以作为一个参考
    subquery_pattern = r'\(\s*SELECT\s+'
    return len(re.findall(subquery_pattern, sql, re.IGNORECASE))


def has_limit(sql: str) -> bool:
    """检查是否有 LIMIT 子句"""
    return bool(re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE))


def has_where(sql: str) -> bool:
    """检查是否有 WHERE 子句"""
    return bool(re.search(r'\bWHERE\s+', sql, re.IGNORECASE))


def has_group_by(sql: str) -> bool:
    """检查是否有 GROUP BY 子句"""
    return bool(re.search(r'\bGROUP\s+BY\s+', sql, re.IGNORECASE))


def has_order_by(sql: str) -> bool:
    """检查是否有 ORDER BY 子句"""
    return bool(re.search(r'\bORDER\s+BY\s+', sql, re.IGNORECASE))


def has_union(sql: str) -> bool:
    """检查是否有 UNION 操作"""
    return bool(re.search(r'\bUNION\s+(?:ALL\s+)?SELECT', sql, re.IGNORECASE))


def analyze_sql_complexity(sql: str) -> Dict[str, Any]:
    """分析 SQL 的复杂度特征"""
    return {
        'join_count': count_joins(sql),
        'cte_count': count_ctes(sql),
        'like_count': count_like_operations(sql),
        'subquery_count': count_subqueries(sql),
        'has_limit': has_limit(sql),
        'has_where': has_where(sql),
        'has_group_by': has_group_by(sql),
        'has_order_by': has_order_by(sql),
        'has_union': has_union(sql),
        'sql_length': len(sql),
        'line_count': sql.count('\n') + 1,
    }


def categorize_timeout_reason(complexity: Dict[str, Any]) -> str:
    """根据复杂度特征分类 timeout 原因"""
    reasons = []
    
    # 1. 高 JOIN 数量
    if complexity['join_count'] >= 10:
        reasons.append(f"高JOIN数量({complexity['join_count']}个)")
    elif complexity['join_count'] >= 5:
        reasons.append(f"中等JOIN数量({complexity['join_count']}个)")
    
    # 2. 大量 CTE
    if complexity['cte_count'] >= 5:
        reasons.append(f"大量CTE({complexity['cte_count']}个)")
    
    # 3. 大量 LIKE 操作（通常性能较差）
    if complexity['like_count'] >= 3:
        reasons.append(f"大量LIKE操作({complexity['like_count']}个)")
    
    # 4. 复杂子查询
    if complexity['subquery_count'] >= 5:
        reasons.append(f"复杂子查询({complexity['subquery_count']}个)")
    
    # 5. UNION 操作
    if complexity['has_union']:
        reasons.append("UNION操作")
    
    # 6. 缺少 LIMIT（可能导致全表扫描）
    if not complexity['has_limit']:
        reasons.append("缺少LIMIT子句")
    
    # 7. 缺少 WHERE（全表扫描）
    if not complexity['has_where']:
        reasons.append("缺少WHERE条件")
    
    # 8. 超长 SQL
    if complexity['sql_length'] > 5000:
        reasons.append(f"超长SQL({complexity['sql_length']}字符)")
    
    # 9. GROUP BY（聚合操作可能慢）
    if complexity['has_group_by']:
        reasons.append("GROUP BY聚合操作")
    
    if not reasons:
        return "其他原因（SQL复杂度中等）"
    
    return " + ".join(reasons)


def analyze_timeout_errors(log_file: str) -> Dict[str, Any]:
    """分析 timeout 错误"""
    print(f"正在读取日志文件: {log_file}")
    
    with open(log_file, 'r', encoding='utf-8') as f:
        logs = json.load(f)
    
    print(f"总日志条数: {len(logs)}")
    
    # 筛选 timeout 错误
    timeout_logs = []
    for log in logs:
        if log.get('type') == 'error' and log.get('error', '').lower().find('timeout') != -1:
            timeout_logs.append(log)
        elif log.get('type') == 'execution' and log.get('context', {}).get('error_type') == 'timeout':
            timeout_logs.append(log)
    
    print(f"Timeout 错误数量: {len(timeout_logs)}")
    print()
    
    if not timeout_logs:
        print("未找到 timeout 错误")
        return {}
    
    # 统计分析
    stats = {
        'total_timeouts': len(timeout_logs),
        'by_reason': defaultdict(int),
        'by_db': Counter(),
        'by_question': Counter(),
        'complexity_stats': {
            'join_counts': [],
            'cte_counts': [],
            'like_counts': [],
            'sql_lengths': [],
        },
        'detailed_logs': []
    }
    
    # 分析每个 timeout 错误
    for log in timeout_logs:
        sql = log.get('sql', '')
        db_name = log.get('db_name', 'unknown')
        question_id = log.get('question_id', 'unknown')
        
        stats['by_db'][db_name] += 1
        stats['by_question'][question_id] += 1
        
        # 分析 SQL 复杂度
        complexity = analyze_sql_complexity(sql)
        reason = categorize_timeout_reason(complexity)
        stats['by_reason'][reason] += 1
        
        # 收集复杂度统计
        stats['complexity_stats']['join_counts'].append(complexity['join_count'])
        stats['complexity_stats']['cte_counts'].append(complexity['cte_count'])
        stats['complexity_stats']['like_counts'].append(complexity['like_count'])
        stats['complexity_stats']['sql_lengths'].append(complexity['sql_length'])
        
        # 保存详细信息
        stats['detailed_logs'].append({
            'question_id': question_id,
            'db_name': db_name,
            'reason': reason,
            'complexity': complexity,
            'duration': log.get('context', {}).get('duration', 0),
            'sql_preview': sql[:200] + '...' if len(sql) > 200 else sql
        })
    
    return stats


def print_analysis_report(stats: Dict[str, Any]):
    """打印分析报告"""
    if not stats:
        return
    
    print("=" * 80)
    print("TIMEOUT 错误原因分析报告")
    print("=" * 80)
    print()
    
    print(f"总超时错误数: {stats['total_timeouts']}")
    print()
    
    # 1. 按原因分类统计
    print("-" * 80)
    print("1. 按原因分类统计（Top 10）")
    print("-" * 80)
    sorted_reasons = sorted(stats['by_reason'].items(), key=lambda x: x[1], reverse=True)
    for reason, count in sorted_reasons[:10]:
        percentage = (count / stats['total_timeouts']) * 100
        print(f"  {reason:60s} : {count:5d} ({percentage:5.1f}%)")
    print()
    
    # 2. 按数据库分类统计
    print("-" * 80)
    print("2. 按数据库分类统计（Top 10）")
    print("-" * 80)
    for db_name, count in stats['by_db'].most_common(10):
        percentage = (count / stats['total_timeouts']) * 100
        print(f"  {db_name:40s} : {count:5d} ({percentage:5.1f}%)")
    print()
    
    # 3. 按问题ID分类统计
    print("-" * 80)
    print("3. 按问题ID分类统计（Top 10）")
    print("-" * 80)
    for question_id, count in stats['by_question'].most_common(10):
        percentage = (count / stats['total_timeouts']) * 100
        print(f"  Question ID {question_id:10s} : {count:5d} ({percentage:5.1f}%)")
    print()
    
    # 4. 复杂度统计
    print("-" * 80)
    print("4. SQL 复杂度统计")
    print("-" * 80)
    comp_stats = stats['complexity_stats']
    
    def print_stat(name: str, values: List[int]):
        if values:
            print(f"  {name:20s}:")
            print(f"    平均值: {sum(values) / len(values):.2f}")
            print(f"    最大值: {max(values)}")
            print(f"    最小值: {min(values)}")
            print(f"    中位数: {sorted(values)[len(values)//2]}")
    
    print_stat("JOIN 数量", comp_stats['join_counts'])
    print_stat("CTE 数量", comp_stats['cte_counts'])
    print_stat("LIKE 操作数", comp_stats['like_counts'])
    print_stat("SQL 长度", comp_stats['sql_lengths'])
    print()
    
    # 5. 特征分布统计
    print("-" * 80)
    print("5. SQL 特征分布")
    print("-" * 80)
    
    # 统计各种特征的分布
    high_join = sum(1 for x in comp_stats['join_counts'] if x >= 10)
    medium_join = sum(1 for x in comp_stats['join_counts'] if 5 <= x < 10)
    low_join = sum(1 for x in comp_stats['join_counts'] if x < 5)
    
    print(f"  JOIN 数量 >= 10: {high_join:5d} ({(high_join/len(comp_stats['join_counts'])*100):5.1f}%)")
    print(f"  JOIN 数量 5-9:   {medium_join:5d} ({(medium_join/len(comp_stats['join_counts'])*100):5.1f}%)")
    print(f"  JOIN 数量 < 5:    {low_join:5d} ({(low_join/len(comp_stats['join_counts'])*100):5.1f}%)")
    print()
    
    many_like = sum(1 for x in comp_stats['like_counts'] if x >= 3)
    print(f"  LIKE 操作 >= 3:   {many_like:5d} ({(many_like/len(comp_stats['like_counts'])*100):5.1f}%)")
    print()
    
    # 6. 详细示例（最严重的几个）
    print("-" * 80)
    print("6. 典型超时错误示例（按 JOIN 数量排序，Top 5）")
    print("-" * 80)
    
    sorted_logs = sorted(stats['detailed_logs'], 
                        key=lambda x: x['complexity']['join_count'], 
                        reverse=True)
    
    for i, log in enumerate(sorted_logs[:5], 1):
        print(f"\n示例 {i}:")
        print(f"  问题ID: {log['question_id']}")
        print(f"  数据库: {log['db_name']}")
        print(f"  原因: {log['reason']}")
        print(f"  JOIN数: {log['complexity']['join_count']}, "
              f"CTE数: {log['complexity']['cte_count']}, "
              f"LIKE数: {log['complexity']['like_count']}")
        print(f"  SQL长度: {log['complexity']['sql_length']} 字符")
        print(f"  执行时长: {log['duration']:.2f}s")
        print(f"  SQL预览: {log['sql_preview']}")
    
    print()
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description='分析 execution_logs.json 中的 timeout 错误')
    parser.add_argument('log_file', type=str, help='execution_logs.json 文件路径')
    parser.add_argument('--output', '-o', type=str, help='输出分析结果到 JSON 文件')
    
    args = parser.parse_args()
    
    # 分析
    stats = analyze_timeout_errors(args.log_file)
    
    # 打印报告
    print_analysis_report(stats)
    
    # 保存到文件（如果指定）
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 准备输出数据（移除详细日志以减小文件大小）
        output_stats = {
            'total_timeouts': stats['total_timeouts'],
            'by_reason': dict(stats['by_reason']),
            'by_db': dict(stats['by_db']),
            'by_question': dict(stats['by_question']),
            'complexity_stats': stats['complexity_stats'],
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_stats, f, ensure_ascii=False, indent=2)
        
        print(f"\n分析结果已保存到: {output_path}")


if __name__ == '__main__':
    main()
