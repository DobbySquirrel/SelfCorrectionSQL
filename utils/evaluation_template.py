import pandas as pd
import sqlite3
import math
import sys
sys.path.insert(0, "{sys_path}")
from utils.sql_atomic_operators import *
from utils.compare_fun import compare_results
import snoop
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

DB_ID = "{db_id}"
SQL_QUERY ="""{sql_query} """

# def execute_chain() -> tuple[tuple, ...]:
{generated_function}

# ========== 执行SQL和Python链并比较 ==========
# 1. SQL执行
sql_result = execute_sql(DB_ID, SQL_QUERY)
# 2. Python链
python_result = execute_chain()
# 3. 比较
results_match = compare_results(sql_result, python_result)
print('结果是否一致:', results_match)
print('SQL结果:', sql_result)
print('Python结果:', python_result)