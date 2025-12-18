import json
import sqlite3
import os
import sys
from typing import Dict, List, Tuple
from multiprocessing import Pool, cpu_count
from tqdm import tqdm
import time
from func_timeout import func_timeout, FunctionTimedOut


# --- Configuration ---
# Set a timeout for SQL queries in seconds. Adjust this value as needed.
SQL_QUERY_TIMEOUT_SECONDS = 300

# --- Functions ---

def load_train_data(file_path: str) -> List[Dict]:
    """加载训练数据集"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data

def execute_sql_internal(db_name: str, sql: str) -> Tuple[bool, str]:
    """
    内部SQL执行函数，将被func_timeout包装
    """
    db_path = f"/home/shenshuyu/SQL_tool/work/bird/train/train_databases/{db_name}/{db_name}.sqlite"
    
    if not os.path.exists(db_path):
        return True, f"数据库文件不存在: {db_path}"

    conn = None 
    try:
        conn = sqlite3.connect(db_path, timeout=SQL_QUERY_TIMEOUT_SECONDS)
        conn.execute(f"PRAGMA busy_timeout = {SQL_QUERY_TIMEOUT_SECONDS * 1000};") 
        
        cursor = conn.cursor()
        cursor.execute(sql)
        result = cursor.fetchall()
        
        # Check if result is empty
        if not result:
            return True, "查询结果为空"
        
        # Check if any cell in any row is None (SQL NULL)
        has_null = any(any(cell is None for cell in row) for row in result)
        if has_null:
            return True, "查询结果包含NULL值"
        
        return False, f"查询成功，返回{len(result)}行数据"

    except sqlite3.OperationalError as e:
        error_msg = str(e).lower()
        if "timeout" in error_msg or "too many sqlite_busy retries" in error_msg:
            return True, f"查询超时 ({SQL_QUERY_TIMEOUT_SECONDS}秒): {str(e)}"
        elif "locked" in error_msg:
            return True, f"数据库被锁定: {str(e)}"
        return True, f"SQLite操作错误: {str(e)}"
    except sqlite3.Error as e:
        return True, f"SQLite错误: {str(e)}"
    except Exception as e:
        return True, f"执行错误: {str(e)}"
    finally:
        if conn:
            conn.close()

def execute_sql_and_check_null(db_name: str, sql: str) -> Tuple[bool, str]:
    """
    执行SQL查询并检查是否返回NULL结果或超时。
    使用func_timeout包装以确保超时处理。
    Returns: (是否为NULL/错误/超时, 信息)
    """
    try:
        # 使用func_timeout包装执行
        result = func_timeout(SQL_QUERY_TIMEOUT_SECONDS, execute_sql_internal, args=(db_name, sql))
        return result
    except FunctionTimedOut:
        return True, f"查询超时 ({SQL_QUERY_TIMEOUT_SECONDS}秒): 函数执行超时"
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        return True, f"执行错误: {str(e)}"

def analyze_one(item: Dict) -> Dict:
    """
    分析单个训练样本的SQL查询。
    返回一个字典，包含查询ID、数据库ID、SQL、是否为问题以及相关信息。
    """
    print(f"Processing QID: {item.get('question_id')} for DB: {item.get('db_id')}", flush=True)
    db_id = item.get('db_id')
    sql = item.get('SQL')
    question_id = item.get('question_id')
    
    # Basic check for incomplete data
    if not sql or not db_id:
        return None 
    
    # Execute SQL and check for problems
    is_problematic, message = execute_sql_and_check_null(db_id, sql)
    
    return {
        'question_id': question_id,
        'db_id': db_id,
        'sql': sql,
        'is_problematic': is_problematic, 
        'message': message
    }

def stat_analyze_results(analyze_results: List[Dict]) -> Dict:
    """统计分析结果"""
    # Filter out None results from analyze_one
    analyze_results = [r for r in analyze_results if r is not None]
    
    total_queries = len(analyze_results)
    
    # Categorize all problematic queries (NULL results, errors, timeouts)
    problematic_queries = [r for r in analyze_results if r['is_problematic']]
    successful_queries = [r for r in analyze_results if not r['is_problematic']]
    
    # Further subdivide problematic queries for detailed statistics
    timeout_queries = [r for r in problematic_queries if "查询超时" in r['message']]
    null_result_queries = [r for r in problematic_queries if "查询结果为空" in r['message']]
    null_value_queries = [r for r in problematic_queries if "查询结果包含NULL值" in r['message']]
    db_error_queries = [r for r in problematic_queries if "数据库文件不存在" in r['message'] or "SQLite错误" in r['message'] or "执行错误" in r['message'] or "数据库被锁定" in r['message'] and "timeout" not in r['message']] # Exclude timeouts from general errors here
    
    # Calculate unique errors if a query falls into multiple categories (e.g., timeout and SQLiteError)
    # This ensures a query is only counted once in problematic_queries_count
    
    result = {
        'total_queries': total_queries,
        'problematic_queries_count': len(problematic_queries),
        'successful_queries_count': len(successful_queries),
        'problematic_percentage': (len(problematic_queries) / total_queries * 100) if total_queries > 0 else 0,
        
        'breakdown_of_problematic_queries': {
            'timeout_queries_count': len(timeout_queries),
            'null_result_queries_count': len(null_result_queries),
            'null_value_queries_count': len(null_value_queries),
            'db_error_queries_count': len(db_error_queries), # Note: this might overlap with timeout/locked if message contains both. For distinct counts, more logic is needed.
        },
        
        'problematic_queries_full_list': problematic_queries, # Full list of all problematic queries
        'timeout_queries_list': timeout_queries,         # List specifically for timeout queries
    }
    return result

def save_analysis_results(results: Dict, output_file: str):
    """保存分析结果到文件"""
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

def main():
    # Define file paths
    train_file = "work/bird/train/train_with_operations.json"
    output_file = "work/bird/train/sql_query_analysis_results.json" 
    
    print("Starting analysis of SQL queries in the training dataset...")

    train_data = load_train_data(train_file)
    print(f"Loaded {len(train_data)} training samples.")

    # Determine the number of processes to use. It's good to cap this.
    process_num = min(64, cpu_count()) 
    print(f"Using {process_num} processes for parallel execution.")

    analyze_results = []
    # Use imap_unordered for better progress reporting and efficient task distribution
    with Pool(processes=process_num) as pool:
        # tqdm wraps the iterator to show a progress bar
        for result in tqdm(pool.imap_unordered(analyze_one, train_data), total=len(train_data), desc="Analyzing SQL queries"):
            if result is not None:
                analyze_results.append(result)

    results = stat_analyze_results(analyze_results)

    # Print summary statistics
    print("\n=== Analysis Results Summary ===")
    print(f"Total queries processed: {results['total_queries']}")
    print(f"Problematic queries (NULL/Error/Timeout): {results['problematic_queries_count']}")
    print(f"Successfully executed queries: {results['successful_queries_count']}")
    print(f"Percentage of problematic queries: {results['problematic_percentage']:.2f}%")
    
    print("\n--- Breakdown of Problematic Queries ---")
    print(f"  Timeout queries: {results['breakdown_of_problematic_queries']['timeout_queries_count']}")
    print(f"  Queries returning empty results: {results['breakdown_of_problematic_queries']['null_result_queries_count']}")
    print(f"  Queries returning NULL values: {results['breakdown_of_problematic_queries']['null_value_queries_count']}")
    print(f"  Database/Execution errors: {results['breakdown_of_problematic_queries']['db_error_queries_count']}")

    # Save the comprehensive results to a JSON file
    save_analysis_results(results, output_file)
    print(f"\nAnalysis results saved to: {output_file}")

    # Display examples of timeout queries
    if results['timeout_queries_list']:
        print("\n=== Timeout Query Examples ===")
        # Display up to the first 5 timeout queries
        for i, query in enumerate(results['timeout_queries_list'][:5]): 
            print(f"\nExample {i+1}:")
            print(f"  Database: {query['db_id']}")
            print(f"  Question ID: {query['question_id']}")
            print(f"  SQL: {query['sql']}")
            print(f"  Message: {query['message']}")
    else:
        print("\nNo timeout queries detected.")

    # Optionally, display some other problematic queries if any exist beyond timeouts
    if results['problematic_queries_count'] > len(results['timeout_queries_list']):
        # Get up to 5 other problematic queries not categorized as timeouts
        other_problematic_queries = [
            q for q in results['problematic_queries_full_list'] 
            if "查询超时" not in q['message']
        ][:5] 
        if other_problematic_queries:
            print("\n=== Other Problematic Query Examples ===")
            for i, query in enumerate(other_problematic_queries):
                print(f"\nExample {i+1}:")
                print(f"  Database: {query['db_id']}")
                print(f"  Question ID: {query['question_id']}")
                print(f"  SQL: {query['sql']}")
                print(f"  Message: {query['message']}")

if __name__ == "__main__":
    main()