import json
import argparse
import os

def process_files(input_txt_path, sub_sampled_path, output_json_path):
    """
    处理baseline_sql_generator.py的输出文件，转换为TXTjson格式
    
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
            sql = line.strip()
            
            # 获取对应的数据库名称
            db_name = db_map[question_id]
            result[question_id] = f"{sql}\t----- bird -----\t{db_name}"
    
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

# python /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson.py --input_txt /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/11_6_parallel.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json --output_json /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/11_6_parallel.json
# python /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson.py --input_txt /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_6all.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set_error.json --output_json /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_6all.json
# python /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson.py --input_txt /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_6all.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json --output_json /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_6all.json
# python /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson.py --input_txt /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/exp2_sql.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json --output_json /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/exp2.json
# python /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson.py --input_txt /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_9_strategy1_sql_bucket.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json --output_json /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_9_strategy1_sql_bucket.json
# python /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson.py --input_txt /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_9_strategy2_avg_cte_bucket.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json --output_json /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_9_strategy2_avg_cte_bucket.json
# python /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson.py --input_txt /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_17_wo_schemaFilter.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json --output_json /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_17_wo_schemaFilter.json
# python /home/shenshuyu/SQL_tool_multiAgent/evaluation/TXTjson.py --input_txt /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_15.txt --sub_sampled_json /home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json --output_json /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_15.json

