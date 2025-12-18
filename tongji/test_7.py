import pandas as pd
import sqlite3
import math
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.sql_atomic_operators import *
from utils.compare_fun import compare_results
import snoop
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

DB_ID = "movie_platform"
SQL_QUERY = "SELECT T3.user_avatar_image_url, T3.rating_date_utc FROM movies AS T1 INNER JOIN ratings AS T2 ON T1.movie_id = T2.movie_id INNER JOIN ratings_users AS T3 ON T3.user_id = T2.user_id WHERE T3.user_id = 41579158 ORDER BY T3.rating_date_utc DESC LIMIT 1"

@snoop
def execute_chain() -> tuple[tuple, ...]:
    # Load all necessary tables
    df_movies = load_df(DB_ID, 'movies') # T1
    df_ratings = load_df(DB_ID, 'ratings') # T2
    df_ratings_users = load_df(DB_ID, 'ratings_users') # T3

    # First INNER JOIN: movies (T1) with ratings (T2) on movie_id
    # 'movie_id' is the common column and join key.
    df_merged1 = build_join(df_movies, df_ratings, 'inner', left_on='movie_id', right_on='movie_id')
    
    # Second INNER JOIN: the result of the first join (df_merged1) with ratings_users (T3) on user_id
    # 'user_id' is the common column and join key.
    # After this join, there will be a single 'user_id' column if it was the explicit join key,
    # and `user_avatar_image_url` (from T3) and `rating_date_utc` (from T2) will be available.
    df_final_merged = build_join(df_merged1, df_ratings_users, 'inner', left_on='user_id', right_on='user_id')
    
    # Filter for the specific user_id = 41579158
    df_filtered_user = df_final_merged[op_eq(df_final_merged['user_id'], 41579158)]
    
    # Order the results by 'rating_date_utc' in descending order to find the latest rating
    # Note: SQL query specifies T3.rating_date_utc, but 'rating_date_utc' is in 'ratings' (T2).
    # Assuming 'rating_date_utc' column is directly available after merges.
    df_ordered = build_order_by(df_filtered_user, ('rating_date_utc', 'DESC'))
    
    # Limit the result to the top 1 row, which will be the latest rating for the user
    df_limited = build_limit(df_ordered, 1)
    
    # Select the required columns: user_avatar_image_url and rating_date_utc
    # Note: SQL query specifies T3.user_avatar_image_url and T3.rating_date_utc.
    # We select these columns as they are named after the merge.
    df_result = df_limited[['user_avatar_image_url', 'rating_date_utc']]
    
    # Convert the resulting DataFrame to a tuple of tuples
    return tuple(df_result.itertuples(index=False, name=None))

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