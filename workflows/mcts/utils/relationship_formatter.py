#!/usr/bin/env python3
"""
关系信息格式化工具

将表关系信息格式化为LLM友好的提示文本
"""

from typing import List, Dict, Set, Optional


def format_relationships_for_prompt(relationships: List[Dict]) -> str:
    """
    将关系信息格式化为提示文本
    
    Args:
        relationships: 关系列表，每个元素包含:
            {
                "table1": "schools",
                "col1": "CDSCode",
                "table2": "frpm",
                "col2": "CDSCode",
                "relationship_type": "1:1",
                "description": "..."
            }
    
    Returns:
        格式化的提示文本
    """
    if not relationships:
        return ""
    
    # 按关系类型分组
    by_type = {
        "1:1": [],
        "1:N": [],
        "N:1": [],
        "M:N": [],
        "unknown": []
    }
    
    for rel in relationships:
        rel_type = rel.get("relationship_type", "unknown")
        if rel_type in by_type:
            by_type[rel_type].append(rel)
        else:
            by_type["unknown"].append(rel)
    
    lines = ["**Important Table Relationships**:", ""]
    
    # 1:1 关系（最重要，优先显示）
    if by_type["1:1"]:
        lines.append("**1:1 Relationships (Vertical Partitioning - Must Join)**:")
        for rel in by_type["1:1"]:
            lines.append(
                f"- `{rel['table1']}`.`{rel['col1']}` <-> "
                f"`{rel['table2']}`.`{rel['col2']}` (1:1): "
                f"{rel.get('description', '')}"
            )
        lines.append("")
    
    # 1:N 关系
    if by_type["1:N"]:
        lines.append("**1:N Relationships (Watch for Row Explosion)**:")
        for rel in by_type["1:N"]:
            lines.append(
                f"- `{rel['table1']}`.`{rel['col1']}` (1) -> "
                f"`{rel['table2']}`.`{rel['col2']}` (N): "
                f"{rel.get('description', '')}"
            )
        lines.append("")
    
    # N:1 关系
    if by_type["N:1"]:
        lines.append("**N:1 Relationships (Many-to-One)**:")
        for rel in by_type["N:1"]:
            lines.append(
                f"- `{rel['table1']}`.`{rel['col1']}` (N) -> "
                f"`{rel['table2']}`.`{rel['col2']}` (1): "
                f"{rel.get('description', '')}"
            )
        lines.append("")
    
    # M:N 关系
    if by_type["M:N"]:
        lines.append("**M:N Relationships (Many-to-Many - Use Aggregation)**:")
        for rel in by_type["M:N"]:
            lines.append(
                f"- `{rel['table1']}`.`{rel['col1']}` <-> "
                f"`{rel['table2']}`.`{rel['col2']}` (M:N): "
                f"{rel.get('description', '')}"
            )
        lines.append("")
    
    return "\n".join(lines)


def get_relationship_hints_for_tables(tables: List[str],
                                     relationships: List[Dict]) -> str:
    """
    获取特定表的关系提示
    
    Args:
        tables: 表名列表
        relationships: 所有关系列表
    
    Returns:
        涉及指定表的关系提示文本
    """
    table_set = set(tables)
    relevant_rels = []
    
    for rel in relationships:
        if rel['table1'] in table_set or rel['table2'] in table_set:
            relevant_rels.append(rel)
    
    if not relevant_rels:
        return ""
    
    return format_relationships_for_prompt(relevant_rels)


def get_join_suggestions_for_question(question: str,
                                     relationships: List[Dict],
                                     schema_tables: List[str]) -> str:
    """
    基于问题和关系信息，生成JOIN建议
    
    Args:
        question: 自然语言问题
        relationships: 关系列表
        schema_tables: Schema中的表列表
    
    Returns:
        JOIN建议文本
    """
    # 简单的关键词匹配（可以后续改进为更智能的匹配）
    question_lower = question.lower()
    
    suggestions = []
    
    # 检查1:1关系（最重要）
    for rel in relationships:
        if rel.get("relationship_type") == "1:1":
            table1 = rel['table1']
            table2 = rel['table2']
            
            # 如果问题中提到其中一个表
            if table1.lower() in question_lower or table2.lower() in question_lower:
                suggestions.append(
                    f"⚠️ **Important**: `{table1}` and `{table2}` have a 1:1 relationship. "
                    f"You MUST join them to access complete information. "
                    f"Join condition: `{table1}`.`{rel['col1']}` = `{table2}`.`{rel['col2']}`"
                )
    
    if suggestions:
        return "\n".join(suggestions)
    
    return ""


def format_relationships_summary(relationships: List[Dict]) -> str:
    """
    生成关系摘要（用于调试和日志）
    
    Args:
        relationships: 关系列表
    
    Returns:
        摘要文本
    """
    if not relationships:
        return "No relationships found."
    
    by_type = {}
    for rel in relationships:
        rel_type = rel.get("relationship_type", "unknown")
        by_type[rel_type] = by_type.get(rel_type, 0) + 1
    
    summary_lines = [f"Total relationships: {len(relationships)}"]
    for rel_type, count in sorted(by_type.items()):
        summary_lines.append(f"  {rel_type}: {count}")
    
    return "\n".join(summary_lines)


def extract_tables_from_relationships(relationships: List[Dict]) -> Set[str]:
    """
    从关系信息中提取所有表名
    
    Args:
        relationships: 关系列表
    
    Returns:
        表名集合
    """
    tables = set()
    for rel in relationships:
        tables.add(rel['table1'])
        tables.add(rel['table2'])
    return tables


if __name__ == "__main__":
    # 测试代码
    test_relationships = [
        {
            "table1": "schools",
            "col1": "CDSCode",
            "table2": "frpm",
            "col2": "CDSCode",
            "relationship_type": "1:1",
            "description": "These tables are vertically partitioned."
        },
        {
            "table1": "schools",
            "col1": "CDSCode",
            "table2": "satscores",
            "col2": "cds",
            "relationship_type": "1:N",
            "description": "One school has multiple SAT score records."
        }
    ]
    
    print("="*80)
    print("关系格式化测试")
    print("="*80)
    print("\n完整格式化:")
    print(format_relationships_for_prompt(test_relationships))
    print("\n摘要:")
    print(format_relationships_summary(test_relationships))

