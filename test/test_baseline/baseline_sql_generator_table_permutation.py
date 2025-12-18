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

# 将当前脚本目录与项目根加入 sys.path，便于相对导入
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
sys.path.append(CURRENT_DIR)
sys.path.append(PROJECT_ROOT)

from openai import OpenAI
from core.database_connector import DatabaseConnector 

# ---- vLLM/OpenAI 兼容配置（参考 test_vllm） ----
BASE_URL = os.environ.get("VLLM_API_URL", "http://localhost:8009/v1")
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
        simplified_ddl: 简化的DDL字符串
        
    Returns:
        字典，键为表名，值为表定义
    """
    tables = {}
    lines = simplified_ddl.strip().split('\n')
    current_table = None
    current_definition = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # 检查是否是表定义开始（通常以CREATE TABLE开头）
        if line.upper().startswith('CREATE TABLE'):
            # 保存前一个表的定义
            if current_table:
                tables[current_table] = '\n'.join(current_definition)
            
            # 开始新表
            # 提取表名
            table_name_match = re.search(r'CREATE TABLE\s+(\w+)', line, re.IGNORECASE)
            if table_name_match:
                current_table = table_name_match.group(1)
                current_definition = [line]
            else:
                current_table = None
                current_definition = []
        elif current_table:
            # 继续当前表的定义
            current_definition.append(line)
    
    # 保存最后一个表的定义
    if current_table:
        tables[current_table] = '\n'.join(current_definition)
    
    # 如果没找到CREATE TABLE格式，尝试其他格式
    if not tables:
        # 尝试匹配 # table_name(`col1`, `col2`, ...) 格式
        for line in lines:
            line = line.strip()
            if line.startswith('#') and '`' in line and '(' in line:
                # 提取表名和列定义
                table_match = re.search(r'#\s*(\w+)\s*\(', line)
                if table_match:
                    table_name = table_match.group(1)
                    tables[table_name] = line
    
    return tables

def generate_table_permutations(tables: Dict[str, str], max_permutations: int = 6) -> List[str]:
    """
    生成表顺序的排列组合
    
    Args:
        tables: 表定义字典
        max_permutations: 最大排列数量
        
    Returns:
        不同表顺序的DDL字符串列表
    """
    table_names = list(tables.keys())
    import random
    import math
    
    # 计算所有排列的数量
    total_permutations = math.factorial(len(table_names))
    
    # 如果排列总数不超过10000且表数量<=6，生成所有排列
    if total_permutations <= 10000 and len(table_names) <= 6:
        permutations = list(itertools.permutations(table_names))
    else:
        # 对于大量表的排列，不计算所有排列，而是直接生成随机排列
        # 使用一个集合来确保不重复
        seen = set()
        permutations = []
        target_count = min(max_permutations, 100)  # 最多生成100个不同的排列
        
        while len(permutations) < target_count:
            perm = tuple(random.sample(table_names, len(table_names)))
            if perm not in seen:
                seen.add(perm)
                permutations.append(perm)
    
    # 为每个排列生成DDL
    ddl_variants = []
    for perm in permutations:
        ddl_lines = []
        for table_name in perm:
            if table_name in tables:
                ddl_lines.append(tables[table_name])
        ddl_variants.append('\n'.join(ddl_lines))
    
    return ddl_variants

def table_info_construct_with_permutation(ppl, permutation_idx: int = 0):
    """
    构建表信息，支持表顺序排列组合
    
    Args:
        ppl: 样本数据
        permutation_idx: 排列索引（0表示原始顺序）
        
    Returns:
        table_info, question, evidence, example
    """
    (question, simple_ddl, ddl_data,
     foreign_key, evidence, example) = (ppl['question'].strip(), ppl['simplified_ddl'].strip(),
                                        ppl['ddl_data'].strip(), ppl['foreign_key'].strip(),
                                        ppl['evidence'].strip(), ppl.get('example', ''))
    
    # 解析表定义
    tables = parse_simplified_ddl(simple_ddl)
    table_names = list(tables.keys())
    
    if permutation_idx == 0 or len(tables) <= 1:
        # 使用原始顺序
        modified_ddl = simple_ddl
    elif permutation_idx == 1:
        # 使用完全倒序
        reversed_names = list(reversed(table_names))
        ddl_lines = []
        for table_name in reversed_names:
            if table_name in tables:
                ddl_lines.append(tables[table_name])
        modified_ddl = '\n'.join(ddl_lines)
    else:
        # 生成随机排列组合
        ddl_variants = generate_table_permutations(tables, max_permutations=6)
        if permutation_idx - 2 < len(ddl_variants):  # -2 因为前两个是原始和倒序
            modified_ddl = ddl_variants[permutation_idx - 2]
        else:
            modified_ddl = simple_ddl  # 回退到原始顺序
    
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
        # 检查数据库连接
        if not db_connector.connection:
            if not db_connector.connect():
                return {
                    'valid': False,
                    'error': '无法连接到数据库',
                    'result': []
                }
        
        result, error = db_connector.execute_query(sql)
        if error:
            print(f"SQL执行错误: {error}")
            return {
                'valid': False,
                'error': error,
                'result': []
            }
        else:
            # 将结果转换为字典列表
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
        print(f"SQL执行异常: {e}")
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
    
    # 基于结果行数和列数创建签名
    row_count = len(query_result)
    if row_count == 0:
        return "empty_result"
    
    # 获取列名
    columns = list(query_result[0].keys()) if query_result else []
    column_count = len(columns)
    
    # 创建基于结果内容的哈希
    # 将字典列表转换为可排序的字符串
    result_content = str(sorted([str(row) for row in query_result]))
    content_hash = hashlib.md5(result_content.encode()).hexdigest()[:16]
    
    return f"{row_count}_{content_hash}"

def process_single_sample_with_permutations(args):
    """处理单个样本的函数，使用不同的表顺序排列组合

    兼容扩展：支持传入(perm_idx, temperature, tag)组合以实现：
    - 倒序 + 多温度
    - 仅多温度（原始顺序）
    - 仅倒序（单温度）
    - 以及原有的多表排列逻辑
    """
    i, ppl, model_name, db_connector, num_permutations, combo_list, base_temperature = args
    try:
        # 检查输入参数
        if not ppl or not model_name or not db_connector:
            print(f"样本 {i}: 输入参数无效")
            return i, []
        
        # 解析表定义以确定排列数量
        tables = parse_simplified_ddl(ppl['simplified_ddl'])
        print(f"  📊 样本 {i}: 解析到 {len(tables)} 个表: {list(tables.keys())}")
        
        # 如果解析失败，使用原始顺序
        if len(tables) == 0:
            print(f"  ⚠️ 样本 {i}: 无法解析表定义，使用原始顺序")
            actual_permutations = 0
        else:
            actual_permutations = min(num_permutations, len(tables) if len(tables) <= 3 else num_permutations)
        
        sql_results = []

        # 如果提供了 combo_list，则严格按照组合生成；否则走原有排列逻辑
        if combo_list:
            generation_plan = []
            for combo in combo_list:
                generation_plan.append({
                    'perm_idx': combo.get('perm_idx', 0),
                    'temperature': combo.get('temperature', base_temperature),
                    'tag': combo.get('tag', 'custom')
                })
        else:
            generation_plan = []
            for perm_idx in range(num_permutations + 2):
                generation_plan.append({
                    'perm_idx': perm_idx,
                    'temperature': base_temperature,
                    'tag': 'permutation_default'
                })

        for plan in generation_plan:
            try:
                perm_idx = plan['perm_idx']
                temperature = plan['temperature']
                tag = plan['tag']

                if perm_idx == 0:
                    perm_type = "原始顺序"
                elif perm_idx == 1:
                    perm_type = "完全倒序"
                else:
                    perm_type = f"随机排列{perm_idx-1}"
                
                print(f"  🔄 样本 {i}: 生成 ({perm_type})，温度 {temperature}，标签 {tag}")
                # 构建提示信息（使用不同的表顺序）
                table_info, question, evidence, example = table_info_construct_with_permutation(ppl, perm_idx)
                user_prompt = build_user_prompt(table_info, question, evidence, example)
                
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "You are a helpful coding assistant."},
                        {"role": "user", "content": SQL_GENERATION_FUNDAMENTAL_RANDOM_STYLE_PROMPT + "\n\n" + user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=512,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False}
                    }
                )
                raw = resp.choices[0].message.content or ""
                parsed = parse_llm_response(raw)
                sql = parsed.get("sql", "").replace('\n', ' ').strip()
                if sql:
                    sql_results.append({
                        'sql': sql,
                        'permutation_idx': perm_idx,
                        'is_original': perm_idx == 0,
                        'is_reversed': perm_idx == 1,
                        'temperature': temperature,
                        'tag': tag
                    })
            except Exception as e:
                print(f"样本 {i} 生成失败 ({perm_type}, 温度 {temperature}, 标签 {tag}): {e}")
                continue
        
        print(f"样本 {i}: 生成了 {len(sql_results)} 个SQL")
        return i, sql_results
        
    except Exception as e:
        print(f"样本 {i} 处理失败: {e}")
        return i, []

def analyze_sql_diversity(sql_results_list: List[List[Dict]]) -> Dict[str, Any]:
    """
    分析SQL多样性
    
    Args:
        sql_results_list: 所有样本的SQL结果列表
        
    Returns:
        多样性分析结果
    """
    all_sqls = []
    permutation_stats = defaultdict(int)
    original_vs_permutation = {'original': [], 'permutation': []}
    # 新增：分标签统计（用于对比 rev+temps / temps-only / reverse-only）
    tag_buckets: Dict[str, List[str]] = defaultdict(list)
    
    # 分别统计三种类型：原始顺序、完全倒序、随机排列
    original_sqls = []
    reversed_sqls = []
    random_sqls = []
    
    for sample_results in sql_results_list:
        for sql_info in sample_results:
            sql = sql_info['sql']
            perm_idx = sql_info['permutation_idx']
            is_original = sql_info['is_original']
            is_reversed = sql_info.get('is_reversed', False)
            tag = sql_info.get('tag', 'unknown')
            
            all_sqls.append(sql)
            permutation_stats[perm_idx] += 1
            tag_buckets[tag].append(sql)
            
            if is_original:
                original_vs_permutation['original'].append(sql)
                original_sqls.append(sql)
            else:
                original_vs_permutation['permutation'].append(sql)
                
                if is_reversed:
                    reversed_sqls.append(sql)
                else:
                    random_sqls.append(sql)
    
    # 计算SQL去重率
    unique_sqls = set(all_sqls)
    total_sqls = len(all_sqls)
    deduplication_rate = (1 - len(unique_sqls) / total_sqls) * 100 if total_sqls > 0 else 0
    
    # 计算原始顺序vs排列组合的多样性
    original_unique = set(original_vs_permutation['original'])
    permutation_unique = set(original_vs_permutation['permutation'])
    
    original_count = len(original_vs_permutation['original'])
    permutation_count = len(original_vs_permutation['permutation'])
    
    original_diversity = (1 - len(original_unique) / original_count) * 100 if original_count > 0 else 0
    permutation_diversity = (1 - len(permutation_unique) / permutation_count) * 100 if permutation_count > 0 else 0
    
    # 计算重叠率
    overlap = len(original_unique & permutation_unique)
    overlap_rate = overlap / len(original_unique | permutation_unique) * 100 if (original_unique | permutation_unique) else 0
    
    # 计算倒序和随机排列的统计
    reversed_unique = len(set(reversed_sqls))
    random_unique = len(set(random_sqls))
    reversed_diversity = (1 - reversed_unique / len(reversed_sqls)) * 100 if reversed_sqls else 0
    random_diversity = (1 - random_unique / len(random_sqls)) * 100 if random_sqls else 0
    
    result = {
        'total_sqls': total_sqls,
        'unique_sqls': len(unique_sqls),
        'deduplication_rate': deduplication_rate,
        'original_sqls': original_count,
        'original_unique': len(original_unique),
        'original_diversity': original_diversity,
        'permutation_sqls': permutation_count,
        'permutation_unique': len(permutation_unique),
        'permutation_diversity': permutation_diversity,
        'reversed_sqls': len(reversed_sqls),
        'reversed_unique': reversed_unique,
        'reversed_diversity': reversed_diversity,
        'random_sqls': len(random_sqls),
        'random_unique': random_unique,
        'random_diversity': random_diversity,
        'overlap_rate': overlap_rate,
        'permutation_stats': dict(permutation_stats)
    }

    # 基于 tag 的多样性统计
    per_tag_stats: Dict[str, Dict[str, Any]] = {}
    for tag, sqls in tag_buckets.items():
        unique = len(set(sqls))
        total = len(sqls)
        diversity = (1 - unique / total) * 100 if total > 0 else 0
        per_tag_stats[tag] = {
            'count': total,
            'unique': unique,
            'diversity': diversity
        }

    result['per_tag_stats'] = per_tag_stats
    return result

def main():
    parser = argparse.ArgumentParser(description="Baseline: 使用表顺序/温度实验生成SQL并对比多样性")
    parser.add_argument("--ppl_file", type=str, required=True, help="样本文件（JSON 数组）")
    parser.add_argument("--sql_out", type=str, required=False, default="", help="输出汇总 SQL 文本文件（每行一个样本的 SQL）。留空则不生成汇总文件")
    parser.add_argument("--analysis_out", type=str, help="输出多样性分析结果文件（JSON格式）")
    parser.add_argument("--start", type=int, default=0, help="从索引开始处理")
    parser.add_argument("--max_workers", type=int, default=32, help="并行线程数")
    parser.add_argument("--num_permutations", type=int, default=3, help="每个样本使用的排列组合数量（不包括原始顺序）")
    parser.add_argument("--db_base_path", type=str, default="/home/shenshuyu/SQL_tool/data/dev_databases", help="数据库基础路径")
    parser.add_argument("--experiment", type=str, default="all", choices=["rev_and_temps", "only_temps", "only_reverse", "all"], help="实验模式：倒序+多温度 / 仅多温度 / 仅倒序 / 全部")
    parser.add_argument("--temperature_list", type=str, default="0.1,0.3,0.5,0.7,0.9", help="温度列表，逗号分隔")
    parser.add_argument("--base_temperature", type=float, default=0.2, help="默认温度（未指定温度时使用）")
    args = parser.parse_args()

    if args.sql_out:
        os.makedirs(os.path.dirname(args.sql_out), exist_ok=True)
    if args.analysis_out:
        os.makedirs(os.path.dirname(args.analysis_out), exist_ok=True)

    with open(args.ppl_file, 'r', encoding='utf-8') as f:
        ppls = json.load(f)

    # 获取模型名称
    model_name = pick_model(PREFERRED_MODEL)
    print(f"使用模型: {model_name}")
    temperatures = [float(x) for x in args.temperature_list.split(',') if x.strip()]
    print(f"实验模式: {args.experiment}")
    print(f"温度列表: {temperatures}")

    # 准备任务（每个任务包含数据库连接器）
    tasks = []
    for i in range(args.start, len(ppls)):
        ppl = ppls[i]
        db_name = ppl.get('db', '')
        # 直接传递数据库名称，让DatabaseConnector内部处理路径
        db_connector = DatabaseConnector(db_name)

        combo_list: List[Dict[str, Any]] = []
        if args.experiment in ("rev_and_temps", "all"):
            # 倒序 + 多温度
            for t in temperatures:
                combo_list.append({'perm_idx': 1, 'temperature': t, 'tag': 'rev_and_temps'})
        if args.experiment in ("only_temps", "all"):
            # 原始顺序 + 多温度
            for t in temperatures:
                combo_list.append({'perm_idx': 0, 'temperature': t, 'tag': 'only_temps'})
        if args.experiment in ("only_reverse", "all"):
            # 倒序 + 重复 base_temperature 次数与温度列表长度一致，保证与其他组对齐
            repeat_n = max(1, len(temperatures))
            for _ in range(repeat_n):
                combo_list.append({'perm_idx': 1, 'temperature': args.base_temperature, 'tag': 'only_reverse'})

        tasks.append((i, ppl, model_name, db_connector, args.num_permutations, combo_list, args.base_temperature))
    
    # 初始化结果数组
    sql_results_list = [[] for _ in range(len(ppls))]
    save_lock = threading.Lock()
    
    # 使用线程池并行处理
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_to_task = {executor.submit(process_single_sample_with_permutations, task): task for task in tasks}
        
        completed_count = 0
        for future in tqdm(as_completed(future_to_task), total=len(tasks), desc="生成SQL（多种表顺序）"):
            try:
                i, sql_results = future.result()
                sql_results_list[i] = sql_results
                completed_count += 1
                
                # 每完成5个任务或全部完成时保存一次
                if completed_count % 5 == 0 or completed_count == len(tasks):
                    with save_lock:
                        # 可选：保存汇总TXT（只保存第一个SQL，优先选择原始顺序/only_temps；若无则任意一个）
                        if args.sql_out:
                            try:
                                with open(args.sql_out, 'w', encoding='utf-8') as fw:
                                    for j in range(args.start, len(sql_results_list)):
                                        if sql_results_list[j]:
                                            preferred = None
                                            for item in sql_results_list[j]:
                                                if item.get('tag') == 'only_temps' and item.get('permutation_idx') == 0:
                                                    preferred = item['sql']
                                                    break
                                            if preferred is None:
                                                preferred = sql_results_list[j][0]['sql']
                                            first_sql = preferred
                                            fw.write(str(first_sql) + "\n")
                                        else:
                                            fw.write("\n")
                                print(f"TXT文件保存成功: {args.sql_out}")
                            except Exception as e:
                                print(f"TXT文件保存失败: {e}")

                        # 额外按标签分别保存：每个样本一行，内容为该标签下该样本所有SQL的JSON数组
                        try:
                            # 选择输出目录：优先 sql_out 的目录，否则使用 analysis_out 的目录，否则当前目录
                            if args.sql_out:
                                out_dir = os.path.dirname(args.sql_out)
                            elif args.analysis_out:
                                out_dir = os.path.dirname(args.analysis_out)
                            else:
                                out_dir = os.getcwd()
                            os.makedirs(out_dir, exist_ok=True)
                            tag_to_path = {
                                'rev_and_temps': os.path.join(out_dir, 'rev_and_temps.txt'),
                                'only_temps': os.path.join(out_dir, 'only_temps.txt'),
                                'only_reverse': os.path.join(out_dir, 'only_reverse.txt'),
                            }
                            # 准备每个标签的行缓存
                            tag_to_lines = {k: [] for k in tag_to_path.keys()}
                            for j in range(args.start, len(sql_results_list)):
                                results = sql_results_list[j]
                                # 为每个标签收集本样本对应的SQL字符串数组
                                for tag in tag_to_lines.keys():
                                    tag_sqls = [r['sql'] for r in results if r.get('tag') == tag]
                                    tag_to_lines[tag].append(json.dumps(tag_sqls, ensure_ascii=False))
                            # 写入文件
                            for tag, path in tag_to_path.items():
                                with open(path, 'w', encoding='utf-8') as tf:
                                    tf.write("\n".join(tag_to_lines[tag]))
                            print("按标签TXT文件保存成功: ")
                            for tag, path in tag_to_path.items():
                                print(f"  - {tag}: {path}")
                        except Exception as e:
                            print(f"按标签TXT文件保存失败: {e}")
                        
                        print(f"已保存 {completed_count}/{len(tasks)} 个结果")
            except Exception as e:
                print(f"处理任务时出错: {e}")
    
    # 分析SQL多样性
    print("\n" + "="*80)
    print("【SQL多样性分析】")
    print("="*80)
    
    diversity_analysis = analyze_sql_diversity(sql_results_list)
    
    print(f"总SQL数量: {diversity_analysis['total_sqls']}")
    print(f"唯一SQL数量: {diversity_analysis['unique_sqls']}")
    print(f"整体去重率: {diversity_analysis['deduplication_rate']:.2f}%")
    print()
    print(f"原始顺序SQL数量: {diversity_analysis['original_sqls']}")
    print(f"原始顺序唯一SQL: {diversity_analysis['original_unique']}")
    print(f"原始顺序多样性: {diversity_analysis['original_diversity']:.2f}%")
    print()
    print(f"排列组合SQL数量: {diversity_analysis['permutation_sqls']}")
    print(f"排列组合唯一SQL: {diversity_analysis['permutation_unique']}")
    print(f"排列组合多样性: {diversity_analysis['permutation_diversity']:.2f}%")
    print()
    print(f"原始顺序与排列组合重叠率: {diversity_analysis['overlap_rate']:.2f}%")
    print()
    print("各排列索引使用统计:")
    for perm_idx, count in diversity_analysis['permutation_stats'].items():
        print(f"  排列 {perm_idx}: {count} 次")
    # 分标签统计打印
    if 'per_tag_stats' in diversity_analysis:
        print()
        print("按标签统计(用于比较 倒序+多温度 / 仅多温度 / 仅倒序):")
        for tag, stats in diversity_analysis['per_tag_stats'].items():
            print(f"  标签 {tag}: 数量 {stats['count']}, 唯一 {stats['unique']}, 多样性 {stats['diversity']:.2f}%")
    
    # 保存分析结果
    if args.analysis_out:
        try:
            with open(args.analysis_out, 'w', encoding='utf-8') as f:
                json.dump(diversity_analysis, f, indent=4, ensure_ascii=False)
            print(f"\n多样性分析结果已保存到: {args.analysis_out}")
        except Exception as e:
            print(f"保存分析结果失败: {e}")
    
    print("\n" + "="*80)
    print("实验完成！")
    print("="*80)

if __name__ == "__main__":
    main()
