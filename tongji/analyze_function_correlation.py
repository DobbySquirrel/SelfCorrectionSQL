import sys
import os
import json
import csv
import sqlglot
import pandas as pd
import numpy as np
from collections import Counter, defaultdict
from typing import Dict, List, Set, Tuple
from itertools import combinations
import matplotlib.pyplot as plt
import seaborn as sns

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.auto_stat_atomic_operators import node_to_func

def extract_operators_from_sql(sql: str) -> List[str]:
    """从SQL中提取所有操作符"""
    operators = []
    try:
        ast = sqlglot.parse_one(sql, read='sqlite')
        if not ast:
            return operators
        
        for node in ast.walk():
            op = node.key
            operators.append(op)
            
            # 特殊处理函数节点
            if op == "function":
                func_name = node.name.upper() if hasattr(node, "name") else None
                if func_name:
                    operators.append(func_name)
            
            # 特殊处理anonymous节点（如JULIANDAY等函数）
            elif op == "anonymous":
                func_name = node.name.upper() if hasattr(node, "name") else None
                if func_name:
                    operators.append(func_name)
            
            # 特殊处理其他函数节点
            elif op in ["replace", "exists", "abs", "round", "length", "trim", "lower", "substring", "strposition", "cast", "datatype", "count", "sum", "avg", "max", "min", "groupconcat", "timestampdiff", "datediff", "timetostr", "tsordstotimestamp", "date", "currenttimestamp", "from", "group", "order", "limit", "offset", "join", "union", "except", "distinct", "rownumber", "if", "case", "rank", "dense_rank", "now", "time", "total", "dpipe", "datetime", "boolean", "neg", "add", "sub", "mul", "div", "eq", "gt", "lt", "ge", "le", "ne", "neq", "is", "null", "in", "between", "like", "not", "and", "or", "having", "paren", "star", "alias", "tablealias", "table_rename", "subquery", "cte", "with", "window"]:
                func_name = op.upper()
                operators.append(func_name)
            
            # 处理JOIN类型
            if op == "join":
                join_type = node.args.get("kind")
                if join_type:
                    operators.append(f"{join_type.upper()} JOIN")
                    
    except Exception as e:
        print(f"解析SQL失败: {e}")
        print(f"SQL: {sql}")
    
    return operators

def map_operators_to_functions(operators: List[str]) -> List[str]:
    """将操作符映射为函数名"""
    function_names = []
    for op in operators:
        mapped_func = node_to_func.get(op, "未映射")
        # 提取函数名（去掉参数部分）
        if '(' in mapped_func:
            func_name = mapped_func.split('(')[0].strip()
        else:
            func_name = mapped_func
        function_names.append(func_name)
    return function_names

def analyze_dataset_correlation(filepath: str, dataset_name: str) -> Tuple[Dict[str, int], List[Set[str]]]:
    """分析单个数据集的操作符使用频率和共现关系"""
    print(f"正在分析 {dataset_name} 数据集的关联性...")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {filepath}")
        return {}, []
    except json.JSONDecodeError as e:
        print(f"错误: JSON解析失败 {e}")
        return {}
    
    func_counter = Counter()
    sql_function_sets = []  # 每个SQL中出现的函数集合
    total_sql = len(data)
    
    for i, item in enumerate(data):
        if i % 1000 == 0:
            print(f"     处理进度: {i}/{total_sql}")
        
        sql = item.get('SQL', '').strip()
        if not sql:
            continue
            
        operators = extract_operators_from_sql(sql)
        if operators:
            # 将操作符映射为函数名
            function_names = map_operators_to_functions(operators)
            # 去重
            unique_functions = list(set(function_names))
            # 过滤掉"未映射"
            valid_functions = [f for f in unique_functions if f != "未映射"]
            
            if valid_functions:
                func_counter.update(valid_functions)
                sql_function_sets.append(set(valid_functions))
    
    print(f"     {dataset_name} 数据集分析完成，共处理 {total_sql} 条SQL")
    return dict(func_counter), sql_function_sets

def calculate_correlation_matrix(function_sets: List[Set[str]]) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """计算函数之间的关联性矩阵"""
    print("计算关联性矩阵...")
    
    # 统计每个函数的总出现次数
    all_functions = set()
    for func_set in function_sets:
        all_functions.update(func_set)
    
    func_freq = Counter()
    for func_set in function_sets:
        func_freq.update(func_set)
    
    print(f"总共发现 {len(all_functions)} 个函数")
    
    # 计算共现矩阵
    cooccurrence = defaultdict(int)
    total_sqls = len(function_sets)
    
    for func_set in function_sets:
        if len(func_set) >= 2:
            for func1, func2 in combinations(func_set, 2):
                cooccurrence[(func1, func2)] += 1
                cooccurrence[(func2, func1)] += 1
    
    # 构建关联性矩阵
    funcs_list = sorted(all_functions)
    correlation_matrix = pd.DataFrame(0, index=funcs_list, columns=funcs_list)
    
    for (func1, func2), count in cooccurrence.items():
        if func1 in funcs_list and func2 in funcs_list:
            correlation_matrix.loc[func1, func2] = count
    
    # 计算Jaccard相似度
    jaccard_matrix = pd.DataFrame(0.0, index=funcs_list, columns=funcs_list)
    
    for i, func1 in enumerate(funcs_list):
        for j, func2 in enumerate(funcs_list):
            if i == j:
                jaccard_matrix.loc[func1, func2] = 1.0
            else:
                # 计算包含func1的SQL数量
                func1_count = sum(1 for func_set in function_sets if func1 in func_set)
                # 计算包含func2的SQL数量
                func2_count = sum(1 for func_set in function_sets if func2 in func_set)
                # 计算同时包含func1和func2的SQL数量
                both_count = cooccurrence.get((func1, func2), 0)
                
                if func1_count + func2_count - both_count > 0:
                    jaccard = both_count / (func1_count + func2_count - both_count)
                    jaccard_matrix.loc[func1, func2] = jaccard
    
    return jaccard_matrix, dict(func_freq)

def find_strong_correlations(correlation_matrix: pd.DataFrame, threshold: float = 0.1) -> List[Tuple[str, str, float]]:
    """找出强关联的函数对"""
    strong_correlations = []
    
    for i in range(len(correlation_matrix.index)):
        for j in range(i + 1, len(correlation_matrix.columns)):
            func1 = correlation_matrix.index[i]
            func2 = correlation_matrix.columns[j]
            correlation = correlation_matrix.iloc[i, j]
            
            if correlation >= threshold:
                strong_correlations.append((func1, func2, correlation))
    
    # 按关联强度排序
    strong_correlations.sort(key=lambda x: x[2], reverse=True)
    return strong_correlations

def analyze_function_clusters(correlation_matrix: pd.DataFrame, threshold: float = 0.05) -> List[List[str]]: # Changed threshold to 0.05
    """分析函数聚类"""
    print("分析函数聚类...")
    
    # 使用简单的聚类方法：基于关联强度
    clusters = []
    used_funcs = set()
    
    for func1 in correlation_matrix.index:
        if func1 in used_funcs:
            continue
            
        cluster = [func1]
        used_funcs.add(func1)
        
        for func2 in correlation_matrix.index:
            if func2 not in used_funcs:
                correlation = correlation_matrix.loc[func1, func2]
                if correlation >= threshold:
                    cluster.append(func2)
                    used_funcs.add(func2)
        
        if len(cluster) > 1:  # 只保留有多个函数的聚类
            clusters.append(cluster)
    
    return clusters

def main():
    """主函数"""
    print("=== 分析SQL函数关联性（按函数名分组） ===")
    
    # 数据集文件路径
    train_file = '/home/shenshuyu/SQL_tool/work/bird/train/train.json'
    dev_file = '/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev.json'
    
    # 分析数据集
    train_stats, train_sets = analyze_dataset_correlation(train_file, "训练")
    dev_stats, dev_sets = analyze_dataset_correlation(dev_file, "开发")
    
    # 合并数据集
    all_sets = train_sets + dev_sets
    print(f"总共分析 {len(all_sets)} 条SQL")
    
    # 计算关联性矩阵（不进行频率过滤）
    correlation_matrix, func_freq = calculate_correlation_matrix(all_sets)
    
    # 找出强关联
    strong_correlations = find_strong_correlations(correlation_matrix, threshold=0.05)
    
    # --- 恢复表格输出部分 ---
    print(f"\n=== 强关联函数对 (Jaccard相似度 >= 0.05) ===")
    print(f"{'函数1':<20} {'函数2':<20} {'Jaccard相似度':<15} {'共现次数':<10}")
    print("-" * 70)
    for func1, func2, correlation in strong_correlations[:50]:
        # To correctly display cooccurrence_count for the printed table:
        # We need to re-calculate it as 'correlation_matrix.loc[func1, func2]' directly reflects
        # the count used to build the matrix, which should be consistent with the Jaccard if both_count was used properly.
        # However, for absolute clarity and to address the previous contradiction,
        # let's derive it directly from the combined sets if it's truly problematic.
        # But given that 'cooccurrence' dict already stores this, let's use that.
        
        # Access the cooccurrence count from the dictionary directly
        # Note: 'calculate_correlation_matrix' returns jaccard_matrix and func_freq,
        # but 'cooccurrence' dict itself is local to that function.
        # For simplicity and correctness in this display loop, we can either:
        # 1. Pass the 'cooccurrence' dict back from calculate_correlation_matrix.
        # 2. Re-calculate both_count here (less efficient).
        # 3. Trust correlation_matrix.loc[func1, func2] as it was built from cooccurrence.
        
        # Sticking with correlation_matrix.loc[func1, func2] as it's the direct output
        # from the matrix calculation that drives the correlation.
        cooccurrence_val = correlation_matrix.loc[func1, func2]
        print(f"{func1:<20} {func2:<20} {correlation:<15.4f} {int(cooccurrence_val):<10}")
    
    # 分析函数聚类
    # Changed the threshold in the function call here
    clusters = analyze_function_clusters(correlation_matrix, threshold=0.05)
    
    # --- 恢复表格输出部分 ---
    print(f"\n=== 函数聚类分析 (Jaccard相似度 >= 0.05) ===") # Updated header
    for i, cluster in enumerate(clusters, 1):
        print(f"聚类 {i}: {', '.join(cluster)}")
    
    # --- 删除文件保存部分 ---
    # output_file = 'tongji/function_correlation_analysis.json'
    # try:
    #     os.makedirs(os.path.dirname(output_file), exist_ok=True)
    #     save_data = {
    #         'summary': {
    #             'total_sqls': len(all_sets),
    #             'total_functions': len(correlation_matrix.index),
    #             'strong_correlations': len(strong_correlations),
    #             'clusters': len(clusters)
    #         },
    #         'strong_correlations': [
    #             {
    #                 'function1': func1,
    #                 'function2': func2,
    #                 'jaccard_similarity': float(correlation),
    #                 'cooccurrence_count': int(correlation_matrix.loc[func1, func2])
    #             }
    #             for func1, func2, correlation in strong_correlations
    #         ],
    #         'clusters': clusters,
    #         'function_frequencies': func_freq
    #     }
    #     with open(output_file, 'w', encoding='utf-8') as f:
    #         json.dump(save_data, f, ensure_ascii=False, indent=2)
    #     print(f"\n详细结果已保存到: {output_file}")
        
    #     csv_file = 'tongji/function_correlation_matrix.csv'
    #     correlation_matrix.to_csv(csv_file)
    #     print(f"关联性矩阵已保存到: {csv_file}")
        
    #     strong_corr_csv = 'tongji/strong_function_correlations.csv'
    #     with open(strong_corr_csv, 'w', newline='', encoding='utf-8') as f:
    #         writer = csv.writer(f)
    #         writer.writerow(['函数1', '函数2', 'Jaccard相似度', '共现次数'])
    #         for func1, func2, correlation in strong_correlations:
    #             cooccurrence_val = correlation_matrix.loc[func1, func2]
    #             writer.writerow([func1, func2, f"{correlation:.4f}", int(cooccurrence_val)])
    #     print(f"强关联函数对已保存到: {strong_corr_csv}")
        
    # except Exception as e:
    #     print(f"保存文件失败: {e}")

if __name__ == "__main__":
    main()