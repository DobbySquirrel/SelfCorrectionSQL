"""
将您的数据格式转换为 Alpha-SQL 需要的格式

使用方法:
    python convert_data_format.py --input_file data/dev.json --output_file data/dev_alpha_sql.json --db_path_prefix /ssd/shenshuyu/work/bird/dev_20240627/dev_databases
"""

import json
import argparse
import os
from pathlib import Path


def convert_to_alpha_sql_format(input_file: str, output_file: str, db_path_prefix: str = None):
    """
    将数据格式转换为 Alpha-SQL 需要的格式
    
    Args:
        input_file: 输入 JSON 文件路径
        output_file: 输出 JSON 文件路径
        db_path_prefix: 数据库路径前缀（如果 db_id 需要映射到实际路径）
    """
    # 处理相对路径和绝对路径
    input_path = Path(input_file)
    if not input_path.is_absolute():
        # 如果是相对路径，尝试从项目根目录查找
        script_dir = Path(__file__).parent.parent  # Alpha-SQL-2.2.4 的父目录
        input_path = script_dir / input_file
        if not input_path.exists():
            # 如果还是不存在，尝试从当前工作目录
            input_path = Path(input_file).resolve()
    
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_file} (尝试的路径: {input_path})")
    
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    converted_data = []
    
    for item in data:
        # 提取字段
        question_id = item.get('question_id', None)
        db_id = item.get('db_id') or item.get('db', None)
        question = item.get('question', '')
        evidence = item.get('evidence', '') or item.get('combine_evidence', '') or ''
        sql = item.get('SQL', '') or item.get('sql', '')
        difficulty = item.get('difficulty', None)
        
        # 验证必需字段
        if db_id is None:
            print(f"警告: 跳过 question_id={question_id}，缺少 db_id")
            continue
        
        if not question:
            print(f"警告: 跳过 question_id={question_id}，缺少 question")
            continue
        
        # 构建转换后的数据项
        converted_item = {
            'question_id': question_id if question_id is not None else len(converted_data),
            'db_id': db_id,
            'question': question,
            'evidence': evidence,
        }
        
        if sql:
            converted_item['SQL'] = sql
        
        if difficulty:
            converted_item['difficulty'] = difficulty
        
        converted_data.append(converted_item)
    
    # 保存转换后的数据
    output_path = Path(output_file)
    if not output_path.is_absolute():
        # 如果是相对路径，尝试从脚本所在目录解析
        script_dir = Path(__file__).parent
        output_path = script_dir / output_file
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(converted_data, f, ensure_ascii=False, indent=2)
    
    print(f"转换完成: {len(converted_data)} 条记录")
    print(f"输出文件: {output_path}")
    
    # 检查数据库路径
    if db_path_prefix:
        db_ids = set(item['db_id'] for item in converted_data)
        missing_dbs = []
        for db_id in db_ids:
            db_path = Path(db_path_prefix) / db_id / f"{db_id}.sqlite"
            if not db_path.exists():
                missing_dbs.append(db_id)
        
        if missing_dbs:
            print(f"\n警告: 以下数据库文件不存在:")
            for db_id in list(missing_dbs)[:10]:  # 只显示前10个
                print(f"  - {db_id}")
            if len(missing_dbs) > 10:
                print(f"  ... 还有 {len(missing_dbs) - 10} 个")
        else:
            print(f"\n✓ 所有数据库文件都存在")


def main():
    parser = argparse.ArgumentParser(description="转换数据格式为 Alpha-SQL 格式")
    parser.add_argument("--input_file", type=str, required=True, help="输入 JSON 文件路径")
    parser.add_argument("--output_file", type=str, required=True, help="输出 JSON 文件路径")
    parser.add_argument("--db_path_prefix", type=str, default=None, help="数据库路径前缀（用于验证）")
    args = parser.parse_args()
    
    convert_to_alpha_sql_format(args.input_file, args.output_file, args.db_path_prefix)


if __name__ == "__main__":
    main()

