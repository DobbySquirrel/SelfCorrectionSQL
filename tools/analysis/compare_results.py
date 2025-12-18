import json
from pathlib import Path

def load_json_file(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)

def compare_results():
    # 加载两个文件
    file_6_13 = Path('/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_13/generation_1_pred_major_top2_sqls.json')
    file_6_14 = Path('/home/shenshuyu/SQL_tool/csc_sql/outputs/genetic_output_6_14/generation_1_pred_major_top2_sqls.json')
    
    data_6_13 = load_json_file(file_6_13)
    data_6_14 = load_json_file(file_6_14)
    
    # 创建question_id到结果的映射
    results_6_13 = {item['question_id']: item['correctness'] for item in data_6_13}
    results_6_14 = {item['question_id']: item['correctness'] for item in data_6_14}
    
    # 找出6_13正确但6_14错误的题目
    interesting_cases = []
    for q_id in results_6_13:
        if q_id in results_6_14:
            if results_6_13[q_id] == 1 and results_6_14[q_id] == 0:
                interesting_cases.append(q_id)
    
    print(f"在6_13正确但在6_14错误的题目数量: {len(interesting_cases)}")
    print("题目ID列表:", interesting_cases)
    
    # 打印详细信息
    print("\n详细信息:")
    for q_id in interesting_cases:
        case_6_13 = next(item for item in data_6_13 if item['question_id'] == q_id)
        case_6_14 = next(item for item in data_6_14 if item['question_id'] == q_id)
        
        print(f"\n题目ID: {q_id}")
        print("6_13的SQL:")
        for sql in case_6_13['sql']:
            print(f"- {sql}")
        print("6_14的SQL:")
        for sql in case_6_14['sql']:
            print(f"- {sql}")

if __name__ == "__main__":
    compare_results() 