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
DB_ID = "synthea"
SQL_QUERY = "SELECT COUNT(T2.patient) FROM all_prevalences AS T1 INNER JOIN conditions AS T2 ON lower(T1.ITEM) = lower(T2.DESCRIPTION) INNER JOIN immunizations AS T3 ON T2.PATIENT = T3.PATIENT GROUP BY T2.PATIENT ORDER BY T2.START DESC, T1.\"PREVALENCE RATE\" DESC LIMIT 1"

@snoop
def execute_chain() -> tuple[tuple, ...]:
    # Load data
    df_all_prevalences = load_df(DB_ID, 'all_prevalences') # T1
    df_conditions = load_df(DB_ID, 'conditions') # T2
    df_immunizations = load_df(DB_ID, 'immunizations') # T3

    # --- Step 1: Join all_prevalences (T1) and conditions (T2) ---
    # ON lower(T1.ITEM) = lower(T2.DESCRIPTION)
    # Perform a case-insensitive join by creating temporary lowercased columns.
    df_all_prevalences['ITEM_lower'] = op_lower(df_all_prevalences['ITEM'])
    df_conditions['DESCRIPTION_lower'] = op_lower(df_conditions['DESCRIPTION'])

    # Join T1 and T2. No overlapping column names besides the join keys.
    # So no suffixes are strictly needed for other columns, but _T1/_T2 is good practice for clarity.
    df_merged_1 = build_join(df_all_prevalences, df_conditions, 'inner',
                             left_on='ITEM_lower', right_on='DESCRIPTION_lower')
                             # Suffixes are applied to conflicting columns *not* in `left_on`/`right_on`.
                             # For this schema, `ITEM` and `DESCRIPTION` are join keys and unique to their tables,
                             # so no suffixes are needed for other columns 

    # --- Step 2: Prepare columns for ordering and identify the single target patient ---
    
    # Convert 'START' (from conditions table, T2) to datetime for proper sorting.
    # In df_merged_1, this column is named 'START'.
    df_merged_1['START_dt'] = op_to_datetime(df_merged_1['START'], errors='coerce')

    # Convert 'PREVALENCE RATE' (from all_prevalences table, T1) to numeric.
    # In df_merged_1, this column is named 'PREVALENCE RATE'.
    df_merged_1['PREVALENCE RATE_num'] = op_cast(df_merged_1['PREVALENCE RATE'], 'REAL')

    # Mimic SQLite's GROUP BY ... ORDER BY non-grouped_col ... LIMIT 1 behavior:
    # 1. Sort all merged records (conditions/prevalences) primarily by PATIENT, then by START_dt DESC, then PREVALENCE RATE_num DESC.
    # This places the "best" condition for each patient at the top of their respective block.
    df_ranked_conditions_per_patient = build_order_by(df_merged_1,
                                                      ('PATIENT', 'ASC'), # Group by PATIENT implicitly
                                                      ('START_dt', 'DESC'),
                                                      ('PREVALENCE RATE_num', 'DESC'))

    # 2. For each patient, select their "best" condition. This is achieved by dropping duplicates on PATIENT, keeping the first.
    df_best_condition_per_patient = df_ranked_conditions_per_patient.drop_duplicates(subset=['PATIENT'], keep='first')

    # 3. From these "best conditions" for each patient, identify the single target patient
    # by sorting them again by START_dt DESC and PREVALENCE RATE_num DESC and taking the top one.
    df_target_patient_info = build_order_by(df_best_condition_per_patient,
                                            ('START_dt', 'DESC'),
                                            ('PREVALENCE RATE_num', 'DESC'))
    
    # Check if df_target_patient_info is empty before accessing iloc[0]
    if df_target_patient_info.empty:
        return tuple([(0,)]) # No patient found, so 0 immunizations

    # Get the PATIENT ID of the single target patient (from the first row after final sort and limit).
    target_patient_id = df_target_patient_info['PATIENT'].iloc[0]

    # --- Step 3: Count immunizations for the identified target patient ---
    # Filter immunizations (T3) for this specific target_patient_id
    df_target_patient_immunizations = df_immunizations[op_eq(df_immunizations['PATIENT'], target_patient_id)]

    # Count the number of immunizations for this patient
    count_immunizations = op_count(df_target_patient_immunizations.index)

    # Format the result as a tuple of tuples
    result_list = [(count_immunizations,)]

    return tuple(result_list)


sql_result = execute_sql(DB_ID, SQL_QUERY)
# 2. Python链
python_result = execute_chain()
# 3. 比较
results_match = compare_results(sql_result, python_result)
print('结果是否一致:', results_match)
print('SQL结果:', sql_result)
print('Python结果:', python_result)