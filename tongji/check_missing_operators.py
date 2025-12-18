import re
import ast

def extract_keywords_from_count_file(filepath):
    """从Key_count_updated.txt中提取所有操作符关键词"""
    keywords = set()
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            # 匹配格式: "keyword: count"
            match = re.match(r'^\s*([^:]+):\s*\d+', line)
            if match:
                keyword = match.group(1).strip()
                keywords.add(keyword)
    return keywords

def extract_mapped_operators_from_stat_file(filepath):
    """从stat_atomic_operators.py中提取已映射的操作符"""
    mapped_operators = set()
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        # 使用正则表达式提取node_to_func字典中的键
        matches = re.findall(r"'([^']+)':", content)
        mapped_operators.update(matches)
    return mapped_operators

def main():
    # 提取关键词
    count_keywords = extract_keywords_from_count_file('utils/Key_count_updated.txt')
    mapped_operators = extract_mapped_operators_from_stat_file('utils/stat_atomic_operators.py')
    
    print(f"Key_count_updated.txt中的操作符总数: {len(count_keywords)}")
    print(f"stat_atomic_operators.py中已映射的操作符总数: {len(mapped_operators)}")
    print()
    
    # 找出缺失的操作符
    missing_operators = count_keywords - mapped_operators
    extra_operators = mapped_operators - count_keywords
    
    print("=== 缺失的操作符（在Key_count中出现但stat_atomic_operators中没有映射）===")
    if missing_operators:
        for op in sorted(missing_operators):
            print(f"  {op}")
    else:
        print("  无缺失操作符")
    
    print()
    print("=== 多余的操作符（在stat_atomic_operators中有映射但在Key_count中未出现）===")
    if extra_operators:
        for op in sorted(extra_operators):
            print(f"  {op}")
    else:
        print("  无多余操作符")
    
    print()
    print("=== 建议添加的操作符（按使用频率排序）===")
    # 重新读取文件获取使用频率
    keyword_counts = {}
    with open('utils/Key_count_updated.txt', 'r', encoding='utf-8') as f:
        for line in f:
            match = re.match(r'^\s*([^:]+):\s*(\d+)', line)
            if match:
                keyword = match.group(1).strip()
                count = int(match.group(2))
                keyword_counts[keyword] = count
    
    # 按使用频率排序缺失的操作符
    missing_with_counts = [(op, keyword_counts.get(op, 0)) for op in missing_operators]
    missing_with_counts.sort(key=lambda x: x[1], reverse=True)
    
    for op, count in missing_with_counts:
        print(f"  {op}: {count}次使用")

if __name__ == "__main__":
    main()