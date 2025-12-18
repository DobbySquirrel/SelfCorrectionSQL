import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import sqlite3
import math

from utils.sql_atomic_operators import *
from utils.compare_fun import compare_results
import snoop
import warnings

# In your script top, after all other import statements, before chain definition
warnings.filterwarnings("ignore", category=FutureWarning)

# Global DB_ID and SQL_QUERY will be filled by the template
DB_ID = "works_cycles" # Changed DB_ID to "works_cycles"
SQL_QUERY = "SELECT CAST(SUM(365 * (STRFTIME('%Y', T1.EndDate) - STRFTIME('%Y', T1.StartDate)) + 30 * (STRFTIME('%m', T1.EndDate) - STRFTIME('%m', T1.StartDate)) + STRFTIME('%d', T1.EndDate) - STRFTIME('%d', T1.StartDate)) AS REAL) / COUNT(T1.BusinessEntityID) FROM EmployeeDepartmentHistory AS T1 INNER JOIN Department AS T2 ON T1.DepartmentID = T2.DepartmentID WHERE T2.Name = 'Engineering' AND T1.EndDate IS NOT NULL"

# --- GENERATED FUNCTION WILL BE PLACED HERE BY THE TEMPLATE ---
@snoop
def execute_chain() -> tuple[tuple, ...]:
    # Load data
    df_employee_department_history = load_df(DB_ID, 'EmployeeDepartmentHistory') # Alias T1
    df_department = load_df(DB_ID, 'Department') # Alias T2

    # Convert date columns to datetime objects for calculations
    df_employee_department_history['StartDate'] = op_to_datetime(df_employee_department_history['StartDate'])
    df_employee_department_history['EndDate'] = op_to_datetime(df_employee_department_history['EndDate'])

    # Inner join EmployeeDepartmentHistory (T1) and Department (T2) on DepartmentID
    df_merged = build_join(df_employee_department_history, df_department, 'inner', left_on='DepartmentID', right_on='DepartmentID')

    # WHERE T2.Name = 'Engineering' AND T1.EndDate IS NOT NULL
    condition_department_name = op_eq(df_merged['Name'], 'Engineering')
    condition_end_date_not_null = op_not(op_is_null(df_merged['EndDate'])) # T1.EndDate IS NOT NULL

    df_filtered = df_merged[op_and(condition_department_name, condition_end_date_not_null)]

    # Calculate SUM(365 * (STRFTIME('%Y', T1.EndDate) - STRFTIME('%Y', T1.StartDate)) + 30 * (STRFTIME('%m', T1.EndDate) - STRFTIME('%m', T1.StartDate)) + STRFTIME('%d', T1.EndDate) - STRFTIME('%d', T1.StartDate))

    # Part 1: 365 * (STRFTIME('%Y', T1.EndDate) - STRFTIME('%Y', T1.StartDate))
    year_end = op_timetostr(df_filtered['EndDate'], '%Y').astype(float) # Ensure float for subtraction
    year_start = op_timetostr(df_filtered['StartDate'], '%Y').astype(float)
    year_diff = op_sub(year_end, year_start)
    part1 = op_mul(365, year_diff)

    # Part 2: 30 * (STRFTIME('%m', T1.EndDate) - STRFTIME('%m', T1.StartDate))
    month_end = op_timetostr(df_filtered['EndDate'], '%m').astype(float) # Ensure float for subtraction
    month_start = op_timetostr(df_filtered['StartDate'], '%m').astype(float)
    month_diff = op_sub(month_end, month_start)
    part2 = op_mul(30, month_diff)

    # Part 3: STRFTIME('%d', T1.EndDate) - STRFTIME('%d', T1.StartDate)
    day_end = op_timetostr(df_filtered['EndDate'], '%d').astype(float) # Ensure float for subtraction
    day_start = op_timetostr(df_filtered['StartDate'], '%d').astype(float)
    part3 = op_sub(day_end, day_start)

    # Sum all parts
    sum_of_parts = op_add(op_add(part1, part2), part3)
    total_sum_duration = op_sum(sum_of_parts)

    # COUNT(T1.BusinessEntityID)
    count_business_entity_id = op_count(df_filtered['BusinessEntityID'])

    # Final calculation: CAST(SUM(...) AS REAL) / COUNT(...)
    cast_total_sum_duration = op_cast(total_sum_duration, 'REAL')

    # Handle potential division by zero
    if count_business_entity_id == 0:
        final_result = 0.0
    else:
        final_result = op_div(cast_total_sum_duration, count_business_entity_id)

    # Format the result as a tuple of tuples
    result_list = [(final_result,)]

    return tuple(result_list)
sql_result = execute_sql(DB_ID, SQL_QUERY)
# 2. Python链
python_result = execute_chain()
# 3. 比较
results_match = compare_results(sql_result, python_result)
print('结果是否一致:', results_match)
print('SQL结果:', sql_result)
print('Python结果:', python_result)