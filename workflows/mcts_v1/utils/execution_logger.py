"""
执行日志记录工具

记录所有SQL执行相关的信息：
- 报错信息
- 空结果信息
- 对应的执行内容（SQL语句）
"""

import json
import threading
import shutil
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path


class ExecutionLogger:
    """线程安全的执行日志记录器"""
    
    def __init__(self, log_file: Optional[str] = None):
        """
        初始化日志记录器
        
        Args:
            log_file: 日志文件路径（如果为None，则不写入文件，只保存在内存中）
        """
        self.log_file = log_file
        self.logs: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.question_id: Optional[str] = None
        self.db_name: Optional[str] = None
        
        # 如果提供了日志文件路径，确保目录存在
        if self.log_file:
            Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)
    
    def set_context(self, question_id: Optional[str] = None, db_name: Optional[str] = None):
        """
        设置上下文信息（问题ID和数据库名称）
        
        Args:
            question_id: 问题ID
            db_name: 数据库名称
        """
        with self.lock:
            self.question_id = question_id
            self.db_name = db_name
    
    def log_error(self, sql: str, error: str, execution_type: str = "SQL", 
                  context: Optional[Dict[str, Any]] = None):
        """
        记录错误信息
        
        Args:
            sql: 执行的SQL语句
            error: 错误信息
            execution_type: 执行类型（如 "SQL", "CTE", "FinalSQL"）
            context: 额外的上下文信息
        """
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'type': 'error',
            'execution_type': execution_type,
            'sql': sql,
            'error': error,
            'question_id': self.question_id,
            'db_name': self.db_name,
        }
        if context:
            log_entry['context'] = context
        
        with self.lock:
            self.logs.append(log_entry)
            if self.log_file:
                self._append_to_file(log_entry)
    
    def log_empty_result(self, sql: str, execution_type: str = "SQL",
                        context: Optional[Dict[str, Any]] = None):
        """
        记录空结果信息
        
        Args:
            sql: 执行的SQL语句
            execution_type: 执行类型（如 "SQL", "CTE", "FinalSQL"）
            context: 额外的上下文信息
        """
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'type': 'empty_result',
            'execution_type': execution_type,
            'sql': sql,
            'question_id': self.question_id,
            'db_name': self.db_name,
        }
        if context:
            log_entry['context'] = context
        
        with self.lock:
            self.logs.append(log_entry)
            if self.log_file:
                self._append_to_file(log_entry)
    
    def log_execution(self, sql: str, result_count: int = 0, execution_type: str = "SQL",
                     success: bool = True, error: Optional[str] = None,
                     context: Optional[Dict[str, Any]] = None):
        """
        记录执行信息（通用方法）
        
        Args:
            sql: 执行的SQL语句
            result_count: 结果行数
            execution_type: 执行类型
            success: 是否成功
            error: 错误信息（如果有）
            context: 额外的上下文信息
        """
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'type': 'execution',
            'execution_type': execution_type,
            'sql': sql,
            'result_count': result_count,
            'success': success,
            'question_id': self.question_id,
            'db_name': self.db_name,
        }
        if error:
            log_entry['error'] = error
        if context:
            log_entry['context'] = context
        
        with self.lock:
            self.logs.append(log_entry)
            if self.log_file:
                self._append_to_file(log_entry)
    
    def _append_to_file(self, log_entry: Dict[str, Any]):
        """将日志条目追加到文件（JSON格式，追加到数组中，线程安全）"""
        if not self.log_file:
            return
        
        try:
            import fcntl  # 用于文件锁（Unix系统）
            file_path = Path(self.log_file)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 使用文件锁确保线程安全
            with open(self.log_file, 'a+', encoding='utf-8') as f:
                try:
                    # 尝试获取文件锁（非阻塞）
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    
                    # 读取现有内容
                    f.seek(0)  # 移动到文件开头
                    content = f.read()
                    if content.strip():
                        try:
                            existing_logs = json.loads(content)
                            if not isinstance(existing_logs, list):
                                existing_logs = []
                        except (json.JSONDecodeError, ValueError):
                            existing_logs = []
                    else:
                        existing_logs = []
                    
                    # 追加新条目
                    existing_logs.append(log_entry)
                    
                    # 写回文件
                    f.seek(0)
                    f.truncate()
                    json.dump(existing_logs, f, ensure_ascii=False, indent=2)
                    f.flush()
                    
                    # 释放文件锁
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (IOError, OSError):
                    # 如果文件锁不可用（Windows系统），使用简单方式
                    # 读取现有内容
                    f.seek(0)
                    content = f.read()
                    if content.strip():
                        try:
                            existing_logs = json.loads(content)
                            if not isinstance(existing_logs, list):
                                existing_logs = []
                        except (json.JSONDecodeError, ValueError):
                            existing_logs = []
                    else:
                        existing_logs = []
                    
                    # 追加新条目
                    existing_logs.append(log_entry)
                    
                    # 写回文件
                    f.seek(0)
                    f.truncate()
                    json.dump(existing_logs, f, ensure_ascii=False, indent=2)
                    f.flush()
        except ImportError:
            # 如果fcntl不可用（Windows系统），使用简单方式（可能有并发问题，但通常可以接受）
            try:
                file_path = Path(self.log_file)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 读取现有内容
                if file_path.exists():
                    with open(self.log_file, 'r', encoding='utf-8') as f:
                        try:
                            existing_logs = json.load(f)
                            if not isinstance(existing_logs, list):
                                existing_logs = []
                        except (json.JSONDecodeError, ValueError):
                            existing_logs = []
                else:
                    existing_logs = []
                
                # 追加新条目
                existing_logs.append(log_entry)
                
                # 写回文件（使用临时文件确保原子性）
                temp_file = str(file_path) + '.tmp'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(existing_logs, f, ensure_ascii=False, indent=2)
                
                # 原子性替换
                shutil.move(temp_file, self.log_file)
            except Exception as e:
                print(f"⚠️ 写入日志文件失败: {e}")
        except Exception as e:
            print(f"⚠️ 写入日志文件失败: {e}")
    
    def get_logs(self) -> List[Dict[str, Any]]:
        """获取所有日志条目"""
        with self.lock:
            return self.logs.copy()
    
    def get_errors(self) -> List[Dict[str, Any]]:
        """获取所有错误日志"""
        with self.lock:
            return [log for log in self.logs if log.get('type') == 'error']
    
    def get_empty_results(self) -> List[Dict[str, Any]]:
        """获取所有空结果日志"""
        with self.lock:
            return [log for log in self.logs if log.get('type') == 'empty_result']
    
    def save_to_file(self, file_path: str):
        """
        将所有日志保存到文件（JSON格式）
        
        Args:
            file_path: 输出文件路径
        """
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.logs, f, ensure_ascii=False, indent=2)
    
    def clear(self):
        """清空所有日志"""
        with self.lock:
            self.logs.clear()
    
    def get_summary(self) -> Dict[str, Any]:
        """获取日志摘要统计"""
        with self.lock:
            total = len(self.logs)
            errors = len([log for log in self.logs if log.get('type') == 'error'])
            empty_results = len([log for log in self.logs if log.get('type') == 'empty_result'])
            executions = len([log for log in self.logs if log.get('type') == 'execution'])
            
            return {
                'total_logs': total,
                'errors': errors,
                'empty_results': empty_results,
                'executions': executions,
                'question_id': self.question_id,
                'db_name': self.db_name,
            }


# 全局日志记录器实例（用于单例模式）
_global_logger: Optional[ExecutionLogger] = None
_logger_lock = threading.Lock()


def get_global_logger() -> Optional[ExecutionLogger]:
    """获取全局日志记录器"""
    return _global_logger


def set_global_logger(logger: ExecutionLogger):
    """设置全局日志记录器"""
    global _global_logger
    with _logger_lock:
        _global_logger = logger


def init_global_logger(log_file: Optional[str] = None) -> ExecutionLogger:
    """
    初始化全局日志记录器
    
    Args:
        log_file: 日志文件路径
        
    Returns:
        日志记录器实例
    """
    global _global_logger
    with _logger_lock:
        if _global_logger is None:
            _global_logger = ExecutionLogger(log_file=log_file)
        return _global_logger
