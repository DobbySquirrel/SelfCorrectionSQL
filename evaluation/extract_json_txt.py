import json
import re

def extract_sql_to_txt(json_file_path, output_txt_path):
    """
    从JSON文件中提取SQL语句并保存到TXT文件，每行一条SQL
    
    Args:
        json_file_path: JSON文件路径
        output_txt_path: 输出的TXT文件路径
    """
    # 读取JSON文件
    with open(json_file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    # 从JSON文件中提取SQL语句
    sql_statements = []
    for item in json_data:
        # 获取sql字段
        sql = item.get('sql', '')
        # 确保sql是字符串类型
        if sql is None:
            sql = ''
        elif not isinstance(sql, str):
            sql = str(sql)
        
        # 规范化SQL：移除多余的空格和换行，使其成为单行
        sql = re.sub(r'\s+', ' ', sql).strip()
        
        sql_statements.append(sql)
    
    # 打印基本信息
    print(f"从JSON文件中提取的SQL数量: {len(sql_statements)}")
    
    # 将SQL语句写入TXT文件，每行一条SQL
    with open(output_txt_path, 'w', encoding='utf-8') as f:
        for sql in sql_statements:
            f.write(sql + '\n')
    
    print(f"SQL语句已保存到: {output_txt_path}")

# 文件路径
json_file = "/home/shenshuyu/SQL_tool/Output/5_16/updated_full_results.json"
output_txt = "/home/shenshuyu/SQL_tool/Output/5_16/extracted_sql_results.txt"

# 执行提取
extract_sql_to_txt(json_file, output_txt)