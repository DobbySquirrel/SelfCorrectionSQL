#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为train.json文件添加question_id字段
从1534开始递增
"""

import json
import os
from pathlib import Path

def add_question_id_to_train():
    """
    为train.json文件中的每个记录添加question_id字段
    question_id从1534开始递增
    """
    
    # 文件路径
    train_file_path = "work/bird/train/train.json"
    
    # 检查文件是否存在
    if not os.path.exists(train_file_path):
        print(f"错误：文件 {train_file_path} 不存在")
        return
    
    try:
        # 读取原始train.json文件
        print(f"正在读取文件: {train_file_path}")
        with open(train_file_path, 'r', encoding='utf-8') as f:
            train_data = json.load(f)
        
        print(f"原始数据包含 {len(train_data)} 条记录")
        
        # 为每个记录添加question_id字段
        start_id = 1534
        for i, record in enumerate(train_data):
            record['question_id'] = start_id + i
        
        print(f"已为 {len(train_data)} 条记录添加question_id，范围: {start_id} - {start_id + len(train_data) - 1}")
        
        # 保存修改后的数据到原文件
        print("正在保存修改后的数据...")
        with open(train_file_path, 'w', encoding='utf-8') as f:
            json.dump(train_data, f, ensure_ascii=False, indent=4)
        
        print(f"成功保存到: {train_file_path}")
        
        # 显示前几条记录作为验证
        print("\n前3条记录示例:")
        for i in range(min(3, len(train_data))):
            print(f"记录 {i+1}: question_id = {train_data[i]['question_id']}")
            print(f"  db_id: {train_data[i]['db_id']}")
            print(f"  question: {train_data[i]['question'][:50]}...")
            print()
            
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
    except Exception as e:
        print(f"处理过程中发生错误: {e}")

if __name__ == "__main__":
    add_question_id_to_train()