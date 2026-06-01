"""
批量处理 relationships.json，只保留 foreign_key 中明确提到的关系

对于每个数据库，从样本数据中提取 foreign_key 信息，然后过滤 relationships.json，
只保留那些在 foreign_key 中明确提到的关系。
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple


def parse_foreign_key_from_schema(schema_info: str) -> Set[Tuple[str, str, str, str]]:
    """
    从 schema_info 中解析 foreign_key 信息
    
    Args:
        schema_info: 包含 foreign_key 的 schema 信息字符串
        
    Returns:
        Set of tuples: (table1, col1, table2, col2)
    """
    foreign_keys = set()
    
    if 'foreign_key:' not in schema_info:
        return foreign_keys
    
    # 提取 foreign_key 部分
    fk_section = schema_info.split('foreign_key:')[1]
    
    # 解析每一行：格式如 "# table1(col1) references table2(col2)"
    pattern = r'#\s*(\w+)\(([^)]+)\)\s+references\s+(\w+)\(([^)]+)\)'
    matches = re.findall(pattern, fk_section, re.IGNORECASE)
    
    for match in matches:
        table1, col1, table2, col2 = match
        # 规范化：统一大小写，去除空格
        table1 = table1.strip().lower()
        col1 = col1.strip().lower()
        table2 = table2.strip().lower()
        col2 = col2.strip().lower()
        
        # 添加两个方向的关系（因为关系是双向的）
        foreign_keys.add((table1, col1, table2, col2))
        foreign_keys.add((table2, col2, table1, col1))  # 反向关系
    
    return foreign_keys


def normalize_relationship_key(table1: str, col1: str, table2: str, col2: str) -> Tuple[str, str, str, str]:
    """规范化关系键，统一大小写"""
    return (
        table1.strip().lower(),
        col1.strip().lower(),
        table2.strip().lower(),
        col2.strip().lower()
    )


def filter_relationships_by_foreign_keys(
    relationships_data: Dict,
    ppl_file: str,
    output_file: str = None
) -> Dict:
    """
    根据 foreign_key 信息过滤 relationships.json
    
    Args:
        relationships_data: 原始的 relationships.json 数据
        ppl_file: 包含样本数据的文件路径（用于提取 foreign_key）
        output_file: 输出文件路径（如果为 None，则覆盖原文件）
        
    Returns:
        过滤后的 relationships 数据
    """
    # 加载样本数据
    with open(ppl_file, 'r', encoding='utf-8') as f:
        samples = json.load(f)
    
    # 为每个数据库收集 foreign_key 信息
    db_foreign_keys = {}  # {db_name: set of (table1, col1, table2, col2)}
    
    for sample in samples:
        db_name = sample.get('db', '')
        if not db_name:
            continue
        
        # 优先从 foreign_key 字段获取，如果没有则从 simplified_ddl 中解析
        foreign_key_str = sample.get('foreign_key', '')
        schema_info = sample.get('simplified_ddl', '')
        
        # 合并 foreign_key 和 schema_info
        combined_schema = f"{schema_info}\nforeign_key:{foreign_key_str}" if foreign_key_str else schema_info
        
        if not combined_schema:
            continue
        
        # 解析 foreign_key
        fks = parse_foreign_key_from_schema(combined_schema)
        if db_name not in db_foreign_keys:
            db_foreign_keys[db_name] = set()
        db_foreign_keys[db_name].update(fks)
    
    # 过滤每个数据库的关系
    filtered_data = {}
    
    for db_name, db_relationships in relationships_data.items():
        if db_name == 'metadata':
            continue
        
        if db_name not in db_foreign_keys or len(db_foreign_keys[db_name]) == 0:
            # 如果没有 foreign_key 信息，清空所有关系
            original_count = len(db_relationships.get('relationships', []))
            print(f"⚠️ {db_name}: 未找到 foreign_key 信息，清空所有关系 ({original_count} -> 0)")
            filtered_data[db_name] = {
                "relationships": [],
                "metadata": {
                    **db_relationships.get('metadata', {}),
                    "total_relationships": 0
                }
            }
            continue
        
        # 获取该数据库的 foreign_key 集合
        fk_set = db_foreign_keys[db_name]
        
        # 过滤关系
        filtered_relationships = []
        for rel in db_relationships.get('relationships', []):
            table1 = rel.get('table1', '')
            col1 = rel.get('col1', '')
            table2 = rel.get('table2', '')
            col2 = rel.get('col2', '')
            
            # 规范化关系键
            rel_key = normalize_relationship_key(table1, col1, table2, col2)
            
            # 检查是否在 foreign_key 中
            if rel_key in fk_set:
                filtered_relationships.append(rel)
        
        # 更新数据
        original_count = len(db_relationships.get('relationships', []))
        filtered_count = len(filtered_relationships)
        
        print(f"✅ {db_name}: {original_count} -> {filtered_count} 个关系")
        
        filtered_data[db_name] = {
            "relationships": filtered_relationships,
            "metadata": {
                **db_relationships.get('metadata', {}),
                "total_relationships": filtered_count
            }
        }
    
    # 保存结果
    if output_file is None:
        output_file = Path(__file__).parent.parent / "data" / "relationships.json"
    
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(filtered_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 已保存到: {output_path}")
    
    return filtered_data


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="根据 foreign_key 过滤 relationships.json")
    parser.add_argument("--ppl_file", type=str, required=True, 
                       help="样本数据文件路径（用于提取 foreign_key）")
    parser.add_argument("--relationships_file", type=str, 
                       default="workflows/mcts_v1/data/relationships.json",
                       help="relationships.json 文件路径")
    parser.add_argument("--output_file", type=str, default=None,
                       help="输出文件路径（默认覆盖原文件）")
    
    args = parser.parse_args()
    
    # 加载 relationships.json
    print(f"📂 加载 relationships.json: {args.relationships_file}")
    with open(args.relationships_file, 'r', encoding='utf-8') as f:
        relationships_data = json.load(f)
    
    print(f"   找到 {len([k for k in relationships_data.keys() if k != 'metadata'])} 个数据库\n")
    
    # 过滤关系
    filtered_data = filter_relationships_by_foreign_keys(
        relationships_data,
        args.ppl_file,
        args.output_file
    )
    
    # 统计
    total_original = sum(len(v.get('relationships', [])) for k, v in relationships_data.items() if k != 'metadata')
    total_filtered = sum(len(v.get('relationships', [])) for k, v in filtered_data.items() if k != 'metadata')
    
    print(f"\n{'='*60}")
    print(f"📊 统计:")
    print(f"   原始关系总数: {total_original}")
    print(f"   过滤后关系总数: {total_filtered}")
    print(f"   保留比例: {total_filtered/total_original*100:.1f}%")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
