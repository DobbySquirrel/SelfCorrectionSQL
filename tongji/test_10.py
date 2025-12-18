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

DB_ID = "sales_in_weather"
SQL_QUERY = "SELECT T2.sunrise FROM relation AS T1 INNER JOIN weather AS T2 ON T1.station_nbr = T2.station_nbr WHERE sunrise IS NOT NULL AND T2.`date` LIKE '2012-02%' AND T1.station_nbr IN ( SELECT station_nbr FROM relation GROUP BY station_nbr HAVING COUNT(store_nbr) = 1 ) ORDER BY sunrise LIMIT 1"

@snoop
def execute_chain() -> tuple[tuple, ...]:
    # Load data
    df_relation = load_df(DB_ID, 'relation') # Alias T1
    df_weather = load_df(DB_ID, 'weather') # Alias T2

    # --- Subquery: Select station_nbr with COUNT(store_nbr) = 1 ---
    # GROUP BY station_nbr HAVING COUNT(store_nbr) = 1
    df_grouped_relation = build_group_by(df_relation, 'station_nbr')
    df_filtered_stations = op_eq(op_count(df_grouped_relation['store_nbr']), 1)
    
    # Get the station numbers from the filtered groups
    stations_with_one_store = df_filtered_stations.index.tolist()

    # --- WHERE clause for weather table: sunrise IS NOT NULL AND T2.`date` LIKE '2012-02%' ---
    # Filter for non-null sunrise
    df_weather_filtered_sunrise = df_weather[op_is_not_null(df_weather['sunrise'])]
    
    # Filter for February 2012 dates
    df_weather_filtered_date = df_weather_filtered_sunrise[op_like(op_cast(df_weather_filtered_sunrise['date'], 'TEXT'), '2012-02%')]

    # --- Filter weather data based on stations with one store ---
    df_weather_filtered_by_station = df_weather_filtered_date[op_in(df_weather_filtered_date['station_nbr'], stations_with_one_store)]

    # --- Find the earliest sunrise ---
    # ORDER BY sunrise LIMIT 1 implies finding the minimum sunrise
    if not df_weather_filtered_by_station.empty:
        earliest_sunrise_df = build_order_by(df_weather_filtered_by_station, ('sunrise', 'ASC'))
        earliest_sunrise = earliest_sunrise_df.iloc[0]['sunrise']
        result_list = [(earliest_sunrise,)]
    else:
        result_list = [] # No data found for the criteria

    return tuple(result_list)

sql_result = execute_sql(DB_ID, SQL_QUERY)
# 2. Python链
python_result = execute_chain()
# 3. 比较
results_match = compare_results(sql_result, python_result)
print('结果是否一致:', results_match)
print('SQL结果:', sql_result)
print('Python结果:', python_result)