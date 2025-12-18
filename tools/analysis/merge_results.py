import json
import os

def merge_results(all_file, update_file, output_file):
    # 读取两个文件
    with open(all_file, 'r') as f:
        all_data = json.load(f)
    with open(update_file, 'r') as f:
        update_data = json.load(f)
    
    # 创建ID到数据的映射
    all_dict = {int(item['id']): item for item in all_data}
    update_dict = {int(item['id']): item for item in update_data}
    
    # 更新数据
    all_dict.update(update_dict)
    
    # 转换回列表并按ID排序
    merged_data = list(all_dict.values())
    merged_data.sort(key=lambda x: int(x['id']))
    
    # 保存结果
    with open(output_file, 'w') as f:
        json.dump(merged_data, f, indent=2)
    
    print(f"总问题数: {len(merged_data)}")
    print(f"更新了 {len(update_data)} 个问题的结果")
    print(f"结果已保存到: {output_file}")

if __name__ == "__main__":
    all_file = "/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_16/generation_1_all.json"
    update_file = "/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_16/generation_1.json"
    output_file = "/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_16/generation_1_all_updated.json"
    
    merge_results(all_file, update_file, output_file) 