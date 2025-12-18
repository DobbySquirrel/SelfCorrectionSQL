"""
Foreign Key 筛选工具

参考 RSL-SQL 的做法：
1. 从完整的 dev_tables.json 中加载所有 foreign_key 信息
2. 根据候选表集合筛选 foreign_key（只保留两端表都在候选表集中的）
3. 生成符合格式的 foreign_key 文本块
"""

import json
from typing import Set, List, Tuple, Optional, Dict


def load_foreign_keys_from_dev_tables(dev_tables_file: str, db_id: str) -> List[Tuple[str, str, str, str]]:
    """
    从 dev_tables.json 中加载指定数据库的所有 foreign_key 信息
    
    Args:
        dev_tables_file: dev_tables.json 文件路径
        db_id: 数据库ID
        
    Returns:
        foreign_key 列表，每个元素为 (src_table, src_col, tgt_table, tgt_col)
        如果找不到数据库或没有 foreign_key，返回空列表
    """
    try:
        with open(dev_tables_file, 'r', encoding='utf-8') as f:
            dev_tables_data = json.load(f)
        
        # 查找对应的数据库
        for db_info in dev_tables_data:
            if db_info.get('db_id') == db_id:
                table_names_original = db_info.get('table_names_original', [])
                column_names_original = db_info.get('column_names_original', [])
                foreign_keys = db_info.get('foreign_keys', [])
                
                if not foreign_keys:
                    return []
                
                # 构建索引到表名和列名的映射
                # column_names_original 格式: [[table_idx, col_name], ...]
                # table_idx = -1 表示 "*"（所有列）
                col_idx_to_info = {}
                for col_idx, col_info in enumerate(column_names_original):
                    if isinstance(col_info, list) and len(col_info) >= 2:
                        table_idx = col_info[0]
                        col_name = col_info[1]
                        if table_idx >= 0:  # 排除 -1（*）
                            col_idx_to_info[col_idx] = (table_idx, col_name)
                
                # 解析 foreign_keys
                # foreign_keys 格式: [[col_idx1, col_idx2], ...]
                # col_idx1 是外键列的索引，col_idx2 是被引用的列的索引
                fk_list = []
                for fk_pair in foreign_keys:
                    if isinstance(fk_pair, list) and len(fk_pair) >= 2:
                        src_col_idx = fk_pair[0]
                        tgt_col_idx = fk_pair[1]
                        
                        if src_col_idx in col_idx_to_info and tgt_col_idx in col_idx_to_info:
                            src_table_idx, src_col = col_idx_to_info[src_col_idx]
                            tgt_table_idx, tgt_col = col_idx_to_info[tgt_col_idx]
                            
                            if src_table_idx < len(table_names_original) and tgt_table_idx < len(table_names_original):
                                src_table = table_names_original[src_table_idx]
                                tgt_table = table_names_original[tgt_table_idx]
                                fk_list.append((src_table, src_col, tgt_table, tgt_col))
                
                return fk_list
        
        return []
    except Exception as e:
        print(f"[Foreign Key筛选] ⚠️ 从dev_tables.json加载foreign_key失败: {e}")
        return []


def filter_foreign_keys_by_tables(
    foreign_keys: List[Tuple[str, str, str, str]], 
    candidate_tables: Set[str]
) -> List[Tuple[str, str, str, str]]:
    """
    根据候选表集合筛选 foreign_key
    
    参考 RSL-SQL 的做法：只保留两端表都在候选表集中的 foreign_key
    
    Args:
        foreign_keys: foreign_key 列表，每个元素为 (src_table, src_col, tgt_table, tgt_col)
        candidate_tables: 候选表集合
        
    Returns:
        筛选后的 foreign_key 列表
    """
    filtered_fks = []
    for src_table, src_col, tgt_table, tgt_col in foreign_keys:
        # 只保留两端表都在候选表集中的 foreign_key
        if src_table in candidate_tables and tgt_table in candidate_tables:
            filtered_fks.append((src_table, src_col, tgt_table, tgt_col))
    
    return filtered_fks


def format_foreign_keys_as_text(foreign_keys: List[Tuple[str, str, str, str]]) -> str:
    """
    将 foreign_key 列表格式化为文本格式
    
    格式：
    foreign_key:#
    # table1(col1) references table2(col2)
    # table3(col3) references table4(col4)
    
    Args:
        foreign_keys: foreign_key 列表，每个元素为 (src_table, src_col, tgt_table, tgt_col)
        
    Returns:
        格式化后的 foreign_key 文本块
    """
    if not foreign_keys:
        return ""
    
    lines = ["foreign_key:#"]
    for src_table, src_col, tgt_table, tgt_col in foreign_keys:
        # 处理表名和列名中的特殊字符（空格、括号等），用反引号包裹
        src_table_escaped = f"`{src_table}`" if ' ' in src_table or '-' in src_table else src_table
        src_col_escaped = f"`{src_col}`" if ' ' in src_col or '-' in src_col or '(' in src_col else src_col
        tgt_table_escaped = f"`{tgt_table}`" if ' ' in tgt_table or '-' in tgt_table else tgt_table
        tgt_col_escaped = f"`{tgt_col}`" if ' ' in tgt_col or '-' in tgt_col or '(' in tgt_col else tgt_col
        
        lines.append(f"# {src_table_escaped}({src_col_escaped}) references {tgt_table_escaped}({tgt_col_escaped})")
    
    return "\n".join(lines)


def get_filtered_foreign_keys_text(
    dev_tables_file: str,
    db_id: str,
    candidate_tables: Set[str]
) -> str:
    """
    一站式函数：从 dev_tables.json 加载 foreign_key，根据候选表筛选，并格式化为文本
    
    Args:
        dev_tables_file: dev_tables.json 文件路径
        db_id: 数据库ID
        candidate_tables: 候选表集合
        
    Returns:
        格式化后的 foreign_key 文本块，如果没有则返回空字符串
    """
    # 1. 从 dev_tables.json 加载所有 foreign_key
    all_foreign_keys = load_foreign_keys_from_dev_tables(dev_tables_file, db_id)
    
    if not all_foreign_keys:
        return ""
    
    # 2. 根据候选表集合筛选
    filtered_foreign_keys = filter_foreign_keys_by_tables(all_foreign_keys, candidate_tables)
    
    # 3. 格式化为文本
    return format_foreign_keys_as_text(filtered_foreign_keys)


# 使用示例
if __name__ == "__main__":
    # 测试
    dev_tables_file = "/home/shenshuyu/SQL_tool_multiAgent/data/dev_tables.json"
    db_id = "california_schools"
    candidate_tables = {"schools", "frpm", "satscores"}
    
    fk_text = get_filtered_foreign_keys_text(dev_tables_file, db_id, candidate_tables)
    print("筛选后的 foreign_key 文本：")
    print(fk_text)
