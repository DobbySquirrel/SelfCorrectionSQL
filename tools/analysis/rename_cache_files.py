import os
import json
from datetime import datetime

def load_ordered_question_ids():
    """加载原始数据集获取已解决问题的question_ids"""
    dataset_path = "/home/shenshuyu/SQL_tool/Output/6_8/test_results_straightforward_ongoing_initial.json"
    try:
        with open(dataset_path, 'r', encoding='utf-8') as f:
            dataset = json.load(f)
        # 只获取已解决问题的question_ids
        question_ids = [str(item['question_id']) for item in dataset]
        return question_ids
    except Exception as e:
        print(f"加载数据集时出错: {str(e)}")
        return None

def rename_cache_files():
    # 加载数据集中的question_ids
    question_ids = load_ordered_question_ids()
    if not question_ids:
        print("无法加载数据集，退出程序")
        return
    
    print(f"数据集中的前10个question_ids: {question_ids[:10]}")

    # 源目录和目标目录
    source_cache_dir = "/home/shenshuyu/SQL_tool/generated_code_cache"
    target_cache_dir = "/home/shenshuyu/SQL_tool/generated_code_cache_test"

    # 确保目录存在
    if not os.path.exists(source_cache_dir):
        print("源缓存目录不存在")
        return
    if not os.path.exists(target_cache_dir):
        print("目标缓存目录不存在")
        return

    # 获取源目录中的所有文件及其访问时间
    source_files = os.listdir(source_cache_dir)
    source_file_info = {}
    for file_name in source_files:
        if file_name.startswith("code_gen_result_") and file_name.endswith(".json"):
            file_path = os.path.join(source_cache_dir, file_name)
            atime = os.path.getatime(file_path)  # 获取访问时间
            source_file_info[file_name] = atime

    # 获取目标目录中的所有文件，并更新时间戳
    target_files_info = []
    for file_name in os.listdir(target_cache_dir):
        if file_name.startswith("code_gen_result_") and file_name.endswith(".json"):
            target_file_path = os.path.join(target_cache_dir, file_name)
            if file_name in source_file_info:
                source_atime = source_file_info[file_name]
                try:
                    os.utime(target_file_path, (source_atime, source_atime))
                    print(f"更新文件时间: {file_name} -> {datetime.fromtimestamp(source_atime).strftime('%Y-%m-%d %H:%M:%S')}")
                    target_files_info.append({
                        'file_name': file_name,
                        'file_path': target_file_path,
                        'time': source_atime
                    })
                except Exception as e:
                    print(f"更新文件时间时出错 {file_name}: {str(e)}")
            else:
                print(f"在源目录中未找到对应文件: {file_name}")

    # 按时间排序文件
    target_files_info.sort(key=lambda x: x['time'])

    # 重命名文件
    for i, file_info in enumerate(target_files_info):
        if i >= len(question_ids):
            print("警告：文件数量超过question_ids数量")
            break

        question_id = question_ids[i]
        new_name = f"code_gen_result_{question_id}.json"
        new_path = os.path.join(target_cache_dir, new_name)
        
        try:
            os.rename(file_info['file_path'], new_path)
            print(f"重命名文件: {file_info['file_name']} -> {new_name}")
        except Exception as e:
            print(f"重命名文件时出错 {file_info['file_name']}: {str(e)}")

if __name__ == "__main__":
    print("开始更新缓存文件时间并重命名...")
    rename_cache_files()
    print("更新和重命名完成！")