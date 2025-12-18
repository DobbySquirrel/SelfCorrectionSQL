import json
import argparse
import os

def process_files(input_txt_path, sub_sampled_path, output_json_path):
    """
    处理baseline_sql_generator.py的输出文件，转换为TXTjson格式

    现在输入文件的每一行是一个JSON数组，需提取其中的第一个元素（List[0]）作为SQL。

    Args:
        input_txt_path: baseline_sql_generator.py输出的SQL文件路径
        sub_sampled_path: sub_sampled_bird_dev_set.json文件路径
        output_json_path: 输出的JSON文件路径
    """
    # 读取sub_sampled_bird_dev_set.json文件
    with open(sub_sampled_path, 'r', encoding='utf-8') as f:
        sub_sampled_data = json.load(f)
    
    # 创建question_id到db_id的映射
    db_map = {str(item['question_id']): item['db_id'] for item in sub_sampled_data}
    
    # 读取输入的SQL文件
    with open(input_txt_path, 'r', encoding='utf-8') as f:
        sql_lines = f.readlines()
    
    # 创建新的结果字典
    result = {}
    
    # 获取sub_sampled_json中的question_ids列表
    question_ids = list(db_map.keys())
    
    # 确保SQL语句数量与question_ids数量匹配
    if len(sql_lines) != len(question_ids):
        print(f"警告: SQL语句数量({len(sql_lines)})与question_ids数量({len(question_ids)})不匹配")
    
    # 处理每一行SQL，按顺序与question_ids匹配
    for i, line in enumerate(sql_lines):
        if i < len(question_ids):
            question_id = question_ids[i]

            # line 可能是 JSON 数组，形如 ["sql1", "sql2", ...]
            # 需要取 List[0] 作为最终 SQL；解析失败时回退为原始去空白字符串
            parsed_sql = line.strip()
            try:
                candidate_list = json.loads(line)
                if isinstance(candidate_list, list) and len(candidate_list) > 0 and isinstance(candidate_list[0], str):
                    parsed_sql = candidate_list[0].strip()
            except Exception:
                # 如果整行 JSON 解析失败，尝试截取出方括号内的片段再次解析
                try:
                    start_idx = line.find('[')
                    end_idx = line.rfind(']')
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        inner = line[start_idx:end_idx+1]
                        candidate_list = json.loads(inner)
                        if isinstance(candidate_list, list) and len(candidate_list) > 0 and isinstance(candidate_list[0], str):
                            parsed_sql = candidate_list[0].strip()
                except Exception:
                    # 最后兜底：从行中提取第一个双引号括起来的字符串
                    try:
                        first_quote = line.find('"')
                        if first_quote != -1:
                            second_quote = line.find('"', first_quote + 1)
                            if second_quote != -1:
                                raw_first = line[first_quote:second_quote+1]  # 包含两侧引号
                                # 用 json 反序列化去转义
                                parsed_sql = json.loads(raw_first).strip()
                    except Exception:
                        # 保持回退值 parsed_sql 为 line.strip()
                        pass

            # 获取对应的数据库名称
            db_name = db_map[question_id]
            result[question_id] = f"{parsed_sql}\t----- bird -----\t{db_name}"
    
    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
    
    # 保存为新的JSON文件
    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=4, ensure_ascii=False)
    
    print(f"转换完成！结果已保存到 {output_json_path}")
    print(f"处理了 {len(result)} 个SQL语句")

def main():
    parser = argparse.ArgumentParser(description="将baseline_sql_generator.py的输出转换为TXTjson格式")
    parser.add_argument("--input_txt", type=str, required=True, help="baseline_sql_generator.py输出的SQL文件路径")
    parser.add_argument("--sub_sampled_json", type=str, required=True, help="sub_sampled_bird_dev_set.json文件路径")
    parser.add_argument("--output_json", type=str, required=True, help="输出的JSON文件路径")
    
    args = parser.parse_args()
    
    process_files(args.input_txt, args.sub_sampled_json, args.output_json)

if __name__ == "__main__":
    main()

# /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson_multiline.py --input_txt test/test_baseline/out/only_temps.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json --output_json test/test_baseline/out/only_temps.json