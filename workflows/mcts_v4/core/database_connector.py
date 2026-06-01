import sqlite3
import pandas as pd
import os
import threading
import time as _time
from typing import Tuple
from pathlib import Path

# 尝试加载 .env 文件（如果存在）
try:
    from dotenv import load_dotenv
    # 从项目根目录加载 .env 文件
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent  # workflows/mcts_v1/core -> 项目根目录
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    # 如果没有安装 python-dotenv，跳过
    pass

class DatabaseConnector:
    """数据库连接器，处理数据库连接和查询"""
    
    def __init__(self, db_name):
        """初始化数据库连接器
        
        Args:
            db_name: 数据库名称或完整路径
                - 如果是绝对路径且存在，直接使用
                - 否则从环境变量 DB_ROOT_DIR 或项目相对路径中查找数据库
        """
        # 检查是否提供了完整路径
        if os.path.isabs(db_name) and os.path.exists(db_name):
            self.db_path = db_name
        else:
            # 获取数据库根目录（优先使用环境变量）
            db_root_dir = os.getenv('DB_ROOT_DIR', None)
            
            # 构建可能的数据库路径列表
            possible_paths = []
            
            # 1. 如果设置了环境变量，优先使用
            if db_root_dir:
                possible_paths.append(
                    os.path.join(db_root_dir, db_name, f"{db_name}.sqlite")
                )
            
            # 2. 尝试项目相对路径（基于当前文件位置）
            # 假设数据库在项目根目录的 data/dev_databases 下
            current_file = Path(__file__).resolve()
            project_root = current_file.parent.parent.parent.parent  # workflows/mcts_v1/core -> 项目根目录
            relative_path = project_root / "data" / "dev_databases" / db_name / f"{db_name}.sqlite"
            possible_paths.append(str(relative_path))
            
            # 查找第一个存在的路径
            self.db_path = None
            for path in possible_paths:
                if os.path.exists(path):
                    self.db_path = path
                    break
            
            if self.db_path is None:
                # 如果所有路径都不存在，优先使用环境变量或相对路径
                if db_root_dir:
                    self.db_path = os.path.join(db_root_dir, db_name, f"{db_name}.sqlite")
                else:
                    self.db_path = str(relative_path)
        
        self.connection = None
        
    def connect(self):
        """连接到数据库"""
        try:
            # 检查数据库文件是否存在
            if not os.path.exists(self.db_path):
                print(f"Error: Database file does not exist: {self.db_path}")
                return False
                
            # timeout: 等待锁的最长时间（秒），避免多进程/多任务同库时无限阻塞
            self.connection = sqlite3.connect(self.db_path, timeout=30)
            return True
        except Exception as e:
            print(f"Database connection error: {e}")
            return False
            
    def disconnect(self):
        """断开数据库连接"""
        if self.connection:
            self.connection.close()
            self.connection = None

    def execute_query_cell(self, query):
        """执行SQL查询并返回结果

        Returns:
            成功时返回(列表的元组，None) 例如： [(), ()]
            失败时返回(None, 错误信息)
        """
        if not self.connection:
            if not self.connect():
                return None, "Unable to connect to database"
        # SQLite queries don't strictly require a semicolon at the end
        # but adding it defensively if your original logic implies it for other DBs
        if not query.strip().endswith(';'):
             query = query.strip() + ";"

        try:
            # 使用cursor直接执行查询
            cursor = self.connection.cursor()
            cursor.execute(query)
            result = cursor.fetchall() # This already returns a list of tuples

            return result, None # Return the list of tuples directly
        except Exception as e:
            error_msg = f"Query execution error: {e}"
            return None, error_msg 
            
    def execute_query(self, query, timeout_s: float = None):
        """执行SQL查询并返回结果
        
        Returns:
            成功时返回pandas DataFrame
            失败时返回(None, 错误信息)
        """
        if not self.connection:
            if not self.connect():
                return None, "Unable to connect to database"
        if not query.strip().endswith(';'):
             query = query.strip() + ";"
        try:
            # 可选：设置超时进度回调（SQLite）
            cancel_handler_set = False
            if timeout_s is not None:
                import time as _time
                start_ts = _time.time()
                def _progress_handler():
                    # 每 N 步检查一次时间，超时则返回非零中断查询
                    if _time.time() - start_ts > timeout_s:
                        return 1  # 非零 -> 触发 sqlite3.OperationalError: interrupted
                    return 0
                # 每执行约 1000 个虚拟机指令调用一次回调（经验值）
                self.connection.set_progress_handler(_progress_handler, 1000)
                cancel_handler_set = True
            # 使用cursor直接执行查询
            cursor = self.connection.cursor()
            cursor.execute(query)
            result = cursor.fetchall()
            
            # 获取列名
            column_names = [description[0] for description in cursor.description] if cursor.description else []
            
            # 转换为pandas DataFrame
            df = pd.DataFrame(result, columns=column_names)
            return df, None
        except Exception as e:
            # 将 SQLite 的中断错误识别为超时
            msg = str(e)
            if 'interrupted' in msg.lower() and timeout_s is not None:
                error_msg = f"Query execution timeout ({timeout_s:.0f}s)"
            else:
                error_msg = f"Query execution error: {e}"
            return None, error_msg
        finally:
            if 'cancel_handler_set' in locals() and cancel_handler_set:
                # 清除进度回调，避免影响后续查询
                try:
                    self.connection.set_progress_handler(None, 0)
                except Exception:
                    pass

    def execute_query_parallel_safe(self, query: str, timeout_s: float = None) -> Tuple[pd.DataFrame, str]:
        """
        并行安全执行：为每次调用建立独立连接，避免多线程共享同一连接的问题。
        使用线程级超时机制，确保即使progress_handler失效也能可靠中断。
        """
        if not query or not isinstance(query, str):
            return None, "无效的查询"
        if not query.strip().endswith(';'):
            query = query.strip() + ";"
        if not os.path.exists(self.db_path):
            return None, f"Database file does not exist: {self.db_path}"

        # 如果没有超时，直接执行（保持向后兼容）
        if timeout_s is None:
            return self._execute_query_simple(query)
        
        # 使用线程级超时机制
        result_container = {'df': None, 'error': None, 'exception': None}
        stop_event = threading.Event()
        
        def execute_in_thread():
            """在独立线程中执行查询"""
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
                # 设置progress_handler，检查stop_event
                # 减少检查间隔（从1000减到100），更频繁地检查超时，提高响应速度
                def _progress_handler():
                    if stop_event.is_set():
                        return 1  # 中断查询
                    return 0
                conn.set_progress_handler(_progress_handler, 100)  # 每100个指令检查一次（更频繁）
                
                cur = conn.cursor()
                cur.execute(query)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
                result_container['df'] = pd.DataFrame(rows, columns=cols)
            except Exception as e:
                result_container['exception'] = e
                msg = str(e)
                if 'interrupted' in msg.lower() or stop_event.is_set():
                    result_container['error'] = f"Query execution timeout ({timeout_s:.0f}s)"
                else:
                    result_container['error'] = f"{e}"
            finally:
                if conn is not None:
                    try:
                        conn.set_progress_handler(None, 0)
                        conn.close()
                    except Exception:
                        pass
        
        # 启动执行线程
        exec_thread = threading.Thread(target=execute_in_thread, daemon=True)
        exec_thread.start()
        
        # 等待线程完成或超时
        exec_thread.join(timeout=timeout_s)
        
        # 如果线程仍在运行，说明超时了
        if exec_thread.is_alive():
            stop_event.set()  # 设置停止标志
            # 尝试更激进的中断：关闭连接
            # 注意：由于daemon=True，线程会在主线程退出时自动终止
            # 但为了更可靠，我们等待一小段时间让线程有机会响应stop_event
            exec_thread.join(timeout=0.5)  # 减少等待时间，更快返回
            
            # 如果仍然在运行，尝试强制中断（通过关闭连接）
            # 由于daemon=True，即使线程还在运行，也不会阻塞主进程
            if result_container['error'] is None:
                result_container['error'] = f"Query execution timeout ({timeout_s:.0f}s)"
        
        # 返回结果
        if result_container['df'] is not None:
            return result_container['df'], None
        else:
            error_msg = result_container['error'] or str(result_container['exception']) or "Unknown error"
            return None, error_msg
    
    def _execute_query_simple(self, query: str) -> Tuple[pd.DataFrame, str]:
        """简单执行查询（无超时）"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            cur = conn.cursor()
            cur.execute(query)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            df = pd.DataFrame(rows, columns=cols)
            return df, None
        except Exception as e:
            return None, f"{e}"
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            
    def _extract_tables_from_query(self, query):
        """从SQL查询中提取表名（简化版）"""
        # 这是一个简化的实现，可能需要更复杂的SQL解析
        query = query.lower()
        tables = []
        
        # 查找FROM和JOIN后面的表名
        keywords = ['from', 'join']
        for keyword in keywords:
            parts = query.split(f' {keyword} ')
            for i in range(1, len(parts)):
                table_part = parts[i].strip().split(' ')[0]
                # 移除可能的别名和标点符号
                table_name = table_part.split(' ')[0].split('(')[-1].split(')')[-1].split(';')[0].split(',')[0]
                if table_name and table_name not in tables:
                    tables.append(table_name)
        
        return tables
    
    def get_table_schema(self, table_name):
        """获取表结构 (仅获取列信息，不包含外键)"""
        if not self.connection:
            if not self.connect():
                return None
                
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"PRAGMA table_info('{table_name}')")
            schema = cursor.fetchall()
            return schema
        except Exception:
            return None
            
    def get_all_tables(self):
        """获取所有表名"""
        if not self.connection:
            if not self.connect():
                return None
                
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            return [table[0] for table in tables]
        except Exception:
            return None


    def get_schema_info(self):
        """
        检索连接数据库的详细 Schema 信息。
        包括所有表名、列信息和外键关系。
        
        返回:
            dict: 详细的 Schema 信息。
                  格式: {table_name: {'columns': [...], 'foreign_keys': [...]}}
                  或 None 如果检索失败。
        """
        if not self.connection:
            return None

        schema_info = {}
        try:
            cursor = self.connection.cursor()
            
            # 1. 获取所有表名
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            tables = [row[0] for row in cursor.fetchall()]

            for table_name in tables:
                columns = []
                # 2. 获取每个表的列信息
                cursor.execute(f"PRAGMA table_info('{table_name}');")
                for col_info in cursor.fetchall():
                    # col_info: (cid, name, type, notnull, dflt_value, pk)
                    columns.append({
                        "name": col_info[1],
                        "type": col_info[2],
                        "notnull": bool(col_info[3]),
                        "default": col_info[4],
                        "pk": bool(col_info[5])
                    })
                
                foreign_keys = []
                # 3. 获取每个表的外键信息
                cursor.execute(f"PRAGMA foreign_key_list('{table_name}');")
                for fk_info in cursor.fetchall():
                    # fk_info: (id, seq, table, from, to, on_update, on_delete, match)
                    foreign_keys.append({
                        "column": fk_info[3],          # 当前表中的列 (from)
                        "references_table": fk_info[2], # 引用到的表 (table)
                        "references_column": fk_info[4] # 引用到的列 (to)
                    })
                
                schema_info[table_name] = {
                    "columns": columns,
                    "foreign_keys": foreign_keys
                }
            
            return schema_info

        except Exception:
            return None