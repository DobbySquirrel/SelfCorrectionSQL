#!/usr/bin/env python3
"""
分析SQL中的表连接关系类型（一对一、一对多、多对多）

统计predicted_sql和ground_truth中的JOIN关系类型
"""

import json
import re
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional, Any
from collections import defaultdict, Counter
import pandas as pd
import numpy as np

# 添加项目路径
sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from core.database_connector import DatabaseConnector


def extract_joins_from_sql(sql: str) -> List[Dict[str, Any]]:
    """
    从SQL中提取JOIN信息
    
    Returns:
        List of dicts: [{'type': 'INNER JOIN', 'table1': 't1', 'table2': 't2', 'col1': 'id', 'col2': 'id', 'condition': 't1.id = t2.id'}, ...]
    """
    if not sql:
        return []
    
    joins = []
    
    # 提取FROM子句中的第一个表（可能是主表）
    from_match = re.search(r'FROM\s+([^\s(,]+)(?:\s+AS\s+)?([^\s(,)]+)?', sql, re.IGNORECASE)
    if not from_match:
        return []
    
    main_table = from_match.group(1).strip()
    main_alias = from_match.group(2).strip() if from_match.group(2) else main_table
    
    # 提取所有JOIN语句（更健壮的正则表达式）
    # 匹配: JOIN table [AS alias] ON condition
    join_pattern = r'(INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|FULL\s+JOIN|JOIN)\s+([^\s(,]+)(?:\s+AS\s+)?([^\s(,)]+)?\s+ON\s+([^;]+?)(?=\s+(?:JOIN|WHERE|GROUP|ORDER|LIMIT|;|$))'
    
    for match in re.finditer(join_pattern, sql, re.IGNORECASE | re.DOTALL):
        join_type = match.group(1).strip().upper()
        table2_raw = match.group(2).strip()
        alias2 = match.group(3).strip() if match.group(3) else table2_raw
        condition = match.group(4).strip()
        
        # 清理表名（移除反引号）
        table2 = table2_raw.strip('`')
        alias2 = alias2.strip('`')
        
        # 解析JOIN条件，提取表名/别名和列名
        # 格式通常是: table1.col1 = table2.col2 或 alias1.col1 = alias2.col2
        # 先清理条件字符串，移除可能的额外内容
        # 只提取等号两边的表达式（可能在括号内）
        condition_clean = condition.strip()
        
        # 找到第一个等号的位置
        eq_pos = condition_clean.find('=')
        if eq_pos == -1:
            continue
        
        left_expr = condition_clean[:eq_pos].strip()
        right_expr = condition_clean[eq_pos+1:].strip()
        
        # 移除可能的括号和额外内容
        # 只保留等号前的最后一个有效表达式
        left_expr = re.sub(r'.*?([^\s(,]+\.?[^\s(,]+)\s*$', r'\1', left_expr)
        right_expr = re.sub(r'^([^\s(,]+\.?[^\s(,]+).*', r'\1', right_expr)
        
        # SQL关键字列表（用于过滤无效的列名）
        sql_keywords = {'INNER', 'JOIN', 'LEFT', 'RIGHT', 'FULL', 'OUTER', 'ON', 'WHERE', 
                      'GROUP', 'ORDER', 'BY', 'HAVING', 'LIMIT', 'SELECT', 'FROM', 'AS', 'AND', 'OR'}
        
        # 提取表名/别名和列名（支持反引号）
        # 改进正则：匹配 table.col 或 `table`.`col` 格式，确保不包含关键字
        left_match = re.match(r'([^.`\s]+)[.`]([^.`\s]+)', left_expr)
        right_match = re.match(r'([^.`\s]+)[.`]([^.`\s]+)', right_expr)
        
        if left_match and right_match:
            table1_or_alias = left_match.group(1).strip().strip('`')
            col1 = left_match.group(2).strip().strip('`')
            table2_or_alias = right_match.group(1).strip().strip('`')
            col2 = right_match.group(2).strip().strip('`')
            
            # 验证列名和表名不包含SQL关键字，且不为空
            if (col1 and col2 and 
                col1.upper() not in sql_keywords and col2.upper() not in sql_keywords and
                table1_or_alias.upper() not in sql_keywords and table2_or_alias.upper() not in sql_keywords and
                not any(kw in col1.upper() for kw in sql_keywords) and
                not any(kw in col2.upper() for kw in sql_keywords)):
                joins.append({
                    'type': join_type,
                    'table1': table1_or_alias,
                    'table2': table2_or_alias,
                    'table2_name': table2,  # 实际表名
                    'col1': col1,
                    'col2': col2,
                    'condition': condition
                })
    
    return joins


def extract_tables_from_sql(sql: str) -> Set[str]:
    """从SQL中提取所有表名 (修复版：排除关键字)"""
    if not sql:
        return set()
    
    tables = set()
    
    # 定义需要忽略的 SQL 关键字
    KEYWORDS = {
        'SELECT', 'FROM', 'JOIN', 'ON', 'WHERE', 'GROUP', 'ORDER', 'BY', 
        'HAVING', 'LIMIT', 'AS', 'AND', 'OR', 'LEFT', 'RIGHT', 'INNER', 
        'OUTER', 'FULL', 'UNION', 'INTERSECT', 'EXCEPT', 'DISTINCT',
        'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'IS', 'NULL', 'NOT',
        'IN', 'EXISTS', 'LIKE', 'BETWEEN', 'WITH'
    }
    
    # 简单的清洗，移除换行
    sql_clean = sql.replace('\n', ' ')
    
    # 1. 提取 FROM 后的表
    # 匹配 FROM table_name [AS alias] [WHERE/JOIN/...]
    from_matches = re.finditer(r'FROM\s+([^\s(),;]+)', sql_clean, re.IGNORECASE)
    for match in from_matches:
        table = match.group(1).strip().strip('`')
        # 移除可能的AS别名
        table = re.sub(r'\s+AS\s+\w+$', '', table, flags=re.IGNORECASE).strip()
        # 过滤关键字
        if table and table.upper() not in KEYWORDS:
            tables.add(table)
    
    # 2. 提取 JOIN 后的表
    join_matches = re.finditer(r'JOIN\s+([^\s(),;]+)', sql_clean, re.IGNORECASE)
    for match in join_matches:
        table = match.group(1).strip().strip('`')
        # 移除可能的AS别名
        table = re.sub(r'\s+AS\s+\w+$', '', table, flags=re.IGNORECASE).strip()
        # 过滤关键字
        if table and table.upper() not in KEYWORDS:
            tables.add(table)
    
    return tables

def determine_relationship_type(db_connector: DatabaseConnector, table1: str, col1: str, 
                               table2: str, col2: str) -> Optional[str]:
    """
    确定两个表之间的连接关系类型 (修正版)
    逻辑：
    1. 检查 Table1.Col1 是否为 Unique (代表 1 端)
    2. 检查 Table2.Col2 是否为 Unique (代表 1 端)
    3. 组合判定：
       - Unique + Unique = 1:1
       - Unique + Not Unique = 1:N
       - Not Unique + Unique = N:1
       - Not Unique + Not Unique = M:N
    """
    try:
        clean_table1 = table1.strip().strip('`')
        clean_col1 = col1.strip().strip('`')
        clean_table2 = table2.strip().strip('`')
        clean_col2 = col2.strip().strip('`')

        # 辅助函数：检测列是否唯一
        def check_is_unique(table, col):
            # 关键修改：只查询非空数据
            # COUNT(col) 不包含 NULL，COUNT(DISTINCT col) 也不包含 NULL
            # 这样比较才是公平的
            sql = f"""
            SELECT 
                COUNT(`{col}`) as cnt, 
                COUNT(DISTINCT `{col}`) as dist 
            FROM `{table}` 
            WHERE `{col}` IS NOT NULL
            """
            res, err = db_connector.execute_query(sql)
            if err or res is None or res.empty:
                return False
            
            total = res.iloc[0]['cnt']
            dist = res.iloc[0]['dist']
            
            if total == 0:
                return False # 空表视为非 Unique，或者根据需求处理
            
            # 容错逻辑：如果重复率极低（<1%），依然认为是 Unique (应对 BIRD 数据集中的脏数据)
            # 例如：有 1000 行，999 行唯一，只有 1 行重复，这通常是主键，只是数据脏了
            duplication_rate = (total - dist) / total
            return duplication_rate < 0.01 

        # 1. 检查 Table 1
        is_t1_unique = check_is_unique(clean_table1, clean_col1)
        
        # 2. 检查 Table 2
        is_t2_unique = check_is_unique(clean_table2, clean_col2)

        # 3. 组合判定
        if is_t1_unique and is_t2_unique:
            return '1:1'
        elif is_t1_unique and not is_t2_unique:
            # T1 是 1，T2 是 N -> T1 (One) to T2 (Many) -> 1:N
            return '1:N'
        elif not is_t1_unique and is_t2_unique:
            # T1 是 N，T2 是 1 -> T1 (Many) to T2 (One) -> N:1
            return 'N:1'
        else:
            return 'M:N'

    except Exception as e:
        # print(f"Debug: Error in relationship check: {e}")
        return None

def resolve_table_name(alias: str, sql: str) -> str:
    """
    通过别名解析实际表名
    
    Args:
        alias: 表别名
        sql: SQL语句
        
    Returns:
        实际表名（如果找不到则返回别名）
    """
    # 查找 FROM table AS alias 或 JOIN table AS alias
    patterns = [
        rf'FROM\s+([^\s(,]+)\s+AS\s+{re.escape(alias)}\b',
        rf'JOIN\s+([^\s(,]+)\s+AS\s+{re.escape(alias)}\b',
        rf'FROM\s+{re.escape(alias)}\b',  # 如果没有AS，别名可能就是表名
        rf'JOIN\s+{re.escape(alias)}\b',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, sql, re.IGNORECASE)
        if match:
            table_name = match.group(1) if match.lastindex >= 1 else alias
            return table_name.strip('`')
    
    return alias.strip('`')


def analyze_sql_joins(predicted_sql: str, ground_truth: str, db_connector: DatabaseConnector) -> Dict[str, Any]:
    """
    分析predicted_sql和ground_truth中的JOIN关系
    
    Returns:
        {
            'predicted_joins': [...],
            'ground_truth_joins': [...],
            'relationship_types': {...}
        }
    """
    predicted_joins = extract_joins_from_sql(predicted_sql)
    ground_truth_joins = extract_joins_from_sql(ground_truth)
    
    # 确定每个JOIN的关系类型
    relationship_types = {}
    
    # 处理predicted_sql的JOIN
    for join_info in predicted_joins:
        # 解析实际表名
        table1_alias = join_info['table1']
        table2_name = join_info.get('table2_name', join_info['table2'])
        
        # 尝试从SQL中解析table1的实际表名
        table1_name = resolve_table_name(table1_alias, predicted_sql)
        if table1_name == table1_alias:
            # 如果找不到，可能是FROM子句中的主表
            from_match = re.search(r'FROM\s+([^\s(,]+)', predicted_sql, re.IGNORECASE)
            if from_match:
                table1_name = from_match.group(1).strip().strip('`')
        
        col1 = join_info['col1']
        col2 = join_info['col2']
        
        # 创建关系键（规范化：小的表名在前）
        key = f"{min(table1_name, table2_name)}.{col1} <-> {max(table1_name, table2_name)}.{col2}"
        
        if key not in relationship_types:
            rel_type = determine_relationship_type(db_connector, table1_name, col1, table2_name, col2)
            relationship_types[key] = rel_type
    
    # 处理ground_truth的JOIN
    for join_info in ground_truth_joins:
        table1_alias = join_info['table1']
        table2_name = join_info.get('table2_name', join_info['table2'])
        
        table1_name = resolve_table_name(table1_alias, ground_truth)
        if table1_name == table1_alias:
            from_match = re.search(r'FROM\s+([^\s(,]+)', ground_truth, re.IGNORECASE)
            if from_match:
                table1_name = from_match.group(1).strip().strip('`')
        
        col1 = join_info['col1']
        col2 = join_info['col2']
        
        key = f"{min(table1_name, table2_name)}.{col1} <-> {max(table1_name, table2_name)}.{col2}"
        
        if key not in relationship_types:
            rel_type = determine_relationship_type(db_connector, table1_name, col1, table2_name, col2)
            relationship_types[key] = rel_type
    
    return {
        'predicted_joins': predicted_joins,
        'ground_truth_joins': ground_truth_joins,
        'relationship_types': relationship_types
    }


def main():
    parser = argparse.ArgumentParser(description="分析SQL中的表连接关系类型")
    parser.add_argument("--json_file", type=str, required=True, help="包含predicted_sql和ground_truth的JSON文件")
    parser.add_argument("--ppl_file", type=str, default=None, help="数据库映射文件（用于获取db_id）")
    parser.add_argument("--output_file", type=str, default="join_relationship_analysis.json", help="输出文件")
    args = parser.parse_args()
    
    print("="*80)
    print("SQL表连接关系分析")
    print("="*80)
    
    # 加载JSON文件
    print(f"\n📂 加载文件: {args.json_file}")
    with open(args.json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 加载数据库映射
    db_info_map = {}
    if args.ppl_file:
        try:
            with open(args.ppl_file, 'r', encoding='utf-8') as f:
                ppls = json.load(f)
            items = ppls if isinstance(ppls, list) else list(ppls.values())
            for item in items:
                qid = str(item.get('question_id') or item.get('id', '')).strip()
                db_name = item.get('db') or item.get('db_id')
                if qid and db_name:
                    db_info_map[qid] = db_name
            print(f"✅ 加载了 {len(db_info_map)} 条数据库映射")
        except Exception as e:
            print(f"⚠️ 加载数据库映射失败: {e}")
    
    # 分析每个问题的JOIN关系
    error_details = data.get('error_details', [])
    
    relationship_stats = {
        '1:1': {'predicted': 0, 'ground_truth': 0, 'both': 0},
        '1:N': {'predicted': 0, 'ground_truth': 0, 'both': 0},
        'N:1': {'predicted': 0, 'ground_truth': 0, 'both': 0},
        'M:N': {'predicted': 0, 'ground_truth': 0, 'both': 0},
        'unknown': {'predicted': 0, 'ground_truth': 0, 'both': 0}
    }
    
    detailed_results = []
    total_processed = 0
    
    for item in error_details:
        qid = str(item.get('question_id') or item.get('idx', '')).strip()
        predicted_sql = item.get('predicted_sql', '')
        ground_truth = item.get('ground_truth', '')
        db_id = item.get('db_id', '') or db_info_map.get(qid, '')
        
        if not predicted_sql or not ground_truth:
            continue
        
        # 检查是否有JOIN
        has_join_pred = 'JOIN' in predicted_sql.upper()
        has_join_truth = 'JOIN' in ground_truth.upper()
        
        if not has_join_pred and not has_join_truth:
            continue
        
        total_processed += 1
        
        db_connector = None
        if db_id:
            try:
                db_connector = DatabaseConnector(db_id)
            except Exception as e:
                print(f"⚠️ 连接数据库 {db_id} 失败: {e}")
        
        if not db_connector:
            continue
        
        try:
            # 分析JOIN关系
            analysis = analyze_sql_joins(predicted_sql, ground_truth, db_connector)
            
            predicted_joins = analysis['predicted_joins']
            ground_truth_joins = analysis['ground_truth_joins']
            relationship_types = analysis['relationship_types']
            
            # 统计predicted_sql的关系类型
            predicted_rels = set()
            for join_info in predicted_joins:
                table1_alias = join_info['table1']
                table2_name = join_info.get('table2_name', join_info['table2'])
                
                # 解析实际表名
                table1_name = resolve_table_name(table1_alias, predicted_sql)
                if table1_name == table1_alias:
                    from_match = re.search(r'FROM\s+([^\s(,]+)', predicted_sql, re.IGNORECASE)
                    if from_match:
                        table1_name = from_match.group(1).strip().strip('`')
                
                col1 = join_info['col1']
                col2 = join_info['col2']
                key = f"{min(table1_name, table2_name)}.{col1} <-> {max(table1_name, table2_name)}.{col2}"
                rel_type = relationship_types.get(key, 'unknown')
                if rel_type:
                    predicted_rels.add(rel_type)
            
            # 统计ground_truth的关系类型
            truth_rels = set()
            for join_info in ground_truth_joins:
                table1_alias = join_info['table1']
                table2_name = join_info.get('table2_name', join_info['table2'])
                
                table1_name = resolve_table_name(table1_alias, ground_truth)
                if table1_name == table1_alias:
                    from_match = re.search(r'FROM\s+([^\s(,]+)', ground_truth, re.IGNORECASE)
                    if from_match:
                        table1_name = from_match.group(1).strip().strip('`')
                
                col1 = join_info['col1']
                col2 = join_info['col2']
                key = f"{min(table1_name, table2_name)}.{col1} <-> {max(table1_name, table2_name)}.{col2}"
                rel_type = relationship_types.get(key, 'unknown')
                if rel_type:
                    truth_rels.add(rel_type)
            
            # 更新统计
            for rel_type in predicted_rels:
                relationship_stats[rel_type]['predicted'] += 1
                if rel_type in truth_rels:
                    relationship_stats[rel_type]['both'] += 1
            
            for rel_type in truth_rels:
                relationship_stats[rel_type]['ground_truth'] += 1
            
            detailed_results.append({
                'question_id': qid,
                'db_id': db_id,
                'predicted_sql': predicted_sql,
                'ground_truth': ground_truth,
                'predicted_joins': predicted_joins,
                'ground_truth_joins': ground_truth_joins,
                'predicted_relationship_types': list(predicted_rels),
                'ground_truth_relationship_types': list(truth_rels),
                'relationship_types_detail': relationship_types
            })
            
        except Exception as e:
            print(f"⚠️ 分析问题 {qid} 失败: {e}")
        finally:
            if db_connector:
                db_connector.disconnect()
    
    # 输出统计结果
    print("\n" + "="*80)
    print("📊 表连接关系类型统计")
    print("="*80)
    print(f"\n总共分析了 {total_processed} 个包含JOIN的问题")
    print(f"\n关系类型分布:")
    print(f"{'关系类型':<10} {'Predicted':<15} {'Ground Truth':<15} {'Both':<15}")
    print("-" * 60)
    
    for rel_type in ['1:1', '1:N', 'N:1', 'M:N', 'unknown']:
        stats = relationship_stats[rel_type]
        print(f"{rel_type:<10} {stats['predicted']:<15} {stats['ground_truth']:<15} {stats['both']:<15}")
    
    # 保存详细结果
    output_data = {
        'summary': {
            'total_processed': total_processed,
            'relationship_stats': relationship_stats
        },
        'detailed_results': detailed_results
    }
    
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 详细结果已保存至: {output_path}")
    print("="*80)


if __name__ == "__main__":
    main()

