import sys
import os
import json
import re
from collections import Counter, defaultdict
from typing import Dict, List, Set

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.auto_stat_atomic_operators import node_to_func

def extract_functions_from_operations(operations: List[str]) -> List[str]:
    """从operations列表中提取函数名"""
    function_names = []
    for op_str in operations:
        # 匹配格式: "operation: xxx -> function_name"
        match = re.search(r'->\s*([^(]+)', op_str)
        if match:
            func_name = match.group(1).strip()
            if func_name and func_name != "None":
                function_names.append(func_name)
    return function_names

def analyze_dataset_for_coverage(filepath: str, dataset_name: str) -> List[Dict]:
    """分析数据集，返回包含函数信息的SQL条目"""
    print(f"正在分析 {dataset_name} 数据集...")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {filepath}")
        return []
    except json.JSONDecodeError as e:
        print(f"错误: JSON解析失败 {e}")
        return []
    
    sql_with_functions = []
    total_sql = len(data)
    
    for i, item in enumerate(data):
        if i % 1000 == 0:
            print(f"    处理进度: {i}/{total_sql}")
        
        operations = item.get('operations', [])
        if operations:
            # 从operations中提取函数名
            function_names = extract_functions_from_operations(operations)
            # 去重
            unique_functions = list(set(function_names))
            
            if unique_functions:
                # 添加函数信息到条目中（仅用于分析，不保存）
                item_with_functions = item.copy()
                item_with_functions['functions'] = unique_functions
                item_with_functions['function_count'] = len(unique_functions)
                sql_with_functions.append(item_with_functions)
    
    print(f"    {dataset_name} 数据集分析完成，共处理 {total_sql} 条SQL，有效 {len(sql_with_functions)} 条")
    return sql_with_functions

def generate_coverage_subset(train_data: List[Dict], dev_data: List[Dict]) -> List[Dict]:
    """生成包含所有函数至少出现一次的子集"""
    print("生成函数覆盖子集...")
    
    # 获取所有需要覆盖的函数
    all_functions = set(node_to_func.values())
    # 提取函数名（去掉参数部分）
    function_names = set()
    for func in all_functions:
        if '(' in func:
            func_name = func.split('(')[0].strip()
        else:
            func_name = func
        function_names.add(func_name)
    
    print(f"需要覆盖的函数数量: {len(function_names)}")
    
    # 合并所有数据
    all_data = train_data + dev_data
    
    # 贪心算法：选择能覆盖最多未覆盖函数的SQL
    selected_items = []
    covered_functions = set()
    
    # 按函数数量排序，优先选择包含更多函数的SQL
    all_data.sort(key=lambda x: x['function_count'], reverse=True)
    
    for item in all_data:
        item_functions = set(item['functions'])
        new_functions = item_functions - covered_functions
        
        if new_functions:  # 如果这个SQL包含新的函数
            # 只保存原始数据，不包含functions字段
            original_item = {k: v for k, v in item.items() if k not in ['functions', 'function_count']}
            selected_items.append(original_item)
            covered_functions.update(new_functions)
            print(f"选择SQL: 包含 {len(item_functions)} 个函数，新增 {len(new_functions)} 个函数")
            print(f"  SQL: {item['SQL'][:100]}...")
            print(f"  新增函数: {list(new_functions)}")
            
            if len(covered_functions) == len(function_names):
                print("所有函数都已覆盖！")
                break
    
    # 检查覆盖情况
    uncovered_functions = function_names - covered_functions
    if uncovered_functions:
        print(f"警告: 以下函数未被覆盖: {list(uncovered_functions)}")
    else:
        print(f"成功覆盖所有 {len(function_names)} 个函数！")
    
    print(f"子集大小: {len(selected_items)} 条SQL")
    print(f"覆盖函数数: {len(covered_functions)} / {len(function_names)}")
    
    return selected_items

def main():
    """主函数"""
    print("=== 生成函数覆盖子集 ===")
    
    # 数据集文件路径（使用已处理的文件）
    train_file = '/home/shenshuyu/SQL_tool/work/bird/train/train_with_operations.json'
    dev_file = '/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_with_operations.json'
    
    # 分析数据集
    train_data = analyze_dataset_for_coverage(train_file, "训练")
    dev_data = analyze_dataset_for_coverage(dev_file, "开发")
    
    # 生成覆盖子集
    subset = generate_coverage_subset(train_data, dev_data)
    
    # 保存子集
    output_file = 'tongji/function_coverage_subset.json'
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(subset, f, ensure_ascii=False, indent=2)
        print(f"\n函数覆盖子集已保存到: {output_file}")
        
        # 生成覆盖统计（重新分析保存的数据）
        all_functions_in_subset = set()
        total_function_count = 0
        
        for item in subset:
            operations = item.get('operations', [])
            if operations:
                function_names = extract_functions_from_operations(operations)
                unique_functions = list(set(function_names))
                all_functions_in_subset.update(unique_functions)
                total_function_count += len(unique_functions)
        
        print(f"\n=== 子集统计 ===")
        print(f"子集SQL数量: {len(subset)}")
        print(f"覆盖函数数量: {len(all_functions_in_subset)}")
        print(f"平均每条SQL函数数: {total_function_count / len(subset):.2f}")
        
        # 按函数数量分布
        function_count_dist = Counter()
        for item in subset:
            operations = item.get('operations', [])
            if operations:
                function_names = extract_functions_from_operations(operations)
                unique_functions = list(set(function_names))
                function_count_dist[len(unique_functions)] += 1
        
        print(f"\n函数数量分布:")
        for count, num_sql in sorted(function_count_dist.items()):
            print(f"  {count} 个函数: {num_sql} 条SQL")
        
        # 显示每个函数在哪些SQL中出现
        print(f"\n=== 函数出现位置 ===")
        function_locations = defaultdict(list)
        for i, item in enumerate(subset):
            operations = item.get('operations', [])
            if operations:
                function_names = extract_functions_from_operations(operations)
                unique_functions = list(set(function_names))
                for func in unique_functions:
                    function_locations[func].append(i)
        
        for func in sorted(function_locations.keys()):
            locations = function_locations[func]
            print(f"{func}: 出现在SQL {locations}")
            
    except Exception as e:
        print(f"保存文件失败: {e}")

if __name__ == "__main__":
    main()