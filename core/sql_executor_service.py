"""
SQL执行器服务 - 独立服务架构

参考Reward-SQL的实现，将SQL执行器独立为Flask服务
优点：
1. 避免重复加载数据库连接
2. 支持并发请求
3. 可以部署到多台机器
4. 统一管理数据库连接池

使用方法：
1. 启动服务：
   python core/sql_executor_service.py --port 5887

2. 在代码中使用：
   from core.sql_executor_client import SQLExecutorClient
   client = SQLExecutorClient("http://localhost:5887")
   result, error = client.execute_sql(sql, db_name)
"""

import sqlite3
import os
import json
import traceback
import argparse
from flask import Flask, request, jsonify
from functools import wraps
from typing import Tuple, Optional, Dict, Any
import pandas as pd
import time
from collections import Counter

app = Flask(__name__)

# 数据库路径配置（与DatabaseConnector保持一致）
DEFAULT_DB_PATHS = [
    "/home/shenshuyu/SQL_tool/data/dev_databases",
    "/ssd/shenshuyu/work/bird/dev_20240627/dev_databases",
    "/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_databases",
    "/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_databases"  # 重复但保留以兼容
]

# 也可以从环境变量读取
import os
if os.getenv('DB_BASE_PATH'):
    DEFAULT_DB_PATHS.insert(0, os.getenv('DB_BASE_PATH'))


def find_db_path(db_name: str) -> Optional[str]:
    """查找数据库文件路径"""
    for base_path in DEFAULT_DB_PATHS:
        db_path = os.path.join(base_path, f"{db_name}/{db_name}.sqlite")
        if os.path.exists(db_path):
            return db_path
    return None


def execute_sql_in_subprocess(sql: str, db_path: str, timeout: int = 60) -> Tuple[Any, Optional[str]]:
    """
    在子进程中执行SQL（避免阻塞主进程）
    
    Args:
        sql: SQL查询
        db_path: 数据库文件路径
        timeout: 超时时间（秒）
    
    Returns:
        (result, error): 成功返回(DataFrame或结果列表, None)，失败返回(None, 错误信息)
    """
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        cursor = conn.cursor()
        
        # 确保SQL以分号结尾
        if not sql.strip().endswith(';'):
            sql = sql.strip() + ';'
        
        cursor.execute(sql)
        result = cursor.fetchall()
        
        # 获取列名
        column_names = [description[0] for description in cursor.description] if cursor.description else []
        
        # 转换为DataFrame
        df = pd.DataFrame(result, columns=column_names)
        
        conn.close()
        return df, None
    except Exception as e:
        if 'conn' in locals():
            conn.close()
        return None, f"Execution error: {str(e)}"


def compact_result_with_counter(result: pd.DataFrame, max_length: int = 500) -> str:
    """
    压缩执行结果（参考Reward-SQL的实现）
    
    Args:
        result: pandas DataFrame
        max_length: 最大长度
    
    Returns:
        压缩后的结果字符串
    """
    try:
        # 转换为字典列表
        records = result.to_dict('records')
        
        # 使用Counter压缩
        hashable_result = []
        for row in records:
            if isinstance(row, dict):
                hashable_row = tuple(sorted(row.items()))
            else:
                hashable_row = row
            hashable_result.append(hashable_row)
        
        counter = Counter(hashable_result)
        result_str = str(dict(counter))
        
        # 限制长度
        if len(result_str) > max_length:
            result_str = result_str[:max_length] + "...}"
        
        return result_str
    except Exception as e:
        return f"Error compacting result: {str(e)}"


def execute_single_sql(sql: str, db_name: str, timeout: int = 60) -> Tuple[int, Any]:
    """
    执行单个SQL查询
    
    Args:
        sql: SQL查询
        db_name: 数据库名称
        timeout: 超时时间（秒）
    
    Returns:
        (status, result): status=1表示成功，status=0表示失败
    """
    db_path = find_db_path(db_name)
    if not db_path:
        return 0, f"Database not found: {db_name}"
    
    result, error = execute_sql_in_subprocess(sql, db_path, timeout)
    if error:
        return 0, error
    else:
        return 1, result


def compare_sql(predicted_sql: str, ground_truth: str, db_name: str, timeout: int = 60) -> Tuple[int, str]:
    """
    比较两个SQL的执行结果
    
    Args:
        predicted_sql: 预测的SQL
        ground_truth: 标准答案SQL
        db_name: 数据库名称
        timeout: 超时时间（秒）
    
    Returns:
        (status, message): status=1表示结果匹配，status=0表示不匹配或错误
    """
    db_path = find_db_path(db_name)
    if not db_path:
        return 0, f"Database not found: {db_name}"
    
    # 执行两个SQL
    pred_result, pred_error = execute_sql_in_subprocess(predicted_sql, db_path, timeout)
    truth_result, truth_error = execute_sql_in_subprocess(ground_truth, db_path, timeout)
    
    if pred_error or truth_error:
        error_msg = pred_error if pred_error else truth_error
        return 0, error_msg
    
    # 比较结果（转换为集合进行比较，忽略顺序）
    try:
        # 转换为字典列表格式
        pred_records = pred_result.to_dict('records') if isinstance(pred_result, pd.DataFrame) else []
        truth_records = truth_result.to_dict('records') if isinstance(truth_result, pd.DataFrame) else []
        
        # 标准化行数据（处理NaN、None等）
        def normalize_row(row):
            normalized = {}
            for k, v in row.items():
                if pd.isna(v) or v is None:
                    normalized[k] = None
                elif isinstance(v, (int, float)):
                    normalized[k] = float(v) if isinstance(v, float) else int(v)
                else:
                    normalized[k] = str(v).strip().lower()
            return tuple(sorted(normalized.items()))
        
        pred_set = {normalize_row(row) for row in pred_records}
        truth_set = {normalize_row(row) for row in truth_records}
        
        if pred_set == truth_set:
            return 1, f"Results match (rows: {len(pred_set)})"
        else:
            return 0, f"Results mismatch: pred={len(pred_set)} rows, truth={len(truth_set)} rows"
    except Exception as e:
        return 0, f"Comparison error: {str(e)}"


# 错误处理装饰器
def handle_exceptions(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            error_traceback = traceback.format_exc()
            return jsonify({
                'status': 'error',
                'message': str(e),
                'traceback': error_traceback
            }), 500
    return decorated_function


# Flask路由 - 执行单个SQL
@app.route('/execute_sql', methods=['POST'])
@handle_exceptions
def api_execute_single_sql():
    """执行单个SQL查询"""
    data = request.json
    if not data or not all(k in data for k in ['sql', 'db_name']):
        return jsonify({'status': 'error', 'message': '缺少必要参数: sql, db_name'}), 400
    
    sql = data['sql']
    db_name = data['db_name']
    timeout = data.get('timeout', 60)
    
    status, result = execute_single_sql(sql, db_name, timeout)
    
    if status == 1:
        # 成功：返回DataFrame的JSON格式
        if isinstance(result, pd.DataFrame):
            # 转换为字典列表
            records = result.to_dict('records')
            return jsonify({
                'status': 'success',
                'result': records,
                'columns': list(result.columns),
                'row_count': len(result)
            })
        else:
            return jsonify({
                'status': 'success',
                'result': result
            })
    else:
        # 失败：返回错误信息（状态码200，但status='error'）
        return jsonify({
            'status': 'error',
            'message': result
        })


# Flask路由 - 比较SQL
@app.route('/compare_sql', methods=['POST'])
@handle_exceptions
def api_compare_sql():
    """比较两个SQL的执行结果"""
    data = request.json
    if not data or not all(k in data for k in ['predicted_sql', 'ground_truth', 'db_name']):
        return jsonify({'status': 'error', 'message': '缺少必要参数'}), 400
    
    predicted_sql = data['predicted_sql']
    ground_truth = data['ground_truth']
    db_name = data['db_name']
    timeout = data.get('timeout', 60)
    
    status, message = compare_sql(predicted_sql, ground_truth, db_name, timeout)
    
    return jsonify({
        'status': 'success' if status else 'error',
        'match': bool(status),
        'message': message
    })


# Flask路由 - 压缩结果
@app.route('/compact_result', methods=['POST'])
@handle_exceptions
def api_compact_result():
    """压缩执行结果"""
    data = request.json
    if not data or 'result' not in data:
        return jsonify({'status': 'error', 'message': '缺少必要参数: result'}), 400
    
    result = data['result']
    max_length = data.get('max_length', 500)
    
    # 如果是字典列表，转换为DataFrame
    if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
        df = pd.DataFrame(result)
    else:
        return jsonify({'status': 'error', 'message': 'result格式不正确'}), 400
    
    compacted = compact_result_with_counter(df, max_length)
    
    return jsonify({
        'status': 'success',
        'compacted_result': compacted
    })


# Flask路由 - 健康检查
@app.route('/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'service': 'SQL Executor Service'
    })


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="SQL执行器服务")
    parser.add_argument("--port", type=int, default=5887, help="服务端口")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="服务地址")
    args = parser.parse_args()
    
    print(f"=" * 60)
    print(f"SQL执行器服务启动")
    print(f"地址: http://{args.host}:{args.port}")
    print(f"=" * 60)
    print(f"可用接口:")
    print(f"  POST /execute_sql - 执行SQL查询")
    print(f"  POST /compare_sql - 比较两个SQL的执行结果")
    print(f"  POST /compact_result - 压缩执行结果")
    print(f"  GET  /health - 健康检查")
    print(f"=" * 60)
    
    # 设置线程模式以支持并发请求
    app.run(host=args.host, port=args.port, threaded=True, debug=False)

