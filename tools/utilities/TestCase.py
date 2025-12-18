import pandas as pd
import snoop
import sqlite3


@snoop
def get_table_names(conn):
    """获取数据库中所有表名"""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    return cursor.fetchall()


@snoop
def get_table_columns(conn, table_name):
    """获取指定表的所有列名"""
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name});")
    return cursor.fetchall()


@snoop
def get_special_notes_pandas():
    """获取青少年生育率最高国家的特殊说明"""
    conn = sqlite3.connect('/home/shenshuyu/SQL_tool/work/bird/train/train_databases/world_development_indicators/world_development_indicators.sqlite')
    
    # 首先探索数据库结构
    tables = get_table_names(conn)
    print("数据库中的表:", tables)
    
    # 假设我们找到了相关的表，现在执行主查询
    query = """
    WITH avg_fertility AS (
        SELECT 
            CountryName,
            AVG(value) as avg_rate
        FROM Indicators
        WHERE IndicatorName LIKE 'adolescent fertility rate%'
        GROUP BY CountryName
    )
    SELECT 
        i.CountryName,
        i.value as fertility_rate,
        i.SpecialNotes
    FROM Indicators i
    JOIN avg_fertility af ON i.CountryName = af.CountryName
    WHERE i.IndicatorName LIKE 'adolescent fertility rate%'
    AND af.avg_rate = (SELECT MAX(avg_rate) FROM avg_fertility)
    LIMIT 1;
    """
    
    result = pd.read_sql_query(query, conn)
    conn.close()
    return result


if __name__ == "__main__":
    # 执行SQL查询
    conn = sqlite3.connect('/home/shenshuyu/SQL_tool/work/bird/train/train_databases/world_development_indicators/world_development_indicators.sqlite')
    
    # 获取结果
    result = get_special_notes_pandas()
    print("\n查询结果:")
    print(result)
   