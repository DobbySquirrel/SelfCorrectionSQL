#!/usr/bin/env python3
import json
import os

def convert_txt_to_json(txt_file_path, sub_sampled_json_path, output_json_path):
    """
    将TXT文件转换为JSON格式
    
    Args:
        txt_file_path: TXT文件路径（每行一个SQL）
        sub_sampled_json_path: sub_sampled_bird_dev_set.json文件路径
        output_json_path: 输出的JSON文件路径
    """
    print(f"开始转换TXT文件到JSON格式...")
    print(f"TXT文件: {txt_file_path}")
    print(f"参考文件: {sub_sampled_json_path}")
    print(f"输出文件: {output_json_path}")
    
    try:
        # 读取TXT文件
        with open(txt_file_path, 'r', encoding='utf-8') as f:
            sql_lines = f.readlines()
        print(f"成功读取TXT文件，包含 {len(sql_lines)} 行SQL")
    except FileNotFoundError:
        print(f"错误: TXT文件 '{txt_file_path}' 未找到。")
        return
    except Exception as e:
        print(f"错误: 读取TXT文件失败: {e}")
        return
    
    try:
        # 读取sub_sampled_bird_dev_set.json文件
        with open(sub_sampled_json_path, 'r', encoding='utf-8') as f:
            sub_sampled_data = json.load(f)
        print(f"成功读取sub_sampled_data，包含 {len(sub_sampled_data)} 条记录")
    except FileNotFoundError:
        print(f"错误: 参考文件 '{sub_sampled_json_path}' 未找到。")
        return
    except json.JSONDecodeError:
        print(f"错误: 参考文件 '{sub_sampled_json_path}' 不是有效的JSON格式。")
        return
    
    # 创建question_id到db_id的映射
    db_map = {str(item['question_id']): item['db_id'] for item in sub_sampled_data if 'question_id' in item and 'db_id' in item}
    print(f"创建db_map，包含 {len(db_map)} 个映射")
    
    # 创建JSON结果
    json_result = {}
    question_ids = list(db_map.keys())
    
    # 按顺序处理SQL语句
    for i, sql_line in enumerate(sql_lines):
        sql = sql_line.strip()
        if sql:  # 跳过空行
            if i < len(question_ids):
                question_id = question_ids[i]
                db_name = db_map[question_id]
                json_result[question_id] = f"{sql}\t----- bird -----\t{db_name}"
            else:
                print(f"警告: SQL行 {i+1} 没有对应的question_id")
    
    print(f"构建JSON结果，包含 {len(json_result)} 条记录")
    
    # 根据question_id进行排序
    try:
        sorted_result = dict(sorted(json_result.items(), key=lambda item: int(item[0])))
        print(f"按question_id排序完成")
    except ValueError as e:
        print(f"警告: 无法将部分 question_id 转换为整数进行排序: {e}")
        sorted_result = json_result
    
    # 保存JSON文件
    try:
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_result, f, indent=4, ensure_ascii=False)
        print(f"转换完成! 已生成结果文件: {output_json_path}")
        print(f"成功转换的SQL语句数量: {len(sorted_result)}")
    except Exception as e:
        print(f"错误: 无法写入输出文件 '{output_json_path}'。错误信息: {e}")

if __name__ == "__main__":
    # 文件路径
    txt_file = "/home/shenshuyu/SQL_tool_multiAgent/test/test_baseline/out/results/baseline_5runs_10_26.txt"
    sub_sampled_json = "/home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json"
    output_json = "/home/shenshuyu/SQL_tool_multiAgent/test/test_baseline/out/results/baseline_5runs_10_26.json"
    
    # 执行转换
    convert_txt_to_json(txt_file, sub_sampled_json, output_json)

