"""
Schema过滤工具模块

提供从完整schema中提取和过滤表/列的功能，用于基于rollout的schema选择策略。

完全参考RSL-SQL的后处理逻辑：
1. 使用sqlglot解析SQL提取表和列（参考util.py的extract_tables_and_columns）
2. 从SQL中提取列：检查列名是否在SQL中出现（参考bid_schema_linking.py的extract_from_sql）
3. 从列列表中提取表名（参考bid_schema_linking.py的逻辑）
4. 根据需要的列过滤schema DDL（参考simplified_schema.py的逻辑）
"""

from typing import Dict, Set, Optional, Tuple, List
import re
import sqlglot


def extract_tables_and_columns_from_sql(sql_query: str) -> Dict[str, Set[str]]:
    """
    利用sqlglot工具，从一个sql语句中，提取出其中涉及的所有表和列
    
    完全参考RSL-SQL/src/utils/util.py的extract_tables_and_columns函数
    
    Args:
        sql_query: SQL查询语句
        
    Returns:
        {
            'table': {表名集合},
            'column': {列名集合}
        }
    """
    try:
        parsed_query = sqlglot.parse_one(sql_query, read="sqlite")
        table_names = parsed_query.find_all(sqlglot.exp.Table)
        column_names = parsed_query.find_all(sqlglot.exp.Column)
        return {
            'table': {_table.name for _table in table_names},
            'column': {_column.alias_or_name for _column in column_names}
        }
    except Exception as e:
        # 如果解析失败，返回空集合
        print(f"[Schema过滤] ⚠️ sqlglot解析失败: {e}")
        return {'table': set(), 'column': set()}


def extract_columns_from_sql_rsl_style(sql: str, db_schema: List[str]) -> List[str]:
    """
    参考RSL-SQL的extract_from_sql逻辑：从SQL中提取列
    
    完全按照RSL-SQL/src/bid_schema_linking.py的extract_from_sql方法
    
    Args:
        sql: SQL语句
        db_schema: 数据库schema列表，格式为['table.column', ...]
        
    Returns:
        提取的列列表，格式为['table.column', ...]（小写）
    """
    sql_lower = sql.lower()
    pred_truth = []
    
    # 将db_schema转为小写列表（参考RSL-SQL的处理）
    list_db = [item.lower() for item in db_schema]
    
    for item in list_db:
        table = item.split('.')[0]
        # 跳过系统表（参考RSL-SQL）
        if table == 'sqlite_sequence':
            continue
        column = item.split('.')[1]
        # 检查列名是否在SQL中出现（参考RSL-SQL的逻辑）
        if column in sql_lower:
            pred_truth.append(item)
    
    return pred_truth


def extract_tables_from_columns(columns: List[str]) -> List[str]:
    """
    从列列表中提取表名（去重）
    
    完全参考RSL-SQL/src/bid_schema_linking.py的逻辑
    
    Args:
        columns: 列列表，格式为['table.column', ...] 或 ['table.`column`', ...]
        
    Returns:
        表名列表（去重，保持顺序）
    """
    # 先格式化列名（参考RSL-SQL：添加反引号）
    formatted_columns = [item.replace('.', '.`') + '`' if '`' not in item else item for item in columns]
    
    tables = []
    for item in formatted_columns:
        # 提取表名（参考RSL-SQL的逻辑）
        t = item.split('.')[0].strip().strip('`')
        if t and t not in tables:
            tables.append(t)
    
    return tables


def extract_table_ddl(schema: str, table_name: str) -> Optional[str]:
    """
    从schema中提取指定表的完整DDL定义
    
    Args:
        schema: 完整的schema DDL字符串
        table_name: 表名
        
    Returns:
        表的CREATE TABLE定义，如果找不到则返回None
    """
    # 使用更精确的匹配：匹配CREATE TABLE到对应的右括号
    pattern = rf'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?{re.escape(table_name)}`?\s*\([^;]+?\);'
    match = re.search(pattern, schema, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(0).strip()
    
    # 如果上面的模式没匹配到，尝试匹配到分号（更宽松的模式）
    pattern2 = rf'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?{re.escape(table_name)}`?[^;]+;'
    match2 = re.search(pattern2, schema, re.IGNORECASE | re.DOTALL)
    if match2:
        return match2.group(0).strip()
    
    return None


def parse_column_definitions(table_ddl: str) -> Dict[str, str]:
    """
    解析表DDL中的列定义
    
    参考RSL-SQL的逻辑：从CREATE TABLE语句中提取所有列定义
    
    Args:
        table_ddl: CREATE TABLE语句
        
    Returns:
        {列名（小写）: 列定义字符串} 的字典
    """
    columns = {}
    
    # 提取CREATE TABLE table_name (...)中的列定义部分
    match = re.search(r'CREATE\s+TABLE\s+[^\(]+\((.*?)\)\s*;', table_ddl, re.IGNORECASE | re.DOTALL)
    if not match:
        return columns
    
    columns_def = match.group(1).strip()
    
    # 分割列定义（按逗号分割，但要注意括号内的逗号和字符串）
    column_parts = []
    current_part = ""
    paren_depth = 0
    in_string = False
    string_char = None
    
    i = 0
    while i < len(columns_def):
        char = columns_def[i]
        
        # 处理字符串字面量（单引号或双引号）
        if char in ("'", '"') and (i == 0 or columns_def[i-1] != '\\'):
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char:
                in_string = False
                string_char = None
            current_part += char
        elif not in_string:
            if char == '(':
                paren_depth += 1
                current_part += char
            elif char == ')':
                paren_depth -= 1
                current_part += char
            elif char == ',' and paren_depth == 0:
                if current_part.strip():
                    column_parts.append(current_part.strip())
                current_part = ""
            else:
                current_part += char
        else:
            current_part += char
        
        i += 1
    
    # 添加最后一部分
    if current_part.strip():
        column_parts.append(current_part.strip())
    
    # 解析每个列定义，提取列名
    for col_def in column_parts:
        col_def = col_def.strip()
        if not col_def:
            continue
        
        # 跳过约束定义（如PRIMARY KEY, FOREIGN KEY等）
        if re.match(r'^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT)', col_def, re.IGNORECASE):
            continue
        
        # 提取列名（可能是 `column_name` 或 column_name）
        # 优先匹配反引号包裹的列名（支持空格等特殊字符，如 `Low Grade`）
        col_match_quoted = re.match(r'`([^`]+)`', col_def)
        if col_match_quoted:
            col_name = col_match_quoted.group(1).lower()  # 统一转为小写，保留空格
            columns[col_name] = col_def
        else:
            # 如果没有反引号，匹配简单列名（只包含字母、数字、下划线）
            col_match = re.match(r'(\w+)', col_def)
            if col_match:
                col_name = col_match.group(1).lower()  # 统一转为小写，参考RSL-SQL
                columns[col_name] = col_def
    
    return columns


def filter_table_columns(table_ddl: str, required_columns: Set[str]) -> str:
    """
    从表的DDL中过滤出指定的列
    
    参考RSL-SQL的逻辑：根据需要的列集合过滤schema DDL
    
    Args:
        table_ddl: CREATE TABLE语句
        required_columns: 需要保留的列名集合（列名应该统一为小写）
        
    Returns:
        过滤后的CREATE TABLE语句（只包含指定的列）
    """
    if not required_columns:
        return table_ddl
    
    # 统一列名为小写（参考RSL-SQL的处理方式）
    required_columns_lower = {col.lower() for col in required_columns}
    
    # 提取表名和完整的CREATE TABLE结构
    table_match = re.match(r'(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[^\(]+)\(', table_ddl, re.IGNORECASE | re.DOTALL)
    if not table_match:
        return table_ddl
    
    table_header = table_match.group(1).strip()
    
    # 解析所有列定义
    all_columns = parse_column_definitions(table_ddl)
    
    # 过滤出需要的列
    filtered_column_defs = []
    for col_name, col_def in all_columns.items():
        if col_name.lower() in required_columns_lower:
            filtered_column_defs.append(col_def)
    
    # 如果没有匹配的列，返回原始DDL
    if not filtered_column_defs:
        return table_ddl
    
    # 重新构建CREATE TABLE语句
    columns_str = ',\n    '.join(filtered_column_defs)
    filtered_ddl = f"{table_header} (\n    {columns_str}\n);"
    
    return filtered_ddl


def build_simplified_schema_ddl(tables: List[str], columns: List[str], original_schema: str) -> str:
    """
    构建简化的schema DDL
    
    完全参考RSL-SQL/src/utils/simplified_schema.py的simplified函数
    
    Args:
        tables: 表名列表
        columns: 列列表，格式为['table.column', ...] 或 ['table.`column`', ...]
        original_schema: 原始完整schema DDL
        
    Returns:
        简化后的schema DDL字符串
    """
    # 构建table_list字典：{表名: [列名列表]}
    table_list = {}
    simple_ddl_parts = []
    
    for table in tables:
        column_list = []
        for column in columns:
            # 处理格式：table.column 或 table.`column`
            # 先移除反引号，统一处理
            col_clean = column.replace('`', '')
            _table = col_clean.split('.')[0].strip()
            if _table == table:
                col_name = col_clean.split('.')[1].strip()
                column_list.append(col_name)
        
        if column_list:
            table_list[table] = column_list
            
            # 从原始schema中提取该表的DDL
            table_ddl = extract_table_ddl(original_schema, table)
            if table_ddl:
                # 过滤列（使用小写列名）
                filtered_ddl = filter_table_columns(table_ddl, {col.lower() for col in column_list})
                simple_ddl_parts.append(filtered_ddl)
    
    return '\n\n'.join(simple_ddl_parts) if simple_ddl_parts else ""


def build_filtered_schema_ddl(candidate_tables: Set[str], 
                              columns_by_table: Dict[str, Set[str]],
                              original_schema: str,
                              filter_columns: bool = False,
                              normalize_column_names: bool = True) -> str:
    """
    从原始schema中构建过滤后的DDL（可以过滤列）
    
    参考RSL-SQL的逻辑：根据需要的表和列构建简化的schema DDL
    
    Args:
        candidate_tables: 候选表集合
        columns_by_table: 每个表涉及的列集合（可选）
        original_schema: 原始完整schema
        filter_columns: 是否过滤列（如果为True且columns_by_table不为空，则只保留指定的列）
        normalize_column_names: 是否规范化列名（统一转为小写，参考RSL-SQL）
        
    Returns:
        过滤后的schema DDL字符串
    """
    if not candidate_tables:
        return ""
    
    candidate_ddl_parts = []
    
    for table_name in candidate_tables:
        # 提取表的完整DDL
        table_ddl = extract_table_ddl(original_schema, table_name)
        if not table_ddl:
            continue
        
        # 如果需要过滤列
        if filter_columns and table_name in columns_by_table and columns_by_table[table_name]:
            # 规范化列名（统一转为小写，参考RSL-SQL的处理方式）
            required_columns = columns_by_table[table_name]
            if normalize_column_names:
                required_columns = {col.lower() for col in required_columns}
            
            filtered_ddl = filter_table_columns(table_ddl, required_columns)
            candidate_ddl_parts.append(filtered_ddl)
        else:
            # 保留所有列
            candidate_ddl_parts.append(table_ddl)
    
    return '\n\n'.join(candidate_ddl_parts) if candidate_ddl_parts else ""
