"""
SQL执行器客户端 - 用于调用SQL执行器服务

使用方法：
    from core.sql_executor_client import SQLExecutorClient
    
    client = SQLExecutorClient("http://localhost:5887")
    result, error = client.execute_sql(sql, db_name)
    match, message = client.compare_sql(predicted_sql, ground_truth, db_name)
"""

import requests
import pandas as pd
from typing import Tuple, Optional, Dict, Any
import json


class SQLExecutorClient:
    """SQL执行器客户端"""
    
    def __init__(self, service_url: str = "http://localhost:5887", timeout: int = 60):
        """
        初始化客户端
        
        Args:
            service_url: SQL执行器服务地址
            timeout: 请求超时时间（秒）
        """
        self.service_url = service_url.rstrip('/')
        self.timeout = timeout
    
    def _make_request(self, endpoint: str, data: Dict[str, Any]) -> Tuple[bool, Any]:
        """
        发送HTTP请求
        
        Args:
            endpoint: API端点
            data: 请求数据
        
        Returns:
            (success, response_data)
        """
        url = f"{self.service_url}{endpoint}"
        try:
            response = requests.post(url, json=data, timeout=self.timeout)
            
            # 解析响应
            try:
                result = response.json()
            except:
                # 如果无法解析JSON，使用文本
                result = {'status': 'error', 'message': response.text[:500]}
            
            # 检查响应状态（服务端统一返回200，但status字段表示实际状态）
            if result.get('status') == 'success':
                return True, result
            else:
                # 返回错误信息
                error_msg = result.get('message', 'Unknown error')
                return False, error_msg
        except requests.exceptions.RequestException as e:
            return False, f"Request error: {str(e)}"
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def execute_sql(self, sql: str, db_name: str, timeout: int = None) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """
        执行SQL查询
        
        Args:
            sql: SQL查询
            db_name: 数据库名称
            timeout: 超时时间（秒），如果为None则使用客户端默认值
        
        Returns:
            (DataFrame, error): 成功返回(DataFrame, None)，失败返回(None, 错误信息)
        """
        data = {
            'sql': sql,
            'db_name': db_name,
            'timeout': timeout if timeout is not None else self.timeout
        }
        
        success, response = self._make_request('/execute_sql', data)
        
        if success:
            if 'result' in response:
                # 转换为DataFrame
                records = response.get('result', [])
                if records:
                    df = pd.DataFrame(records)
                    return df, None
                else:
                    # 空结果
                    columns = response.get('columns', [])
                    return pd.DataFrame(columns=columns), None
            else:
                return None, "No result in response"
        else:
            return None, response
    
    def compare_sql(self, predicted_sql: str, ground_truth: str, db_name: str, timeout: int = None) -> Tuple[bool, str]:
        """
        比较两个SQL的执行结果
        
        Args:
            predicted_sql: 预测的SQL
            ground_truth: 标准答案SQL
            db_name: 数据库名称
            timeout: 超时时间（秒）
        
        Returns:
            (match, message): match=True表示结果匹配，False表示不匹配或错误
        """
        data = {
            'predicted_sql': predicted_sql,
            'ground_truth': ground_truth,
            'db_name': db_name,
            'timeout': timeout if timeout is not None else self.timeout
        }
        
        success, response = self._make_request('/compare_sql', data)
        
        if success:
            match = response.get('match', False)
            message = response.get('message', '')
            return match, message
        else:
            return False, response
    
    def compact_result(self, result: pd.DataFrame, max_length: int = 500) -> str:
        """
        压缩执行结果
        
        Args:
            result: pandas DataFrame
            max_length: 最大长度
        
        Returns:
            压缩后的结果字符串
        """
        data = {
            'result': result.to_dict('records'),
            'max_length': max_length
        }
        
        success, response = self._make_request('/compact_result', data)
        
        if success:
            return response.get('compacted_result', '')
        else:
            return response
    
    def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            True表示服务正常，False表示服务异常
        """
        try:
            url = f"{self.service_url}/health"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            result = response.json()
            return result.get('status') == 'healthy'
        except:
            return False

