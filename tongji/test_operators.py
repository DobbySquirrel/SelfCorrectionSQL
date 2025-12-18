import sys
import pandas as pd
import sqlite3
from abc import ABC, abstractmethod
import os
import math
from typing import Union, List, Dict, Any, Optional, Tuple
from collections import Counter
import sqlglot
import json
import re

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.auto_stat_atomic_operators import node_to_func

def extract_column_name(expr_str: str) -> str:
    """从表达式字符串中提取纯列名，去掉表名前缀"""
    if not expr_str:
        return ""
    
    # 去掉括号
    expr_str = expr_str.strip('()')
    
    # 如果包含点号，取最后一部分作为列名
    if '.' in expr_str:
        return expr_str.split('.')[-1]
    
    return expr_str

def extract_table_name(table_node):
    """递归提取表名"""
    if table_node is None:
        return ""
    if hasattr(table_node, "key") and table_node.key.lower() == "table":
        # table节点，取其this
        return extract_table_name(table_node.args.get("this"))
    if hasattr(table_node, "key") and table_node.key.lower() == "identifier":
        # identifier节点，取其name
        return table_node.args.get("this") or table_node.sql()
    # 兜底
    if hasattr(table_node, "sql"):
        return table_node.sql()
    return str(table_node)


def detect_potential_sql_issues(sql: str) -> list:
    """
    检测SQL中的潜在逻辑错误，返回问题列表
    """
    issues = []
    # 检查年龄计算方式
    if "strftime('%J'" in sql and "-" in sql:
        issues.append("年龄计算方式为 strftime('%J') 差值，可能不准确")
    # 检查LIMIT 1对后续COUNT/JOIN的影响
    if "LIMIT 1" in sql and ("COUNT" in sql or "JOIN" in sql):
        issues.append("LIMIT 1 导致后续 COUNT/JOIN 只作用于一行")
    # 你可以根据实际情况继续添加规则
    return issues

def extract_keywords_from_count_file(filepath: str) -> set:
    """从Key_count_updated.txt中提取所有操作符关键词"""
    keywords = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                # 匹配格式: "keyword: count"
                match = re.match(r'^\s*([^:]+):\s*\d+', line)
                if match:
                    keyword = match.group(1).strip()
                    keywords.add(keyword)
    except FileNotFoundError:
        print(f"警告: 文件 {filepath} 不存在")
    return keywords

def extract_mapped_operators_from_stat_file(filepath: str) -> set:
    """从stat_atomic_operators.py中提取已映射的操作符"""
    mapped_operators = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            # 使用正则表达式提取node_to_func字典中的键
            matches = re.findall(r"'([^']+)':", content)
            mapped_operators.update(matches)
    except FileNotFoundError:
        print(f"警告: 文件 {filepath} 不存在")
    return mapped_operators

def traverse_ast(node, op_list: List[str], detail_list: Optional[List[Dict]] = None):
    """递归遍历AST节点，提取操作符和详细信息"""
    if node is None:
        return
    # 保持原始大小写，不转换为小写
    op = node.key
    op_list.append(op)
    
    # 特殊处理函数节点
    if op == "function":
        func_name = node.name.upper() if hasattr(node, "name") else None
        if func_name:
            op_list.append(func_name)
            if detail_list is not None:
                detail_list.append({"op": func_name})
    
    # 特殊处理anonymous节点（如JULIANDAY等函数）
    elif op == "anonymous":
        func_name = node.name.upper() if hasattr(node, "name") else None
        if func_name:
            op_list.append(func_name)
            if detail_list is not None:
                detail_list.append({"op": func_name})
    
    if detail_list is not None:
        # 二元操作符 - 使用小写进行比较
        if op.lower() in {"eq", "gt", "lt", "ge", "le", "ne", "div", "add", "sub", "mul"}:
            left = node.args.get("this")
            right = node.args.get("expression") or node.args.get("that")
            left_str = f"({left.sql()})" if left is not None else ""
            right_str = f"({right.sql()})" if right is not None else ""
            
            # 提取纯列名，去掉表名前缀
            left_col = extract_column_name(left_str)
            right_col = extract_column_name(right_str)
            
            detail_list.append({
                "op": op,
                "left": f"({left_col})" if left_col else left_str,
                "right": f"({right_col})" if right_col else right_str
            })
        # join区分kind并输出类型
        elif op.lower() == "join":
            kind = node.args.get("kind", "inner")
            func = "build_join"
            type_str = f"({kind.upper()})"
            detail_list.append({
                "op": op,
                "kind": kind,
                "func": func,
                "type_str": type_str
            })
        # tablealias输出原表名和别名
        elif op.lower() in {"tablealias", "table_rename"}:
            table = node.args.get("this")
            alias = node.args.get("alias")
            table_name = extract_table_name(table)
            alias_str = f"({alias.sql()})" if alias is not None and hasattr(alias, 'sql') else (f"({alias})" if alias is not None else "")
            table_str = f"({table_name})" if table_name else ""
            detail_list.append({
                "op": op,
                "left": table_str,
                "right": alias_str
            })
        elif op.lower() == "order":
            # 获取所有排序项
            expressions = node.args.get("expressions", [])
            order_items = []
            for expr in expressions:
                if hasattr(expr, "sql"):
                    order_items.append(expr.sql())
                else:
                    order_items.append(str(expr))
            order_str = f"({', '.join(order_items)})" if order_items else ""
            detail_list.append({
                "op": op,
                "order_str": order_str
            })
        elif op.lower() == "limit":
            # 获取 limit 的参数
            count = node.args.get("expression")
            offset = node.args.get("offset")
            count_str = count.sql() if count is not None and hasattr(count, "sql") else str(count) if count is not None else ""
            offset_str = offset.sql() if offset is not None and hasattr(offset, "sql") else str(offset) if offset is not None else ""
            if offset_str:
                limit_str = f"({count_str}, {offset_str})"
            else:
                limit_str = f"({count_str})"
            detail_list.append({
                "op": op,
                "limit_str": limit_str
            })
        else:
            detail_list.append({"op": op})
    
    # 遍历所有子节点
    for arg in node.args.values():
        if isinstance(arg, list):
            for sub in arg:
                if hasattr(sub, "key"):
                    traverse_ast(sub, op_list, detail_list)
        elif hasattr(arg, "key"):
            traverse_ast(arg, op_list, detail_list)
def analyze_sql_operators(sql: str, question_id: str = "") -> Dict[str, Any]:
    """分析单个SQL语句的操作符"""
    result = {
        "question_id": question_id,
        "sql": sql,
        "success": False,
        "operators": [],
        "details": [],
        "error": None
    }
    
    try:
        # 使用sqlglot解析SQL
        ast = sqlglot.parse_one(sql, read='sqlite')
        
        op_list = []
        detail_list = []
        traverse_ast(ast, op_list, detail_list)
        
        result["success"] = True
        result["operators"] = op_list
        result["details"] = detail_list
        
    except Exception as e:
        result["error"] = f"SQL解析失败: {e}"
    
    return result

def generate_operation_mappings(detail_list: List[Dict]) -> List[str]:
    """根据详细信息生成操作映射字符串"""
    operations = []
    seen = set()
    
    for detail in detail_list:
        op = detail['op']
        left = detail.get('left', '')
        right = detail.get('right', '')
        key = (op, left, right)
        
        if key in seen:
            continue
        seen.add(key)
        
        if op == "join":
            func = node_to_func.get(op, None)
            type_str = detail.get("type_str", "")
            op_str = f"    operation: {op:12s}    ->    {func}    {type_str}"
            operations.append(op_str)
        elif "left" in detail and "right" in detail:
            func = node_to_func.get(op, None)
            # 对于二元操作符，直接使用已经处理过的left和right（已经去掉了表名前缀）
            op_str = f"    operation: {op:12s}    ->    {func}    ({left}, {right})"
            operations.append(op_str)
        elif op == "order":
            func = node_to_func.get(op, None)
            order_str = detail.get("order_str", "")
            op_str = f"    operation: {op:12s}    ->    {func}    {order_str} columns_with_direction: 可以是 ('列名', 'DESC')、('列名', 'ASC')"
            operations.append(op_str)
        elif op == "limit":
            func = node_to_func.get(op, None)
            limit_str = detail.get("limit_str", "")
            op_str = f"    operation: {op:12s}    ->    {func}    {limit_str}"
            operations.append(op_str)
        else:
            func = node_to_func.get(op, None)
            op_str = f"    operation: {op:12s}    ->    {func}"
            operations.append(op_str)
    
    # 添加merge操作
    operations.append("    operation: merge            ->    op_merge(df1: pd.DataFrame, df2: pd.DataFrame, on: list, how: str = 'inner')")
    
    return operations

def process_dev_dataset():
    """处理开发数据集"""
    print("=== 处理开发数据集 ===")
    
    # 读取开发数据集
    dev_file = '/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev.json'
    output_file = '/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_with_operations.json'
    
    try:
        with open(dev_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {dev_file}")
        return
    except json.JSONDecodeError as e:
        print(f"错误: JSON解析失败 {e}")
        return
    
    total = len(data)
    processed_count = 0
    
    print(f"总共需要处理 {total} 条SQL语句")
    
    for i, item in enumerate(data):
        sql = item['SQL']
        db_id = item['db_id']
        question_id = item['question_id']
        
        print(f"\n处理进度: {i+1}/{total}")

        # 分析SQL操作符
        result = analyze_sql_operators(sql, question_id)
        
        # Assuming conversion is always correct as per user instruction
        processed_count += 1
        # 生成操作映射
        operations = generate_operation_mappings(result["details"])
        # 保存到item中
        item['operations'] = operations
        item['potential_sql_issues'] = detect_potential_sql_issues(sql)
        # Removed item['operator_analysis'] as per user request
    
    # 保存结果
    try:
        with open(output_file, 'w', encoding='utf-8') as fout:
            json.dump(data, fout, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {output_file}")
    except Exception as e:
        print(f"保存文件失败: {e}")
    
    # 统计信息
    print(f"\n=== 处理统计 ===")
    print(f"总SQL语句数: {total}")
    print(f"成功解析数: {processed_count}")


def process_train_dataset():
    """处理训练数据集"""
    print("=== 处理训练数据集 ===")
    
    # 读取训练数据集
    train_file = '/home/shenshuyu/SQL_tool/work/bird/train/train.json'
    output_file = '/home/shenshuyu/SQL_tool/work/bird/train/train_with_operations.json'
    
    try:
        with open(train_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {train_file}")
        return
    except json.JSONDecodeError as e:
        print(f"错误: JSON解析失败 {e}")
        return
    
    total = len(data)
    processed_count = 0
    
    print(f"总共需要处理 {total} 条SQL语句")
    
    for i, item in enumerate(data):
        sql = item['SQL']
        db_id = item['db_id']
        question_id =item['question_id']
        
        print(f"\n处理进度: {i+1}/{total}")
  
        
        # 分析SQL操作符
        result = analyze_sql_operators(sql, str(question_id)) # Ensure question_id is a string

        processed_count += 1
        # 生成操作映射
        operations = generate_operation_mappings(result["details"])
        
        
        # 保存到item中
        item['operations'] = operations
        item['potential_sql_issues'] = detect_potential_sql_issues(sql)
 
    # 保存结果
    try:
        with open(output_file, 'w', encoding='utf-8') as fout:
            json.dump(data, fout, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {output_file}")
    except Exception as e:
        print(f"保存文件失败: {e}")
    
    # 统计信息
    print(f"\n=== 处理统计 ===")
    print(f"总SQL语句数: {total}")
    print(f"成功解析数: {processed_count}")


def main():
    """主函数"""

    # 2. 处理开发数据集
    process_dev_dataset()
    print()
    
    # 3. 处理训练数据集
    process_train_dataset()
    
    print("\n=== 分析完成 ===")

if __name__ == '__main__':
    main()