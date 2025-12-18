from typing import Dict, List, Any, Optional, Union
import re
import pandas as pd
import numpy as np
import sqlite3
import datetime
import snoop
import os
from typing import Tuple
# ==================== 原子操作符函数 ====================
def load_df(db_name: str, table_name: str) -> pd.DataFrame:
    """
    Loads a DataFrame from a specified table in a SQLite database.
    Handles table names with spaces by quoting them.
    Tries to find the database in both dev and train paths.
    """
    dev_db_path = f"/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_databases/{db_name}/{db_name}.sqlite"
    train_db_path = f"/home/shenshuyu/SQL_tool/work/bird/train/train_databases/{db_name}/{db_name}.sqlite"
    
    db_path = None
    if os.path.exists(dev_db_path):
        db_path = dev_db_path
    elif os.path.exists(train_db_path):
        db_path = train_db_path
    else:
        print(f"Error: Database file for '{db_name}' not found at either {dev_db_path} or {train_db_path}")
        return pd.DataFrame() # Return empty DataFrame if DB not found
    
    conn = None # Initialize conn to None
    df = pd.DataFrame() # Initialize df to an empty DataFrame

    try:
        conn = sqlite3.connect(db_path)
        
        # --- CRITICAL CHANGE HERE FOR TABLE NAMES WITH SPACES ---
        # Quote table names to handle spaces or special characters in SQLite
        quoted_table_name = f'"{table_name}"'
        sql_query = f"SELECT * FROM {quoted_table_name}"
        
        # print(f"DEBUG: Executing SQL in load_df: {sql_query}") # Uncomment for debugging
        
        df = pd.read_sql_query(sql_query, conn)

        print(f"\n--- Loaded DataFrame Info for Table: '{table_name}' from DB: '{db_name}' ---") # Modified header
        print(f" Database Path: {db_path}")
        
        print(f"  Columns and Dtypes:")
        for col, dtype in df.dtypes.items():
            has_null = df[col].isnull().any()
            unique_count = df[col].nunique()
            print(f"    - {col}: dtype={dtype}, has_null={has_null}, unique_values={unique_count}")

        print(f"---------------------------------------------------\n") # Modified footer

        return df
    except sqlite3.Error as e:
        print(f"ERROR: SQL execution failed for table '{table_name}' in DB '{db_name}': {e}")
        return pd.DataFrame() # Return empty DataFrame on SQL error
    except Exception as e:
        print(f"An unexpected error occurred loading table '{table_name}' from DB '{db_name}': {e}")
        return pd.DataFrame() # Return empty DataFrame on other errors
    finally:
        if conn: # Ensure connection is closed even if an error occurs
            conn.close()
            
def load_db(db_name: str) -> dict[str, pd.DataFrame]:
    """
    Loads all tables from a specified SQLite database into a dictionary of pandas DataFrames
    and prints a summary for each table.
    Tries to find the database in both dev and train paths.

    Args:
        db_name (str): The ID of the database (e.g., "regional_sales").

    Returns:
        dict[str, pd.DataFrame]: A dictionary where keys are table names and values are
                                 the corresponding pandas DataFrames. Returns an empty
                                 dictionary if there's an error.
    """
    dev_db_path = f"/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_databases/{db_name}/{db_name}.sqlite"
    train_db_path = f"/home/shenshuyu/SQL_tool/work/bird/train/train_databases/{db_name}/{db_name}.sqlite"
    
    db_path = None
    if os.path.exists(dev_db_path):
        db_path = dev_db_path
    elif os.path.exists(train_db_path):
        db_path = train_db_path
    else:
        print(f"Error: Database file for '{db_name}' not found at either {dev_db_path} or {train_db_path}")
        return {} # Return empty dict if DB not found

    conn = None
    all_tables_df = {}

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        table_names = [row[0] for row in cursor.fetchall()]

        print(f"\n--- Database Overview: '{db_name}' ---")
        print(f"  Path: '{db_path}'")
        print(f"  Total Tables: {len(table_names)}")
        print(f"  Table Names: {', '.join(table_names)}\n")

        for table_name in table_names:
            print(f"--- Info for Table: '{table_name}' from DB: '{db_name}' ---") # Modified header
            try:
                # --- CRITICAL CHANGE HERE FOR TABLE NAMES WITH SPACES ---
                # Quote table names when querying all tables
                quoted_table_name = f'"{table_name}"'
                df = pd.read_sql_query(f"SELECT * FROM {quoted_table_name}", conn)
                all_tables_df[table_name] = df # Store the DataFrame

                print(f"  Shape: {df.shape} (rows, columns)")
                print(f"  Columns and Dtypes:")
                for col, dtype in df.dtypes.items():
                    has_null = df[col].isnull().any()
                    unique_count = df[col].nunique()
                    print(f"    - {col}: dtype={dtype}, has_null={has_null}, unique_values={unique_count}")
                # You can uncomment df.head() for a quick peek at data
                # print(f"  Head:\n{df.head()}\n")
            except Exception as e:
                print(f"  Error loading table '{table_name}': {e}")
            print(f"---------------------------------------------------\n")
    except sqlite3.Error as e:
        print(f"ERROR: SQL execution failed for database '{db_name}': {e}")
        return {} # Return empty dict on SQL error
    except Exception as e:
        print(f"An unexpected error occurred loading database '{db_name}': {e}")
        return {} # Return empty dict on other errors
    finally:
        if conn:
            conn.close()
    
    print(f"--- Database '{db_name}' Loading Complete ---")
    return all_tables_df
def execute_sql(db_name, sql):
    # 首先尝试dev路径
    dev_db_path = f"/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_databases/{db_name}/{db_name}.sqlite"
    train_db_path = f"/home/shenshuyu/SQL_tool/work/bird/train/train_databases/{db_name}/{db_name}.sqlite"
    
    # 检查路径是否存在
    import os
    if os.path.exists(dev_db_path):
        db_path = dev_db_path
    elif os.path.exists(train_db_path):
        db_path = train_db_path
    else:
        return f"Error: Database file not found at either {dev_db_path} or {train_db_path}"
    
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(sql)
        result = cur.fetchall()
        conn.close()
        return result
    except Exception as e:
        return str(e)


def op_eq(series1: pd.Series, series2_or_literal: Union[pd.Series, int, float, str]) -> pd.Series:
    return series1 == series2_or_literal

def op_gt(series1: pd.Series, series2_or_literal: Union[pd.Series, int, float, str]) -> pd.Series:
    return series1 > series2_or_literal
    
def op_lt(series1: pd.Series, series2_or_literal: Union[pd.Series, int, float, str]) -> pd.Series:
    return series1 < series2_or_literal

def op_gte(series1: pd.Series, series2_or_literal: Union[pd.Series, int, float, str]) -> pd.Series:
    return series1 >= series2_or_literal

def op_lte(series1: pd.Series, series2_or_literal: Union[pd.Series, int, float, str]) -> pd.Series:
    return series1 <= series2_or_literal

def op_neq(series1: pd.Series, series2_or_literal: Union[pd.Series, int, float, str]) -> pd.Series:
    return series1 != series2_or_literal

def op_distinct(df: pd.DataFrame, columns: Optional[List[str]] = None) -> pd.DataFrame:
    """模拟 SQL 的 SELECT DISTINCT 或 SELECT DISTINCT ON（取决于是否指定 columns）操作"""
    # pandas模式：返回去重后的DataFrame
    if columns is None:
        # 如果没有指定列，对整个DataFrame去重
        return df.drop_duplicates()
    else:
        # 如果指定了列，只基于这些列去重
        return df.drop_duplicates(subset=columns)


def op_like(series: pd.Series, pattern: str) -> pd.Series:
    # 确保 Series 是字符串类型
    if not pd.api.types.is_string_dtype(series):
        series = series.astype(str)

    # 首先转义所有正则表达式特殊字符，然后替换 SQL 通配符
    # re.escape 会转义 '.'、'*'、'?' 等
    escaped_pattern = re.escape(pattern)

    # 现在将 SQL 通配符替换为正则表达式通配符
    # 使用 replace 避免重复转义已替换的部分
    regex_pattern = escaped_pattern.replace(re.escape('%'), '.*').replace(re.escape('_'), '.')

    # na=False 确保 Series 中的 NaN 结果为 False
    return series.str.contains(regex_pattern, regex=True, na=False,case=False)


def op_between(
    series: pd.Series,
    min_val: Any, # 扩展为 Any，因为它可以是数值、字符串、日期等任何可比较的类型
    max_val: Any
) -> pd.Series:
    """
    检查 Series 中的值是否在指定范围内（包含边界）。
    类似于 SQL 的 BETWEEN 操作。

    参数:
        series (pd.Series): 要检查的 Pandas Series。可以包含数值、字符串、日期等类型。
        min_val (Any): 范围的最小值。类型应与 series 中的元素类型兼容。
        max_val (Any): 范围的最大值。类型应与 series 中的元素类型兼容。

    返回:
        pd.Series: 布尔型 Series，表示每个元素是否在指定范围内。

    注意:
        该函数依赖 Pandas 比较操作符的隐式类型转换和对齐能力。
        确保 series、min_val 和 max_val 之间的类型是可比较的。
    """
    # Pandas 的比较操作符通常能智能地处理不同类型 Series/标量之间的比较
    # 只要这些类型是 Pandas 内部可兼容的 (例如，数值 vs 数值，字符串 vs 字符串，日期 vs 日期)

    # 显式地将 min_val 和 max_val 转换为 Series，确保逐元素比较的健壮性
    # 这样可以处理 min_val/max_val 是 Series 的情况，也能处理标量的情况
    # Pandas 在内部会处理 Series 和标量之间的广播
    
    # 确保 series 是可比较的类型
    if not pd.api.types.is_numeric_dtype(series) and \
       not pd.api.types.is_string_dtype(series) and \
       not pd.api.types.is_datetime64_any_dtype(series):
        # 如果 series 既不是数值、字符串也不是日期，尝试转换为对象类型进行通用比较
        # 但这可能导致意想不到的行为，最好让调用者传入可比较的 Series
        # 否则，可能需要抛出错误
        # raise TypeError("series 的数据类型不可比较（例如，不支持范围比较）。")
        pass # 让 Pandas 自己的比较操作符去决定是否能比较

    return (series >= min_val) & (series <= max_val)

def op_is_null(series: pd.Series) -> pd.Series:
    return series.isnull()

def op_is_not_null(series: pd.Series) -> pd.Series:
    return series.notnull()

def op_in(series: pd.Series, values: list) -> pd.Series:
    return series.isin(values)


# ==================== 算术操作符 ====================
def op_add(series1: Union[pd.Series, int, float, np.number], series2_or_literal: Union[pd.Series, int, float, np.number]) -> Union[pd.Series, int, float, np.number]:
    # 统一处理 series1，确保它是 Series
    if not isinstance(series1, pd.Series):
        actual_series1 = pd.Series([series1]) # 将标量包装成单元素Series
    else:
        actual_series1 = series1

    # 处理 series2_or_literal：如果是单元素 Series，提取其值作为标量；否则保持原样
    if isinstance(series2_or_literal, pd.Series) and len(series2_or_literal) == 1:
        actual_series2_or_literal_val = series2_or_literal.iloc[0]
    else:
        actual_series2_or_literal_val = series2_or_literal

    # 执行加法，Pandas 会自动处理 Series 和标量之间的广播
    result = actual_series1 + actual_series2_or_literal_val

    # 如果原始输入都是标量，则返回标量
    if not isinstance(series1, pd.Series) and not isinstance(series2_or_literal, pd.Series):
        if isinstance(result, pd.Series) and len(result) == 1:
            return result.iloc[0]
        return result # 以防万一
    
    return result

def op_sub(series1: Union[pd.Series, int, float, np.number], series2_or_literal: Union[pd.Series, int, float, np.number]) -> Union[pd.Series, int, float, np.number]:
    # 统一处理 series1，确保它是 Series
    if not isinstance(series1, pd.Series):
        actual_series1 = pd.Series([series1]) # 将标量包装成单元素Series
    else:
        actual_series1 = series1

    # 处理 series2_or_literal：如果是单元素 Series，提取其值作为标量；否则保持原样
    if isinstance(series2_or_literal, pd.Series) and len(series2_or_literal) == 1:
        actual_series2_or_literal_val = series2_or_literal.iloc[0]
    else:
        actual_series2_or_literal_val = series2_or_literal

    # 执行减法，Pandas 会自动处理 Series 和标量之间的广播
    result = actual_series1 - actual_series2_or_literal_val

    # 如果原始输入都是标量，则返回标量
    if not isinstance(series1, pd.Series) and not isinstance(series2_or_literal, pd.Series):
        if isinstance(result, pd.Series) and len(result) == 1:
            return result.iloc[0]
        return result # 以防万一
    
    return result

def op_mul(series1: Union[pd.Series, int, float, np.number], series2_or_literal: Union[pd.Series, int, float, np.number]) -> Union[pd.Series, int, float, np.number]:
    # 统一处理 series1，确保它是 Series 或单个标量
    if not isinstance(series1, pd.Series):
        actual_series1 = pd.Series([series1]) # 将标量包装成单元素Series
    else:
        actual_series1 = series1

    # 处理 series2_or_literal
    if isinstance(series2_or_literal, pd.Series) and len(series2_or_literal) == 1:
        # 如果是单元素 Series，提取其值作为标量
        actual_series2_or_literal_val = series2_or_literal.iloc[0]
    else:
        # 否则保持原样，可以是 Series 也可以是标量
        actual_series2_or_literal_val = series2_or_literal

    # 执行乘法
    result = actual_series1 * actual_series2_or_literal_val

    # 如果原始输入都是标量，则返回标量
    if not isinstance(series1, pd.Series) and not isinstance(series2_or_literal, pd.Series):
        if isinstance(result, pd.Series) and len(result) == 1:
            return result.iloc[0]
        return result # Fallback
    
    return result

def op_div(series1: Union[pd.Series, int, float, np.number], series2_or_literal: Union[pd.Series, int, float, np.number]) -> Union[pd.Series, int, float]:
    # 统一处理输入类型，确保它们是 Pandas Series 或 Python 原生数值类型
    # 如果 series1 是标量，先将其包装成一个 Pandas Series，以便后续操作一致
    if not isinstance(series1, pd.Series):
        # 创建一个单元素的Series，如果原始输入有索引（尽管这里可能没有），则保留
        # 为了兼容性，我们可以创建一个临时的Series，或者直接在标量层面进行操作
        # 但是由于函数设计最终返回Series，所以包装为Series更安全
        actual_series1 = pd.Series([series1])
    else:
        actual_series1 = series1

    # 处理 series2_or_literal：如果是单元素 Series，提取其值作为标量；否则保持原样
    if isinstance(series2_or_literal, pd.Series) and len(series2_or_literal) == 1:
        actual_series2_or_literal_val = series2_or_literal.iloc[0]
    else:
        actual_series2_or_literal_val = series2_or_literal

    # 确保操作数是浮点类型，避免整数除法行为，并能正确处理 NaN
    # 这里对 actual_series1_float 直接进行类型转换，因为它是 Series
    actual_series1_float = actual_series1.astype(float)

    # 2. 执行除法
    # Pandas 会处理 Series 和标量之间的广播
    # 如果 actual_series2_or_literal_val 是 Series，Pandas 会对齐索引
    result = actual_series1_float / actual_series2_or_literal_val

    # 3. 将结果中的无穷大值 (±inf) 替换为 NaN (SQL 的 NULL)
    # 这是由于 0.0/0.0 -> NaN, 正数/0 -> inf, 负数/0 -> -inf
    result = result.replace([np.inf, -np.inf], np.nan)

    # 4. 额外处理 0/0 的情况，确保结果为 NaN
    # Pandas 的 0/0 会直接得到 NaN，但有时其他操作可能导致它变为 0
    # SQL 通常会将除以 0 的结果视为 NULL
    if isinstance(series2_or_literal, pd.Series):
        # 找到 original series2_or_literal 中为 0 的位置
        zero_divisor_mask = (series2_or_literal == 0)
        # 确保这些位置的结果是 NaN (因为 0/0 = NaN, 但其他情况也需要显式处理)
        # result.where(~zero_divisor_mask, np.nan) 会在 zero_divisor_mask 为 True 的地方放 NaN
        result = result.where(~zero_divisor_mask, np.nan)
    elif (isinstance(series2_or_literal, (int, float, np.number)) and series2_or_literal == 0):
        # 如果除数是标量 0，整个结果 Series 都应为 NaN
        # 此时 result 可能是单个 NaN 值，也可能是一个 Series，确保其形状正确
        # 这里的 index 应该从 original series1 获取，以保持长度和索引一致
        result = pd.Series(np.nan, index=actual_series1.index)

    # 最终返回结果的类型应该根据原始输入决定
    # 如果 original series1 和 series2_or_literal 都是标量，则返回标量
    if not isinstance(series1, pd.Series) and not isinstance(series2_or_literal, pd.Series):
        if isinstance(result, pd.Series) and len(result) == 1:
            return result.iloc[0] # 返回标量
        return result # 以防万一，直接返回
    
    return result


def op_neg(series: Union[pd.Series, int, float, np.number]) -> Union[pd.Series, int, float, np.number]:
    return -series

# ==================== 逻辑操作符 ====================
def op_and(*conditions: pd.Series) -> pd.Series:
    """
    对多个布尔型 Pandas Series 执行逻辑 AND 操作。
    类似于 SQL 的 AND。

    参数:
        *conditions: 一个或多个要组合的布尔型 Pandas Series。

    返回:
        pd.Series: 表示 AND 操作结果的布尔型 Series。

    引发:
        ValueError: 如果未提供任何条件。
        TypeError: 如果提供的任何条件无法转换为布尔型 Series。
    """
    if not conditions:
        raise ValueError("op_and 至少需要一个条件 Series。")

    first_cond = conditions[0]
    if not pd.api.types.is_bool_dtype(first_cond):
        try:
            first_cond = first_cond.astype(bool)
        except Exception as e:
            raise TypeError(
                f"op_and 的第一个条件无法转换为布尔型 Series: {e}"
            ) from e
    result = first_cond

    for i, cond in enumerate(conditions[1:]):
        if not pd.api.types.is_bool_dtype(cond):
            try:
                cond = cond.astype(bool)
            except Exception as e:
                raise TypeError(
                    f"op_and 中索引为 {i+1} 的条件无法转换为布尔型 Series: {e}"
                ) from e
        result = result & cond # 执行 AND 操作
        
    return result

def op_or(*conditions: pd.Series) -> pd.Series:
    """
    对多个布尔型 Pandas Series 执行逻辑 OR 操作。
    类似于 SQL 的 OR。

    参数:
        *conditions: 一个或多个要组合的布尔型 Pandas Series。

    返回:
        pd.Series: 表示 OR 操作结果的布尔型 Series。

    引发:
        ValueError: 如果未提供任何条件。
        TypeError: 如果提供的任何条件无法转换为布尔型 Series。
    """
    if not conditions:
        raise ValueError("op_or 至少需要一个条件 Series。")

    first_cond = conditions[0]
    if not pd.api.types.is_bool_dtype(first_cond):
        try:
            first_cond = first_cond.astype(bool)
        except Exception as e:
            raise TypeError(
                f"op_or 的第一个条件无法转换为布尔型 Series: {e}"
            ) from e
    result = first_cond

    for i, cond in enumerate(conditions[1:]):
        if not pd.api.types.is_bool_dtype(cond):
            try:
                cond = cond.astype(bool)
            except Exception as e:
                raise TypeError(
                    f"op_or 中索引为 {i+1} 的条件无法转换为布尔型 Series: {e}"
                ) from e
        result = result | cond # 执行 OR 操作
        
    return result
def op_not(condition: pd.Series) -> pd.Series:
    """
    对布尔型 Pandas Series 执行逻辑 NOT 操作。
    类似于 SQL 的 NOT。

    参数:
        condition (pd.Series): 要取反的布尔型 Pandas Series。

    返回:
        pd.Series: 表示 NOT 操作结果的布尔型 Series。

    引发:
        TypeError: 如果提供的条件无法转换为布尔型 Series。
    """
    if not pd.api.types.is_bool_dtype(condition):
        try:
            condition = condition.astype(bool)
        except Exception as e:
            raise TypeError(
                f"op_not 的条件无法转换为布尔型 Series: {e}"
            ) from e
    return ~condition
# ==================== 聚合函数 ====================


def op_sum(grouped: pd.core.groupby.SeriesGroupBy) -> pd.Series:
    return grouped.sum()

def op_avg(grouped: pd.core.groupby.SeriesGroupBy) -> pd.Series:
    return grouped.mean()

def op_count(obj: Union[pd.Series, pd.DataFrame, pd.core.groupby.SeriesGroupBy, pd.Index]) -> Union[int, pd.Series]:
    """
    Simulates SQL's COUNT function.
    - If a Series, counts non-null values.
    - If a DataFrame, counts rows (COUNT(*)).
    - If a SeriesGroupBy, performs grouped count.
    - If an Index, counts elements (useful for COUNT(*)).
    """
    if isinstance(obj, pd.core.groupby.SeriesGroupBy):
        return obj.count()  # This handles grouped counts
    elif isinstance(obj, (pd.Series, pd.Index)):
        # For COUNT(column_name) or COUNT(DISTINCT column_name), use .dropna().count() or .nunique()
        # For COUNT(*), when passed df.index, just return its length.
        # When passed a Series, .count() ignores NaN values, which matches SQL COUNT(column).
        return obj.shape[0] # Changed to obj.shape[0] for counting total elements/rows, similar to COUNT(*)
    elif isinstance(obj, pd.DataFrame):
        return obj.shape[0] # COUNT(*) for a DataFrame means number of rows
    else:
        # Fallback or error for unsupported types
        raise TypeError(f"op_count does not support type: {type(obj)}")

def op_max(grouped: pd.core.groupby.SeriesGroupBy) -> pd.Series:
    return grouped.max()

def op_min(grouped: pd.core.groupby.SeriesGroupBy) -> pd.Series:
    return grouped.min()

# ==================== 字符串函数 ====================
# 

def op_substring(series: pd.Series, start: Union[int, pd.Series], length: Optional[Union[int, pd.Series]] = None) -> pd.Series:
    """
    模拟 SQL 的 SUBSTR 函数。
    SQL SUBSTR 是 1-indexed。负数的 start 从字符串末尾开始计数。
    如果 length 被省略，子字符串会截取到字符串的末尾。
    """
    processed_series = series.astype(object)
    was_na = processed_series.isna()
    temp_series_for_slicing = processed_series.fillna('') # 用空字符串填充 NaN，以便进行一致的切片操作

    # 优先处理标量 start 和 length，以提高清晰度，然后应用矢量化操作
    if isinstance(start, int) and (length is None or isinstance(length, int)):
        def single_slice(s, s_val, l_val):
            if not isinstance(s, str):
                return '' # 或者返回 pd.NA，取决于你的空值处理策略
            
            # SQL 的 start 是 1-indexed。转换为 0-indexed 的 Python 索引。
            if s_val > 0:
                python_start_idx = s_val - 1
            else: # 负数的 start 意味着从字符串末尾开始计数
                # 例如，对于 s_val = -4，这意味着从倒数第4个字符开始 (len(s) - 4)
                python_start_idx = len(s) + s_val 
                # 确保起始索引不会超出字符串的左边界
                python_start_idx = max(0, python_start_idx)

            # 处理结束索引
            if l_val is None:
                python_end_idx = None # 切片到字符串末尾
            else:
                python_end_idx = python_start_idx + l_val
                # 确保结束索引不会超出字符串的右边界
                python_end_idx = min(len(s), python_end_idx)

            return s[python_start_idx:python_end_idx]
        
        # 对系列中的每个字符串应用切片函数
        sliced_result = pd.Series([
            single_slice(s, start, length) for s in temp_series_for_slicing
        ], index=series.index, dtype=object)

    elif isinstance(start, pd.Series) or (length is not None and isinstance(length, pd.Series)):
        # 处理 start 或 length 是 Series 的情况（更复杂的矢量化）
        # 你的 SQL 查询中 start 和 length 都是标量 (-4, 4)，所以会进入上面的标量处理分支。
        # 如果未来有需要，此分支需要更健壮的实现来处理 Series 类型的 start/length。
        
        # 获取每个字符串的长度
        string_lengths = temp_series_for_slicing.str.len()
        
        # 确保 start 和 length 如果不是 Series 也转换为 Series，并与主 Series 对齐索引
        _start_series = start if isinstance(start, pd.Series) else pd.Series(start, index=series.index)
        _length_series = length if isinstance(length, pd.Series) else pd.Series(length, index=series.index) if length is not None else None

        # 计算 Python 0-indexed 的起始位置
        # 这里需要一个辅助函数来应用每个元素的计算
        python_start_idx_series = pd.Series([
            (l + s_val if s_val < 0 else s_val - 1) if l is not None else 0 # 简化逻辑，需要更严谨处理边界和NaN
            for l, s_val in zip(string_lengths, _start_series)
        ], index=series.index, dtype=int)
        
        # 计算 Python 结束位置
        if _length_series is not None:
             python_end_idx_series = python_start_idx_series + _length_series
        else:
             python_end_idx_series = pd.Series([None] * len(series), index=series.index, dtype=object) # 表示切片到末尾

        # 使用 numpy.vectorize 进行实际切片操作
        # 注意：np.vectorize 无法直接处理 None，需要确保 python_end_idx_series 没有 None
        # 如果 python_end_idx_series 包含 None，需要更复杂的逻辑，比如用 apply 或先处理 None
        
        # 重新实现 vectorize，确保 None 值被正确处理为切片到末尾
        def vectorized_slice_func(s, ps, pe):
            if not isinstance(s, str): return ''
            if pe is None: return s[ps:]
            return s[ps:pe]

        if python_end_idx_series is not None:
            sliced_result = pd.Series([
                vectorized_slice_func(s, ps, pe)
                for s, ps, pe in zip(temp_series_for_slicing, python_start_idx_series, python_end_idx_series)
            ], index=series.index, dtype=object)
        else: # Length is None, slice to end
             sliced_result = pd.Series([
                vectorized_slice_func(s, ps, None) # Pass None to slice function
                for s, ps in zip(temp_series_for_slicing, python_start_idx_series)
            ], index=series.index, dtype=object)
            
    else:
        raise TypeError(f"Unsupported 'start' ({type(start)}) or 'length' ({type(length)}) type in op_substring.")

    sliced_result.loc[was_na] = pd.NA
    return sliced_result.astype(str) # 确保最终输出是字符串类型以便比较



def op_concat(*args: pd.Series, handle_nulls_strictly: bool = False) -> pd.Series:
    """
    连接多个 Pandas Series 中的字符串。
    类似于 SQL 的 CONCAT 函数。

    参数:
        *args: 一个或多个要连接的 Pandas Series。
        handle_nulls_strictly (bool): 如果为 True（SQL 严格模式），
                                     任何输入 Series 中有 NaN/NULL，结果对应位置为 NaN/NULL。
                                     如果为 False（SQL 宽松模式，如 Oracle 默认），
                                     NaN/NULL 值将被视为空字符串进行连接。

    返回:
        pd.Series: 连接后的字符串 Series。
    """
    if not args:
        raise ValueError("op_concat 至少需要一个 Series。")

    # 获取所有输入 Series 的共同索引，以确保结果 Series 的长度和对齐方式正确
    common_index = args[0].index
    for arg_series in args[1:]:
        common_index = common_index.union(arg_series.index)
    
    # 准备用于拼接的 Series 列表
    str_series_list = []
    for arg_series in args:
        # 将每个 Series 对齐到共同索引
        aligned_series = arg_series.reindex(common_index)
        
        if handle_nulls_strictly:
            # 严格模式：将所有值转换为字符串，但如果原始值是 NaN/None，则将其转换为 Pandas 的缺失值 (pd.NA)
            # 这允许 pd.NA 在字符串拼接中“传染”，导致结果也为 pd.NA
            str_series_list.append(aligned_series.astype(str).replace('nan', pd.NA))
        else:
            # 宽松模式：将 NaN/None 填充为空字符串，然后转换为字符串
            str_series_list.append(aligned_series.fillna('').astype(str))
    
    # 执行连接
    # 从第一个 Series 开始，逐个进行拼接
    result_series = str_series_list[0]
    for s in str_series_list[1:]:
        # Pandas 的 Series 加法 (+) 可以自动处理字符串拼接，
        # 且如果操作数中包含 pd.NA，其行为符合严格模式的“传染”性
        result_series = result_series + s
            
    return result_series.astype(str) # 确保返回的是字符串 Series


def op_length(series: pd.Series) -> pd.Series:
    # Pandas 的 .str.len() 已经很好地处理了 NaN，会返回 NaN。
    # 确保输入是字符串类型
    if not pd.api.types.is_string_dtype(series):
        series = series.astype(str)
    return series.str.len()

def op_trim(series: pd.Series, chars: Optional[str] = None) -> pd.Series:
    """
    Simulates SQL's TRIM function.
    Can trim specified characters or default whitespace.
    """
    if not pd.api.types.is_string_dtype(series):
        # Convert to string, coercing errors, and then fill potential NaNs for strip to work consistently
        series = series.astype(str)
    
    # Pandas .str.strip() handles NaN values gracefully by returning NaN.
    # It also takes an optional `chars` argument.
    return series.str.strip(chars)

def op_lower(series: pd.Series) -> pd.Series:
    # Pandas 的 .str.lower() 已经很好地处理了 NaN，会返回 NaN。
    if not pd.api.types.is_string_dtype(series):
        series = series.astype(str)
    return series.str.lower()


def op_strposition(substr: str, series: pd.Series) -> pd.Series:
    # Pandas 的 .str.find() 返回 0-based 索引，未找到返回 -1。
    # SQL 的 INSTR/POSITION 通常返回 1-based 索引，未找到返回 0。
    # 这里已处理 +1 转换为 1-based，未找到的 -1+1=0 也符合 SQL。
    if not pd.api.types.is_string_dtype(series):
        series = series.astype(str)
    return series.str.find(substr) + 1

def op_replace(series: pd.Series, old_str: str, new_str: str) -> pd.Series:
    # Pandas 的 .str.replace() 已经很好地处理了 NaN，会返回 NaN。
    # regex=False 确保按字面值匹配，而非正则表达式。
    if not pd.api.types.is_string_dtype(series):
        series = series.astype(str)
    return series.str.replace(old_str, new_str, regex=False)

# ==================== 类型转换函数 ====================
# 
def op_cast(series: pd.Series, target_type: str) -> pd.Series:
    """
    构建 CAST 类型转换函数。
    将 Pandas Series 转换为指定目标类型。
    
    参数:
        series (pd.Series): 要进行类型转换的 Pandas Series。
        target_type (str): 目标类型字符串，例如 'INT', 'FLOAT', 'DATE', 'STRING' 等。
                            支持常见的 SQL 类型映射到 Pandas 类型。
    
    返回:
        pd.Series: 转换类型后的新 Series。
    
    注意:
        对于无法成功转换的值，结果中可能会出现 NaN (对于数值类型) 或 NaT (对于日期时间类型)。
        此函数不捕获所有转换错误，对于无法处理的类型转换，错误可能会向上抛出。
    """
    pandas_type_map = {
        'INT': 'int64',
        'INTEGER': 'int64',
        'BIGINT': 'int64',
        'SMALLINT': 'int64',
        'TINYINT': 'int64',
        'FLOAT': 'float64',
        'REAL': 'float64',
        'DOUBLE': 'float64',
        'DECIMAL': 'float64',
        'NUMERIC': 'float64',
        'BOOLEAN': 'bool',
        'BOOL': 'bool',
        'DATE': 'datetime64[ns]',
        'DATETIME': 'datetime64[ns]',
        'TIMESTAMP': 'datetime64[ns]',
        'CHAR': 'object',
        'VARCHAR': 'object',
        'TEXT': 'object',
        'STRING': 'object'
    }
    
    pandas_type = pandas_type_map.get(target_type.upper(), 'object') # 默认为 'object' 类型

    if pandas_type == 'datetime64[ns]':
        result = pd.to_datetime(series, errors='coerce') # 强制转换失败时返回 NaT
    elif pandas_type == 'bool':
        # 注意：NaN 会转换为 True。如果需要 NaN 保持 NaN，可能需要更复杂逻辑或使用 bool_dtype
        result = series.astype(bool) 
    elif pandas_type in ['int64', 'float64']:
        result = pd.to_numeric(series, errors='coerce') # 强制转换失败时返回 NaN
        # 如果目标是 int64 且存在 NaN，则需要转换为可空整数类型
        if pandas_type == 'int64' and result.isna().any():
            result = result.astype(pd.Int64Dtype()) # Pandas 1.0+ 支持的可空整数类型
    else:
        result = series.astype(pandas_type) # 其他类型直接使用 astype 转换

    # 移除原先的 try...except Exception 块，让转换错误（如果未被 coerce 捕获）正常传播
    # 如果别名逻辑被移除了，这里也不再需要 result.rename(alias)
    
    return result



# ==================== 条件表达式 ====================
# 

def op_case(
    *when_then_pairs: Tuple[pd.Series, Union[Any, pd.Series]], # 条件必须是布尔型 Series
    else_value: Optional[Union[Any, pd.Series]] = None, # else_value 可以是标量或 Series
    index_source: Optional[pd.Index] = None # 显式传递一个共同的索引，如果 df 未使用
) -> pd.Series:
    """
    构建 CASE 条件表达式 - 根据多个 WHEN-THEN 条件选择结果。
    
    参数:
        *when_then_pairs: 可变参数，每个元素是一个元组 (when_condition_series, then_value)。
                          when_condition_series 必须是布尔型的 Pandas Series。
                          then_value 可以是标量或 Pandas Series。
        else_value (Optional[Union[Any, pd.Series]]): 当所有 WHEN 条件都不满足时返回的值。
                                                    可以是标量或 Pandas Series。
        index_source (Optional[pd.Index]): 用于初始化结果 Series 的索引。
                                            如果未提供，将从第一个 when_condition_series 推断。
                                            如果所有 when_then_pairs 都是空的，则此参数为必需。

    返回:
        pd.Series: 条件选择的结果 Series。
    
    注意:
        'when_condition_series' 必须是预先计算好的布尔型 Pandas Series (例如，通过 op_gt, op_eq 等)。
        此函数不直接解析字符串形式的 SQL 条件。
    """
    # 如果没有提供任何条件，且没有 else_value，则返回一个带有正确索引的空 Series
    if not when_then_pairs and else_value is None:
        if index_source is None:
            raise ValueError("op_case: 如果没有 'when_then_pairs' 且没有 'else_value'，则必须提供 'index_source' 以确定 Series 长度/索引。")
        return pd.Series(index=index_source, dtype='object') # 返回带有正确索引的空 Series

    # 确定结果 Series 的共同索引
    if index_source is None:
        if when_then_pairs:
            # 如果有条件 Series，则使用第一个条件 Series 的索引
            index_source = when_then_pairs[0][0].index
        else:
            raise ValueError("op_case: 如果没有 'when_then_pairs'，则必须提供 'index_source' 以确定 'else_value' 的 Series 长度/索引。")
    
    # 初始化结果 Series，填充 NaN（这些 NaN 将被覆盖）
    # 使用 object dtype 以适应混合类型（数字、字符串、布尔值）
    result_series = pd.Series(pd.NA, index=index_source, dtype='object') # 使用 pd.NA 更好地表示 NaN

    # 遍历每个 WHEN-THEN 对
    for when_condition_series, then_value in when_then_pairs:
        # 验证条件必须是布尔型 Series
        if not isinstance(when_condition_series, pd.Series) or not pd.api.types.is_bool_dtype(when_condition_series):
            raise TypeError(f"CASE WHEN 条件必须是布尔型 Pandas Series，但得到类型 {type(when_condition_series).__name__}。")
        
        # 将条件索引与结果 Series 索引对齐
        aligned_condition = when_condition_series.reindex(index_source, fill_value=False)
        
        # 应用 'THEN' 值，仅当条件为 True 且 result_series 对应位置仍为 NaN 时（未被赋值）
        # 这模仿了 SQL 的顺序评估：第一个匹配的条件胜出。
        # 使用 .loc 进行安全的布尔索引，并避免 SettingWithCopyWarning
        mask = aligned_condition & result_series.isna()
        if isinstance(then_value, pd.Series):
            # 如果 then_value 是 Series，则对齐其索引后赋值
            aligned_then_value = then_value.reindex(index_source)
            result_series.loc[mask] = aligned_then_value.loc[mask]
        else:
            # 标量 then_value
            result_series.loc[mask] = then_value
    
    # 对剩余的 NaN（即没有满足任何 'when' 条件的位置）应用 ELSE 值
    if else_value is not None:
        if isinstance(else_value, pd.Series):
            # 如果 else_value 是 Series，则对齐其索引
            aligned_else_value = else_value.reindex(index_source)
            result_series = result_series.fillna(aligned_else_value)
        else:
            # 标量 else_value
            result_series = result_series.fillna(else_value)
            
    return result_series

# 
def op_if(
    condition: pd.Series, # 条件必须是布尔型 Series
    true_value: Union[Any, pd.Series], # 可以是标量或 Series
    false_value: Union[Any, pd.Series], # 可以是标量或 Series
    index_source: pd.Index # 显式传递共同索引
) -> pd.Series:
    """
    构建 IF 条件表达式 - 如果条件为真返回 true_value，否则返回 false_value。
    
    参数:
        condition (pd.Series): 布尔型 Pandas Series，表示条件。
        true_value (Union[Any, pd.Series]): 当条件为 True 时返回的值，可以是标量或 Pandas Series。
        false_value (Union[Any, pd.Series]): 当条件为 False 时返回的值，可以是标量或 Pandas Series。
        index_source (pd.Index): 用于结果 Series 的索引，应与 condition 的索引一致。

    返回:
        pd.Series: 条件选择的结果 Series。
    
    注意:
        'condition' 必须是预先计算好的布尔型 Pandas Series。
        如果 'true_value' 或 'false_value' 是 Series，它们的索引应与 'condition' 对齐。
    """
    if not isinstance(condition, pd.Series) or not pd.api.types.is_bool_dtype(condition):
        raise TypeError(f"condition 参数必须是布尔型 Pandas Series，但得到类型 {type(condition).__name__}。")

    # 初始化结果 Series，使用合适的索引和 dtype（object 以保持灵活性）
    result_series = pd.Series(pd.NA, index=index_source, dtype='object')

    # 将条件索引与共同索引对齐
    aligned_condition = condition.reindex(index_source, fill_value=False) # fill_value=False 更安全

    # 处理 true_value
    if isinstance(true_value, pd.Series):
        aligned_true_value = true_value.reindex(index_source)
        result_series.loc[aligned_condition] = aligned_true_value.loc[aligned_condition]
    else:
        result_series.loc[aligned_condition] = true_value

    # 处理 false_value
    if isinstance(false_value, pd.Series):
        aligned_false_value = false_value.reindex(index_source)
        result_series.loc[~aligned_condition] = aligned_false_value.loc[~aligned_condition]
    else:
        result_series.loc[~aligned_condition] = false_value
        
    return result_series

def op_isin(series1: pd.Series, series2: pd.Series) -> pd.Series:
    """
    判断series1中的每个元素是否存在于series2中，等价于SQL的EXISTS/IN。
    """
    return series1.isin(series2)

# ==================== 辅助函数 ====================

def op_null_if(
    expr1: pd.Series, # 必须是 Pandas Series
    expr2: Union[Any, pd.Series] # 可以是任意标量（int, float, str等）或 Pandas Series
) -> pd.Series:
    """
    构建 NULLIF 函数。
    如果 expr1 等于 expr2，则返回 NULL (NaN)；否则返回 expr1。
    
    参数:
        expr1 (pd.Series): 第一个表达式，必须是一个 Pandas Series。
        expr2 (Union[Any, pd.Series]): 第二个表达式。
                                       可以是一个标量常数（如 int, float, str 等），
                                       或一个 Pandas Series。
                                       
    返回:
        pd.Series: 结果 Series。如果 expr1 == expr2，对应位置为 NaN。
    
    注意:
        由于移除了 'df' 参数，你必须在调用此函数之前，
        手动将 DataFrame 的列提取为 Pandas Series。
        例如：op_null_if(my_df['column_a'], my_df['column_b'])
        或：op_null_if(my_df['column_a'], 0)
    """
    # --- 处理 expr1 ---
    # expr1 强制要求为 pd.Series，所以直接使用
    series1 = expr1 
    
    # --- 处理 expr2 ---
    series2: pd.Series # 预定义 series2 的类型
    # 如果 expr2 是 Series
    if isinstance(expr2, pd.Series):
        series2 = expr2
    # 如果 expr2 是标量常数
    else:
        # 将标量常数扩展成与 series1 等长的 Series，确保索引一致
        series2 = pd.Series([expr2] * len(series1), index=series1.index)
    
    # --- 类型对齐进行比较 ---
    # 尽可能将 series2 转换为 series1 的数据类型，以便进行有意义的比较。
    # 模拟 SQL 的隐式类型转换行为。
    
    try:
        series2_aligned = series2.astype(series1.dtype, errors='ignore')

        if (pd.isna(series2_aligned).any() and not pd.isna(series2).any()) or \
           not (series1.dtype == series2_aligned.dtype or
                (pd.api.types.is_numeric_dtype(series1.dtype) and pd.api.types.is_numeric_dtype(series2_aligned.dtype))):
            
            if pd.api.types.is_numeric_dtype(series1.dtype) or pd.api.types.is_numeric_dtype(series2.dtype):
                series1 = pd.to_numeric(series1, errors='coerce')
                series2_aligned = pd.to_numeric(series2, errors='coerce')
            else:
                series1 = series1.astype(str).str.strip()
                series2_aligned = series2_aligned.astype(str).str.strip()

    except Exception:
        series1 = series1.astype(str).str.strip()
        series2_aligned = series2_aligned.astype(str).str.strip()

    # --- 执行 NULLIF 逻辑 ---
    return series1.where(series1 != series2_aligned)

def _parse_order_by_cols(order_by_cols_with_direction: Optional[List[Tuple[str, str]]]) -> Tuple[List[str], List[bool]]:
    """
    Parses a list of (column_name, direction) tuples for sorting.
    Returns a list of column names and a list of boolean (True for ASC, False for DESC)
    """
    order_cols = []
    ascending_flags = []

    if order_by_cols_with_direction is None:
        return [], []

    for col_info in order_by_cols_with_direction:
        if isinstance(col_info, tuple) and len(col_info) == 2:
            col_name, direction = col_info
            order_cols.append(col_name)
            ascending_flags.append(direction.upper() == 'ASC')
        elif isinstance(col_info, str):
            # Handle cases where just column name string is passed (default to ASC)
            # This makes it more flexible if you also use it for simple `ORDER BY col`
            order_cols.append(col_info)
            ascending_flags.append(True)
        else:
            raise ValueError(f"Invalid format for order_by_cols_with_direction: {col_info}. Expected (column_name, direction) tuple or column_name string.")

    return order_cols, ascending_flags

def op_row_number(
    df: pd.DataFrame,
    partition_by_cols: Optional[List[str]] = None,
    order_by_cols_with_direction: Optional[List[str]] = None,
) -> pd.Series:
    """
    构建 ROW_NUMBER() 窗口函数。
    为每个分区内的行返回唯一的连续数字。

    参数:
        df (pd.DataFrame): 输入的 DataFrame。
        partition_by_cols (Optional[List[str]]): 用于分区的列名列表。
        order_by_cols_with_direction (Optional[List[str]]): 用于排序的列名和方向列表，
                                                              例如 ['col1 ASC', 'col2 DESC']。

    返回:
        pd.Series: 包含行号的 Series。

    引发:
        ValueError: 如果未提供 ORDER BY 子句，因为 SQL ROW_NUMBER() 需要它。
    """
    if order_by_cols_with_direction is None:
        raise ValueError("ROW_NUMBER() 需要一个 ORDER BY 子句以确保确定性排序。")

    order_cols, ascending = _parse_order_by_cols(order_by_cols_with_direction)

    if partition_by_cols:
        # 有分区 (PARTITION BY)
        # 使用 apply 处理每个组，并在组内进行排序和排名
        def _rank_group(group_df_chunk: pd.DataFrame) -> pd.Series:
            # 按所有排序列对数据块进行排序，'stable' 保证相等元素顺序一致
            sorted_chunk = group_df_chunk.sort_values(by=order_cols, ascending=ascending, kind='stable')
            # 为排序后的块分配行号
            return pd.Series(range(1, len(sorted_chunk) + 1), index=sorted_chunk.index)
        
        # apply 会返回一个 Series，其索引包含原始 DataFrame 的索引
        # group_keys=False 阻止将分组键添加到结果 Series 的索引中
        result = df.groupby(partition_by_cols, group_keys=False).apply(_rank_group)
    else:
        # 无分区 (OVER ()) - 对整个 DataFrame 排名
        # 按所有排序列对整个 DataFrame 进行排序
        sorted_df = df.sort_values(by=order_cols, ascending=ascending, kind='stable')
        # 为排序后的 DataFrame 分配行号
        result = pd.Series(range(1, len(sorted_df) + 1), index=sorted_df.index)
    
    # 重新索引到原始 DataFrame 的顺序，以确保结果与原始 DataFrame 的行对齐
    return result.reindex(df.index)

def op_rank(
    df: pd.DataFrame,
    partition_by_cols: Optional[List[str]] = None,
    order_by_cols_with_direction: Optional[List[str]] = None,
) -> pd.Series:
    """
    构建 RANK() 窗口函数。
    返回其分区内每行的排名，相同的值有相同排名，但排名之间有间隙。

    参数:
        df (pd.DataFrame): 输入的 DataFrame。
        partition_by_cols (Optional[List[str]]): 用于分区的列名列表。
        order_by_cols_with_direction (Optional[List[str]]): 用于排序的列名和方向列表，
                                                              例如 ['col1 ASC', 'col2 DESC']。

    返回:
        pd.Series: 包含排名的 Series。

    引发:
        ValueError: 如果未提供 ORDER BY 子句。
    """
    if order_by_cols_with_direction is None:
        raise ValueError("RANK() 需要一个 ORDER BY 子句。")

    order_cols, ascending = _parse_order_by_cols(order_by_cols_with_direction)

    # Pandas 的 .rank() 方法可以直接处理多列排序，但要求这些列是 DataFrame 的列，而不是 GroupBy 对象直接提供 Series。
    # 这里我们模拟 SQL 的行为，即根据 `order_cols` 的顺序进行排名。
    # method='min' 对应 SQL 的 RANK()，相同值的排名取最小。

    if partition_by_cols:
        # 有分区 (PARTITION BY)
        # GroupBy.apply 会传递每个组的子 DataFrame
        def _rank_group(group_df_chunk: pd.DataFrame) -> pd.Series:
            # 确保在对 rank 传入的 Series 上调用 rank() 方法
            # 对于多列排序，rank() 会根据 sorted_chunk 的值进行排名
            # 这里我们取第一个排序的列作为 rank 的“目标”，但实际排序是基于所有列的
            # 更准确的 Pandas Rank() 实现通常是在排序后对索引进行操作，或利用 transform
            
            # 一个更直接的 Pandas 实现是：在排序后，对一个虚拟列（或第一个排序列）应用rank
            # 关键在于 `sort_values` 已经决定了顺序和平局分组
            sorted_chunk = group_df_chunk.sort_values(by=order_cols, ascending=ascending, kind='stable')
            # 对排序后的数据应用 rank，method='min' 模拟 RANK()
            # `rank` 需要作用于一个 Series，所以我们选择第一个排序列作为代表，
            # 但其实排名是根据整个排序顺序决定的
            # 这里的 rank 方法是基于 sorted_chunk 的值，而不是原始索引。
            # 为了确保结果的索引与原始组的索引一致，最后需要 reindex。
            result_rank_series = sorted_chunk[order_cols[0]].rank(method='min', ascending=ascending[0])
            return result_rank_series.reindex(group_df_chunk.index) # 重新索引到原始组的顺序
            
        result = df.groupby(partition_by_cols, group_keys=False).apply(_rank_group)
    else:
        # 无分区 (OVER ()) - 对整个 DataFrame 排名
        sorted_df = df.sort_values(by=order_cols, ascending=ascending, kind='stable')
        result = sorted_df[order_cols[0]].rank(method='min', ascending=ascending[0])
        result = result.reindex(df.index) # 重新索引到原始 DataFrame 的顺序
    
    return result
def op_dense_rank(
    df: pd.DataFrame,
    partition_by_cols: Optional[List[str]] = None,
    order_by_cols_with_direction: Optional[List[str]] = None,
) -> pd.Series:
    """
    构建 DENSE_RANK() 窗口函数。
    返回其分区内每行的密集排名，相同的值有相同排名，排名之间无间隙。

    参数:
        df (pd.DataFrame): 输入的 DataFrame。
        partition_by_cols (Optional[List[str]]): 用于分区的列名列表。
        order_by_cols_with_direction (Optional[List[str]]): 用于排序的列名和方向列表，
                                                              例如 ['col1 ASC', 'col2 DESC']。

    返回:
        pd.Series: 包含排名的 Series。

    引发:
        ValueError: 如果未提供 ORDER BY 子句。
    """
    if order_by_cols_with_direction is None:
        raise ValueError("DENSE_RANK() 需要一个 ORDER BY 子句。")

    order_cols, ascending = _parse_order_by_cols(order_by_cols_with_direction)

    if partition_by_cols:
        # 有分区 (PARTITION BY)
        def _rank_group(group_df_chunk: pd.DataFrame) -> pd.Series:
            sorted_chunk = group_df_chunk.sort_values(by=order_cols, ascending=ascending, kind='stable')
            result_rank_series = sorted_chunk[order_cols[0]].rank(method='dense', ascending=ascending[0])
            return result_rank_series.reindex(group_df_chunk.index)
            
        result = df.groupby(partition_by_cols, group_keys=False).apply(_rank_group)
    else:
        # 无分区 (OVER ()) - 对整个 DataFrame 排名
        sorted_df = df.sort_values(by=order_cols, ascending=ascending, kind='stable')
        result = sorted_df[order_cols[0]].rank(method='dense', ascending=ascending[0])
        result = result.reindex(df.index)
    
    return result




# ==================== 日期函数 ====================
def op_extract_date_part(
    part: str,
    date_input: Optional[Union[str, pd.Timestamp, pd.Series]] = None
) -> Union[int, datetime.date, pd.Series]:
    """
    从日期输入中提取特定的日期部分（年、月、日）。
    可以作用于单个日期（默认为当前时间）或 Pandas Series。

    参数:
        part (str): 要提取的日期部分（'year'、'month'、'day'、'curdate'）。
        date_input (Optional[Union[str, pd.Timestamp, pd.Series]]): 要从中提取的日期或 Series。
                                                                     如果为 None，则默认为当前时间戳。

    返回:
        Union[int, datetime.date, pd.Series]: 提取的日期部分。
                                               - 'curdate' 返回 datetime.date 类型。
                                               - 'year'、'month'、'day' 对于单个输入返回 int，对于 Series 输入返回 pd.Series。

    引发:
        ValueError: 如果请求了不支持的日期部分。
        TypeError: 如果 date_input 是字符串/Timestamp 但 part 不是 'curdate'，
                   或者如果 date_input 是 Series 但 part 是 'curdate'。
    """
    part_lower = part.lower()
    
    if date_input is None:
        # 如果未提供输入，默认为当前时间戳
        current_ts = pd.Timestamp.now()
        if part_lower == 'curdate':
            return current_ts.date() # 返回 datetime.date
        elif part_lower == 'year':
            return current_ts.year
        elif part_lower == 'month':
            return current_ts.month
        elif part_lower == 'day':
            return current_ts.day
        else:
            raise ValueError(f"当前日期不支持的日期部分: '{part}'。")

    # 处理 Series 输入
    if isinstance(date_input, pd.Series):
        if not pd.api.types.is_datetime64_any_dtype(date_input):
            date_input = pd.to_datetime(date_input, errors='coerce')

        if part_lower == 'year':
            return date_input.dt.year
        elif part_lower == 'month':
            return date_input.dt.month
        elif part_lower == 'day':
            return date_input.dt.day
        elif part_lower == 'curdate':
            # 不能为 Series 输入返回 datetime.date 对象，因此抛出错误或进行适配
            raise TypeError("不能为 Series 输入提取 'curdate'。请改用 'day'、'month'、'year'。")
        else:
            raise ValueError(f"Series 输入不支持的日期部分: '{part}'。")

    # 处理单个日期输入（字符串或 Timestamp）
    if isinstance(date_input, (str, pd.Timestamp)):
        single_ts = pd.to_datetime(date_input, errors='coerce')
        if single_ts is pd.NaT: # pd.NaT 表示 Not a Time，即转换失败
            raise ValueError(f"无法将 '{date_input}' 转换为有效日期。")

        if part_lower == 'curdate':
            return single_ts.date() # 返回 datetime.date
        elif part_lower == 'year':
            return single_ts.year
        elif part_lower == 'month':
            return single_ts.month
        elif part_lower == 'day':
            return single_ts.day
        else:
            raise ValueError(f"单个日期输入不支持的日期部分: '{part}'。")
    
    raise TypeError("date_input 的类型无效。必须是 str、pd.Timestamp 或 pd.Series。")


def op_to_datetime(date_input: Union[str, pd.Series], errors: str = 'coerce') -> pd.Series:
    """
    将字符串或Pandas Series转换为datetime64[ns]类型。

    参数:
        date_input (Union[str, pd.Series]): 要转换的日期字符串或包含日期字符串的Series。
        errors (str): 指定如何处理解析错误。
                      'coerce': 无效日期解析时设为NaT（Not a Time）。
                      'ignore': 无效日期解析时返回原始输入。

    返回:
        pd.Series: 包含datetime64[ns]日期时间的Series。
    """
    return pd.to_datetime(date_input, errors=errors)




def op_julianday(series: Union[pd.Series, pd.Timestamp, datetime.date]) -> pd.Series:
    """
    计算序列中每个日期时间的儒略日（Julian day number）。
    儒略日0是公元前4713年1月1日中午，儒略历。
    （这通常用于天文学）。SQLite的 %J 也表示儒略日。
    Pandas 的 to_julian_date() 返回类似的结果，但以午夜而非中午为基准。
    对于日期之间的“差值”而言，只要保持一致性，这种差异并不重要。
    """
    if isinstance(series, (pd.Timestamp, datetime.date)):
        series = pd.Series([series])

    dt_series = pd.to_datetime(series, errors='coerce')
    # 使用列表推导式逐行应用 to_julian_date()
    # 处理 NaT 值 (Not a Time)
    return pd.Series([ts.to_julian_date() if pd.notna(ts) else np.nan for ts in dt_series], index=dt_series.index)

def op_timestamp_diff(
    unit: str,
    start_series: pd.Series,
    end_series: pd.Series
) -> pd.Series:
    """
    计算两个 datetime Series 之间以指定单位表示的差异。
    类似于 SQL 的 TIMESTAMPDIFF。

    参数:
        unit (str): 差异的单位（'year'、'month'、'day'、'hour'、'minute'、'second'）。
        start_series (pd.Series): 开始 datetime Series。
        end_series (pd.Series): 结束 datetime Series。

    返回:
        pd.Series: 以指定单位表示的差异 Series（浮点数）。

    引发:
        ValueError: 如果提供了不支持的单位。

    注意:
        'year' 和 'month' 的计算是基于平均天数的近似值。
        对于精确的日历感知差异，可能需要自定义逻辑。
    """
    # 确保 Series 是 datetime 类型
    if not pd.api.types.is_datetime64_any_dtype(start_series):
        start_series = pd.to_datetime(start_series, errors='coerce')
    if not pd.api.types.is_datetime64_any_dtype(end_series):
        end_series = pd.to_datetime(end_series, errors='coerce')

    # 计算时间差 Series
    diff = end_series - start_series

    unit_lower = unit.lower()

    if unit_lower == 'year':
        # 近似值：总秒数除以一年中的平均秒数
        return diff.dt.total_seconds() / (365.25 * 24 * 3600)
    elif unit_lower == 'month':
        # 近似值：总秒数除以一月中的平均秒数
        return diff.dt.total_seconds() / (30.44 * 24 * 3600)
    elif unit_lower == 'day':
        return diff.dt.total_seconds() / (24 * 3600)
    elif unit_lower == 'hour':
        return diff.dt.total_seconds() / 3600
    elif unit_lower == 'minute':
        return diff.dt.total_seconds() / 60
    elif unit_lower == 'second':
        return diff.dt.total_seconds()
    else:
        raise ValueError(f"op_timestamp_diff 不支持的单位: '{unit}'。 "
                         "支持的单位有 'year'、'month'、'day'、'hour'、'minute'、'second'。")

def op_date_diff(
    unit: str,
    start_series: pd.Series,
    end_series: pd.Series,
    as_integer: bool = True # 新参数，用于控制是否返回整数结果
) -> pd.Series:
    """
    计算两个 datetime Series 之间以指定单位表示的差异，
    通常返回一个整数。类似于 SQL 的 DATEDIFF。

    参数:
        unit (str): 差异的单位（'day'、'hour'、'minute'、'second'）。
        start_series (pd.Series): 开始 datetime Series。
        end_series (pd.Series): 结束 datetime Series。
        as_integer (bool): 如果为 True（默认），则返回整数（向下取整）。
                           如果为 False，则返回浮点数。

    返回:
        pd.Series: 以指定单位表示的差异 Series。

    引发:
        ValueError: 如果提供了不支持的单位。
    """
    # 确保 Series 是 datetime 类型
    if not pd.api.types.is_datetime64_any_dtype(start_series):
        start_series = pd.to_datetime(start_series, errors='coerce')
    if not pd.api.types.is_datetime64_any_dtype(end_series):
        end_series = pd.to_datetime(end_series, errors='coerce')

    diff = end_series - start_series
    unit_lower = unit.lower()
    
    result_series: pd.Series # 中间结果的类型提示

    if unit_lower == 'day':
        result_series = diff.dt.total_seconds() / (24 * 3600)
    elif unit_lower == 'hour':
        result_series = diff.dt.total_seconds() / 3600
    elif unit_lower == 'minute':
        result_series = diff.dt.total_seconds() / 60
    elif unit_lower == 'second':
        result_series = diff.dt.total_seconds()
    else:
        raise ValueError(f"op_date_diff 不支持的单位: '{unit}'。 "
                         "支持的单位有 'day'、'hour'、'minute'、'second'。")

    if as_integer:
        # SQL DATEDIFF 通常返回整数，因此我们对结果进行向下取整
        return result_series.astype(int)
    else:
        return result_series

def op_timetostr(datetime_input: Union[pd.Series, pd.Timestamp, datetime.date], format_string: str) -> pd.Series:
    """
    根据格式字符串将日期时间序列或标量转换为字符串。
    模拟 SQLite 的 strftime 函数。

    Args:
        datetime_input: pandas 日期时间序列或单个日期时间对象。
        format_string: 表示所需格式的字符串。
                       支持的类 SQLite 格式：
                       '%Y': 年份 (YYYY)
                       '%m': 月份 (01-12)
                       '%d': 月份中的日期 (01-31)
                       '%H': 小时 (00-23)
                       '%M': 分钟 (00-59)
                       '%S': 秒 (00-59)
                       '%f': 毫秒 (000-999)
                       '%J': 儒略日数字（例如，2459787.5）- 这是导致您错误的根本原因。
                       '%w': 星期几 (0-6，星期日是 0)
                       '%W': 一年中的周数 (00-53)，周一作为一周的第一天。
                       '%j': 一年中的日期 (001-366)
    """
    # 确保 datetime_input 是一个 Series
    if isinstance(datetime_input, (pd.Timestamp, datetime.date)):
        series_to_format = pd.Series([datetime_input])
    else:
        series_to_format = datetime_input

    # 如果不是日期时间对象，则转换为日期时间对象
    series_to_format = pd.to_datetime(series_to_format, errors='coerce')

    if format_string == '%J':
        # 对于 '%J'，返回儒略日*数字*（浮点数），而不是字符串。
        # SQL 查询中需要这些数字进行数值减法。
        return op_julianday(series_to_format)
    elif format_string == '%Y':
        return series_to_format.dt.strftime('%Y')
    elif format_string == '%m':
        return series_to_format.dt.strftime('%m')
    elif format_string == '%d':
        return series_to_format.dt.strftime('%d')
    elif format_string == '%H':
        return series_to_format.dt.strftime('%H')
    elif format_string == '%M':
        return series_to_format.dt.strftime('%M')
    elif format_string == '%S':
        return series_to_format.dt.strftime('%S')
    elif format_string == '%f':
        # 毫秒 (000-999)
        return series_to_format.dt.strftime('%f').str[:3]
    elif format_string == '%w':
        # 星期几 (0=星期日, 6=星期六)
        # 调整以匹配 SQLite 的星期日=0
        return series_to_format.dt.dayofweek.replace({6:0, 0:1, 1:2, 2:3, 3:4, 4:5, 5:6})
    elif format_string == '%W':
        # 一年中的周数 (00-53)，周一作为一周的第一天。
        # Pandas 的 weekofyear 是 1-53，基于周一。如果只是格式化为两位数则匹配。
        return series_to_format.dt.isocalendar().week.astype(str).str.zfill(2)
    elif format_string == '%j':
        # 一年中的日期 (001-366)
        return series_to_format.dt.dayofyear.astype(str).str.zfill(3)
    else:
        # 对于其他格式，尝试直接使用 strftime（可能不完全兼容所有 SQLite 格式）
        return series_to_format.dt.strftime(format_string)
    
def op_time_extract(
    time_input: Union[str, pd.Timestamp, pd.Series]
) -> Union[datetime.time, pd.Series]:
    """
    从日期/时间输入中提取时间部分（时、分、秒）。
    可以作用于单个日期/时间或 Pandas Series。

    参数:
        time_input (Union[str, pd.Timestamp, pd.Series]): 要从中提取时间的日期/时间或 Series。

    返回:
        Union[datetime.time, pd.Series]: 提取的时间部分。
                                         - 对于单个输入，返回 datetime.time。
                                         - 对于 Series 输入，返回 pd.Series 的 datetime.time 对象。

    引发:
        ValueError: 如果输入无法转换为有效日期时间。
        TypeError: 如果输入类型不受支持。
    """
    # 处理 Series 输入
    if isinstance(time_input, pd.Series):
        if not pd.api.types.is_datetime64_any_dtype(time_input):
            time_input = pd.to_datetime(time_input, errors='coerce')
        return time_input.dt.time # 返回 datetime.time 对象的 Series

    # 处理单个字符串或 Timestamp 输入
    if isinstance(time_input, (str, pd.Timestamp)):
        single_ts = pd.to_datetime(time_input, errors='coerce')
        if single_ts is pd.NaT:
            raise ValueError(f"无法将 '{time_input}' 转换为有效日期/时间。")
        return single_ts.time() # 返回 datetime.time 对象
    
    raise TypeError("time_input 的类型无效。必须是 str、pd.Timestamp 或 pd.Series。")


def op_current_timestamp() -> pd.Timestamp:
    """构建CURRENT_TIMESTAMP函数 - """
    # pandas模式：返回当前时间戳
    return pd.Timestamp.now()

def op_date(series: pd.Series) -> pd.Series:
    """
    模拟 SQL 的 date() 函数：将 datetime/timestamp 字段转为日期字符串。
    返回格式为 'YYYY-MM-DD' 的字符串 Series。
    """
    return pd.to_datetime(series, errors='coerce').dt.strftime('%Y-%m-%d')

# ==================== 数学函数 ====================

def op_round(series: pd.Series, decimals: int = 0) -> pd.Series:
    """构建ROUND函数 - 只接受Series"""
    return series.round(decimals)

def op_abs(series: pd.Series) -> pd.Series:
    """构建ABS函数 - 只接受Series"""
    return series.abs()


# ==================== 聚合函数扩展 ====================
def _group_concat_agg_func(
    series: pd.Series,
    separator: str,
    distinct: bool,
    order_by_col: Optional[str] = None,
    group_df: Optional[pd.DataFrame] = None # 类型提示可以改为 pd.DataFrame，因为在 apply 中它不会是 None
) -> str:
    """
    内部聚合逻辑，用于 GroupBy.apply。
    series: 当前组的目标列 Series。
    separator: 连接字符串的分隔符。
    distinct: 是否只连接唯一值。
    order_by_col: 用于组内排序的列名。
    group_df: 当前组的完整 DataFrame，用于访问 order_by_col。
    """
    current_values = series.dropna()

    # 步骤 2: 处理排序
    if order_by_col and group_df is not None:
        # 确保 order_by_col 存在于当前组的 DataFrame 中
        if order_by_col not in group_df.columns:
            # 这种情况不应该发生，因为外层 op_group_concat 应该已经检查过
            # 但作为内部函数，我们可以选择抛出错误或跳过排序
            # 这里选择跳过排序，并可能记录一个警告
            # print(f"警告: 排序字段 '{order_by_col}' 在当前组中不存在，跳过排序。")
            pass # 保持原 series 的自然顺序
        else:
            # 创建一个临时 DataFrame，包含目标列和排序列
            temp_df = pd.DataFrame({
                '__target_col__': current_values,
                '__order_col__': group_df.loc[current_values.index, order_by_col] # 确保索引对齐
            })
            # 按排序列进行排序
            temp_df = temp_df.sort_values(by='__order_col__', kind='mergesort')
            # 更新 current_values 为排序后的目标列 Series
            current_values = temp_df['__target_col__']

    values_to_process = series.dropna()

    # 2. 如果 distinct 为 True，先进行去重
    if distinct:
        values_to_process = values_to_process.drop_duplicates()

    # 3. 如果 order_by_col 存在且 group_df 也存在，则进行排序
    if order_by_col and group_df is not None and order_by_col in group_df.columns:
        # 获取用于排序的列的值，并确保其索引与 values_to_process 对齐
        order_values_for_current_group = group_df.loc[values_to_process.index, order_by_col]
        
        # 将目标值和排序值打包成一个 DataFrame 进行排序
        temp_df = pd.DataFrame({
            '__target_col__': values_to_process,
            '__order_col__': order_values_for_current_group
        })
        temp_df = temp_df.sort_values(by='__order_col__', kind='mergesort')
        values_to_process = temp_df['__target_col__']
    
    # 步骤 4: 连接所有值
    # 确保所有值都转换为字符串，然后使用分隔符连接
    return separator.join(values_to_process.astype(str))


def op_group_concat(col: str, df: Union[pd.DataFrame, pd.core.groupby.DataFrameGroupBy], separator: str = ",", distinct: bool = False, order_by: Optional[str] = None) -> Union[pd.Series, str]:
    """
    构建 GROUP_CONCAT 函数 - 可以在 DataFrame 上直接调用（连接整个列），
    或在 GroupBy 对象上作为聚合函数（在每个组内连接）。

    参数:
        col (str): 要连接的列名。
        df (Union[pd.DataFrame, pd.core.groupby.DataFrameGroupBy]): 输入的 DataFrame 或 GroupBy 对象。
        separator (str): 连接字符串的分隔符。
        distinct (bool): 是否只连接唯一值。
        order_by (Optional[str]): 如果提供，则按此列进行排序后再连接。
                                   如果 df 是 GroupBy 对象，则在每个组内排序。
    返回:
        Union[pd.Series, str]: 如果是 GroupBy 对象，返回一个 Series (每个组一个连接字符串)；
                               如果是 DataFrame，返回一个单一连接字符串。
    """
    if isinstance(df, pd.core.groupby.DataFrameGroupBy):
        # 作为 GroupBy 聚合函数
        # GroupBy.apply 传递的是每个组的 DataFrame，我们可以用它来访问 order_by 列
        return df.apply(lambda x: _group_concat_agg_func(x[col], separator, distinct, order_by, x))
    
    elif isinstance(df, pd.DataFrame):
        # 作为非聚合函数（连接整个 DataFrame 的一列）
        return _group_concat_agg_func(df[col], separator, distinct, order_by, df)
    
    else:
        raise TypeError("df 必须是 Pandas DataFrame 或 GroupBy 对象。")



# ==================== 集合操作 ====================


def op_union(df1: pd.DataFrame, df2: pd.DataFrame, keep_duplicates: bool = False) -> pd.DataFrame:
    """
    构建 UNION 操作。
    
    参数:
        df1 (pd.DataFrame): 第一个 DataFrame。
        df2 (pd.DataFrame): 第二个 DataFrame。
        keep_duplicates (bool): 如果为 True，则模仿 UNION ALL（保留所有行，包括重复行）；
                                 如果为 False（默认），则模仿 UNION（去除重复行）。
    
    返回:
        pd.DataFrame: 合并后的 DataFrame。
    
    注意:
        为了准确模拟 SQL 的 UNION，请确保 df1 和 df2 具有相同的列名和数据类型，
        且列的顺序一致。Pandas 的 concat 在列名不一致时会进行外连接并填充 NaN。
    """
    if keep_duplicates:
        # UNION ALL：保留所有行，包括重复行
        return pd.concat([df1, df2], ignore_index=True)
    else:
        # UNION：去除重复行
        return pd.concat([df1, df2], ignore_index=True).drop_duplicates()
    
def op_except(df1: pd.DataFrame, df2: pd.DataFrame) -> pd.DataFrame:
    """
    构建 EXCEPT 操作（SQL 标准）。
    返回 df1 中存在但 df2 中不存在的行（去除重复行）。
    
    参数:
        df1 (pd.DataFrame): 第一个 DataFrame。
        df2 (pd.DataFrame): 第二个 DataFrame。
    
    返回:
        pd.DataFrame: 差集 DataFrame。
    
    注意:
        为了准确模拟 SQL 的 EXCEPT，请确保 df1 和 df2 具有相同的列名和数据类型，
        且列的顺序一致。否则，合并操作可能不会按预期工作。
    """
    # 使用 merge 来模拟 EXCEPT 操作：
    # 进行左连接，并使用 indicator=True 来标记行的来源
    merged = df1.merge(df2, how='left', indicator=True)
    
    # 筛选出只存在于左表（df1）中的行，并去除用于指示来源的 '_merge' 列
    # SQL 的 EXCEPT 默认会去除结果中的重复行，所以这里无需额外 drop_duplicates()
    return merged[merged['_merge'] == 'left_only'].drop(columns=['_merge'])



# ==================== SQL子句构建器 ====================

def build_join(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    join_type: str = 'inner',
    left_on: Optional[Union[str, List[str]]] = None,
    right_on: Optional[Union[str, List[str]]] = None,
    # 修改参数名为更清晰的名称
    suffix_for_overlap_cols: Tuple[str, str] = ('_x', '_y') 
) -> pd.DataFrame:
    """
    构建 JOIN 操作 - 合并两个 DataFrame。

    参数:
        df1 (pd.DataFrame): 左边的 DataFrame。
        df2 (pd.DataFrame): 右边的 DataFrame。
        join_type (str): 连接类型，如 'inner', 'left', 'right', 'outer'。
        left_on (Optional[Union[str, List[str]]]): 左边 DataFrame 的连接键。
        right_on (Optional[Union[str, List[str]]]): 右边 DataFrame 的连接键。
        suffix_for_overlap_cols (Tuple[str, str]): 当合并后出现同名且非连接键的列时，Pandas 默认添加的后缀。

    返回:
        pd.DataFrame: 合并后的 DataFrame。

    注意:
        如果 left_on 和 right_on 指定的列名不同，Pandas 将在结果中保留这两个列。
        只有当存在**其他同名但非连接键**的列时，才会应用 `suffix_for_overlap_cols`。
    """
    join_type_lower = join_type.lower()
    
    # 执行合并。始终传递 suffixes 以进行明确控制。
    # Pandas 会根据列重叠情况决定是否实际使用它们。
    if left_on is not None or right_on is not None:
        result = df1.merge(df2, how=join_type_lower, left_on=left_on, right_on=right_on, suffixes=suffix_for_overlap_cols)
    else:
        # 如果没有指定连接键，Pandas 会尝试基于共同列名进行连接
        result = df1.merge(df2, how=join_type_lower, suffixes=suffix_for_overlap_cols)
    
    return result


def build_group_by(df: pd.DataFrame,*columns: str) -> pd.core.groupby.DataFrameGroupBy:
    """
    构建 GROUP BY 子句。
    
    参数:
        *columns (str): 用于分组的一个或多个列名。
        df (pd.DataFrame): 输入的 DataFrame。
    
    返回:
        pandas.core.groupby.DataFrameGroupBy: 分组后的 GroupBy 对象。
                                               该对象支持进一步的聚合操作。
    注意:
        与 SQL 的 GROUP BY 类似，Pandas 默认会将分组键作为结果的索引。
        'dropna=False' 参数模仿了 SQL 中将 NULL 视为一个独立组的行为。
    """
    # 返回分组后的 DataFrameGroupBy 对象，保留 NaN 值作为单独的组
    return df.groupby(list(columns), dropna=False)

def op_merge(df1: pd.DataFrame, df2: pd.DataFrame, on: list, how: str = 'inner') -> pd.DataFrame:
    """
    合并两个DataFrame，支持指定on和how参数，默认inner join。
    :param df1: 左表
    :param df2: 右表
    :param on: 连接键（列名列表）
    :param how: 连接方式，'inner'/'outer'/'left'/'right'
    :return: 合并后的DataFrame
    """
    return pd.merge(df1, df2, on=on, how=how)

@snoop
def build_order_by(
    df: pd.DataFrame,
    *columns_with_direction: Union[Tuple[str, str], str],
) ->  pd.DataFrame:
    """
    columns_with_direction: 例如 ('Price', 'desc'), ('A', 'asc')，也可以只写 'col' 或 ('col',)，默认升序
    用法示例：
        build_order_by(('Enrollment (K-12)', 'DESC'), 'date', df=df_frpm)
    """
    columns = []
    ascending = []
    for item in columns_with_direction:
        if isinstance(item, tuple):
            col = item[0]
            direction = item[1] if len(item) > 1 else 'ASC'
        else:
            col = item
            direction = 'ASC'
        columns.append(col)
        ascending.append(direction.lower() != 'desc')
    return df.sort_values(columns, ascending=ascending)

def build_limit(df: pd.DataFrame, count: int, offset: int = 0) -> pd.DataFrame:
    return df.iloc[offset : offset + count]



# def build_select(*columns_or_expressions: str, distinct: bool = False, df: Optional[pd.DataFrame] = None) -> Union[str, pd.DataFrame]:
#     """构建SELECT子句 - """
#      # 兼容传入单个list/tuple的情况
#     if len(columns_or_expressions) == 1 and isinstance(columns_or_expressions[0], (list, tuple)):
#         columns = list(columns_or_expressions[0])
#     else:
#         columns = list(columns_or_expressions)
#     # pandas模式：返回选择的列
#     if distinct:
#         return df[columns].drop_duplicates()
#     else:
#         return df[columns]

# def build_where(condition: str, df: Optional[pd.DataFrame] = None) -> Union[str, pd.DataFrame]:
#     """构建WHERE子句 - """
#      # pandas模式：这里需要解析condition字符串并应用到DataFrame
#     # 注意：这是一个简化实现，实际使用时需要更复杂的条件解析
#     # 建议直接使用pandas的布尔索引
#     raise NotImplementedError("在pandas模式下，建议直接使用DataFrame的布尔索引而不是build_where")

# def build_having(condition: str, df: Optional[pd.DataFrame] = None) -> Union[str, pd.DataFrame]:
#     """构建HAVING子句 - """
#     # pandas模式：这里需要解析condition字符串并应用到分组后的DataFrame
#     # 注意：这是一个简化实现，实际使用时需要更复杂的条件解析
#     raise NotImplementedError("在pandas模式下，建议直接使用groupby后的filter方法而不是build_having")


# def get_table_column(table_alias: str, column_name: str) -> str:
#     """组合表别名和列名"""
#     return f"{table_alias}.{quote_identifier(column_name)}"

# def quote_literal(value: Union[str, int, float]) -> str:
#     """为字面量添加引号"""
#     if isinstance(value, (int, float)):
#         return str(value)
#     else:
#         return f"'{value}'"

# def is_column_reference(value: str) -> bool:
#     """判断是否是列引用（包含点号或反引号）"""
#     return '.' in value or '`' in value

# --- 窗口函数 (OVER 子句的构建) ---

# def build_window_spec(
#     partition_by_cols: Optional[List[str]] = None,
#     order_by_cols_with_direction: Optional[List[Union[str, tuple]]] = None
# ) -> str:
#     """构建通用的OVER子句内容 (PARTITION BY ... ORDER BY ...)"""
#     parts = []
#     if partition_by_cols:
#         quoted_partition_cols = [quote_identifier(col.split('.')[-1]) if '.' in col else quote_identifier(col) for col in partition_by_cols]
#         parts.append(f"PARTITION BY {', '.join(quoted_partition_cols)}")
#     if order_by_cols_with_direction:
#         # 处理order_by_cols_with_direction，支持两种格式：
#         # 1. 字符串列表: ["col_name ASC", "col_name DESC"]
#         # 2. 元组列表: [("col_name", "ASC"), ("col_name", "DESC")]
#         formatted_order_by = []
#         for item in order_by_cols_with_direction:
#             if isinstance(item, tuple):
#                 # 元组格式: (col_name, direction)
#                 col_name, direction = item
#                 formatted_order_by.append(f"{quote_identifier(col_name)} {direction.upper()}")
#             else:
#                 # 字符串格式: "col_name ASC" 或 "col_name DESC"
#                 formatted_order_by.append(item)
#         parts.append(f"ORDER BY {', '.join(formatted_order_by)}")
    
#     if not parts:
#         return "" # Empty OVER() is also possible for some functions
#     return f"OVER ({' '.join(parts)})"
# def quote_identifier(name: str) -> str:
#     """为SQL标识符添加反引号"""
#     # 如果已经包含反引号，直接返回
#     if name.startswith('`') and name.endswith('`'):
#         return name
#     return f"`{name}`"
# def column_rename(expr: str, alias: str, df: Optional[pd.DataFrame] = None, keep_origin: bool = False):
#     if keep_origin:
#         df = df.copy()
#         df[alias] = df[expr]
#         return df
#     else:
#         return df.rename(columns={expr: alias})


# def table_rename(table_name: str, alias: str, df: Optional[pd.DataFrame] = None) -> Union[str, pd.DataFrame]:
#     """表重命名，"""
#      # pandas模式：DataFrame没有表别名，直接返回原DataFrame
    
#     return df
