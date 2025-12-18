import sys
import json
import argparse
import sqlite3
import multiprocessing as mp
from func_timeout import func_timeout, FunctionTimedOut
from tqdm import tqdm
import os


def load_json(dir):
    with open(dir, 'r') as j:
        contents = json.loads(j.read())
    return contents


def result_callback(result):
    exec_result.append(result)


def execute_sql(predicted_sql, ground_truth, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(ground_truth)
        ground_truth_res = cursor.fetchall()
        cursor.execute(predicted_sql)
        predicted_res = cursor.fetchall()
        res = 0
        if set(predicted_res) == set(ground_truth_res):
            res = 1
        return {
            'status': 'success',
            'correct': res,
            'predicted_res': predicted_res,
            'ground_truth_res': ground_truth_res
        }
    except Exception as e:
        return {
            'status': 'error',
            'error_msg': str(e),
            'correct': 0,
            'ground_truth_res': ground_truth_res
        }


def execute_model(predicted_sql, ground_truth, db_place, sql_id, meta_time_out):
    try:
        res = func_timeout(meta_time_out, execute_sql,
                          args=(predicted_sql, ground_truth, db_place))
    except FunctionTimedOut:
        res = {
            'status': 'timeout',
            'correct': 0
        }
    except Exception as e:
        res = {
            'status': 'error',
            'error_msg': str(e),
            'correct': 0
        }
    
    return {
        'sql_id': sql_id,
        'predicted_sql': predicted_sql,
        'ground_truth': ground_truth,
        **res
    }


def package_sqls(sql_path, db_root_path, mode='gpt', data_mode='dev'):
    clean_sqls = []
    db_path_list = []
    sql_ids = []  # 新增：存储SQL的ID
    
    if mode == 'gpt':
        sql_data = json.load(open(sql_path, 'r'))
        for idx, (sql_id, sql_str) in enumerate(sql_data.items()):
            if type(sql_str) == str:
                sql, db_name = sql_str.split('\t----- bird -----\t')
            else:
                sql, db_name = " ", "financial"
            clean_sqls.append(sql)
            db_path_list.append(db_root_path + db_name + '/' + db_name + '.sqlite')
            sql_ids.append(sql_id)  # 存储JSON中的键作为ID

    elif mode == 'gt':
        sqls = open(sql_path + data_mode)
        sql_txt = sqls.readlines()
        # sql_txt = [sql.split('\t')[0] for sql in sql_txt]
        for idx, sql_str in enumerate(sql_txt):
            sql, db_name = sql_str.strip().split('\t')
            clean_sqls.append(sql)
            db_path_list.append(db_root_path + db_name + '/' + db_name + '.sqlite')
            sql_ids.append(str(idx))  # 对于gt模式，仍使用索引作为ID

    return clean_sqls, db_path_list, sql_ids  # 返回SQL ID列表


def run_sqls_parallel(sqls, db_places, sql_ids, num_cpus=1, meta_time_out=30.0):
    pool = mp.Pool(processes=num_cpus)
    pbar = tqdm(total=len(sqls), desc="执行SQL查询")
    
    def update_pbar(result):
        exec_result.append(result)
        pbar.update(1)
    
    for i, sql_pair in enumerate(sqls):
        predicted_sql, ground_truth = sql_pair
        pool.apply_async(execute_model, 
                        args=(predicted_sql, ground_truth, db_places[i], sql_ids[i], meta_time_out),
                        callback=update_pbar)
    pool.close()
    pool.join()
    pbar.close()


def sort_results(list_of_dicts):
    return sorted(list_of_dicts, key=lambda x: x['sql_id'])



def analyze_errors(results, diff_json_path, output_path, db_paths=None):
    contents = json.load(open(diff_json_path, 'r'))
    
    # 创建一个以question_id为键的字典
    contents_dict = {}
    for content in contents:
        if 'question_id' in content:
            contents_dict[str(content['question_id'])] = content
        else:
            # 如果没有question_id，则使用索引作为备选
            contents_dict[str(content.get('id', contents.index(content)))] = content
    
    # 创建数据库路径字典（如果提供）
    db_path_dict = {}
    if db_paths:
        for sql_id, db_path in zip(sql_ids, db_paths):
            db_path_dict[sql_id] = db_path
    
    # 确保结果和内容的长度匹配
    if len(results) != len(contents):
        print(f"警告: 结果数量 ({len(results)}) 与内容数量 ({len(contents)}) 不匹配")
    
    # # 对结果进行排序
    # sorted_results = sort_results(results)
    
    error_details = []
    stats = {
        'timeout': 0,
        'syntax_error': 0,
        'wrong_result': 0,
        'success': 0
    }
    
    for res in results:
        sql_id = res['sql_id']
        
        if res['status'] == 'timeout':
            stats['timeout'] += 1
        elif res['status'] == 'error':
            stats['syntax_error'] += 1
        elif res['correct'] == 0:
            stats['wrong_result'] += 1
        else:
            stats['success'] += 1
        
        # 尝试使用sql_id匹配question_id
        content = contents_dict.get(sql_id)
        
        # 如果没有直接匹配，尝试在contents中查找匹配的question_id
        if not content:
            for c in contents:
                if str(c.get('question_id', '')) == sql_id:
                    content = c
                    break
        
        if content:
            error_detail = {
                'idx': sql_id,
                "success": res['correct'],
                'question_id': str(content.get('question_id', '未知ID')),
                'question': content.get('question', '未知问题'),
                'evidence': content.get('evidence', '无证据'),
                'difficulty': content.get('difficulty', '未知难度'),
                'db_id': content.get('db_id', '未知数据库'),
                'db_path': db_path_dict.get(sql_id, '未知数据库路径') if db_path_dict else None,
                'predicted_sql': res['predicted_sql'],
                'ground_truth': res['ground_truth'],
                'error_message': res.get('error_msg', '') if res['status'] == 'error' else '',
                'predicted_res': res.get('predicted_res', []),
                'ground_truth_res': res.get('ground_truth_res', [])
            }
            error_details.append(error_detail)
    
    # 保存错误分析结果
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            'stats': stats,
            'error_details': error_details
        }, f, indent=4, ensure_ascii=False)
    
    return stats


def compute_acc_by_diff(exec_results, diff_json_path, predicted_sql_path):
    # 保存完整的执行结果

    num_queries = len(exec_results)
    results = [res['correct'] for res in exec_results]

    contents = load_json(diff_json_path)
    
    # 创建一个以question_id为键的字典
    contents_dict = {}
    for content in contents:
        if 'question_id' in content:
            contents_dict[str(content['question_id'])] = content
        else:
            contents_dict[str(content.get('id', contents.index(content)))] = content
    
    simple_results, moderate_results, challenging_results = [], [], []

    for res in exec_results:
        sql_id = res['sql_id']
        
        # 尝试使用sql_id匹配question_id
        content = contents_dict.get(sql_id)
        
        # 如果没有直接匹配，尝试在contents中查找匹配的question_id
        if not content:
            for c in contents:
                if str(c.get('question_id', '')) == sql_id:
                    content = c
                    break
        
        if content:
            if content['difficulty'] == 'simple':
                simple_results.append(res)
            elif content['difficulty'] == 'moderate':
                moderate_results.append(res)
            elif content['difficulty'] == 'challenging':
                challenging_results.append(res)
    
    # 处理可能的空列表情况
    simple_acc = sum([res['correct'] for res in simple_results]) / max(len(simple_results), 1)
    moderate_acc = sum([res['correct'] for res in moderate_results]) / max(len(moderate_results), 1)
    challenging_acc = sum([res['correct'] for res in challenging_results]) / max(len(challenging_results), 1)
    all_acc = sum(results) / num_queries
    count_lists = [len(simple_results), len(moderate_results), len(challenging_results), num_queries]

    # 错误分析
    error_stats = analyze_errors(exec_results, diff_json_path, 'error_analysis.json')
    
    return simple_acc * 100, moderate_acc * 100, challenging_acc * 100, all_acc * 100, count_lists, error_stats


def print_data(score_lists, count_lists, error_stats):
    levels = ['simple', 'moderate', 'challenging', 'total']
    print("{:20} {:20} {:20} {:20} {:20}".format("", *levels))
    print("{:20} {:<20} {:<20} {:<20} {:<20}".format('count', *count_lists))

    print('======================================    ACCURACY    =====================================')
    print("{:20} {:<20.2f} {:<20.2f} {:<20.2f} {:<20.2f}".format('accuracy', *score_lists))

    print('\n======================================    ERROR ANALYSIS    =====================================')
    print(f"超时次数: {error_stats['timeout']}")
    print(f"语法错误: {error_stats['syntax_error']}")
    print(f"结果错误: {error_stats['wrong_result']}")
    print(f"完全正确: {error_stats['success']}")
    print('\n详细错误信息已保存到 error_analysis.json')


if __name__ == '__main__':
    args_parser = argparse.ArgumentParser()
    args_parser.add_argument('--predicted_sql_path', type=str, required=True, default='Bird/')
    args_parser.add_argument('--ground_truth_path', type=str, required=True, default='Bird/dev_20240627/')
    args_parser.add_argument('--data_mode', type=str, required=True, default='dev')
    args_parser.add_argument('--db_root_path', type=str, required=True, default='Bird/dev_20240627/dev_databases/')
    args_parser.add_argument('--num_cpus', type=int, default=1)
    args_parser.add_argument('--meta_time_out', type=float, default=30.0)
    args_parser.add_argument('--mode_gt', type=str, default='gt')
    args_parser.add_argument('--mode_predict', type=str, default='gpt')
    args_parser.add_argument('--difficulty', type=str, default='simple')
    args_parser.add_argument('--diff_json_path', type=str, default='')
    args = args_parser.parse_args()

    # args.predicted_sql_path = '../predict/'
    # args.ground_truth_path = '../data/'
    # args.data_mode = 'dev'
    # args.db_root_path = '../database/dev_databases/'
    # args.diff_json_path = '../data/dev.json'
    # --predicted_sql_path ../predict/ --ground_truth_path ../data/ --data_mode dev --db_root_path ../database/dev_databases/ --diff_json_path ../data/dev.json
    # python evaluation/evaluation.py --predicted_sql_path ./predict/predict_dev.json --ground_truth_path ./data/ --data_mode dev --db_root_path ./database/dev_databases/ --diff_json_path ./data/dev.json

    exec_result = []

    pred_queries, db_paths, sql_ids = package_sqls(args.predicted_sql_path, args.db_root_path, mode=args.mode_predict,
                                          data_mode=args.data_mode)
    # generate gt sqls:·
    gt_queries, db_paths_gt, _ = package_sqls(args.ground_truth_path, args.db_root_path, mode='gt',
                                           data_mode=args.data_mode)

    query_pairs = list(zip(pred_queries, gt_queries))
    run_sqls_parallel(query_pairs, db_places=db_paths, sql_ids=sql_ids, num_cpus=args.num_cpus, meta_time_out=args.meta_time_out)
    # exec_result = sort_results(exec_result)


    print('开始计算评估指标')
    simple_acc, moderate_acc, challenging_acc, acc, count_lists, error_stats = \
        compute_acc_by_diff(exec_result, args.diff_json_path, args.predicted_sql_path)
    score_lists = [simple_acc, moderate_acc, challenging_acc, acc]
    print_data(score_lists, count_lists, error_stats)
    print('===========================================================================================')
    print("评估完成")

# python evaluation/evaluation.py --predicted_sql_path ./predict/predict_dev.json --ground_truth_path ./data/ --data_mode sub_sampled_dev_gold.sql --db_root_path ./database/dev_databases/ --diff_json_path ./data/sub_sampled_bird_dev_set.json