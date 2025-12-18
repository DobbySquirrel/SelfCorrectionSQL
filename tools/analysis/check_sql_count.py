import json
import os
import random

def check_sql_counts(file_path):
    # 读取JSON文件
    with open(file_path, 'r') as f:
        data = json.load(f)
    
    # 统计不满足16个SQL的问题
    issues = []
    fixed_data = []
    
    for item in data:
        sql_count = len(item['pred_sqls'])
        if sql_count != 16:
            if sql_count > 16:
                # 随机选择16个SQL
                selected_sqls = random.sample(item['pred_sqls'], 16)
                fixed_item = item.copy()
                fixed_item['pred_sqls'] = selected_sqls
                fixed_data.append(fixed_item)
            else:
                issues.append({
                    'id': item['id'],
                    'db_id': item['db_id'],
                    'sql_count': sql_count
                })
                fixed_data.append(item)
        else:
            fixed_data.append(item)
    
    # 打印统计信息
    print(f"总问题数: {len(data)}")
    print(f"不满足16个SQL的问题数: {len(issues)}")
    print("\n不满足16个SQL的问题详情:")
    for issue in issues:
        print(f"ID: {issue['id']}, DB: {issue['db_id']}, SQL数量: {issue['sql_count']}")
    
    # 保存不满足16个SQL的问题ID到JSON文件
    output_dir = os.path.dirname(file_path)
    output_file = os.path.join(output_dir, "issues_less_than_16.json")
    
    # 只保存ID列表
    issue_ids = [issue['id'] for issue in issues]
    with open(output_file, 'w') as f:
        json.dump(issue_ids, f, indent=2)
    
    # 保存修复后的数据
    fixed_output_file = os.path.join(output_dir, "fixed_" + os.path.basename(file_path))
    with open(fixed_output_file, 'w') as f:
        json.dump(fixed_data, f, indent=2)
    
    print(f"\n已将不满足16个SQL的问题ID保存到: {output_file}")
    print(f"已将修复后的数据保存到: {fixed_output_file}")
    
    return issues

if __name__ == "__main__":
    # 检查所有生成的文件
    output_dir = "/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_16"

    # 检查合并后的结果文件
    print("\n检查合并后的结果文件:")
    check_sql_counts("/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_16_all/generation_1.json") 