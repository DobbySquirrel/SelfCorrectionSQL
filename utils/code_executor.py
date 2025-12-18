import sys
import io
import traceback
import pandas as pd
from contextlib import redirect_stdout, redirect_stderr

class CodeExecutor:
    """执行Python代码并返回结果的工具类"""
    
    def __init__(self, db_connector=None):
        """初始化代码执行器"""
        self.db_connector = db_connector
        self.globals = {
            'pd': pd,
            'db_connector': db_connector
        }

    def execute(self, code, mode="train"):
        """执行Python代码并返回结果
        
        Args:
            code: 要执行的Python代码字符串
            mode: 执行模式，可选值为"train"或"dev"
            
        Returns:
            dict: 包含执行结果、标准输出、标准错误和错误信息的字典
        """
        # 创建输出捕获对象
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        # 初始化结果
        result = None
        error_info = None
        
        # 准备执行环境
        exec_globals = {
            'pd': pd,
            'db_connector': self.db_connector,
            '__result': None
        }
        
        # 添加返回结果的代码
        if mode == "train":
            code_with_return = "from core.api_tools_train import *\n" + code + "\n\n__result = locals().get('result', None)"
        else:
            code_with_return = "from core.api_tools import *\n" + code + "\n\n__result = locals().get('result', None)"
        try:
            # 捕获标准输出和标准错误
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                # 执行代码
                exec(code_with_return, exec_globals)
                result = exec_globals['__result']
        except Exception as e:
            # 捕获异常并获取完整的堆栈跟踪
            error_type = type(e).__name__
            error_message = str(e)
            error_traceback = traceback.format_exc()
            
            # 获取发生错误的代码行及其上下文
            tb = sys.exc_info()[2]
            while tb.tb_next:
                tb = tb.tb_next
            frame = tb.tb_frame
            line_no = tb.tb_lineno
            
            # 尝试获取错误发生的代码上下文
            code_lines = code.split('\n')
            context_start = max(0, line_no - 3)
            context_end = min(len(code_lines), line_no + 2)
            code_context = '\n'.join([f"{i+1}: {line}" for i, line in enumerate(code_lines[context_start:context_end])])
            
            # 构建详细的错误信息
            detailed_error = f"错误类型: {error_type}\n"
            detailed_error += f"错误信息: {error_message}\n"
            detailed_error += f"错误位置: 大约在第 {line_no} 行\n"
            detailed_error += f"代码上下文:\n{code_context}\n"
            detailed_error += f"完整堆栈跟踪:\n{error_traceback}"
            
            error_info = {
                'type': error_type,
                'message': error_message,
                'traceback': error_traceback,
                'detailed': detailed_error
            }
        
        # 获取捕获的输出
        stdout = stdout_capture.getvalue()
        stderr = stderr_capture.getvalue()
        
        # 返回结果字典
        return {
            'result': result,
            'stdout': stdout,
            'stderr': stderr,
            'error': error_info
        }
        
        return execution_result 
    def execute_train(self, code):
        """执行Python代码并返回结果
        
        Args:
            code: 要执行的Python代码字符串
            
        Returns:
            dict: 包含执行结果、标准输出、标准错误和错误信息的字典
        """
        # 创建输出捕获对象
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        # 初始化结果
        result = None
        error_info = None
        
        # 准备执行环境
        exec_globals = {
            'pd': pd,
            'db_connector': self.db_connector,
            '__result': None
        }
        
        # 添加返回结果的代码
        code_with_return = "from core.api_tools_train import *\n" + code + "\n\n__result = locals().get('result', None)"
        try:
            # 捕获标准输出和标准错误
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                # 执行代码
                exec(code_with_return, exec_globals)
                result = exec_globals['__result']
        except Exception as e:
            # 捕获异常并获取完整的堆栈跟踪
            error_type = type(e).__name__
            error_message = str(e)
            error_traceback = traceback.format_exc()
            
            # 获取发生错误的代码行及其上下文
            tb = sys.exc_info()[2]
            while tb.tb_next:
                tb = tb.tb_next
            frame = tb.tb_frame
            line_no = tb.tb_lineno
            
            # 尝试获取错误发生的代码上下文
            code_lines = code.split('\n')
            context_start = max(0, line_no - 3)
            context_end = min(len(code_lines), line_no + 2)
            code_context = '\n'.join([f"{i+1}: {line}" for i, line in enumerate(code_lines[context_start:context_end])])
            
            # 构建详细的错误信息
            detailed_error = f"错误类型: {error_type}\n"
            detailed_error += f"错误信息: {error_message}\n"
            detailed_error += f"错误位置: 大约在第 {line_no} 行\n"
            detailed_error += f"代码上下文:\n{code_context}\n"
            detailed_error += f"完整堆栈跟踪:\n{error_traceback}"
            
            error_info = {
                'type': error_type,
                'message': error_message,
                'traceback': error_traceback,
                'detailed': detailed_error
            }
        
        # 获取捕获的输出
        stdout = stdout_capture.getvalue()
        stderr = stderr_capture.getvalue()
        
        # 返回结果字典
        return {
            'result': result,
            'stdout': stdout,
            'stderr': stderr,
            'error': error_info
        }
        
        return execution_result 