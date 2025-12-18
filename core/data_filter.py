import pandas as pd
import sqlite3
import re
import sqlparse
import random
import Levenshtein

def data_filter(data, filter_command):
    """
    使用SQL过滤数据
    
    参数:
    data - pandas DataFrame
    filter_command - SQL WHERE子句格式的过滤条件字符串
    
    返回:
    过滤后的DataFrame
    """
    # 检查是否包含子查询
    if "SELECT" in filter_command.upper():
        raise Exception("当前不支持子查询(嵌套SELECT)。请分步执行您的查询，先写内部查询，然后将结果用于外部查询。")
    
    # 预处理多值等于条件，转换为IN条件
    filter_command = _preprocess_multi_value_equals(filter_command)
    
    # 处理IN条件中的重复值
    filter_command = _remove_duplicate_in_values(filter_command)
    
    # --- 新增修正：处理类似 'column = ['value']' 的格式 ---
    # 这会匹配 column = ['value'] 或 column = ["value"] 这种不规范的列表表示
    # 并将其转换为 column = 'value'
    # 这个 regex 查找 ' = ['value']' 或 ' = ["value"]'
    # 注意：这只处理单值列表的情况，更复杂的列表仍应使用 IN 语法
    list_value_pattern = re.compile(r"=\s*\[\s*['\"]?([^'\"\]]+)['\"]?\s*\]")
    filter_command = list_value_pattern.sub(r"= '\1'", filter_command)
    # --- 修正结束 ---

    # 创建内存数据库连接
    conn = sqlite3.connect(':memory:')
    
    try:
        # 将DataFrame写入SQLite临时表
        temp_table_name = 'temp_data_table'
        data.to_sql(temp_table_name, conn, if_exists='replace', index=False)
        
        sql_command = filter_command
        
        # 执行SQL查询
        query = f"SELECT * FROM {temp_table_name} WHERE {sql_command}"
        result_data = pd.read_sql_query(query, conn)
        
        # 如果过滤结果为空，提供详细的错误信息
        if len(result_data) == 0:
            _handle_empty_result(conn, temp_table_name, sql_command, filter_command, data)
        
        return result_data
    
    except Exception as e:
        _handle_filter_exception(e, filter_command, data)
    
    finally:
        # 关闭连接
        conn.close()
def _preprocess_multi_value_equals(filter_command):
    # ... (您的 _preprocess_multi_value_equals 函数代码) ...
    processed_command = filter_command
    multi_value_pattern = re.compile(
        r'([`"\']?[a-zA-Z0-9_]+[`"\']?)\s*=\s*((?:\'[^\']+\'|"[^"]+"|[^,\s\'"]+)(?:,\s*(?:\'[^\']+\'|"[^"]+"|[^,\s\'"]+))+)',
        re.IGNORECASE
    )
    matches = list(multi_value_pattern.finditer(processed_command))
    for match in reversed(matches):
        column = match.group(1)
        values_str_raw = match.group(2)
        values = []
        value_finder = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\\"]|\\.)*\"|[^,\s]+")
        for val_match in value_finder.finditer(values_str_raw):
            val = val_match.group(0).strip()
            if val:
                values.append(val)
        if not values:
            continue
        in_values_str = ', '.join(values)
        in_condition = f"{column} IN ({in_values_str})"
        start, end = match.span()
        processed_command = processed_command[:start] + in_condition + processed_command[end:]
    return processed_command

def _remove_duplicate_in_values(filter_command):
    # ... (您的 _remove_duplicate_in_values 函数代码) ...
    in_pattern = re.compile(r'(\w+)\s+IN\s*\((.*?)\)', re.IGNORECASE)
    in_matches = in_pattern.findall(filter_command)
    for column, values_str in in_matches:
        values = [v.strip() for v in values_str.split(',')]
        unique_values = []
        for value in values:
            if value not in unique_values:
                unique_values.append(value)
        if len(values) != len(unique_values):
            unique_values_str = ', '.join(unique_values)
            old_in_clause = f"{column} IN ({values_str})"
            new_in_clause = f"{column} IN ({unique_values_str})"
            filter_command = filter_command.replace(old_in_clause, new_in_clause)
    return filter_command


def _handle_empty_result(conn, temp_table_name, sql_command, filter_command, data):
    # ... (您的 _handle_empty_result 函数代码) ...
    conditions = _parse_conditions(sql_command)
    condition_results = _test_individual_conditions(conn, temp_table_name, conditions, data)
    error_msg = _generate_error_message(condition_results)
    raise Exception(error_msg)


def _parse_conditions(sql_command):
    # ... (您的 _parse_conditions 函数代码) ...
    conditions = []
    full_sql = f"SELECT * FROM table WHERE {sql_command}"
    parsed = sqlparse.parse(full_sql)[0]
    where_found = False
    for token in parsed.tokens:
        if where_found:
            if token.ttype is not sqlparse.tokens.Whitespace:
                conditions_text = token.value
                break
        elif token.ttype is sqlparse.tokens.Keyword and token.value.upper() == 'WHERE':
            where_found = True
    if where_found:
        pattern = r'\bAND\b(?=(?:[^\']*\'[^\']*\')*[^\']*$)(?=(?:[^"]*"[^"]*")*[^"]*$)'
        conditions = [c.strip() for c in re.split(pattern, conditions_text, flags=re.IGNORECASE)]
    if not conditions:
        conditions = [c.strip() for c in sql_command.split(' AND ')]
    if len(conditions) == 1 and ' AND ' in conditions[0].upper():
        column_pattern = r'[`"\']?([a-zA-Z0-9_]+)[`"\']?\s*(>=|<=|>|<|=)'
        matches = re.findall(column_pattern, conditions[0], re.IGNORECASE)
        if len(matches) >= 2 and matches[0][0] == matches[1][0]:
            parts = conditions[0].split(' AND ', 1)
            conditions = [p.strip() for p in parts]
    return conditions

def _test_individual_conditions(conn, temp_table_name, conditions, data):
    # ... (您的 _test_individual_conditions 函数代码) ...
    condition_results = []
    for condition in conditions:
        single_query = f"SELECT * FROM {temp_table_name} WHERE {condition}"
        try:
            single_result = pd.read_sql_query(single_query, conn)
            condition_display = _format_condition_display(condition)
            condition_results.append({
                'condition': condition_display,
                'matches': len(single_result),
                'has_results': len(single_result) > 0
            })
            if len(single_result) == 0:
                detailed_error = _get_detailed_error_for_condition(condition, data)
                if detailed_error:
                    condition_results[-1]['detailed_error'] = detailed_error
        except Exception as e:
            condition_results.append({
                'condition': condition,
                'error': str(e),
                'has_results': False
            })
    return condition_results

def _format_condition_display(condition):
    # ... (您的 _format_condition_display 函数代码) ...
    condition_display = condition
    in_match = re.search(r"([a-zA-Z0-9_`]+)\s+IN\s*\((.*?)\)$", condition, re.IGNORECASE)
    if in_match and len(in_match.group(2)) > 100:
        column_name = in_match.group(1).strip()
        values_str = in_match.group(2)
        values = [v.strip() for v in values_str.split(',')]
        values_count = len(values)
        sample_values = values[:3]
        sample_str = ', '.join(sample_values)
        condition_display = f"{column_name} IN ({sample_str}, ...共{values_count}个值...)"
    return condition_display


def _get_detailed_error_for_condition(condition, data):
    """获取条件的详细错误信息"""
    # 检查是否是复合条件
    if ' AND ' in condition.upper():
        # 尝试分割复合条件
        sub_conditions = [c.strip() for c in condition.split(' AND ')]
        
        # 分别检查每个子条件
        sub_errors = []
        for sub_condition in sub_conditions:
            sub_error = _get_detailed_error_for_single_condition(sub_condition, data)
            if sub_error:
                sub_errors.append(sub_error)
        
        if sub_errors:
            return "\n".join(sub_errors)
    
    # 处理单一条件
    return _get_detailed_error_for_single_condition(condition, data)


def _get_detailed_error_for_condition(condition, data):
    # ... (您的 _get_detailed_error_for_condition 函数代码) ...
    if ' AND ' in condition.upper():
        sub_conditions = [c.strip() for c in condition.split(' AND ')]
        sub_errors = []
        for sub_condition in sub_conditions:
            sub_error = _get_detailed_error_for_single_condition(sub_condition, data)
            if sub_error:
                sub_errors.append(sub_error)
        if sub_errors:
            return "\n".join(sub_errors)
    return _get_detailed_error_for_single_condition(condition, data)

def _get_detailed_error_for_single_condition(condition, data):
    # ... (您的 _get_detailed_error_for_single_condition 函数代码) ...
    column_match = re.search(r"[`'\"]?([a-zA-Z0-9_]+)[`'\"]?\s*(=|==|LIKE|IN|>|<|>=|<=|!=|<>)\s*(.+)$", condition, re.IGNORECASE)
    if column_match:
        column_name = column_match.group(1).strip()
        operator = column_match.group(2).strip()
        value_part = column_match.group(3).strip()
        if column_name in data.columns:
            sample_values = list(set(data[column_name].tolist()))
            if sample_values:
                if operator in ['=', '==', '>', '<', '>=', '<=', '!=', '<>'] and not value_part.startswith('('):
                    clean_value = value_part.strip("'\"")
                    str_values = [str(v) for v in sample_values]
                    max_samples_for_distance = 100
                    if len(str_values) > max_samples_for_distance:
                        str_values_for_distance = random.sample(str_values, max_samples_for_distance)
                    else:
                        str_values_for_distance = str_values
                    closest_values = []
                    for val in str_values_for_distance:
                        distance = Levenshtein.distance(clean_value.lower(), val.lower())
                        closest_values.append((val, distance))
                    closest_values.sort(key=lambda x: x[1])
                    suggestions = [val for val, _ in closest_values[:3]]
                    if len(sample_values) > 5:
                        display_samples = random.sample(sample_values, 5)
                    else:
                        display_samples = sample_values
                    sample_values_str = ', '.join([str(i) for i in display_samples])
                    return f"列 '{column_name}' 中没有值满足条件 '{operator} {clean_value}'。\n最接近的值: {', '.join(suggestions)}\n列的示例值: {sample_values_str}"
                if len(sample_values) > 5:
                    sample_values = random.sample(sample_values, 5)
                sample_values_str = ', '.join([str(i) for i in sample_values])
                return f"列 '{column_name}' 的示例值: {sample_values_str}"
        else:
            columns = data.columns.tolist()
            similar_columns = []
            for col in columns:
                distance = Levenshtein.distance(column_name.lower(), col.lower())
                if distance <= max(len(column_name) // 2, 3):
                    similar_columns.append((col, distance))
            similar_columns.sort(key=lambda x: x[1])
            if similar_columns:
                suggestions = [col for col, _ in similar_columns[:3]]
                return f"列 '{column_name}' 不存在。您是否想要使用以下列名之一? {', '.join(suggestions)}"
    return None

def _generate_error_message(condition_results):
    # ... (您的 _generate_error_message 函数代码) ...
    error_msg = f"过滤条件没有返回结果。各条件测试结果:\n"
    for i, result in enumerate(condition_results, 1):
        if 'error' in result:
            error_msg += f"{i}. 条件 '{result['condition']}' 执行出错: {result['error']}\n"
        else:
            status = "匹配" if result['has_results'] else "无匹配"
            error_msg += f"{i}. 条件 '{result['condition']}' - {status} ({result['matches']}条记录)\n"
            if 'detailed_error' in result:
                error_msg += f"   {result['detailed_error']}\n"
    if all(result.get('has_results', False) for result in condition_results):
        error_msg += "\n所有条件单独测试都有匹配记录，但组合后没有共同匹配的记录。条件之间可能互斥。"
    return error_msg

def _handle_filter_exception(e, filter_command, data):
    # ... (您的 _handle_filter_exception 函数代码) ...
    if "过滤条件没有返回结果" in str(e):
        raise e
    if "syntax error" in str(e).lower():
        raise Exception(f"过滤查询 {filter_command} 存在语法错误。请检查您的条件。")
    no_column_match = re.search(r"no such column: [`\"']?([a-zA-Z0-9_ ]+)[`\"']?", str(e))
    if no_column_match:
        missing_column = no_column_match.group(1)
        columns = data.columns.tolist()
        similar_columns = []
        for col in columns:
            distance = Levenshtein.distance(missing_column.lower(), col.lower())
            if distance <= max(len(missing_column) // 2, 3):
                similar_columns.append((col, distance))
        similar_columns.sort(key=lambda x: x[1])
        error_msg = f"应用过滤器 '{filter_command}' 时出错: 列 '{missing_column}' 不存在。可用的列: {', '.join(columns)}"
        if similar_columns:
            suggestions = [col for col, _ in similar_columns[:3]]
            error_msg += f"\n您是否想要使用以下列名之一? {', '.join(suggestions)}"
        raise Exception(error_msg)
    raise Exception(f"应用过滤器 '{filter_command}' 时出错: {str(e)}")