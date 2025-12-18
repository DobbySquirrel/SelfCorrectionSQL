import json

def convert_pred_sqls_to_rf(pred_sqls_path, reference_json_path, sub_sampled_path, output_path):
    """
    将pred_sqls.json转换为rf格式，按照reference_json的key排序并补全缺失的key
    
    Args:
        pred_sqls_path: pred_sqls.json文件路径（只包含SQL，没有格式）
        reference_json_path: 11_17_new.json文件路径（作为参考，获取所有key和格式）
        sub_sampled_path: sub_sampled_bird_dev_set.json文件路径（获取db_id映射）
        output_path: 输出文件路径
    """
    print(f"开始处理...")
    print(f"输入文件: {pred_sqls_path}")
    print(f"参考文件: {reference_json_path}")
    print(f"sub_sampled文件: {sub_sampled_path}")
    print(f"输出文件: {output_path}")
    
    # 读取pred_sqls.json
    try:
        with open(pred_sqls_path, 'r', encoding='utf-8') as f:
            pred_sqls_data = json.load(f)
        print(f"✓ 读取pred_sqls.json，包含 {len(pred_sqls_data)} 条记录")
    except FileNotFoundError:
        print(f"错误: 输入文件 '{pred_sqls_path}' 未找到。")
        return
    except json.JSONDecodeError:
        print(f"错误: 输入文件 '{pred_sqls_path}' 不是有效的JSON格式。")
        return
    
    # 读取参考文件（11_17_new.json）
    try:
        with open(reference_json_path, 'r', encoding='utf-8') as f:
            reference_data = json.load(f)
        print(f"✓ 读取参考文件，包含 {len(reference_data)} 条记录")
    except FileNotFoundError:
        print(f"错误: 参考文件 '{reference_json_path}' 未找到。")
        return
    except json.JSONDecodeError:
        print(f"错误: 参考文件 '{reference_json_path}' 不是有效的JSON格式。")
        return
    
    # 读取sub_sampled文件获取db_id映射
    try:
        with open(sub_sampled_path, 'r', encoding='utf-8') as f:
            sub_sampled_data = json.load(f)
        print(f"✓ 读取sub_sampled文件，包含 {len(sub_sampled_data)} 条记录")
    except FileNotFoundError:
        print(f"错误: sub_sampled文件 '{sub_sampled_path}' 未找到。")
        return
    except json.JSONDecodeError:
        print(f"错误: sub_sampled文件 '{sub_sampled_path}' 不是有效的JSON格式。")
        return
    
    # 创建question_id到db_id的映射
    db_map = {str(item['question_id']): item['db_id'] for item in sub_sampled_data if 'question_id' in item and 'db_id' in item}
    
    # 创建结果字典
    result = {}
    
    # 获取参考文件中的所有key，并按数字排序
    reference_keys = list(reference_data.keys())
    try:
        reference_keys_sorted = sorted(reference_keys, key=lambda x: int(x))
    except ValueError:
        reference_keys_sorted = reference_keys
        print("警告: 部分question_id无法转换为整数，将按原始顺序处理")
    
    # 处理每个key
    for question_id in reference_keys_sorted:
        db_name = db_map.get(question_id, "")
        
        if question_id in pred_sqls_data:
            # 如果pred_sqls中有该id，使用pred_sqls中的SQL，转换为rf格式
            sql = pred_sqls_data[question_id]
            result[question_id] = f"{sql}\t----- bird -----\t{db_name}"
            print(f"✓ question_id {question_id}: 使用pred_sqls中的SQL")
        else:
            # 如果pred_sqls中没有该id，使用空SQL补全
            result[question_id] = f"\t----- bird -----\t{db_name}"
            print(f"⚠ question_id {question_id}: 在pred_sqls中不存在，已使用空SQL补全")
    
    # 按question_id（转换为整数）进行数值排序
    try:
        sorted_result = dict(sorted(result.items(), key=lambda item: int(item[0])))
    except ValueError as e:
        print(f"警告: 无法将部分 question_id 转换为整数进行排序。错误: {e}")
        sorted_result = result
    
    # 保存结果
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_result, f, indent=4, ensure_ascii=False)
        print(f"\n✓ 处理完成! 已生成结果文件: {output_path}")
        print(f"✓ 成功处理的记录数量: {len(sorted_result)}")
        print(f"  - 来自pred_sqls的记录: {len([k for k in sorted_result.keys() if k in pred_sqls_data])}")
        print(f"  - 补全的空记录: {len([k for k in sorted_result.keys() if k not in pred_sqls_data])}")
    except IOError as e:
        print(f"错误: 无法写入输出文件 '{output_path}'。错误信息: {e}")

if __name__ == "__main__":
    pred_sqls_path = "/home/shenshuyu/SQL_tool_multiAgent/data/ppl_dev.json"
    reference_json_path = "/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/11_17_new.json"
    sub_sampled_path = "/home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set.json"
    output_path = "/home/shenshuyu/SQL_tool_multiAgent/Alpha-SQL-2.2.4/results/pred_sqls_rf.json"
    
    convert_pred_sqls_to_rf(pred_sqls_path, reference_json_path, sub_sampled_path, output_path)

