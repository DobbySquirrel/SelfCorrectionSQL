import json
import pandas as pd
import io
import sqlite3
import numpy as np # Used for handling NaN values

def db_loader(db_name, table_name):
    """
    Simulates loading data for a given table from a database.
    Replace this with your actual database loading logic.
    """
    db_path = f"/home/shenshuyu/SQL_tool/data/dev_databases/{db_name}/{db_name}.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table_name} LIMIT 100;", conn)
        return df
    finally:
        conn.close()

def get_table_info_string(df):
    """
    Retrieves DataFrame structure information concisely.
    Format: 'col_name(type,NN:count,S:value); col_name(type,NN:count,S:value)'
    """
    if df.empty:
        return "Empty"

    column_info_parts = []
    for col_name in df.columns:
        dtype = str(df[col_name].dtype)
        non_null_count = df[col_name].count()
        sample_values = df[col_name].dropna().head(1).tolist()
        samples_str = str(sample_values) # e.g., '[1]' or "['Text']"

        # Concise format for each column
        column_info_parts.append(
            f"{col_name}({dtype},NN:{non_null_count},S:{samples_str})"
        )
    
    return ";".join(column_info_parts) # Join columns within a table with a semicolon


def get_tables_from_db(db_name):
    """Retrieves all table names from a given database."""
    db_path = f"/home/shenshuyu/SQL_tool/data/dev_databases/{db_name}/{db_name}.sqlite"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [table[0] for table in cursor.fetchall()]
        conn.close()
        return tables
    except Exception as e:
        print(f"Error: Could not retrieve table names for database {db_name}: {str(e)}")
        return []

# --- Main execution part ---

with open('/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

dflist = {}
db_names = set(entry.get('db') for entry in data if entry.get('db'))

for db_name in db_names:
    table_names = get_tables_from_db(db_name)
    db_info_fluent_strings = []
    
    for table_name in table_names:
        try:
            df = db_loader(db_name, table_name)
            table_info_str = get_table_info_string(df)
            # Concise format for each table: 'table_name:col_info;col_info'
            db_info_fluent_strings.append(f"{table_name}:{table_info_str}")
        except Exception as e:
            db_info_fluent_strings.append(f"{table_name}:Error loading({str(e)})")

    # Join all table fluent strings for this database with a pipe '|'
    dflist[db_name] = "|".join(db_info_fluent_strings)

with open('/home/shenshuyu/SQL_tool/data/table_info_string.json', 'w', encoding='utf-8') as f:
    json.dump(dflist, f, ensure_ascii=False, indent=2)

with open('/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json', 'r', encoding='utf-8') as f:
    subset_data = json.load(f)

for item in subset_data:
    if isinstance(item, dict) and 'db' in item:
        db_name = item['db']
        if db_name in dflist:
            item['df_list'] = dflist[db_name]

with open('/home/shenshuyu/SQL_tool/data/subset_ppl_dev_python.json', 'w', encoding='utf-8') as f:
    json.dump(subset_data, f, ensure_ascii=False, indent=2)

print("Table structure information saved to table_info_string.json")
print("Updated subset_ppl_dev_python.json with matched df_list fields in highly concise fluent string format")