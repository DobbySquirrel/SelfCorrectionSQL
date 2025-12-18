import json

def sort_json_keys(input_file_path, output_file_path):
    """
    读取JSON文件，按其顶级键（假定为数字字符串）排序后写入新文件。

    Args:
        input_file_path (str): 输入JSON文件的路径。
        output_file_path (str): 输出排序后JSON文件的新路径。
    """
    print(f"开始处理文件: {input_file_path}")
    print(f"排序后的结果将保存到: {output_file_path}")

    try:
        with open(input_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 输入文件 '{input_file_path}' 未找到。请检查路径是否正确。")
        return
    except json.JSONDecodeError:
        print(f"错误: 文件 '{input_file_path}' 不是有效的JSON格式。")
        return
    except Exception as e:
        print(f"读取文件时发生未知错误: {e}")
        return

    if not isinstance(data, dict):
        print(f"警告: JSON文件的顶级结构不是字典，无法进行键排序。")
        # 如果不是字典，直接复制内容到新文件
        try:
            with open(output_file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            print("非字典结构已复制到新文件，未进行排序。")
        except IOError as e:
            print(f"错误: 无法写入输出文件 '{output_file_path}'。错误信息: {e}")
        return

    try:
        # 对字典的键进行排序。假定键是表示数字的字符串，所以转换为int进行数值排序。
        # 如果键是纯字符串，可以移除 int() 转换。
        sorted_keys = sorted(data.keys(), key=lambda k: int(k))
        sorted_data = {key: data[key] for key in sorted_keys}

    except ValueError:
        print("警告: 部分键无法转换为整数进行排序。将按照字符串进行字典序排序。")
        sorted_data = dict(sorted(data.items())) # 回退到默认的字符串排序
    except Exception as e:
        print(f"排序过程中发生错误: {e}")
        sorted_data = data # 保持原始顺序

    try:
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_data, f, indent=4, ensure_ascii=False)
        print(f"文件处理完成，排序结果已保存到: {output_file_path}")
    except IOError as e:
        print(f"错误: 无法写入输出文件 '{output_file_path}'。错误信息: {e}")

# 定义输入和输出文件路径
input_file = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/extracted_sql_results_6_2_validated_2.json"
output_file = "/home/shenshuyu/SQL_tool/evaluation/predict_answer/extracted_sql_results_6_2_validated_2_sorted.json"
# 执行排序
sort_json_keys(input_file, output_file)