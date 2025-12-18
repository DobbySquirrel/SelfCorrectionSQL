import json
import argparse
import os

def load_json(file_path):
    """
    Loads a JSON file from the given file path.
    Includes error handling for FileNotFoundError and json.JSONDecodeError.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"警告：文件未找到: {file_path}")
        return None
    except json.JSONDecodeError:
        print(f"警告：无法解码JSON文件: {file_path}")
        return None

def main():
    parser = argparse.ArgumentParser(description='计算多种SQL生成方法的交集准确率')
    parser.add_argument('--pandas_path', type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/predict/preliminary_sql1_thinking_pandas.json', help='pandas方法的预测结果路径')
    parser.add_argument('--python_path', type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/predict/preliminary_sql1_thinking_python.json', help='python方法的预测结果路径')
    parser.add_argument('--sqlalchemy_path', type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/predict/preliminary_sql1_thinking_sqlalchemy.json', help='sqlalchemy方法的预测结果路径')
    parser.add_argument('--random_path', type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/predict/preliminary_sql1_thinking_random.json', help='random方法的预测结果路径')
    parser.add_argument('--straightforward_path', type=str, default='/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_17_wo_schemaFilter.json', help='straightforward方法的预测结果路径')
    parser.add_argument('--ground_truth_path', type=str, default='/home/shenshuyu/SQL_tool_multiAgent/data/', help='ground truth路径')
    parser.add_argument('--data_mode', type=str, default='sub_sampled_dev_gold.sql', help='数据模式')
    # parser.add_argument('--data_mode', type=str, default='dev_gold_error.sql', help='数据模式')
    parser.add_argument('--db_root_path',   type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/database/dev_databases/', help='数据库根路径')
    # parser.add_argument('--diff_json_path', type=str, default='/home/shenshuyu/SQL_tool_multiAgent/data/sub_sampled_bird_dev_set_error.json', help='难度分类JSON路径')
    parser.add_argument('--diff_json_path', type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/data/sub_sampled_bird_dev_set.json', help='难度分类JSON路径')
    # AlphaSQL Top50 30个
    args = parser.parse_args()
# mv /ssd/shenshuyu/SQL_tool/score_caluation/error_analysis_straightforward.json  /home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_10_acc.json   
    evaluation_script = "/ssd/shenshuyu/SQL_tool/score_caluation/evaluation.py"
    error_analysis_dir = "/ssd/shenshuyu/SQL_tool/score_caluation/" # This is the directory where error_analysis.json files are saved/moved
    # Define a dictionary of methods and their corresponding predicted SQL paths
    methods = {
        "straightforward": args.straightforward_path,
        # "pandas": args.pandas_path,
        # "python": args.python_path,
        # "sqlalchemy": args.sqlalchemy_path,
        # "random": args.random_path,
    }

    # --- Run evaluation script for each method and move results to unique filenames ---
    for name, path in methods.items():
        # Expected default output filename from evaluation.py (generated in current working directory)
        error_analysis_default_output = "error_analysis.json"

        
        # Desired unique filename after moving (in the specified error_analysis_dir)
        error_analysis_unique_name = os.path.join(error_analysis_dir, f"error_analysis_{name}.json")
        
        print(f"Running evaluation for {name}...")
        # Execute the evaluation script
        os.system(f"python {evaluation_script} --predicted_sql_path {path} --ground_truth_path {args.ground_truth_path} --data_mode {args.data_mode} --db_root_path {args.db_root_path} --diff_json_path {args.diff_json_path}")
        
        # Move the generated error_analysis.json to a unique name in the specified directory
        if os.path.exists(error_analysis_default_output):
            os.system(f"mv {error_analysis_default_output} {error_analysis_unique_name}")
            print(f"Moved {error_analysis_default_output} to {error_analysis_unique_name}")
        else:
            print(f"警告: {error_analysis_default_output} 未生成或未找到，请检查 {evaluation_script} 的输出。")
        print("-" * 30) # Separator for readability

    # --- Load evaluation results (error_analysis.json) ---
    all_error_analysis_results = {}
    for name in methods.keys(): # Iterate through method names to load their unique error analysis files
        error_analysis_filename_to_load = os.path.join(error_analysis_dir, f"error_analysis_{name}.json")
        all_error_analysis_results[name] = load_json(error_analysis_filename_to_load)

    # Assign loaded results to individual variables, providing empty defaults if files weren't found or were corrupted
    # pandas_analysis = all_error_analysis_results.get('pandas', {"error_details": [], "stats": {}})
    # python_analysis = all_error_analysis_results.get('python', {"error_details": [], "stats": {}})
    # sqlalchemy_analysis = all_error_analysis_results.get('sqlalchemy', {"error_details": [], "stats": {}})
    # random_analysis = all_error_analysis_results.get('random', {"error_details": [], "stats": {}})
    straightforward_analysis = all_error_analysis_results.get('straightforward', {"error_details": [], "stats": {}})

    # --- Load difficulty classification information to get all possible query IDs ---
    diff_data = load_json(args.diff_json_path)
    if diff_data is None:
        print("致命错误：无法加载难度分类JSON文件，请检查路径和文件内容。程序将退出。")
        return # Exit if difficulty data is not loaded or is invalid
    
    total_queries = len(diff_data)

    # Create ID to difficulty mapping
    difficulty_map = {}
    for item in diff_data:
        question_id = str(item.get('question_id', ''))
        if question_id:
            difficulty_map[question_id] = item.get('difficulty', 'unknown')

    # Get all possible query IDs from difficulty data (these are the ground truth IDs)
    all_ids = set(difficulty_map.keys())

    # --- Determine correct query IDs based on error_analysis.json's 'success' field ---
    # pandas_correct_ids = {str(item['idx']) for item in (pandas_analysis['error_details'] if pandas_analysis and 'error_details' in pandas_analysis else []) if item.get('success') == 1}
    # python_correct_ids = {str(item['idx']) for item in (python_analysis['error_details'] if python_analysis and 'error_details' in python_analysis else []) if item.get('success') == 1}
    # sqlalchemy_correct_ids = {str(item['idx']) for item in (sqlalchemy_analysis['error_details'] if sqlalchemy_analysis and 'error_details' in sqlalchemy_analysis else []) if item.get('success') == 1}
    # random_correct_ids = {str(item['idx']) for item in (random_analysis['error_details'] if random_analysis and 'error_details' in random_analysis else []) if item.get('success') == 1}
    straightforward_correct_ids = {str(item['idx']) for item in (straightforward_analysis['error_details'] if straightforward_analysis and 'error_details' in straightforward_analysis else []) if item.get('success') == 1}

    # Calculate overall correct counts
    # pandas_correct = len(pandas_correct_ids)
    # python_correct = len(python_correct_ids)
    # sqlalchemy_correct = len(sqlalchemy_correct_ids)
    # random_correct = len(random_correct_ids)
    straightforward_correct = len(straightforward_correct_ids)

    # --- Calculate intersections and unions of correct query IDs ---
    # Intersection of all five methods (IDs that all methods got correct)
    # all_correct_ids = pandas_correct_ids & python_correct_ids & sqlalchemy_correct_ids & random_correct_ids & straightforward_correct_ids

    # Union of methods excluding 'random' (IDs that at least one of these methods got correct)
    # any_correct_ids = pandas_correct_ids | python_correct_ids | sqlalchemy_correct_ids | straightforward_correct_ids

    # --- Calculate differences between methods for specific insights ---
    # Queries where straightforward was wrong, but at least one of the other methods (pandas, python, sqlalchemy) was correct
    # To determine "wrong", we need to know the *total* set of relevant IDs for each method.
    # Since error_details seems to contain ALL evaluated queries with a success flag,
    # we can use the complement of correct_ids relative to all_ids for a method.
    
    # For robust difference calculation, let's get the set of IDs evaluated by each method from error_details
    # pandas_evaluated_ids = {str(item['idx']) for item in (pandas_analysis['error_details'] if pandas_analysis and 'error_details' in pandas_analysis else [])}
    # python_evaluated_ids = {str(item['idx']) for item in (python_analysis['error_details'] if python_analysis and 'error_details' in python_analysis else [])}
    # sqlalchemy_evaluated_ids = {str(item['idx']) for item in (sqlalchemy_analysis['error_details'] if sqlalchemy_analysis and 'error_details' in sqlalchemy_analysis else [])}
    # random_evaluated_ids = {str(item['idx']) for item in (random_analysis['error_details'] if random_analysis and 'error_details' in random_analysis else [])}
    straightforward_evaluated_ids = {str(item['idx']) for item in (straightforward_analysis['error_details'] if straightforward_analysis and 'error_details' in straightforward_analysis else [])}

    # Derive incorrect IDs based on the 'success' flag
    # pandas_incorrect_ids = {str(item['idx']) for item in (pandas_analysis['error_details'] if pandas_analysis and 'error_details' in pandas_analysis else []) if item.get('success') == 0}
    # python_incorrect_ids = {str(item['idx']) for item in (python_analysis['error_details'] if python_analysis and 'error_details' in python_analysis else []) if item.get('success') == 0}
    # sqlalchemy_incorrect_ids = {str(item['idx']) for item in (sqlalchemy_analysis['error_details'] if sqlalchemy_analysis and 'error_details' in sqlalchemy_analysis else []) if item.get('success') == 0}
    # For random, we'll assume it doesn't get anything right, or its 'success' flag is typically 0.
    # If the random analysis also has a 'success' flag, this logic works.
    # random_incorrect_ids = {str(item['idx']) for item in (random_analysis['error_details'] if random_analysis and 'error_details' in random_analysis else []) if item.get('success') == 0}
    straightforward_incorrect_ids = {str(item['idx']) for item in (straightforward_analysis['error_details'] if straightforward_analysis and 'error_details' in straightforward_analysis else []) if item.get('success') == 0}

    # straightforward_wrong_others_correct = (pandas_correct_ids | python_correct_ids | sqlalchemy_correct_ids) - straightforward_correct_ids
    # Queries where straightforward was correct, but all other methods (pandas, python, sqlalchemy) were wrong
    # straightforward_correct_others_wrong = straightforward_correct_ids - (pandas_correct_ids | python_correct_ids | sqlalchemy_correct_ids)
    
    # Queries where straightforward was wrong, but Pandas, Python, and SQLAlchemy were all correct
    # straightforward_wrong_all_others_correct = (pandas_correct_ids & python_correct_ids & sqlalchemy_correct_ids) - straightforward_correct_ids

    # --- Initialize difficulty-based statistics structure ---
    stats = {
        'simple': {'total': 0, 'straightforward': 0},
        'moderate': {'total': 0, 'straightforward': 0},
        'challenging': {'total': 0, 'straightforward': 0},
        'total': {'total': 0, 'straightforward': 0}
    }

    # --- Populate difficulty statistics ---
    for q_id in all_ids: # Iterate through all known question IDs from diff_data to ensure all queries are considered
        difficulty = difficulty_map.get(q_id, 'unknown')
        
        if difficulty in stats: # Only process if difficulty is one of the recognized categories
            stats[difficulty]['total'] += 1
            stats['total']['total'] += 1
            
            # Increment counts if the query ID is in the respective method's correct set
            # if q_id in pandas_correct_ids:
            #     stats[difficulty]['pandas'] += 1
            #     stats['total']['pandas'] += 1
            
            # if q_id in python_correct_ids:
            #     stats[difficulty]['python'] += 1
            #     stats['total']['python'] += 1
            
            # if q_id in sqlalchemy_correct_ids:
            #     stats[difficulty]['sqlalchemy'] += 1
            #     stats['total']['sqlalchemy'] += 1
            
            # if q_id in random_correct_ids:
            #     stats[difficulty]['random'] += 1
            #     stats['total']['random'] += 1
            
            if q_id in straightforward_correct_ids:
                stats[difficulty]['straightforward'] += 1
                stats['total']['straightforward'] += 1
            
            # if q_id in all_correct_ids: # For intersection of all methods
            #     stats[difficulty]['intersection'] += 1
            #     stats['total']['intersection'] += 1
            
            # if q_id in any_correct_ids: # For union of (pandas, python, sqlalchemy, straightforward)
            #     stats[difficulty]['union'] += 1
            #     stats['total']['union'] += 1
        else:
            print(f"警告：未知难度分类 '{difficulty}' 对于 question_id: {q_id}。该ID将不会被计入难度统计。")

    # --- Print Summary Results ---
    print("\n===== Straightforward方法准确率统计 =====")
    print(f"总查询数: {total_queries}")
    
    if total_queries > 0: # Ensure division by zero is avoided for accuracy calculations
        # print(f"pandas正确数: {pandas_correct} (准确率: {pandas_correct/total_queries*100:.2f}%)")
        # print(f"python正确数: {python_correct} (准确率: {python_correct/total_queries*100:.2f}%)")
        # print(f"sqlalchemy正确数: {sqlalchemy_correct} (准确率: {sqlalchemy_correct/total_queries*100:.2f}%)")
        # print(f"random正确数: {random_correct} (准确率: {random_correct/total_queries*100:.2f}%)")
        print(f"straightforward正确数: {straightforward_correct} (准确率: {straightforward_correct/total_queries*100:.2f}%)")
        # print(f"所有方法都正确的查询数: {len(all_correct_ids)} (交集准确率: {len(all_correct_ids)/total_queries*100:.2f}%)")
        # print(f"至少一种方法正确的查询数(不含random): {len(any_correct_ids)} (上界准确率: {len(any_correct_ids)/total_queries*100:.2f}%)")
    else:
        print("总查询数为0，无法计算准确率。请检查难度分类文件和数据模式。")
    
    # --- Print Method Differences Analysis ---
    # print("\n===== 方法间差异分析 =====")
    # print(f"\nstraightforward错误但至少一个其他方法正确的查询数: {len(straightforward_wrong_others_correct)}")
    # print(f"straightforward正确但所有其他方法都错误的查询数: {len(straightforward_correct_others_wrong)}")
    # print(f"straightforward错误但所有其他方法都正确的查询数: {len(straightforward_wrong_all_others_correct)}")
    
    # print("\nstraightforward错误但所有其他方法都正确的查询ID:")
    # if not straightforward_wrong_all_others_correct:
    #     print("无")
    # else:
    #     for idx in sorted(straightforward_wrong_all_others_correct):
    #         difficulty = difficulty_map.get(idx, '未知')
    #         print(f"ID: {idx}, 难度: {difficulty}")
    
    # print("\nstraightforward正确但所有其他方法都错误的查询ID:")
    # if not straightforward_correct_others_wrong:
    #     print("无")
    # else:
    #     for idx in sorted(straightforward_correct_others_wrong):
    #         difficulty = difficulty_map.get(idx, '未知')
    #         print(f"ID: {idx}, 难度: {difficulty}")
    
    # --- Print Difficulty-based Statistics ---
    print("\n===== 难度分类统计 =====")
    # Adjusted column padding for better alignment in the console output
    print("{:<10} {:<8} {:<15}".format("难度", "总数", "straightforward"))
    
    for diff in ['simple', 'moderate', 'challenging', 'total']:
        if diff in stats and stats[diff]['total'] > 0:
            print("{:<12} {:<10} {:<15.2f}%".format(
                diff, 
                stats[diff]['total'],
                stats[diff]['straightforward'] / stats[diff]['total'] * 100
            ))
        elif diff in stats: # For cases where total is 0, print 0 for percentages
            print("{:<12} {:<10} {:<15.2f}%".format(
                diff, 
                stats[diff]['total'],
                0.00
            ))
            
    # --- Calculate unions ---
    # straightforward and sqlalchemy
    # straightforward_sqlalchemy_union = straightforward_correct_ids | sqlalchemy_correct_ids
    # straightforward_sqlalchemy_accuracy = len(straightforward_sqlalchemy_union) / total_queries * 100

    # sqlalchemy and python
    # sqlalchemy_python_union = sqlalchemy_correct_ids | python_correct_ids
    # sqlalchemy_python_accuracy = len(sqlalchemy_python_union) / total_queries * 100

    # straightforward and python
    # straightforward_python_union = straightforward_correct_ids | python_correct_ids
    # straightforward_python_accuracy = len(straightforward_python_union) / total_queries * 100

    # straightforward, sqlalchemy and random
    # straightforward_sqlalchemy_random_union = straightforward_correct_ids | sqlalchemy_correct_ids | random_correct_ids
    # straightforward_sqlalchemy_random_accuracy = len(straightforward_sqlalchemy_random_union) / total_queries * 100

    # straightforward, sqlalchemy and pandas
    # straightforward_sqlalchemy_pandas_union = straightforward_correct_ids | sqlalchemy_correct_ids | pandas_correct_ids
    # straightforward_sqlalchemy_pandas_accuracy = len(straightforward_sqlalchemy_pandas_union) / total_queries * 100

    # straightforward, sqlalchemy and python
    # straightforward_sqlalchemy_python_union = straightforward_correct_ids | sqlalchemy_correct_ids | python_correct_ids
    # straightforward_sqlalchemy_python_accuracy = len(straightforward_sqlalchemy_python_union) / total_queries * 100
    # straightforward, sqlalchemy and python and pandas
    # straightforward_sqlalchemy_python_Pandas_union = straightforward_correct_ids | sqlalchemy_correct_ids | python_correct_ids | random_correct_ids
    # straightforward_sqlalchemy_python_Pandas_accuracy = len(straightforward_sqlalchemy_python_Pandas_union) / total_queries * 100

    # 将新的结果添加到打印输出中
    # print("\n===== 额外并集分析 =====")
    # print(f"Straightforward | SQLAlchemy 并集数: {len(straightforward_sqlalchemy_union)} (准确率: {straightforward_sqlalchemy_accuracy:.2f}%)")
    # print(f"SQLAlchemy | Python 并集数: {len(sqlalchemy_python_union)} (准确率: {sqlalchemy_python_accuracy:.2f}%)")
    # print(f"Straightforward | Python 并集数: {len(straightforward_python_union)} (准确率: {straightforward_python_accuracy:.2f}%)")
    # print(f"Straightforward | SQLAlchemy | Random 并集数: {len(straightforward_sqlalchemy_random_union)} (准确率: {straightforward_sqlalchemy_random_accuracy:.2f}%)")
    # print(f"Straightforward | SQLAlchemy | Pandas 并集数: {len(straightforward_sqlalchemy_pandas_union)} (准确率: {straightforward_sqlalchemy_pandas_accuracy:.2f}%)")
    # print(f"Straightforward | SQLAlchemy | Python 并集数: {len(straightforward_sqlalchemy_python_union)} (准确率: {straightforward_sqlalchemy_python_accuracy:.2f}%)")
    # print(f"Straightforward | SQLAlchemy | Python | Random 并集数: {len(straightforward_sqlalchemy_python_Pandas_union)} (准确率: {straightforward_sqlalchemy_python_Pandas_accuracy:.2f}%)")



    # --- Save Detailed Results to JSON ---
    result = {
        'stats': stats,
        'straightforward_correct': list(straightforward_correct_ids),
    }

if __name__ == "__main__":
    main()