from pathlib import Path
import os
import json
from typing import List, Dict, Set, Tuple
from collections import defaultdict
import sqlite3
import sys

# Add the cscsql project's source directory to the Python path
sys.path.append('/home/shenshuyu/SQL_tool/csc_sql/src')

# Import the actual classes from your cscsql module
from cscsql.utils.sqlite_db_utils import SqliteDbUtils
from cscsql.utils.chess_sql_parser import ChessSchemaParser
from cscsql.utils.logger_utils import logger

class SchemaConflictDetector:
    @staticmethod
    def identify_column_conflicts(db_path: str, sql: str) -> Dict[str, List[str]]:
        """
        识别 SQL 查询中涉及的表之间存在的重复列名。

        Args:
            db_path (str): SQLite 数据库文件的路径。
            sql (str): 要分析的 SQL 查询字符串。

        Returns:
            Dict[str, List[str]]: 一个字典，键是冲突的列名（原始大小写），
                                  值是包含该冲突列的表名列表。
                                  如果没有任何冲突，返回空字典。
        """
        conflicting_columns = defaultdict(list)

        try:
            # 1. 提取 SQL 中涉及的所有表名
            involved_tables = ChessSchemaParser.get_sql_tables(db_path=db_path, sql=sql)
            if not involved_tables:
                logger.info(f"SQL '{sql[:50]}...' 中未找到表。")
                return {}

            # 2. 获取这些表的完整模式信息 (所有列)
            try:
                conn = SqliteDbUtils.get_cursor_from_path(db_path)
            except sqlite3.OperationalError:
                logger.error(f"无法打开数据库 '{db_path}'。跳过列冲突识别。")
                return {}

            full_schema_for_involved_tables = {}
            for table_name in involved_tables:
                cols = SqliteDbUtils.get_table_column_names(conn, table_name)
                full_schema_for_involved_tables[table_name] = cols
            conn.close()

            # 3. 识别重名列
            column_occurrences = defaultdict(list)
            original_column_names = {}

            for table_name, columns in full_schema_for_involved_tables.items():
                for col_name in columns:
                    lower_col_name = col_name.lower()
                    column_occurrences[lower_col_name].append(table_name)
                    if lower_col_name not in original_column_names:
                        original_column_names[lower_col_name] = col_name

            for lower_col_name, tables_list in column_occurrences.items():
                if len(tables_list) > 1:
                    original_col_name_for_conflict = original_column_names[lower_col_name]
                    conflicting_columns[original_col_name_for_conflict] = sorted(tables_list)

        except Exception as e:
            logger.error(f"识别 SQL 冲突时出错: SQL: '{sql[:50]}...' - 错误: {e}")
            return {}

        return dict(conflicting_columns)

    @staticmethod
    def get_involved_tables_sizes(db_path: str, sql: str) -> Dict[str, Tuple[int, int]]:
        """
        获取 SQL 查询中涉及的每个表的行数和列数。

        Args:
            db_path (str): SQLite 数据库文件的路径。
            sql (str): 要分析的 SQL 查询字符串。

        Returns:
            Dict[str, Tuple[int, int]]: 一个字典，键是表名，值是一个元组 (行数, 列数)。
                                        如果无法获取信息，返回空字典。
        """
        tables_info = {}
        try:
            involved_tables = ChessSchemaParser.get_sql_tables(db_path=db_path, sql=sql)
            if not involved_tables:
                return {}

            conn = SqliteDbUtils.get_cursor_from_path(db_path)
            for table_name in involved_tables:
                try:
                    row_count = SqliteDbUtils.get_table_row_count(conn, table_name) # Assuming this method exists
                    col_names = SqliteDbUtils.get_table_column_names(conn, table_name)
                    col_count = len(col_names)
                    tables_info[table_name] = (row_count, col_count)
                except Exception as table_e:
                    logger.warning(f"无法获取表 '{table_name}' 的大小信息：{table_e}")
            conn.close()
        except sqlite3.OperationalError:
            logger.error(f"无法打开数据库 '{db_path}'。跳过获取表大小。")
        except Exception as e:
            logger.error(f"获取表大小信息时出错: SQL: '{sql[:50]}...' - 错误: {e}")
        return tables_info


if __name__ == "__main__":
    # 定义 JSON 文件路径
    json_file_path = "/home/shenshuyu/SQL_tool/work/bird/train/train_with_operations.json"
    
    # 定义输出文件路径
    output_json_file_path = "/home/shenshuyu/SQL_tool/work/bird/train/train_with_detected_conflicts.json" # 修改为包含大小信息的新文件名

    # 定义数据库路径模板
    base_dev_db_path = "/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_databases"
    base_train_db_path = "/home/shenshuyu/SQL_tool/work/bird/train/train_databases"

    all_results = [] # 用于收集所有 SQL 的处理结果

    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and "SQL" in data:
                data_list = [data]
            elif isinstance(data, list):
                data_list = data
            else:
                print(f"错误: JSON 文件 '{json_file_path}' 格式不符合预期。")
                data_list = []

        for entry in data_list:
            if "db_id" in entry and "SQL" in entry:
                db_name = entry["db_id"]
                sql_query = entry["SQL"]
                question_id = entry.get("question_id", "N/A")

                dev_db_path = Path(base_dev_db_path) / db_name / f"{db_name}.sqlite"
                train_db_path = Path(base_train_db_path) / db_name / f"{db_name}.sqlite"

                db_path = None
                if dev_db_path.exists():
                    db_path = str(dev_db_path)
                elif train_db_path.exists():
                    db_path = str(train_db_path)

                if not db_path:
                    print(f"\n跳过: 数据库 '{db_name}' 未找到于 '{dev_db_path}' 或 '{train_db_path}'。")
                    result_entry = {
                        "question_id": question_id,
                        "db_id": db_name,
                        "sql": sql_query,
                        "status": "Skipped",
                        "reason": "Database not found",
                        "conflicts": {},
                        "tables_info": {} # 添加空字典作为占位符
                    }
                    all_results.append(result_entry)
                    continue

                print(f"\n--- 处理问题 ID: {question_id}, 数据库: {db_name} ---")
                print(f"SQL 查询: {sql_query}")
                print(f"使用的数据库路径: {db_path}")

                conflicts = SchemaConflictDetector.identify_column_conflicts(db_path, sql_query)
                print(f"检测到的列冲突: {conflicts}")
                
                # 获取表的大小信息
                tables_info = SchemaConflictDetector.get_involved_tables_sizes(db_path, sql_query)
                print(f"涉及的表大小 (行数, 列数): {tables_info}")

                # 将冲突和表大小信息添加到原始 entry 中
                entry['conflicts'] = conflicts
                entry['tables_info'] = tables_info # 新增字段
                all_results.append(entry)

            else:
                print(f"警告: JSON 条目缺少 'db_id' 或 'SQL' 键: {entry}")
                all_results.append({"status": "Skipped", "reason": "Missing db_id or SQL", "entry": entry})

    except FileNotFoundError:
        print(f"错误: JSON 文件 '{json_file_path}' 未找到。请检查路径。")
    except json.JSONDecodeError:
        print(f"错误: JSON 文件 '{json_file_path}' 解析失败。请检查文件内容是否为有效的 JSON。")
    except Exception as e:
        print(f"处理过程中发生未预期的错误: {e}")
        # 在发生未预期错误时，也可以选择记录到结果中
        all_results.append({"status": "Error", "reason": str(e)})
    finally:
        # 将所有结果写入 JSON 文件
        try:
            with open(output_json_file_path, 'w', encoding='utf-8') as outfile:
                json.dump(all_results, outfile, indent=4, ensure_ascii=False)
            print(f"\n--- 处理完成 ---")
            print(f"所有结果已保存到文件: {output_json_file_path}")
        except Exception as e:
            print(f"写入输出文件时发生错误: {e}")