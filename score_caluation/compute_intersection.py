import json
import argparse
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

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
    # 获取项目根目录（score_caluation 的父目录）
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    
    parser = argparse.ArgumentParser(description='计算多种SQL生成方法的交集准确率')
    parser.add_argument('--straightforward_path', type=str, 
                        default=str(project_root / 'workflows/mcts_v1/test/out/1_18_fix_duplicate_name_strategy_force-s1_sql.json'), 
                        help='straightforward方法的预测结果路径')
    parser.add_argument('--ground_truth_path', type=str, 
                        default=str(project_root / 'data'), 
                        help='ground truth路径')
    parser.add_argument('--data_mode', type=str, default='sub_sampled_dev_gold.sql', help='数据模式')
    parser.add_argument('--db_root_path', type=str, default=None, 
                        help='数据库根路径（如果未提供，将从环境变量DB_ROOT_DIR读取）')
    parser.add_argument('--diff_json_path', type=str, 
                        default=str(project_root / 'data/sub_sampled_bird_dev_set.json'), 
                        help='难度分类JSON路径')
    args = parser.parse_args()
    
    # 如果没有提供 db_root_path，尝试从环境变量读取
    if args.db_root_path is None:
        db_root_from_env = os.getenv('DB_ROOT_DIR')
        if db_root_from_env:
            args.db_root_path = db_root_from_env
        else:
            # 默认使用项目相对路径
            args.db_root_path = str(project_root / 'data/dev_databases')
    
    evaluation_script = str(script_dir / "evaluation.py")
    
    # 从路径中提取文件名（不含扩展名）作为方法名，并加上_acc后缀
    straightforward_basename = os.path.splitext(os.path.basename(args.straightforward_path))[0]
    method_name = f"{straightforward_basename}_acc"
    
    # 获取straightforward_path所在的目录，用于保存error_analysis文件
    straightforward_dir = os.path.dirname(args.straightforward_path)
    
    methods = {
        method_name: args.straightforward_path,
    }

    for name, path in methods.items():
        error_analysis_default_output = "error_analysis.json"
        # 根据方法名确定保存目录：如果是straightforward相关的方法，保存到straightforward_path所在目录
        if name == method_name:
            save_dir = straightforward_dir
        else:
            save_dir = str(script_dir)  # 默认目录（score_caluation目录）
        error_analysis_unique_name = os.path.join(save_dir, f"error_analysis_{name}.json")
        
        print(f"Running evaluation for {name}...")
        # 确保环境变量传递给子进程
        env = os.environ.copy()
        if args.db_root_path:
            env['DB_ROOT_DIR'] = args.db_root_path
        # 使用 subprocess 而不是 os.system，以便传递环境变量
        import subprocess
        result = subprocess.run(
            ['python', evaluation_script,
             '--predicted_sql_path', path,
             '--ground_truth_path', args.ground_truth_path,
             '--data_mode', args.data_mode,
             '--db_root_path', args.db_root_path,
             '--diff_json_path', args.diff_json_path],
            env=env
        )
        if result.returncode != 0:
            print(f"警告: evaluation.py 返回了非零退出码: {result.returncode}")
        
        if os.path.exists(error_analysis_default_output):
            os.system(f"mv {error_analysis_default_output} {error_analysis_unique_name}")
            print(f"Moved {error_analysis_default_output} to {error_analysis_unique_name}")
        else:
            print(f"警告: {error_analysis_default_output} 未生成或未找到，请检查 {evaluation_script} 的输出。")
        print("-" * 30)

    all_error_analysis_results = {}
    for name in methods.keys():
        # 根据方法名确定加载目录
        if name == method_name:
            load_dir = straightforward_dir
        else:
            load_dir = str(script_dir)  # 默认目录（score_caluation目录）
        error_analysis_filename_to_load = os.path.join(load_dir, f"error_analysis_{name}.json")
        all_error_analysis_results[name] = load_json(error_analysis_filename_to_load)

    straightforward_analysis = all_error_analysis_results.get(method_name, {"error_details": [], "stats": {}})

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
