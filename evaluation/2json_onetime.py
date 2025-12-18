import json
import re

def extract_and_convert_sql(input_json_path, sub_sampled_path, output_json_path, preserve_json_path=None):
    """
    一步完成从JSON文件提取SQL并转换为所需格式

    Args:
        input_json_path: 输入的JSON文件路径（包含SQL语句）
        sub_sampled_path: sub_sampled_bird_dev_set.json文件路径
        output_json_path: 输出的JSON文件路径
        preserve_json_path: 可选，如果提供，对于输入文件中存在的id，保留该文件中的内容
    """
    print(f"开始处理...")
    print(f"输入文件: {input_json_path}")
    print(f"参考文件: {sub_sampled_path}")
    print(f"输出文件: {output_json_path}")

    try:
        # 读取输入的JSON文件
        with open(input_json_path, 'r', encoding='utf-8') as f:
            input_data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 输入文件 '{input_json_path}' 未找到。请检查文件路径是否正确。")
        return
    except json.JSONDecodeError:
        print(f"错误: 输入文件 '{input_json_path}' 不是有效的JSON格式。")
        return

    try:
        # 读取sub_sampled_bird_dev_set.json文件
        with open(sub_sampled_path, 'r', encoding='utf-8') as f:
            sub_sampled_data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 参考文件 '{sub_sampled_path}' 未找到。请检查文件路径是否正确。")
        return
    except json.JSONDecodeError:
        print(f"错误: 参考文件 '{sub_sampled_path}' 不是有效的JSON格式。")
        return

    # 创建question_id到db_id的映射
    db_map = {str(item['question_id']): item['db_id'] for item in sub_sampled_data if 'question_id' in item and 'db_id' in item}

    # 获取sub_sampled中所有的question_id集合
    all_sub_sampled_ids = {str(item['question_id']) for item in sub_sampled_data if 'question_id' in item}

    # 读取preserve_json文件（如果提供）
    preserve_data = {}
    if preserve_json_path:
        try:
            with open(preserve_json_path, 'r', encoding='utf-8') as f:
                preserve_data = json.load(f)
            print(f"已读取保留文件: {preserve_json_path}，包含 {len(preserve_data)} 条记录")
        except FileNotFoundError:
            print(f"警告: 保留文件 '{preserve_json_path}' 未找到，将不使用保留内容。")
        except json.JSONDecodeError:
            print(f"警告: 保留文件 '{preserve_json_path}' 不是有效的JSON格式，将不使用保留内容。")

    # 创建新的结果字典
    result = {}
    
    # 存储输入数据中的SQL（支持列表和字典格式）
    input_sql_map = {}
    if isinstance(input_data, list):
        # 如果输入是列表格式
        for item in input_data:
            if 'question_id' in item and 'sql' in item:
                question_id = str(item['question_id'])
                sql = item['sql']
                input_sql_map[question_id] = sql
    else:
        # 如果输入是字典格式
        for question_id, sql in input_data.items():
            input_sql_map[str(question_id)] = sql

    # 处理所有sub_sampled中的id，补全缺失的id
    for question_id in all_sub_sampled_ids:
        if question_id in db_map:
            db_name = db_map[question_id]
            if question_id in input_sql_map:
                # 输入文件中存在该id
                if preserve_json_path and question_id in preserve_data:
                    # 如果提供了preserve_json_path且该id在preserve文件中存在，保留preserve文件中的内容
                    result[question_id] = preserve_data[question_id]
                    print(f"提示: question_id {question_id} 在输入文件中存在，已保留preserve文件中的内容")
                else:
                    # 否则使用输入的SQL
                    sql = input_sql_map[question_id]
                    result[question_id] = f"{sql}\t----- bird -----\t{db_name}"
            else:
                # 输入文件中不存在该id，置空（即使11_17.json中有内容也要置空）
                result[question_id] = f"\t----- bird -----\t{db_name}"
                if preserve_json_path and question_id in preserve_data:
                    print(f"提示: question_id {question_id} 在输入文件中不存在，已置空（原11_17.json中有内容但已清空）")
                else:
                    print(f"提示: question_id {question_id} 在输入文件中不存在，已使用空SQL补全")
        else:
            print(f"警告: 找不到question_id为 {question_id} 的数据库映射。")

    # 根据question_id（将其转换为整数进行数值排序）对结果字典进行排序
    try:
        sorted_result = dict(sorted(result.items(), key=lambda item: int(item[0])))
    except ValueError as e:
        print(f"警告: 无法将部分 question_id 转换为整数进行排序。请确保 question_id 为数字字符串。错误: {e}")
        sorted_result = result  # 如果转换失败，则不排序

    # 保存为新的JSON文件
    try:
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_result, f, indent=4, ensure_ascii=False)
        print(f"处理完成! 已生成结果文件: {output_json_path}")
        print(f"成功处理的SQL语句数量: {len(sorted_result)}")
    except IOError as e:
        print(f"错误: 无法写入输出文件 '{output_json_path}'。错误信息: {e}")

def find_missing_ids(input_json_path, sub_sampled_path):
    """
    查找在sub_sampled文件中存在但在input文件中不存在的ID

    Args:
        input_json_path: 输入的JSON文件路径
        sub_sampled_path: sub_sampled_bird_dev_set.json文件路径
    """
    print(f"\n开始检查缺失的ID...")
    
    try:
        # 读取输入的JSON文件
        with open(input_json_path, 'r', encoding='utf-8') as f:
            input_data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 输入文件 '{input_json_path}' 未找到。")
        return
    except json.JSONDecodeError:
        print(f"错误: 输入文件 '{input_json_path}' 不是有效的JSON格式。")
        return

    try:
        # 读取sub_sampled文件
        with open(sub_sampled_path, 'r', encoding='utf-8') as f:
            sub_sampled_data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 参考文件 '{sub_sampled_path}' 未找到。")
        return
    except json.JSONDecodeError:
        print(f"错误: 参考文件 '{sub_sampled_path}' 不是有效的JSON格式。")
        return

    # 获取所有ID
    if isinstance(input_data, dict):
        input_ids = set(input_data.keys())
    elif isinstance(input_data, list):
        input_ids = {str(item.get('question_id')) for item in input_data if 'question_id' in item}
    else:
        print(f"错误: 输入文件格式不正确，既不是字典也不是列表。")
        return

    sub_sampled_ids = {str(item['question_id']) for item in sub_sampled_data if 'question_id' in item}

    # 找出缺失的ID
    missing_ids = sub_sampled_ids - input_ids

    # 输出结果
    print(f"输入文件中的ID数量: {len(input_ids)}")
    print(f"参考文件中的ID数量: {len(sub_sampled_ids)}")
    
    if missing_ids:
        print(f"\n在sub_sampled文件中存在但在input文件中缺失的ID数量: {len(missing_ids)}")
        print("缺失的ID列表:")
        for id in sorted(missing_ids, key=lambda x: int(x)):
            print(f"ID: {id}")
    else:
        print("\n没有发现缺失的ID。")

    return missing_ids

# 文件路径


# 执行提取和转换
if __name__ == "__main__":
    # 首先检查缺失的ID
    input_json = "/home/shenshuyu/SQL_tool_multiAgent/Alpha-SQL-2.2.4/results/dev_pred_sqls_simple.json"
    sub_sampled_json = "/home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json"
    output_json = "/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/dev_pred_sqls_simple-rf.json"
    preserve_json = "/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/11_17.json"
    new_11_17_json = "/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/11_17_new.json"
    find_missing_ids(input_json, sub_sampled_json)
    
    # 然后执行原有的转换操作，传入preserve_json路径，输出到新的11_17.json
    extract_and_convert_sql(input_json, sub_sampled_json, new_11_17_json, preserve_json_path=preserve_json)