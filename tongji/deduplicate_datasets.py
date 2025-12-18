import json
from typing import List, Dict, Any
import os

def deduplicate_datasets(file1_path: str, file2_path: str, output_path: str = None) -> Dict[str, Any]:
    """
    对两个JSON数据集进行去重，基于question_id字段
    
    Args:
        file1_path: 第一个JSON文件路径
        file2_path: 第二个JSON文件路径  
        output_path: 输出文件路径，如果为None则不保存文件
        
    Returns:
        包含去重统计信息的字典
    """
    
    # 读取两个文件
    with open(file1_path, 'r', encoding='utf-8') as f:
        data1 = json.load(f)
    
    with open(file2_path, 'r', encoding='utf-8') as f:
        data2 = json.load(f)
    
    print(f"原始数据统计:")
    print(f"文件1 ({file1_path}): {len(data1)} 条记录")
    print(f"文件2 ({file2_path}): {len(data2)} 条记录")
    
    # 创建question_id到数据的映射
    data1_dict = {item['question_id']: item for item in data1}
    data2_dict = {item['question_id']: item for item in data2}
    
    # 找出重复的question_id
    common_ids = set(data1_dict.keys()) & set(data2_dict.keys())
    unique_to_data1 = set(data1_dict.keys()) - set(data2_dict.keys())
    unique_to_data2 = set(data2_dict.keys()) - set(data1_dict.keys())
    
    print(f"\n去重分析:")
    print(f"重复的question_id数量: {len(common_ids)}")
    print(f"仅在文件1中的question_id数量: {len(unique_to_data1)}")
    print(f"仅在文件2中的question_id数量: {len(unique_to_data2)}")
    
    # 合并数据，优先保留文件1中的数据（对于重复的question_id）
    merged_data = []
    
    # 添加文件1中的所有数据
    merged_data.extend(data1)
    
    # 添加文件2中独有的数据
    for question_id in unique_to_data2:
        merged_data.append(data2_dict[question_id])
    
    print(f"\n去重后统计:")
    print(f"合并后的记录总数: {len(merged_data)}")
    print(f"去重减少的记录数: {len(data1) + len(data2) - len(merged_data)}")
    
    # 保存结果
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(merged_data, f, ensure_ascii=False, indent=2)
        print(f"\n去重后的数据已保存到: {output_path}")
    
    # 返回统计信息
    stats = {
        'original_data1_count': len(data1),
        'original_data2_count': len(data2),
        'duplicate_count': len(common_ids),
        'unique_to_data1_count': len(unique_to_data1),
        'unique_to_data2_count': len(unique_to_data2),
        'merged_count': len(merged_data),
        'removed_count': len(data1) + len(data2) - len(merged_data),
        'duplicate_question_ids': list(common_ids)
    }
    
    return stats

def deduplicate_with_custom_key(file1_path: str, file2_path: str, key_field: str, output_path: str = None) -> Dict[str, Any]:
    """
    使用自定义字段作为去重键进行去重
    
    Args:
        file1_path: 第一个JSON文件路径
        file2_path: 第二个JSON文件路径
        key_field: 用作去重键的字段名
        output_path: 输出文件路径，如果为None则不保存文件
        
    Returns:
        包含去重统计信息的字典
    """
    
    # 读取两个文件
    with open(file1_path, 'r', encoding='utf-8') as f:
        data1 = json.load(f)
    
    with open(file2_path, 'r', encoding='utf-8') as f:
        data2 = json.load(f)
    
    print(f"原始数据统计:")
    print(f"文件1 ({file1_path}): {len(data1)} 条记录")
    print(f"文件2 ({file2_path}): {len(data2)} 条记录")
    print(f"使用字段 '{key_field}' 作为去重键")
    
    # 创建key_field到数据的映射
    data1_dict = {item[key_field]: item for item in data1 if key_field in item}
    data2_dict = {item[key_field]: item for item in data2 if key_field in item}
    
    # 找出重复的key_field值
    common_keys = set(data1_dict.keys()) & set(data2_dict.keys())
    unique_to_data1 = set(data1_dict.keys()) - set(data2_dict.keys())
    unique_to_data2 = set(data2_dict.keys()) - set(data1_dict.keys())
    
    print(f"\n去重分析:")
    print(f"重复的{key_field}数量: {len(common_keys)}")
    print(f"仅在文件1中的{key_field}数量: {len(unique_to_data1)}")
    print(f"仅在文件2中的{key_field}数量: {len(unique_to_data2)}")
    
    # 合并数据，优先保留文件1中的数据
    merged_data = []
    
    # 添加文件1中的所有数据
    merged_data.extend(data1)
    
    # 添加文件2中独有的数据
    for key in unique_to_data2:
        merged_data.append(data2_dict[key])
    
    print(f"\n去重后统计:")
    print(f"合并后的记录总数: {len(merged_data)}")
    print(f"去重减少的记录数: {len(data1) + len(data2) - len(merged_data)}")
    
    # 保存结果
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(merged_data, f, ensure_ascii=False, indent=2)
        print(f"\n去重后的数据已保存到: {output_path}")
    
    # 返回统计信息
    stats = {
        'original_data1_count': len(data1),
        'original_data2_count': len(data2),
        'duplicate_count': len(common_keys),
        'unique_to_data1_count': len(unique_to_data1),
        'unique_to_data2_count': len(unique_to_data2),
        'merged_count': len(merged_data),
        'removed_count': len(data1) + len(data2) - len(merged_data),
        'duplicate_keys': list(common_keys)
    }
    
    return stats

if __name__ == "__main__":
    # 示例用法
    file1_path = "/home/shenshuyu/SQL_tool/tongji/function_coverage_subset.json"
    file2_path = "/home/shenshuyu/SQL_tool/tongji/coverage_small_subset.json"
    output_path = "deduplicated_data.json"
    
    # 使用question_id进行去重
    print("=== 使用question_id进行去重 ===")
    stats = deduplicate_datasets(file1_path, file2_path, output_path)
    
    print("\n=== 详细统计信息 ===")
    for key, value in stats.items():
        print(f"{key}: {value}")
    
    # 也可以使用其他字段进行去重
    # stats = deduplicate_with_custom_key(file1_path, file2_path, "db_id", "deduplicated_by_db_id.json")