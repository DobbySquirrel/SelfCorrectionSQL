import os
import sys
import re
import json
import argparse
from tqdm import tqdm
from typing import Optional, Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import hashlib
import itertools
from collections import defaultdict
import random

# 将当前脚本目录与项目根加入 sys.path，便于相对导入
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
sys.path.append(CURRENT_DIR)
sys.path.append(PROJECT_ROOT)

from openai import OpenAI
from core.database_connector import DatabaseConnector 

# ---- vLLM/OpenAI 兼容配置（参考 test_vllm） ----
BASE_URL = os.environ.get("VLLM_API_URL", "http://localhost:8008/v1")
API_KEY = os.environ.get("VLLM_API_KEY", "dummy-key")  # vLLM 不校验
PREFERRED_MODEL = os.environ.get("VLLM_MODEL")  # 可指定，否则自动从 /v1/models 取第一个

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

def pick_model(preferred: Optional[str] = None) -> str:
    models = client.models.list().data
    if not models:
        raise RuntimeError("No models served by vLLM. Check vLLM logs / --served-model-name.")
    ids = sorted([m.id for m in models])
    if preferred and preferred in ids:
        return preferred
    return ids[0]

SQL_GENERATION_FUNDAMENTAL_RANDOM_STYLE_PROMPT = """
You are an experienced database analyst and a programmer. Your task is to generate an SQLite SQL query based on the given database information, natural language question, and potential hints.

**Core Instruction:** Your internal thinking process should simulate data manipulation using basic data structures and logical reasoning. Consider how you would approach the problem step by step: examining the data, filtering elements based on conditions, connecting related information, and building up results gradually. Use a methodical thought process that breaks down complex operations into simpler components.

**Intermediate Thought Process Display:** Show your reasoning process within `<thinking>` and `</thinking>` tags.

**Final Output:** Output ONLY the final, executable SQLite SQL query string, enclosed within `<answer>` and `</answer>` tags. Ensure this final SQL is declarative and efficient. The output format should be:
```xml
<thinking>
    ...
</thinking>
<answer>
    ...
</answer>
```

**Database Admin Instructions (Must Strictly Adhere):**
1.  **SELECT Clause:** Only select columns explicitly mentioned in the question. Avoid unnecessary columns or values.
2.  **Aggregation (MAX/MIN):** Always perform JOINs before using `MAX()` or `MIN()`.
3.  **ORDER BY with Distinct Values:** Use `GROUP BY <column>` before `ORDER BY <column> ASC|DESC` to ensure distinct values.
4.  **Handling NULLs:** If a column may contain NULL values (indicated by "None" in value examples or explicitly stated), use `JOIN` or `WHERE <column> IS NOT NULL`.
5.  **FROM/JOIN Clauses:** Only include tables essential to answer the question.
6.  **Strictly Follow Hints:** Adhere to all provided hints ({hint}).
7.  **Thorough Question Analysis:** Address all conditions mentioned in the question.
8.  **DISTINCT Keyword:** Use `SELECT DISTINCT` when the question requires unique values (e.g., IDs, URLs). Refer to column statistics ("Value Statics") to determine if `DISTINCT` is necessary.
9.  **Column Selection:** When similar columns exist across tables, carefully analyze column descriptions and hints to choose the correct column.
10. **String Concatenation:** Never use `|| ' ' ||` or any other method to concatenate strings in the `SELECT` clause.
11. **JOIN Preference:** Prioritize `INNER JOIN` over nested `SELECT` statements.
12. **SQLite Functions Only:** Use only functions available in SQLite.
13. **Date Processing:** Utilize `STRFTIME()` for date manipulation (e.g., `STRFTIME('%Y', SOMETIME)` to extract the year).

# Output:
"""

def parse_simplified_ddl(simplified_ddl: str) -> Dict[str, str]:
    """
    解析simplified_ddl，提取每个表的定义
    
    Args:
        simplified_ddl: 简化的DDL字符串（格式：# tablename(...)）
        
    Returns:
        字典，键为表名，值为表定义
    """
    tables = {}
    
    # 按行分割
    lines = simplified_ddl.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line or not line.startswith('#'):
            continue
        
        # 移除开头的 # 和空格
        line = line[1:].strip()
        
        # 匹配表定义：tablename(`column1`, `column2`, ...)
        # 使用正则表达式提取表名和列定义
        match = re.match(r'^(\w+)\(`([^`]+)`', line)
        if match:
            table_name = match.group(1)
            # 保存完整的表定义行
            tables[table_name] = line
    
    return tables

def build_ddl_from_tables(tables: Dict[str, str], table_order: List[str]) -> str:
    """根据表顺序构建DDL字符串"""
    ddl_lines = []
    for table_name in table_order:
        if table_name in tables:
            # 重新添加 # 前缀以保持原始格式
            ddl_lines.append('# ' + tables[table_name])
    return '\n'.join(ddl_lines) if ddl_lines else ''

def table_info_construct_with_order(ppl, table_order: List[str]) -> tuple:
    """根据表顺序构建表信息"""
    (question, simple_ddl, ddl_data,
     foreign_key, evidence, example) = (ppl['question'].strip(), ppl['simplified_ddl'].strip(),
                                        ppl['ddl_data'].strip(), ppl['foreign_key'].strip(),
                                        ppl['evidence'].strip(), ppl.get('example', ''))
    
    # 解析表定义
    tables = parse_simplified_ddl(simple_ddl)
    
    # 根据指定顺序重新组织DDL
    if table_order:
        modified_ddl = build_ddl_from_tables(tables, table_order)
    else:
        modified_ddl = simple_ddl
    
    table_info = ('### Sqlite SQL tables, with their properties:\n' + modified_ddl +
                  '\n### Here are some data information about database references.\n' + ddl_data +
                  '\n### Foreign key information of Sqlite SQL tables, used for table joins:\n' + foreign_key)
    return table_info, question, evidence, example

def parse_llm_response(response):
    result = {"sql": "", "thinking": ""}

    thinking_match = re.search(r'<thinking>(.*?)</thinking>', response, re.DOTALL)
    if thinking_match:
        result["thinking"] = thinking_match.group(1).strip()

    answer_match = re.search(r'<answer>(.*?)</answer>', response, re.DOTALL)
    if answer_match:
        answer_content = answer_match.group(1).strip()
        if "SELECT" in answer_content.upper() or "WITH" in answer_content.upper():
            result["sql"] = answer_content.strip()
            return result
        try:
            obj = json.loads(answer_content)
            if isinstance(obj, dict):
                result.update(obj)
            return result
        except Exception:
            pass

    # 兜底：直接把整段当 SQL（若包含 SQL 关键字）
    if "SELECT" in response.upper() or "WITH" in response.upper():
        result["sql"] = response.strip()
        return result

    return result

def build_user_prompt(table_info, question, evidence, example):
    prefix = example.strip() + "\n\n### Answer the question by sqlite SQL query only and with no explanation. You must minimize SQL execution time while ensuring correctness.\n"
    core = table_info.strip() + '\n\n' + '### definition: ' + evidence + "\n### Question: " + question
    return prefix + core

def execute_sql_and_get_result(db_connector, sql: str) -> Dict[str, Any]:
    """执行SQL并获取结果"""
    try:
        if not db_connector.connection:
            if not db_connector.connect():
                return {
                    'valid': False,
                    'error': '无法连接到数据库',
                    'result': []
                }
        
        result, error = db_connector.execute_query(sql)
        if error:
            return {
                'valid': False,
                'error': error,
                'result': []
            }
        else:
            if result is not None:
                query_result = result.to_dict(orient='records')
            else:
                query_result = []
            return {
                'valid': True,
                'error': None,
                'result': query_result
            }
    except Exception as e:
        return {
            'valid': False,
            'error': str(e),
            'result': []
        }

def create_result_signature(result: Dict[str, Any]) -> str:
    """基于执行结果创建唯一标识符"""
    if not result.get('valid', False):
        return f"invalid_{result.get('error', 'unknown_error')}"
    
    query_result = result.get('result', [])
    if not query_result:
        return "empty_result"
    
    row_count = len(query_result)
    if row_count == 0:
        return "empty_result"
    
    columns = list(query_result[0].keys()) if query_result else []
    
    result_content = str(sorted([str(row) for row in query_result]))
    content_hash = hashlib.md5(result_content.encode()).hexdigest()[:16]
    
    return f"{row_count}_{content_hash}"

def select_best_sql_by_sc(sql_results: List[str], db_connector) -> tuple:
    """使用self-consistency选择最优SQL"""
    if not sql_results:
        return "", {}
    
    sql_buckets = {}
    for sql in sql_results:
        try:
            result = execute_sql_and_get_result(db_connector, sql)
            signature = create_result_signature(result)
            
            if signature not in sql_buckets:
                sql_buckets[signature] = {
                    'sql': sql,
                    'result': result,
                    'count': 0
                }
            sql_buckets[signature]['count'] += 1
        except Exception as e:
            continue
    
    if not sql_buckets:
        return "", {}
    
    # 选择桶数最多的SQL
    best_signature = max(sql_buckets.keys(), key=lambda k: sql_buckets[k]['count'])
    best_sql = sql_buckets[best_signature]['sql']
    
    return best_sql, sql_buckets

def process_single_sample_grouped(args):
    """处理单个样本：生成4组SQL，每组5个，使用SC选择最优"""
    i, ppl, model_name, db_connector, num_runs = args
    try:
        if not ppl or not model_name or not db_connector:
            print(f"样本 {i}: 输入参数无效")
            return i, {}
        
        # 解析表定义
        tables = parse_simplified_ddl(ppl['simplified_ddl'])
        table_names = list(tables.keys())
        
        if len(table_names) == 0:
            print(f"样本 {i}: 无法解析表定义")
            return i, {}
        
        print(f"样本 {i}: 解析到 {len(table_names)} 个表: {list(table_names)}")
        
        # 定义两个组：随机排序+Temperature vs 单纯Temperature
        groups = {
            'random_temperature': {'order': random.sample(table_names, len(table_names)), 'temperature_list': [0.1, 0.3, 0.5, 0.7, 0.9]},  # 随机排序+温度变化
            'pure_temperature': {'order': table_names, 'temperature_list': [0.1, 0.3, 0.5, 0.7, 0.9]}  # 原始顺序+温度变化
        }
        
        all_results = {}
        
        # 为每个组生成SQL
        for group_name, group_config in groups.items():
            print(f"  📊 样本 {i}: 处理 {group_name} 组")
            
            group_sqls = []
            table_order = group_config.get('order', [])
            temp_config = group_config.get('temperature', 0.2)
            temp_list = group_config.get('temperature_list', [])
            
            # 生成num_runs次SQL
            for run in range(num_runs):
                try:
                    table_info, question, evidence, example = table_info_construct_with_order(ppl, table_order)
                    user_prompt = build_user_prompt(table_info, question, evidence, example)
                    
                    # 确定temperature值
                    if temp_list:
                        # temperature组：使用不同的temperature
                        temp_value = temp_list[run % len(temp_list)]
                    else:
                        # 其他组：使用固定的temperature
                        temp_value = temp_config
                    
                    resp = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": "You are a helpful coding assistant."},
                            {"role": "user", "content": SQL_GENERATION_FUNDAMENTAL_RANDOM_STYLE_PROMPT + "\n\n" + user_prompt},
                        ],
                        temperature=temp_value,
                        max_tokens=512,
                    )
                    raw = resp.choices[0].message.content or ""
                    parsed = parse_llm_response(raw)
                    sql = parsed.get("sql", "").replace('\n', ' ').strip()
                    if sql:
                        group_sqls.append(sql)
                except Exception as e:
                    continue
            
            if not group_sqls:
                print(f"样本 {i} {group_name} 组生成失败")
                all_results[group_name] = {
                    'best_sql': '',
                    'all_sqls': [],
                    'buckets': {},
                    'bucket_count': 0
                }
            else:
                # 使用SC选择最优SQL
                best_sql, buckets = select_best_sql_by_sc(group_sqls, db_connector)
                all_results[group_name] = {
                    'best_sql': best_sql,
                    'all_sqls': group_sqls,
                    'buckets': buckets,
                    'bucket_count': len(buckets)
                }
                print(f"样本 {i} {group_name} 组: 生成 {len(group_sqls)} 个SQL，{len(buckets)} 个桶")
        
        return i, all_results
        
    except Exception as e:
        print(f"样本 {i} 处理失败: {e}")
        return i, {}

def analyze_group_diversity(results_list: List[Dict]) -> Dict[str, Any]:
    """分析各组的多样性"""
    group_stats = {
        'random_temperature': {'total': 0, 'unique': 0, 'buckets': 0, 'best_sqls': []},
        'pure_temperature': {'total': 0, 'unique': 0, 'buckets': 0, 'best_sqls': []}
    }
    
    all_sqls = []
    group_sqls = {'random_temperature': [], 'pure_temperature': []}
    
    for sample_results in results_list:
        for group_name, group_result in sample_results.items():
            if 'all_sqls' in group_result:
                sqls = group_result['all_sqls']
                group_stats[group_name]['total'] += len(sqls)
                group_stats[group_name]['buckets'] += group_result.get('bucket_count', 0)
                
                best_sql = group_result.get('best_sql', '')
                if best_sql:
                    group_stats[group_name]['best_sqls'].append(best_sql)
                
                for sql in sqls:
                    all_sqls.append(sql)
                    group_sqls[group_name].append(sql)
    
    # 计算唯一性
    for group_name in group_stats:
        group_stats[group_name]['unique'] = len(set(group_sqls[group_name]))
    
    # 计算各组间重叠（最优SQL）
    overlap_matrix = {}
    best_sql_overlap = {}
    groups = ['random_temperature', 'pure_temperature']
    for i, group1 in enumerate(groups):
        for group2 in groups[i+1:]:
            set1 = set(group_stats[group1]['best_sqls'])
            set2 = set(group_stats[group2]['best_sqls'])
            overlap = len(set1 & set2)
            total_unique = len(set1 | set2)
            
            overlap_matrix[f"{group1}_vs_{group2}"] = {
                'overlap_count': overlap,
                'overlap_rate': overlap / total_unique * 100 if total_unique else 0,
                'unique_in_both': total_unique
            }
            best_sql_overlap[f"{group1}_vs_{group2}"] = overlap / total_unique * 100 if total_unique else 0
    
    # 计算桶数统计
    groups_list = ['random_temperature', 'pure_temperature']
    bucket_stats = {
        'random_temperature_buckets': group_stats['random_temperature']['buckets'],
        'pure_temperature_buckets': group_stats['pure_temperature']['buckets'],
        'avg_buckets_per_group': sum([group_stats[g]['buckets'] for g in groups_list]) / len(groups_list) if groups_list else 0
    }
    
    return {
        'group_stats': group_stats,
        'overlap_matrix': overlap_matrix,
        'best_sql_overlap': best_sql_overlap,
        'bucket_stats': bucket_stats,
        'total_sqls': len(all_sqls),
        'unique_sqls': len(set(all_sqls)),
        'total_best_sqls': len([sql for sql in group_stats['original']['best_sqls'] if sql]) + 
                           len([sql for sql in group_stats['reversed']['best_sqls'] if sql]) + 
                           len([sql for sql in group_stats['random']['best_sqls'] if sql])
    }

def main():
    parser = argparse.ArgumentParser(description="Baseline: 分3组生成SQL（每组5次），使用SC选择最优")
    parser.add_argument("--ppl_file", type=str, required=True, help="样本文件")
    parser.add_argument("--sql_out", type=str, required=True, help="输出SQL文件")
    parser.add_argument("--analysis_out", type=str, help="分析结果文件")
    parser.add_argument("--start", type=int, default=0, help="开始索引")
    parser.add_argument("--max_workers", type=int, default=8, help="并行线程数")
    parser.add_argument("--num_runs", type=int, default=5, help="每组生成SQL次数")
    parser.add_argument("--db_base_path", type=str, default="/home/shenshuyu/SQL_tool/data/dev_databases", help="数据库基础路径")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.sql_out), exist_ok=True)
    if args.analysis_out:
        os.makedirs(os.path.dirname(args.analysis_out), exist_ok=True)

    with open(args.ppl_file, 'r', encoding='utf-8') as f:
        ppls = json.load(f)

    model_name = pick_model(PREFERRED_MODEL)
    print(f"使用模型: {model_name}")
    print(f"每个样本将生成2组SQL，每组 {args.num_runs} 次：")
    print(f"  - 第1组: {args.num_runs} 个SQL（随机排序 + temperature变化）")
    print(f"  - 第2组: {args.num_runs} 个SQL（原始顺序 + temperature变化）")
    print(f"  - 每组使用SC选择最优SQL")

    tasks = []
    for i in range(args.start, len(ppls)):
        ppl = ppls[i]
        db_name = ppl.get('db', '')
        db_connector = DatabaseConnector(db_name)
        tasks.append((i, ppl, model_name, db_connector, args.num_runs))
    
    all_results = [{} for _ in range(len(ppls))]
    save_lock = threading.Lock()
    
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_to_task = {executor.submit(process_single_sample_grouped, task): task for task in tasks}
        
        for future in as_completed(future_to_task):
            i, results = future.result()
            all_results[i] = results
    
    # 写入SQL文件（每组的最优SQL分别存储）
    sql_dir = os.path.dirname(args.sql_out)
    base_name = os.path.basename(args.sql_out).replace('.sql', '')
    
    random_temp_out = os.path.join(sql_dir, f"{base_name}_random_temperature.sql")
    pure_temp_out = os.path.join(sql_dir, f"{base_name}_pure_temperature.sql")
    
    with open(random_temp_out, 'w', encoding='utf-8') as f1, \
         open(pure_temp_out, 'w', encoding='utf-8') as f2:
        
        for i, results in enumerate(all_results):
            if results:
                f1.write(results.get('random_temperature', {}).get('best_sql', '') + '\n')
                f2.write(results.get('pure_temperature', {}).get('best_sql', '') + '\n')
    
    # 分析多样性
    analysis = analyze_group_diversity(all_results)
    
    if args.analysis_out:
        with open(args.analysis_out, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)
    
    print("\n📊 分析结果:")
    print(f"  总生成SQL数: {analysis['total_sqls']}")
    print(f"  总唯一SQL数: {analysis['unique_sqls']}")
    print(f"  最优SQL总数: {analysis['total_best_sqls']}")
    
    print(f"\n📈 各组统计:")
    group_names = {
        'random_temperature': '随机排序+温度变化组', 
        'pure_temperature': '单纯温度变化组'
    }
    for group, stats in analysis['group_stats'].items():
        print(f"  {group_names.get(group, group)}:")
        print(f"    生成SQL数: {stats['total']}")
        print(f"    唯一SQL数: {stats['unique']}")
        print(f"    总桶数: {stats['buckets']}")
        print(f"    最优SQL数: {len([s for s in stats['best_sqls'] if s])}")
    
    print(f"\n🔄 桶数统计:")
    bucket_stats = analysis['bucket_stats']
    print(f"  随机排序+温度变化组总桶数: {bucket_stats['random_temperature_buckets']}")
    print(f"  单纯温度变化组总桶数: {bucket_stats['pure_temperature_buckets']}")
    print(f"  平均桶数: {bucket_stats['avg_buckets_per_group']:.2f}")
    
    print(f"\n🎯 组间重叠统计（最优SQL）:")
    for key, value in analysis['best_sql_overlap'].items():
        group1, group2 = key.split('_vs_')
        print(f"  {group_names.get(group1, group1)} vs {group_names.get(group2, group2)}: {value:.2f}%")
    
    print("\n✅ 完成!")

if __name__ == "__main__":
    main()

