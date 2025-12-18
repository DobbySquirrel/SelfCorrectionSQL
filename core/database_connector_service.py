"""
数据库连接器服务包装器

将DatabaseConnector包装为使用SQL执行器服务
这样可以避免重复加载数据库连接，支持并发请求

使用方法：
    from core.database_connector_service import DatabaseConnectorService
    
    # 使用服务模式
    connector = DatabaseConnectorService(db_name, service_url="http://localhost:5887")
    
    # 或者使用传统模式（回退）
    connector = DatabaseConnectorService(db_name, use_service=False)
"""

from typing import Tuple, Optional
import pandas as pd
from .database_connector import DatabaseConnector
from .sql_executor_client import SQLExecutorClient


class DatabaseConnectorService:
    """
    数据库连接器服务包装器
    
    可以选择使用独立的SQL执行器服务，或回退到传统的DatabaseConnector
    """
    
    def __init__(self, db_name: str, service_url: str = "http://localhost:5887", 
                 use_service: bool = True, timeout: int = 60):
        """
        初始化数据库连接器
        
        Args:
            db_name: 数据库名称
            service_url: SQL执行器服务地址（仅在use_service=True时使用）
            use_service: 是否使用服务模式
            timeout: 超时时间（秒）
        """
        self.db_name = db_name
        self.use_service = use_service
        self.timeout = timeout
        
        if use_service:
            try:
                self.client = SQLExecutorClient(service_url, timeout=timeout)
                # 检查服务是否可用
                if not self.client.health_check():
                    print(f"⚠️ SQL执行器服务不可用 ({service_url})，回退到传统模式")
                    self.use_service = False
                    self.connector = DatabaseConnector(db_name)
                else:
                    print(f"✅ 使用SQL执行器服务: {service_url}")
            except Exception as e:
                print(f"⚠️ 无法连接到SQL执行器服务 ({service_url}): {e}，回退到传统模式")
                self.use_service = False
                self.connector = DatabaseConnector(db_name)
        else:
            self.connector = DatabaseConnector(db_name)
            self.client = None
    
    def connect(self):
        """连接到数据库（服务模式下不需要）"""
        if not self.use_service:
            return self.connector.connect()
        return True
    
    def disconnect(self):
        """断开数据库连接（服务模式下不需要）"""
        if not self.use_service:
            self.connector.disconnect()
    
    def execute_query(self, query: str, timeout_s: float = None) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """
        执行SQL查询
        
        Args:
            query: SQL查询
            timeout_s: 超时时间（秒）
        
        Returns:
            (DataFrame, error): 成功返回(DataFrame, None)，失败返回(None, 错误信息)
        """
        if self.use_service:
            return self.client.execute_sql(query, self.db_name, timeout=timeout_s or self.timeout)
        else:
            return self.connector.execute_query(query, timeout_s=timeout_s)
    
    def execute_query_parallel_safe(self, query: str, timeout_s: float = None) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """
        并行安全执行SQL查询（服务模式下天然支持并发）
        
        Args:
            query: SQL查询
            timeout_s: 超时时间（秒）
        
        Returns:
            (DataFrame, error): 成功返回(DataFrame, None)，失败返回(None, 错误信息)
        """
        if self.use_service:
            # 服务模式下天然支持并发，直接调用execute_query
            return self.execute_query(query, timeout_s=timeout_s)
        else:
            return self.connector.execute_query_parallel_safe(query, timeout_s=timeout_s)
    
    def execute_query_cell(self, query: str) -> Tuple[Optional[list], Optional[str]]:
        """
        执行SQL查询并返回原始结果（元组列表）
        
        Args:
            query: SQL查询
        
        Returns:
            (result, error): 成功返回(结果列表, None)，失败返回(None, 错误信息)
        """
        if self.use_service:
            df, error = self.client.execute_sql(query, self.db_name, timeout=self.timeout)
            if error:
                return None, error
            # 转换为元组列表
            return df.values.tolist(), None
        else:
            return self.connector.execute_query_cell(query)

