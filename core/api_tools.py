import pandas as pd
import json
import re
import sqlite3
import sys
import Levenshtein
import os
import numpy as np
import pprint
# from sklearn.feature_extraction.text import TfidfVectorizer
# from sklearn.metrics.pairwise import cosine_similarity
import random
import sqlparse
import time
import math
from .data_filter import data_filter
import pandas as pd
from sqlalchemy import *
# import spacy
# nlp = spacy.load("en_core_web_md")
# 默认数据集路径
# DEFAULT_DATASET_PATH = "/home/shenshuyu/SQL_dataset/train/train_databases"
DEFAULT_DATASET_PATH = "/home/shenshuyu/SQL_tool/data/dev_databases"
def get_tables(db_name, columns):
    """
    查找包含指定列的表名，如果不存在则报错并提供最接近的列名建议。

    参数:
        db_name (str): 数据库名称。
        columns (list): 一个包含要查找的列名字符串的列表。

    返回:
        str: 包含指定列的表名（如果找到）
    """
    db_path = f"{DEFAULT_DATASET_PATH}/{db_name}/{db_name}.sqlite"
    if type(columns) == str:
        columns = [columns]

    # 检查数据库是否存在
    if not os.path.exists(db_path):
        available_dbs = []
        try:
            db_dir = DEFAULT_DATASET_PATH
            available_dbs = [d for d in os.listdir(db_dir) if os.path.isdir(os.path.join(db_dir, d))]
        except:
            pass
        raise Exception(f"数据库 '{db_name}' 不存在。可用的数据库: {', '.join(available_dbs)}")

    all_tables = []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        all_tables = [table[0] for table in cursor.fetchall()]
        conn.close()
    except Exception as e:
        raise Exception(f"无法获取数据库 '{db_name}' 中的表名: {str(e)}")

    if not all_tables:
        raise Exception(f"数据库 '{db_name}' 中没有可用的表。")

    all_available_columns = {} # 存储 {table_name: [col1, col2, ...]}

    for table_name in all_tables:
        try:
            df = db_loader(db_name, table_name)
            if not df.empty:
                all_available_columns[table_name] = [col for col in df.columns.tolist()] # 保持原始大小写
        except Exception as e:
            # 忽略无法加载的表，通常是元数据表或空表
            pass

    # 将用户输入的列名转换为小写以便匹配，但在建议中保留原始大小写
    target_columns_lower = {col: col.lower() for col in columns}

    # 检查所有目标列是否存在于某个表中
    # 这个字典用于存储每个目标列在哪个表中找到
    columns_found_in_tables = {col_lower: [] for col_lower in target_columns_lower.values()}
    
    for table_name, table_cols_original_case in all_available_columns.items():
        table_cols_lower = [col.lower() for col in table_cols_original_case] # 用于匹配
        for original_target_col, target_col_lower in target_columns_lower.items():
            if target_col_lower in table_cols_lower:
                columns_found_in_tables[target_col_lower].append(table_name)

    # 找出所有目标列都存在的表
    candidate_tables = set()
    first_col = True
    for target_col_lower in target_columns_lower.values():
        if first_col:
            candidate_tables.update(columns_found_in_tables[target_col_lower])
            first_col = False
        else:
            candidate_tables.intersection_update(columns_found_in_tables[target_col_lower])

    if candidate_tables:
        return f"列 {', '.join(columns)} 存在于表: {', '.join(list(candidate_tables))}"
    else:
        # 如果没有找到所有列都在的表，则尝试找出最相似的列
        suggestions = []
        
        # 收集所有表的列名和原始大小写到大列表中，用于计算Levenshtein距离
        all_db_column_info = [] # [(col_original_case, col_lower_case, table_name), ...]
        for table_name, table_cols_original_case in all_available_columns.items():
            for col_original in table_cols_original_case:
                all_db_column_info.append((col_original, col_original.lower(), table_name))

        # 限制计算Levenshtein距离的样本数量
        max_samples_for_distance = 100 # 可以调整这个值
        if len(all_db_column_info) > max_samples_for_distance:
            sample_column_info = random.sample(all_db_column_info, max_samples_for_distance)
        else:
            sample_column_info = all_db_column_info


        for original_target_col, target_col_lower in target_columns_lower.items():
            column_distances = [] # [(distance, original_col_name, table_name), ...]
            
            for col_original, col_lower, table_name in sample_column_info:
                distance = Levenshtein.distance(target_col_lower, col_lower)
                column_distances.append((distance, col_original, table_name))
            
            # 按距离排序，获取前3个
            column_distances.sort(key=lambda x: x[0])
            top_3_suggestions = column_distances[:2]

            col_suggestions_for_current_target = []
            for dist, suggested_col, suggested_table in top_3_suggestions:
                col_suggestions_for_current_target.append(f"表 '{suggested_table}' 中的列 '{suggested_col}'")
            
            suggestions.append(f"对于列 '{original_target_col}'，最接近的3个建议是: {'; '.join(col_suggestions_for_current_target)}")

        error_message = f"错误：未找到所有列 {', '.join(columns)} 都存在的表。\n"
        error_message += "可能的相关建议:\n" + "\n".join(suggestions)
        raise Exception(error_message)

def db_loader(db_name, target_table):
    """加载指定数据库中的表格数据"""
    # 如果CSV加载失败或者是元数据表，尝试从SQLite数据库加载
    db_path = f"{DEFAULT_DATASET_PATH}/{db_name}/{db_name}.sqlite"
    try:
        conn = sqlite3.connect(db_path)
        # 为表名添加引号，避免SQL关键字冲突
        quoted_table = f'"{target_table}"'
        query = f"SELECT * FROM {quoted_table}"
        data = pd.read_sql_query(query, conn)
        conn.close()
        return data
    except Exception as e:
        # 检查是否是表不存在的错误
        if "no such table" in str(e).lower():
            available_tables = []
            try:
                # 尝试获取数据库中的所有表
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                available_tables = [table[0] for table in cursor.fetchall()]
                conn.close()
                
                raise Exception(f"表 '{target_table}' 不存在于数据库 '{db_name}' 中。可用的表: {', '.join(available_tables)}")
            except:
                pass
        
        # 检查数据库是否存在
        if not os.path.exists(db_path):
            available_dbs = []
            try:
                # 尝试列出所有可用的数据库
                db_dir = DEFAULT_DATASET_PATH
                available_dbs = [d for d in os.listdir(db_dir) if os.path.isdir(os.path.join(db_dir, d))]
                
                raise Exception(f"数据库 '{db_name}' 不存在。可用的数据库: {', '.join(available_dbs)}")
            except:
                pass
        
        raise Exception(f"{target_table}: {str(e)}")


def group_and_aggregate_df(data, group_by_columns, agg_operations):
    """
    对 DataFrame 进行分组和聚合操作。

    参数:
    - data (pd.DataFrame): 输入的 DataFrame。
    - group_by_columns (str 或 list): 用于分组的列名，可以是单个字符串或列名列表。
    - agg_operations (dict): 聚合操作的字典。
                              **键为新列名**，**值为一个元组 ('原始列名', '聚合函数名')**。
                              例如: {'total_amount': ('amount', 'sum'), 'num_payments': ('paymentDate', 'count')}
                              聚合函数名必须是 Pandas 支持的字符串（如 'sum', 'mean' 等）。

    返回:
    - pd.DataFrame: 包含分组和聚合结果的新 DataFrame。
    """
    if not isinstance(data, pd.DataFrame):
        raise ValueError("输入 'data' 必须是 Pandas DataFrame 类型。")

    if isinstance(group_by_columns, str):
        group_by_columns = [group_by_columns]

    # 检查分组列是否存在
    for col in group_by_columns:
        if col not in data.columns:
            raise ValueError(f"分组列 '{col}' 在 DataFrame 中不存在。可用列: {', '.join(data.columns)}")

    # 准备用于 Pandas .agg() 方法的字典
    # 键为新列名，值为 ('原始列名', '聚合函数名') 元组
    pandas_agg_dict = {}
    for new_col_name, agg_tuple_value in agg_operations.items():
        if not isinstance(agg_tuple_value, tuple) or len(agg_tuple_value) != 2:
            raise ValueError(f"聚合操作 '{new_col_name}' 的值必须是形如 ('原始列名', '聚合函数名') 的元组。")
        original_col_name, agg_func_name = agg_tuple_value
        if original_col_name not in data.columns:
            raise ValueError(f"聚合列 '{original_col_name}' 在 DataFrame 中不存在。可用列: {', '.join(data.columns)}")
        pandas_agg_dict[new_col_name] = (original_col_name, agg_func_name) # Pandas agg 期望的格式

    # 执行分组和聚合
    # .agg() 方法可以接受字典，键为新列名，值为 (原列名, 聚合函数)
    grouped_data = data.groupby(group_by_columns).agg(**pandas_agg_dict).reset_index() # 使用 ** 展开字典

    return grouped_data

import pandas as pd
import re

def get_value(data, argument):
    # Normalize "*,count" to "count(*)"
    if argument.lower().replace(" ", "") == "*,count":
        argument = "count(*)"

    try:
        # Handle list data type
        if isinstance(data, list):
            if not data:
                return []
            
            # If list elements are dictionaries, convert to DataFrame
            if isinstance(data[0], dict):
                data = pd.DataFrame(data)
            else:
                # Original list processing logic remains
                # Check for explicit operation (e.g., 'mean', 'count')
                commands = [cmd.strip() for cmd in argument.split(',')]
                operation = commands[-1].strip().lower() if len(commands) > 1 else None

                # Pattern to extract column and operation from "operation(column)"
                match_func = re.match(r'([a-zA-Z_]+)\((.*?)\)', argument.strip())
                if match_func:
                    operation = match_func.group(1).lower()
                    # For list, the column doesn't directly apply, so we assume operation on the list itself.

                if operation == 'mean':
                    numeric_data = [x for x in data if isinstance(x, (int, float))]
                    return sum(numeric_data) / len(numeric_data) if numeric_data else 0
                elif operation == 'max':
                    numeric_data = [x for x in data if isinstance(x, (int, float))]
                    return max(numeric_data) if numeric_data else None
                elif operation == 'min':
                    numeric_data = [x for x in data if isinstance(x, (int, float))]
                    return min(numeric_data) if numeric_data else None
                elif operation == 'sum':
                    numeric_data = [x for x in data if isinstance(x, (int, float))]
                    return sum(numeric_data) if numeric_data else 0
                elif operation == 'list':
                    return data # For lists, 'list' operation means return the whole list
                elif operation == 'count':
                    return len(data)
                else:
                    return data # If no specific operation, return the list

        # DataFrame processing logic
        # Auxiliary function: clean column name (defined inside for scope or outside globally)
        def clean_column_name(col_name):
            while col_name and (col_name[0] in ['[', "'", '"', '`']):
                col_name = col_name[1:]
            while col_name and (col_name[-1] in [']', "'", '"', '`']):
                col_name = col_name[:-1]
            return col_name.strip()

        # Pattern to extract column and operation from "operation(column)" or "column, operation"
        operation = None
        column_name = None
        
        # Try parsing "operation(column)" first
        match_func = re.match(r'([a-zA-Z_]+)\((.*?)\)', argument.strip())
        if match_func:
            operation = match_func.group(1).lower()
            column_name = clean_column_name(match_func.group(2).strip())
        else:
            # Try parsing "column, operation"
            commands = [cmd.strip() for cmd in argument.split(',')]
            if len(commands) > 1:
                last_param = commands[-1].strip().lower()
                if last_param in ['mean', 'max', 'min', 'sum', 'list', 'count', "count(*)"]:
                    operation = last_param
                    column_name = clean_column_name(commands[0])
            else:
                # If just a single command, it's either a column name or count(*)
                potential_col_or_count = clean_column_name(argument)
                if potential_col_or_count.lower() == 'count(*)':
                    operation = 'count(*)'
                    column_name = None # No specific column for count(*)
                else:
                    column_name = potential_col_or_count
                    operation = None # No explicit operation, just return the column values

        # --- Execute based on parsed operation and column ---
        if operation == 'count(*)':
            return len(data)

        if column_name and column_name not in data.columns:
            raise Exception(f"列 '{column_name}' 在数据中不存在。可用列: {', '.join(data.columns)}")
        
        if operation: # An explicit operation was specified (e.g., 'sum(cost)' or 'cost, sum')
            if 'mean' == operation:
                res_series = pd.to_numeric(data[column_name], errors='coerce')
                numeric_list = res_series.dropna().tolist()
                return sum(numeric_list) / len(numeric_list) if numeric_list else 0
            elif 'max' == operation:
                res_series = pd.to_numeric(data[column_name], errors='coerce')
                numeric_list = res_series.dropna().tolist()
                return max(numeric_list) if numeric_list else None
            elif 'min' == operation:
                res_series = pd.to_numeric(data[column_name], errors='coerce')
                numeric_list = res_series.dropna().tolist()
                return min(numeric_list) if numeric_list else None
            elif 'sum' == operation:
                res_series = pd.to_numeric(data[column_name], errors='coerce')
                numeric_list = res_series.dropna().tolist()
                return sum(numeric_list) if numeric_list else 0
            elif 'list' == operation:
                res_list = data[column_name].dropna().tolist()
                return res_list
            elif 'count' == operation:
                return data[column_name].count()
            else:
                raise Exception(f"操作 '{operation}' 包含语法错误。请检查参数。")
        elif column_name: # No explicit operation, just a column name(s)
            # This handles single column returns (e.g., 'client_id')
            if len(data) == 1:
                return data[column_name].iloc[0]
            else:
                return data[column_name].dropna().tolist()
        else: # This path should ideally not be reached if argument is valid.
            raise Exception(f"参数 '{argument}' 无法解析。请检查语法。")

    except Exception as e:
        import traceback
        error_msg = f"处理数据时出错: {str(e)}\n"
        error_msg += f"数据类型: {type(data)}\n"
        error_msg += f"参数: {argument}\n"
        error_msg += traceback.format_exc()
        raise Exception(error_msg)
# Auxiliary function: clean column name (moved outside to be accessible)
def clean_column_name(col_name):
    while col_name and (col_name[0] in ['[', "'", '"', '`']):
        col_name = col_name[1:]
    while col_name and (col_name[-1] in [']', "'", '"', '`']):
        col_name = col_name[:-1]
    return col_name.strip()
def sql_interpreter(db_name, command):
    """执行SQL命令并返回结果DataFrame"""
    try:
        db_path = f"{DEFAULT_DATASET_PATH}/{db_name}/{db_name}.sqlite"
        
        # 检查数据库文件是否存在
        if not os.path.exists(db_path):
            raise Exception(f"数据库文件不存在: {db_path}")
            
        con = sqlite3.connect(db_path)
        
        # 直接使用pandas的read_sql_query返回DataFrame
        try:
            results = pd.read_sql_query(command, con)
        except Exception as e:
            # 如果出错，尝试使用cursor方式
            try:
                cur = con.cursor()
                results_list = cur.execute(command).fetchall()
                
                # 获取列名
                column_names = [description[0] for description in cur.description]
                
                # 转换为DataFrame
                results = pd.DataFrame(results_list, columns=column_names)
            except Exception as inner_e:
                con.close()
                raise Exception(f"SQL查询执行失败: {str(inner_e)}")
        
        con.close()
        return results
    except Exception as e:
        raise Exception(f"SQL解释器错误: {str(e)}")

def controlled_print(*args, max_len=1000):
    # 将所有参数合并为一个字符串
    data_str = " ".join(str(arg) for arg in args)
    
    if len(data_str) > max_len:
        print(data_str[:max_len] + "...")
    else:
        print(data_str)

def list_summary(data, max_items=50, show_stats=True, max_str_length=1000):
    """
    打印数据的摘要，包括前几个元素、统计信息等
    
    Args:
        data: 要显示的数据（列表、字符串）
        max_items (int): 最大显示项目数，默认50
        show_stats (bool): 是否显示统计信息，默认True
        max_str_length (int): 字符串最大显示长度，默认50
    
    Returns:
        str: 处理后的摘要文本
    """
    if data is None:
        return "无数据"
    
    # 处理字符串类型
    if isinstance(data, str):
        return _summarize_string(data, max_str_length, show_stats)
    
    # 转换为列表以统一处理
    if not isinstance(data, (list, tuple, set)):
        try:
            data = list(data)
        except Exception as e:
            return f"{str(data)} (无法转换为列表: {str(e)})"
    
    # 获取数据长度
    data_length = len(data)
    
    # 如果数据已经足够短，直接返回
    if data_length <= max_items:
        result = str(data)
        if show_stats and data_length > 0:
            result = _get_stats_info(data)
        print(result)
        return result
    
    # 处理过长的列表
    head_items = max_items // 2
    tail_items = max_items - head_items
    
    # 获取前几个和后几个元素
    if isinstance(data, set):
        # 集合需要先转换为列表才能切片
        data_list = list(data)
        head_data = data_list[:head_items]
        tail_data = data_list[-tail_items:]
    else:
        head_data = data[:head_items]
        tail_data = data[-tail_items:]
    
    # 构建摘要字符串
    if isinstance(data, list):
        result = f"[{', '.join(map(str, head_data))}, ... (省略了 {data_length - max_items} 项) ..., {', '.join(map(str, tail_data))}]"
    elif isinstance(data, tuple):
        result = f"({', '.join(map(str, head_data))}, ... (省略了 {data_length - max_items} 项) ..., {', '.join(map(str, tail_data))})"
    elif isinstance(data, set):
        result = f"{{{', '.join(map(str, head_data))}, ... (省略了 {data_length - max_items} 项) ..., {', '.join(map(str, tail_data))}}}"
    else:
        result = f"[{', '.join(map(str, head_data))}, ... (省略了 {data_length - max_items} 项) ..., {', '.join(map(str, tail_data))}]"
    
    # 添加统计信息
    if show_stats and data_length > 0:
        result += _get_stats_info(data)

    print(result)
    return result


def _summarize_string(text, max_length=50, show_stats=True):
    """处理字符串数据的摘要"""
    if len(text) <= max_length:
        result = text
    else:
        half_length = max_length // 2
        result = f"{text[:half_length]}... (省略了 {len(text) - max_length} 个字符) ...{text[-half_length:]}"
    
    if show_stats:
        stats = "\n[字符串统计: "
        stats += f"长度: {len(text)}"
        stats += "]"
        result += stats
    print(result)
    return result

def _get_stats_info(data):
    """获取数据的统计信息"""
    stats = "\n[数据统计: "
    
    # 数据长度
    stats += f"总数量: {len(data)}"
    
    # 不同值的数量和示例
    try:
        # 分别处理字符串和非字符串值
        str_values = [x for x in data if isinstance(x, str)]
        non_str_values = [x for x in data if not isinstance(x, str)]
        
        # 字符串值统计
        if str_values:
            distinct_str = list(set(str_values))
            stats += f", 不同字符串数量: {len(distinct_str)}"
            
            # 显示不同的字符串值（最多50个）
            if distinct_str:
                # 随机选择最多50个样本值
                if len(distinct_str) > 50:
                    sample_values = random.sample(distinct_str, 50)
                    sample_str = ', '.join(str(x) for x in sample_values)
                    # 添加随机抽样提示
                    sample_str += " (随机抽样)"
                else:
                    sample_str = ', '.join(str(x) for x in distinct_str)
                stats += f", 不同字符串值: [{sample_str}]"
        
        # 非字符串值统计
        if non_str_values:
            distinct_non_str = list(set(non_str_values))
            stats += f", 不同非字符串数量: {len(distinct_non_str)}"
            
            # 显示不同的非字符串值（最多50个）并标注类型
            if distinct_non_str:
                # 随机选择最多50个非字符串样本值
                if len(distinct_non_str) > 50:
                    sample_non_str = random.sample(distinct_non_str, 50)
                    is_random_sample = True
                else:
                    sample_non_str = distinct_non_str
                    is_random_sample = False
                
                sample_non_str_display = []
                for x in sample_non_str:
                    type_name = type(x).__name__
                    sample_non_str_display.append(f"{x}({type_name})")
                
                if len(distinct_non_str) <= 50:
                    stats += f", 不同非字符串值: [{', '.join(sample_non_str_display)}]"
                else:
                    stats += f", 部分不同非字符串值(50/{len(distinct_non_str)}): [{', '.join(sample_non_str_display)}]"
                    if is_random_sample:
                        stats += " (随机抽样)"
    except Exception as e:
        # 如果元素不可哈希，记录错误信息
        stats += f", 无法计算不同值统计: {str(e)}"
    
    # 尝试获取数值统计
    try:
        # 更健壮的数值检测
        numeric_data = []
        for x in data:
            try:
                # 尝试转换为浮点数
                num_val = float(x)
                numeric_data.append(num_val)
            except (ValueError, TypeError):
                # 如果转换失败，跳过
                continue
        
        if numeric_data:
            stats += f", 最小值: {min(numeric_data)}"
            stats += f", 最大值: {max(numeric_data)}"
            stats += f", 平均值: {sum(numeric_data)/len(numeric_data):.2f}"
    except Exception as e:
        # 记录错误信息
        stats += f", 无法计算数值统计: {str(e)}"
    
    stats += "]"
    return stats

# def find_similar_text(query_text, text_corpus, top_n=5):
#     """
#     查找与查询文本最相似的文本
    
#     Args:
#         query_text (str): 查询文本
#         text_corpus (list): 文本语料库列表
#         top_n (int): 返回的最相似文本数量，默认5
    
#     Returns:
#         list: 最相似文本的列表，按相似度降序排列
#     """
#     # 处理空输入情况
#     if not query_text or not text_corpus:
#         return []
    
#     query_doc = nlp(query_text)
#     similarities = []
#     text_corpus = list(set(text_corpus))
#     # 如果数量超过100，随机选择100个
#     if len(text_corpus) > 100:
#         text_corpus = random.sample(text_corpus, 100)
#     for text in text_corpus:
#         # 确保文本是字符串类型
#         if not isinstance(text, str):
#             text = str(text)
        
#         doc = nlp(text)
#         similarity = query_doc.similarity(doc)
#         similarities.append((text, similarity))
    
#     # 按相似度降序排序并返回前top_n个结果
#     ranked_results = sorted(similarities, key=lambda x: x[1], reverse=True)[:top_n]
#     # 只返回文本，不返回相似度分数
#     return [item[0] for item in ranked_results]





# if __name__ == "__main__":
    # # 测试函数效果
    # db_name = "california_schools"
    # sql_query = "SELECT `Free Meal Count (Ages 5-17)` / `Enrollment (Ages 5-17)` FROM frpm WHERE `Educational Option Type` = 'Continuation School' AND `Free Meal Count (Ages 5-17)` / `Enrollment (Ages 5-17)` IS NOT NULL ORDER BY `Free Meal Count (Ages 5-17)` / `Enrollment (Ages 5-17)` ASC LIMIT 3"
    
    # print("测试 sql_interpreter 函数:")
    # results = sql_interpreter(db_name, sql_query)
    # print(f"SQL查询结果: {results}")
    
    # try:
    #     # 加载frpm表
    #     frpm_data = db_loader(db_name, "frpm")
    #     print(f"成功加载frpm表，列名: {', '.join(frpm_data.columns.tolist())}")
    #     print(f"数据行数: {len(frpm_data)}")
    #     print("前3行数据:")
    #     print(frpm_data.head(3))
        
    #     # 测试数据过滤
    #     print("\n测试 data_filter 函数:")
    #     filtered_data = data_filter(frpm_data, "Educational Option Type='Continuation School'")
    #     print(f"过滤后数据行数: {len(filtered_data)}")
    #     if len(filtered_data) > 0:
    #         print("过滤后前3行数据:")
    #         print(filtered_data.head(3))
            
    #         # 测试获取值
    #         print("\n测试 get_value 函数:")
    #         value = get_value(filtered_data.head(3), "Free Meal Count (Ages 5-17)")
    #         print(f"获取的值: {value}")
            
    #         # 完整过滤条件
    #         filtered_data = data_filter(frpm_data, "Educational Option Type='Continuation School'")

    #         # 添加非空检查
    #         filtered_data = filtered_data[filtered_data['Free Meal Count (Ages 5-17)'].notna() & 
    #                                      filtered_data['Enrollment (Ages 5-17)'].notna() & 
    #                                      (filtered_data['Enrollment (Ages 5-17)'] > 0)]  # 避免除以零

    #         # 计算比率
    #         filtered_data['ratio'] = filtered_data['Free Meal Count (Ages 5-17)'] / filtered_data['Enrollment (Ages 5-17)']

    #         # 按比率升序排序
    #         sorted_data = filtered_data.sort_values('ratio')

    #         # 取前三条记录
    #         top3 = sorted_data.head(3)

    #         # 计算比率值
    #         ratio_values = top3['ratio'].tolist()
    #         print(f"排序后的比率值: {ratio_values}")
            
    # except Exception as e:
    #     print(f"测试过程中出现错误: {str(e)}")

    # 测试 db_loader 函数示例


    
    # 示例用法
    # corpus = ["This is the first document.", "This document is the second document.", "And this is the third one.", "Is this the first document?"]
    # query = "this is a document"
    # similar_texts = find_similar_text(query, corpus)

    # mixed_data = ["apple", "banana", 1, 2, 3, "apple", "banana", 1, 2, 3] * 10
    # print(print_summary(mixed_data))
    # corpus = [    "The quick brown fox jumps over the lazy dog",
    # "A fast brown fox leaps over a sleepy canine",
    # "The lazy dog sleeps in the sun",
    # "Artificial intelligence is transforming the world",
    # "Machine learning algorithms can recognize patterns"]

    # print("\n测试 db_loader 函数示例:")
    # try:
    #     # 示例: patients_data = db_loader("hospital", "patients")
    #     # 使用实际存在的数据库和表
    #     patients_data = db_loader("california_schools", "frpm")
    #     print(f"成功加载表，列名: {', '.join(patients_data.columns.tolist())}")
    #     print(f"数据行数: {len(patients_data)}")
    # except Exception as e:
    #     print(f"db_loader 示例测试失败: {str(e)}")

    # # 测试 data_filter 函数示例
    # print("\n测试 data_filter 函数示例:")
    # try:
    #     # 示例: filtered_data = data_filter(patients_data, "age>=65||gender=female")
    #     # 使用实际存在的数据和列
    #     frpm_data = db_loader("california_schools", "frpm")
    #     filtered_data = data_filter(frpm_data, "Educational Option Type='Continuation School'||County Name='Los Angeles'")
    #     print(f"过滤后数据行数: {len(filtered_data)}")
    #     if len(filtered_data) > 0:
    #         print("过滤后前3行数据:")
    #         print(filtered_data.head(3))
    # except Exception as e:
    #     print(f"data_filter 示例测试失败: {str(e)}")

    # # 测试 get_value 函数示例
    # print("\n测试 get_value 函数示例:")
    # try:
    #     # 示例1: patient_name = get_value(patient_data, "name")
    #     frpm_data = db_loader("california_schools", "frpm")
    #     filtered_data = data_filter(frpm_data, "Educational Option Type='Continuation School'").head(1)
    #     county_name = get_value(filtered_data, "County Name")
    #     print(f"获取单值示例 - 县名: {county_name}")
        
    #     # 示例2: ages = get_value(patients_data, "age, list")
    #     enrollment_list = get_value(filtered_data, "Enrollment (Ages 5-17), list")
    #     print(f"获取列表示例 - 入学人数列表: {enrollment_list}")
        
    #     # 示例3: avg_age = get_value(patients_data, "age, mean")
    #     # 使用多行数据计算平均值
    #     filtered_data = data_filter(frpm_data, "Educational Option Type='Continuation School'").head(5)
    #     avg_enrollment = get_value(filtered_data, "Enrollment (Ages 5-17), mean")
    #     print(f"计算平均值示例 - 平均入学人数: {avg_enrollment}")
    # except Exception as e:
    #     print(f"get_value 示例测试失败: {str(e)}")

    # # 测试 sql_interpreter 函数示例
    # print("\n测试 sql_interpreter 函数示例:")
    # try:
    #     # 示例: results = sql_interpreter("hospital", "SELECT * FROM patients WHERE age > 65")
    #     results = sql_interpreter("california_schools", "SELECT * FROM frpm WHERE `Educational Option Type` = 'Continuation School' LIMIT 3")
    #     print(f"SQL查询结果前3行: {results[:3]}")
    # except Exception as e:
    #     print(f"sql_interpreter 示例测试失败: {str(e)}")