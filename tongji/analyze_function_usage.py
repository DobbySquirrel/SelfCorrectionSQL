import sys
import os
import json
import csv
import sqlglot
from collections import Counter, defaultdict
from typing import Dict, List, Set

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
            
            # 特殊处理其他函数节点（如replace, exists等）
            elif op in ["replace", "exists", "abs", "round", "length", "trim", "lower", "substring", "strposition", "cast", "datatype", "count", "sum", "avg", "max", "min", "groupconcat", "timestampdiff", "datediff", "timetostr", "tsordstotimestamp", "date", "currenttimestamp", "from", "group", "order", "limit", "offset", "join", "union", "except", "distinct", "rownumber", "if", "case", "rank", "dense_rank", "now", "time", "total", "dpipe", "datetime", "boolean", "neg", "add", "sub", "mul", "div", "eq", "gt", "lt", "ge", "le", "ne", "neq", "is", "null", "in", "between", "like", "not", "and", "or", "having", "paren", "star", "alias", "tablealias", "table_rename", "subquery", "cte", "with", "window"]:
                # 将小写函数名转换为大写以匹配node_to_func中的键
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

def analyze_dataset(filepath: str, dataset_name: str) -> Dict[str, int]:
    """分析单个数据集的操作符使用频率"""
    print(f"正在分析 {dataset_name} 数据集...")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {filepath}")
        return {}
    except json.JSONDecodeError as e:
        print(f"错误: JSON解析失败 {e}")
        return {}
    
    op_counter = Counter()
    total_sql = len(data)
    
    for i, item in enumerate(data):
        if i % 1000 == 0:
            print(f"    处理进度: {i}/{total_sql}")
        
        sql = item.get('SQL', '').strip()
        if not sql:
            continue
            
        operators = extract_operators_from_sql(sql)
        op_counter.update(operators)
    
    print(f"    {dataset_name} 数据集分析完成，共处理 {total_sql} 条SQL")
    return dict(op_counter)

def main():
    """主函数"""
    print("=== 分析 node_to_func 中函数的使用频率 ===")
    
    # 数据集文件路径
    train_file = '/home/shenshuyu/SQL_tool/work/bird/train/train.json'
    dev_file = '/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev.json'
    
    # 分析数据集
    train_stats = analyze_dataset(train_file, "训练")
    dev_stats = analyze_dataset(dev_file, "开发")
    
    # 获取所有在 node_to_func 中的函数
    mapped_functions = set(node_to_func.keys())
    print(f"\nnode_to_func 中共有 {len(mapped_functions)} 个函数")
    
    # 统计结果并构建 results 列表
    results = []
    total_train = sum(train_stats.values())
    total_dev = sum(dev_stats.values())
    
    # 按函数名分组的统计
    function_groups = defaultdict(lambda: {
        'train_count': 0,
        'dev_count': 0,
        'total_count': 0,
        'operators': []
    })
    
    # 处理每个操作符，按函数名分组
    for func in sorted(mapped_functions):
        train_count = train_stats.get(func, 0)
        dev_count = dev_stats.get(func, 0)
        total_count = train_count + dev_count
        
        # 获取对应的函数名映射
        mapped_func_name = node_to_func.get(func, "未映射")
        
        # 提取函数名（去掉参数部分）
        if '(' in mapped_func_name:
            func_name = mapped_func_name.split('(')[0].strip()
        else:
            func_name = mapped_func_name
        
        # 按函数名分组
        function_groups[func_name]['train_count'] += train_count
        function_groups[func_name]['dev_count'] += dev_count
        function_groups[func_name]['total_count'] += total_count
        function_groups[func_name]['operators'].append(func)
    
    # 转换为results列表
    for func_name, group_data in function_groups.items():
        train_count = group_data['train_count']
        dev_count = group_data['dev_count']
        total_count = group_data['total_count']
        
        train_pct = (train_count / total_train * 100) if total_train > 0 else 0
        dev_pct = (dev_count / total_dev * 100) if total_dev > 0 else 0
        total_pct = (total_count / (total_train + total_dev) * 100) if (total_train + total_dev) > 0 else 0
        
        results.append({
            'function': func_name,
            'operators': group_data['operators'],
            'train_count': train_count,
            'train_pct': train_pct,
            'dev_count': dev_count,
            'dev_pct': dev_pct,
            'total_count': total_count,
            'total_pct': total_pct
        })
    
    # Sort the results list by 'total_count' in descending order
    results.sort(key=lambda x: x['total_count'], reverse=True)
    
    print(f"\n=== 函数使用频率统计（按函数名分组） ===")
    print(f"{'函数名':<20} {'训练集':<10} {'训练集%':<10} {'开发集':<10} {'开发集%':<10} {'总计':<10} {'总计%':<10}")
    print("-" * 90)
    
    # Print the sorted table
    for result in results:
        print(f"{result['function']:<20} {result['train_count']:<10} {result['train_pct']:<10.2f} {result['dev_count']:<10} {result['dev_pct']:<10.2f} {result['total_count']:<10} {result['total_pct']:<10.2f}")
    
    # 统计未使用的函数
    unused_functions = [func['function'] for func in results if func['total_count'] == 0]
    
    print(f"\n=== 未使用的函数 ({len(unused_functions)} 个) ===")
    for func in sorted(unused_functions):
        print(f"    {func}")
        
    # 保存CSV格式的表格
    output_csv_file = 'tongji/function_usage_stats.csv'
    try:
        with open(output_csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # 写入表头
            writer.writerow(['函数名', '训练集', '训练集%', '开发集', '开发集%', '总计', '总计%'])
            # 写入数据
            for result in results:
                writer.writerow([
                    result['function'],
                    result['train_count'],
                    f"{result['train_pct']:.2f}",
                    result['dev_count'],
                    f"{result['dev_pct']:.2f}",
                    result['total_count'],
                    f"{result['total_pct']:.2f}"
                ])
        print(f"CSV表格已保存到: {output_csv_file}")
    except Exception as e:
        print(f"保存CSV文件失败: {e}")

if __name__ == "__main__":
    main()