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
DB_ID = "regional_sales"
SQL_QUERY = "SELECT CAST(SUM(CASE WHEN REPLACE(T1.`Unit Price`, ',', '') - REPLACE(T1.`Unit Cost`, ',', '') > 1000 THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(T1.OrderNumber) FROM `Sales Orders` AS T1 INNER JOIN `Sales Team` AS T2 ON T2.SalesTeamID = T1._SalesTeamID WHERE T2.`Sales Team` = 'Stephen Payne'"

# --- GENERATED FUNCTION WILL BE PLACED HERE BY THE TEMPLATE ---
@snoop
def execute_chain() -> tuple[tuple, ...]:
    # Load data
    df_sales_orders = load_df(DB_ID, 'Sales Orders') # Alias T1
    df_sales_team = load_df(DB_ID, 'Sales Team') # Alias T2

    # --- Join: Sales Orders (T1) and Sales Team (T2) ---
    # T1 INNER JOIN T2 ON T2.SalesTeamID = T1._SalesTeamID
    df_merged = build_join(df_sales_orders, df_sales_team, 'inner',
                           left_on='_SalesTeamID', right_on='SalesTeamID')

    # --- WHERE clause: T2.`Sales Team` = 'Stephen Payne' ---
    df_filtered_by_sales_team = df_merged[op_eq(df_merged['Sales Team'], 'Stephen Payne')]

    # --- Calculate Net Profit: REPLACE(T1.`Unit Price`, ',', '') - REPLACE(T1.`Unit Cost`, ',', '') ---
    # Need to remove commas and convert to numeric type (float/real)
    
    # Unit Price: REPLACE(T1.`Unit Price`, ',', '')
    unit_price_cleaned = op_replace(df_filtered_by_sales_team['Unit Price'], ',', '')
    unit_price_numeric = op_cast(unit_price_cleaned, 'REAL') # Assuming it can be float

    # Unit Cost: REPLACE(T1.`Unit Cost`, ',', '')
    unit_cost_cleaned = op_replace(df_filtered_by_sales_team['Unit Cost'], ',', '')
    unit_cost_numeric = op_cast(unit_cost_cleaned, 'REAL') # Assuming it can be float

    # Net Profit: unit_price_numeric - unit_cost_numeric
    net_profit = op_sub(unit_price_numeric, unit_cost_numeric)

    # --- CASE WHEN REPLACE(T1.`Unit Price`, ',', '') - REPLACE(T1.`Unit Cost`, ',', '') > 1000 THEN 1 ELSE 0 END ---
    # Condition: Net Profit > 1000
    condition_net_profit_gt_1000 = op_gt(net_profit, 1000)

    # Apply CASE WHEN logic
    # In Pandas, this translates to creating a new series based on the boolean condition
    # op_case can be used for more complex cases, but for WHEN TRUE THEN 1 ELSE 0, direct integer conversion works.
    
    # We will use op_case as per the operations list
    case_result = op_case(
        (condition_net_profit_gt_1000, 1), # When condition is true, value is 1
        else_value=0, # Otherwise, value is 0
        index_source=df_filtered_by_sales_team.index # Pass the original index for alignment
    )
    
    # --- SUM of CASE result ---
    sum_profitable_orders = op_sum(case_result)

    # --- COUNT(T1.OrderNumber) ---
    # This is the total count of orders for 'Stephen Payne' after all filtering.
    # It should be the count of rows in df_filtered_by_sales_team
    count_total_orders = op_count(df_filtered_by_sales_team['OrderNumber']) # Or df_filtered_by_sales_team.shape[0]

    # --- Final Calculation: CAST(SUM(...) AS REAL) * 100 / COUNT(T1.OrderNumber) ---
    cast_sum_profitable_orders = op_cast(sum_profitable_orders, 'REAL')
    multiplied_sum = op_mul(cast_sum_profitable_orders, 100)

    # Handle potential division by zero
    if count_total_orders == 0:
        final_percentage = 0.0
    else:
        # Corrected line:
        final_percentage = op_div(multiplied_sum, count_total_orders)


    # Format the result as a tuple of tuples
    result_list = [(final_percentage,)]

    return tuple(result_list)
sql_result = execute_sql(DB_ID, SQL_QUERY)
# 2. Python链
python_result = execute_chain()
# 3. 比较
results_match = compare_results(sql_result, python_result)
print('结果是否一致:', results_match)
print('SQL结果:', sql_result)
print('Python结果:', python_result)