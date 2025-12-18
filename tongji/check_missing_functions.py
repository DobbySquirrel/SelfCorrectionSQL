import ast
import re
from collections import OrderedDict
import os

def extract_functions_from_sql_atomic():
    """从 sql_atomic_operators.py 中提取所有 op_xxx 和 build_xxx 函数"""
    sql_atomic_path = os.path.join('utils', 'sql_atomic_operators.py')
    with open(sql_atomic_path, 'r', encoding='utf-8') as f:
        sql_atomic_code = f.read()
    
    parsed = ast.parse(sql_atomic_code)
    functions = set()
    
    for node in parsed.body:
        if isinstance(node, ast.FunctionDef):
            func_name = node.name
            if func_name.startswith('op_') or func_name.startswith('build_'):
                functions.add(func_name)
    
    return functions

def extract_functions_from_auto_stat():
    """从 auto_stat_atomic_operators.py 中提取所有函数名"""
    auto_stat_path = os.path.join('utils', 'auto_stat_atomic_operators.py')
    with open(auto_stat_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 提取所有函数名
    func_pattern = r'([a-zA-Z_][a-zA-Z0-9_]*)\('
    matches = re.findall(func_pattern, content)
    
    # 过滤出 op_ 和 build_ 开头的函数
    functions = set()
    for match in matches:
        if match.startswith('op_') or match.startswith('build_'):
            functions.add(match)
    
    return functions

def main():
    # 获取两个文件中的函数
    sql_functions = extract_functions_from_sql_atomic()
    auto_stat_functions = extract_functions_from_auto_stat()
    
    print(f"sql_atomic_operators.py 中的函数总数: {len(sql_functions)}")
    print(f"auto_stat_atomic_operators.py 中的函数总数: {len(auto_stat_functions)}")
    
    # 找出在 sql_atomic_operators.py 中但不在 auto_stat_atomic_operators.py 中的函数
    missing_functions = sql_functions - auto_stat_functions
    
    print(f"\n在 sql_atomic_operators.py 中但不在 auto_stat_atomic_operators.py 中的函数 ({len(missing_functions)} 个):")
    for func in sorted(missing_functions):
        print(f"  - {func}")
    
    # 找出在 auto_stat_atomic_operators.py 中但不在 sql_atomic_operators.py 中的函数
    extra_functions = auto_stat_functions - sql_functions
    
    if extra_functions:
        print(f"\n在 auto_stat_atomic_operators.py 中但不在 sql_atomic_operators.py 中的函数 ({len(extra_functions)} 个):")
        for func in sorted(extra_functions):
            print(f"  - {func}")
    else:
        print(f"\nauto_stat_atomic_operators.py 中没有额外的函数")
    
    # 显示交集
    common_functions = sql_functions & auto_stat_functions
    print(f"\n两个文件共有的函数 ({len(common_functions)} 个):")
    for func in sorted(common_functions):
        print(f"  - {func}")

if __name__ == "__main__":
    main()