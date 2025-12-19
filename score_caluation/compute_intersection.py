import json
import argparse
import os

def load_json(file_path):
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
    parser.add_argument('--straightforward_path', type=str, default='/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_17_wo_schemaFilter.json', help='straightforward方法的预测结果路径')
    parser.add_argument('--ground_truth_path', type=str, default='/home/shenshuyu/SQL_tool_multiAgent/data/', help='ground truth路径')
    parser.add_argument('--data_mode', type=str, default='sub_sampled_dev_gold.sql', help='数据模式')
    parser.add_argument('--db_root_path',   type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/database/dev_databases/', help='数据库根路径')
    parser.add_argument('--diff_json_path', type=str, default='/home/shenshuyu/RSL_SQL/RSL-SQL/data/sub_sampled_bird_dev_set.json', help='难度分类JSON路径')
    args = parser.parse_args()
    evaluation_script = "/ssd/shenshuyu/SQL_tool/score_caluation/evaluation.py"
    error_analysis_dir = "/ssd/shenshuyu/SQL_tool/score_caluation/"
    methods = {
        "straightforward": args.straightforward_path,
    }

    for name, path in methods.items():
        error_analysis_default_output = "error_analysis.json"
        error_analysis_unique_name = os.path.join(error_analysis_dir, f"error_analysis_{name}.json")
        
        print(f"Running evaluation for {name}...")
        os.system(f"python {evaluation_script} --predicted_sql_path {path} --ground_truth_path {args.ground_truth_path} --data_mode {args.data_mode} --db_root_path {args.db_root_path} --diff_json_path {args.diff_json_path}")
        
        if os.path.exists(error_analysis_default_output):
            os.system(f"mv {error_analysis_default_output} {error_analysis_unique_name}")
            print(f"Moved {error_analysis_default_output} to {error_analysis_unique_name}")
        else:
            print(f"警告: {error_analysis_default_output} 未生成或未找到，请检查 {evaluation_script} 的输出。")
        print("-" * 30)

    all_error_analysis_results = {}
    for name in methods.keys():
        error_analysis_filename_to_load = os.path.join(error_analysis_dir, f"error_analysis_{name}.json")
        all_error_analysis_results[name] = load_json(error_analysis_filename_to_load)

    straightforward_analysis = all_error_analysis_results.get('straightforward', {"error_details": [], "stats": {}})

    diff_data = load_json(args.diff_json_path)
    if diff_data is None:
        print("致命错误：无法加载难度分类JSON文件，请检查路径和文件内容。程序将退出。")
        return
    
    total_queries = len(diff_data)

    difficulty_map = {}
    for item in diff_data:
        question_id = str(item.get('question_id', ''))
        if question_id:
            difficulty_map[question_id] = item.get('difficulty', 'unknown')

    all_ids = set(difficulty_map.keys())

    straightforward_correct_ids = {str(item['idx']) for item in (straightforward_analysis['error_details'] if straightforward_analysis and 'error_details' in straightforward_analysis else []) if item.get('success') == 1}

    straightforward_correct = len(straightforward_correct_ids)

    straightforward_evaluated_ids = {str(item['idx']) for item in (straightforward_analysis['error_details'] if straightforward_analysis and 'error_details' in straightforward_analysis else [])}

    straightforward_incorrect_ids = {str(item['idx']) for item in (straightforward_analysis['error_details'] if straightforward_analysis and 'error_details' in straightforward_analysis else []) if item.get('success') == 0}

    stats = {
        'simple': {'total': 0, 'straightforward': 0},
        'moderate': {'total': 0, 'straightforward': 0},
        'challenging': {'total': 0, 'straightforward': 0},
        'total': {'total': 0, 'straightforward': 0}
    }

    for q_id in all_ids:
        difficulty = difficulty_map.get(q_id, 'unknown')
        
        if difficulty in stats:
            stats[difficulty]['total'] += 1
            stats['total']['total'] += 1
            
            if q_id in straightforward_correct_ids:
                stats[difficulty]['straightforward'] += 1
                stats['total']['straightforward'] += 1
        else:
            print(f"警告：未知难度分类 '{difficulty}' 对于 question_id: {q_id}。该ID将不会被计入难度统计。")

    print("\n===== Straightforward方法准确率统计 =====")
    print(f"总查询数: {total_queries}")
    
    if total_queries > 0:
        print(f"straightforward正确数: {straightforward_correct} (准确率: {straightforward_correct/total_queries*100:.2f}%)")
    else:
        print("总查询数为0，无法计算准确率。请检查难度分类文件和数据模式。")
    
    print("\n===== 难度分类统计 =====")
    print("{:<10} {:<8} {:<15}".format("难度", "总数", "straightforward"))
    
    for diff in ['simple', 'moderate', 'challenging', 'total']:
        if diff in stats and stats[diff]['total'] > 0:
            print("{:<12} {:<10} {:<15.2f}%".format(
                diff, 
                stats[diff]['total'],
                stats[diff]['straightforward'] / stats[diff]['total'] * 100
            ))
        elif diff in stats:
            print("{:<12} {:<10} {:<15.2f}%".format(
                diff, 
                stats[diff]['total'],
                0.00
            ))

    result = {
        'stats': stats,
        'straightforward_correct': list(straightforward_correct_ids),
    }

if __name__ == "__main__":
    main()
