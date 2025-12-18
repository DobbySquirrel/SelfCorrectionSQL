import json
from typing import List, Dict, Any

def generate_new_coverage_subset():
    """
    生成新的coverage_small_subset.json，包含不在function_coverage_subset.json中的内容
    """
    
    # 读取两个文件
    with open('/home/shenshuyu/SQL_tool/tongji/function_coverage_subset.json', 'r', encoding='utf-8') as f:
        function_coverage_data = json.load(f)
    
    with open('/home/shenshuyu/SQL_tool/tongji/coverage_small_subset.json', 'r', encoding='utf-8') as f:
        coverage_small_data = json.load(f)
    
    print(f"原始数据统计:")
    print(f"function_coverage_subset.json: {len(function_coverage_data)} 条记录")
    print(f"coverage_small_subset.json: {len(coverage_small_data)} 条记录")
    
    # 创建question_id到数据的映射
    function_coverage_dict = {item['question_id']: item for item in function_coverage_data}
    coverage_small_dict = {item['question_id']: item for item in coverage_small_data}
    
    # 找出在coverage_small_subset.json中但不在function_coverage_subset.json中的question_id
    unique_to_coverage_small = set(coverage_small_dict.keys()) - set(function_coverage_dict.keys())
    
    print(f"\n分析结果:")
    print(f"仅在coverage_small_subset.json中的question_id数量: {len(unique_to_coverage_small)}")
    
    # 提取独有的数据
    new_coverage_data = []
    for question_id in unique_to_coverage_small:
        new_coverage_data.append(coverage_small_dict[question_id])
    
    print(f"新生成的coverage_small_subset.json将包含: {len(new_coverage_data)} 条记录")
    
    # 保存新的coverage_small_subset.json
    output_path = "new_coverage_small_subset.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(new_coverage_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n新的coverage_small_subset.json已保存到: {output_path}")
    
    # 显示一些示例数据
    if new_coverage_data:
        print(f"\n示例数据 (前3条):")
        for i, item in enumerate(new_coverage_data[:3]):
            print(f"{i+1}. question_id: {item['question_id']}, db_id: {item['db_id']}")
            print(f"   问题: {item['question'][:100]}...")
            print()
    
    return {
        'original_function_coverage_count': len(function_coverage_data),
        'original_coverage_small_count': len(coverage_small_data),
        'unique_to_coverage_small_count': len(unique_to_coverage_small),
        'new_coverage_data_count': len(new_coverage_data),
        'output_file': output_path
    }

def generate_coverage_with_custom_filter():
    """
    使用自定义过滤条件生成新的coverage_small_subset.json
    """
    
    # 读取两个文件
    with open('/home/shenshuyu/SQL_tool/tongji/function_coverage_subset.json', 'r', encoding='utf-8') as f:
        function_coverage_data = json.load(f)
    
    with open('/home/shenshuyu/SQL_tool/tongji/coverage_small_subset.json', 'r', encoding='utf-8') as f:
        coverage_small_data = json.load(f)
    
    # 创建question_id到数据的映射
    function_coverage_dict = {item['question_id']: item for item in function_coverage_data}
    
    # 过滤出不在function_coverage_subset.json中的数据
    new_coverage_data = []
    for item in coverage_small_data:
        if item['question_id'] not in function_coverage_dict:
            new_coverage_data.append(item)
    
    print(f"过滤后的数据统计:")
    print(f"原始coverage_small_subset.json: {len(coverage_small_data)} 条记录")
    print(f"function_coverage_subset.json: {len(function_coverage_data)} 条记录")
    print(f"过滤后的记录数: {len(new_coverage_data)} 条记录")
    
    # 保存结果
    output_path = "/home/shenshuyu/SQL_tool/tongji/filtered_coverage_small_subset.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(new_coverage_data, f, ensure_ascii=False, indent=2)
    
    print(f"过滤后的数据已保存到: {output_path}")
    
    return new_coverage_data

if __name__ == "__main__":
    print("=== 生成新的coverage_small_subset.json ===")
    stats = generate_new_coverage_subset()
    
    print("\n=== 统计信息 ===")
    for key, value in stats.items():
        print(f"{key}: {value}")
    
    print("\n=== 使用自定义过滤 ===")
    filtered_data = generate_coverage_with_custom_filter()