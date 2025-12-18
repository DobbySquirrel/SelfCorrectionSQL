 #!/usr/bin/env python3
import json
import sys

def load_json_file(file_path):
    """加载JSON文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"加载文件 {file_path} 时出错: {e}")
        return None

def extract_problematic_question_ids(analysis_results):
    """从分析结果中提取有问题的question_id列表"""
    problematic_ids = set()
    
    if 'problematic_queries_full_list' in analysis_results:
        for query in analysis_results['problematic_queries_full_list']:
            if 'question_id' in query:
                problematic_ids.add(query['question_id'])
    
    return problematic_ids

def filter_train_data(train_data, problematic_ids):
    """过滤训练数据，排除有问题的查询"""
    filtered_data = []
    excluded_count = 0
    
    for item in train_data:
        if 'question_id' in item:
            if item['question_id'] not in problematic_ids:
                filtered_data.append(item)
            else:
                excluded_count += 1
        else:
            # 如果没有question_id字段，保留该项
            filtered_data.append(item)
    
    return filtered_data, excluded_count

def main():
    # 文件路径
    train_file = '/home/shenshuyu/SQL_tool/work/bird/train/train.json'
    analysis_file = '/home/shenshuyu/SQL_tool/work/bird/train/sql_query_analysis_results.json'
    output_file = '/home/shenshuyu/SQL_tool/work/bird/train/train_wo_null.json'
    
    print("开始处理...")
    
    # 加载训练数据
    print("加载训练数据...")
    train_data = load_json_file(train_file)
    if train_data is None:
        print("无法加载训练数据文件")
        return
    
    print(f"原始训练数据条数: {len(train_data)}")
    
    # 加载分析结果
    print("加载分析结果...")
    analysis_results = load_json_file(analysis_file)
    if analysis_results is None:
        print("无法加载分析结果文件")
        return
    
    # 提取有问题的question_id
    print("提取有问题的查询ID...")
    problematic_ids = extract_problematic_question_ids(analysis_results)
    print(f"发现 {len(problematic_ids)} 个有问题的查询ID")
    
    # 过滤数据
    print("过滤训练数据...")
    filtered_data, excluded_count = filter_train_data(train_data, problematic_ids)
    
    print(f"过滤后数据条数: {len(filtered_data)}")
    print(f"排除的查询数量: {excluded_count}")
    
    # 保存结果
    print("保存过滤后的数据...")
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(filtered_data, f, ensure_ascii=False, indent=4)
        print(f"成功保存到: {output_file}")
    except Exception as e:
        print(f"保存文件时出错: {e}")
        return
    
    # 验证结果
    print("\n验证结果:")
    print(f"- 原始数据条数: {len(train_data)}")
    print(f"- 有问题的查询ID数量: {len(problematic_ids)}")
    print(f"- 过滤后数据条数: {len(filtered_data)}")
    print(f"- 排除的查询数量: {excluded_count}")
    print(f"- 保留率: {len(filtered_data)/len(train_data)*100:.2f}%")

if __name__ == "__main__":
    main()