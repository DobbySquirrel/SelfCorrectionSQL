import json
import numpy as np

def detailed_timing_stats(json_file_path):
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    total_time = 0
    rollout_time = 0
    cte_time = 0
    sql_time = 0
    db_time = 0
    query_count = 0
    
    # 收集每条查询的时间数据
    total_times = []
    rollout_times = []
    cte_times = []
    sql_times = []
    db_times = []
    
    for key, item in data.items():
        if 'stats' in item and 'timing' in item['stats']:
            timing = item['stats']['timing']
            total_t = timing.get('total_s', 0)
            rollout_t = timing.get('rollout_s', 0)
            cte_t = timing.get('cte_gen_s', 0)
            sql_t = timing.get('sql_gen_s', 0)
            db_t = timing.get('db_exec_s', 0)
            
            total_time += total_t
            rollout_time += rollout_t
            cte_time += cte_t
            sql_time += sql_t
            db_time += db_t
            query_count += 1
            
            total_times.append(total_t)
            rollout_times.append(rollout_t)
            cte_times.append(cte_t)
            sql_times.append(sql_t)
            db_times.append(db_t)
    
    if query_count == 0:
        print("未找到有效数据")
        return
    
    # 转换为numpy数组便于计算
    total_times = np.array(total_times)
    rollout_times = np.array(rollout_times)
    cte_times = np.array(cte_times)
    sql_times = np.array(sql_times)
    db_times = np.array(db_times)
    
    print(f"总查询数: {query_count}")
    print(f"总耗时: {total_time/3600:.2f} 小时")
    print()
    
    # 定义时间分布区间（秒）
    time_ranges = [0, 10, 30, 60, 300, 600, 1800, 3600, float('inf')]
    range_names = ['<10秒', '10-30秒', '30-60秒', '1-5分钟', '5-10分钟', '10-30分钟', '30-60分钟', '>1小时']
    
    def print_time_distribution(times, phase_name):
        print(f"{phase_name}时间分布:")
        for i in range(len(time_ranges)-1):
            count = np.sum((times >= time_ranges[i]) & (times < time_ranges[i+1]))
            percentage = (count / len(times)) * 100
            if count > 0:
                print(f"  {range_names[i]}: {count} 个查询 ({percentage:.1f}%)")
    
    # 各阶段时间分布
    print_time_distribution(total_times, "总")
    print()
    print_time_distribution(rollout_times, "Rollout")
    print()
    print_time_distribution(cte_times, "CTE生成")
    print()
    print_time_distribution(sql_times, "SQL生成")
    print()
    print_time_distribution(db_times, "DB执行")
    print()
    
    # 各阶段时间占比统计
    print("各阶段时间占比统计:")
    phases = [
        ('Rollout', rollout_time, rollout_times),
        ('CTE生成', cte_time, cte_times), 
        ('SQL生成', sql_time, sql_times),
        ('DB执行', db_time, db_times)
    ]
    
    for name, phase_total, phase_times in phases:
        percentage = (phase_total / total_time) * 100
        print(f"{name}: {phase_total/3600:.2f} 小时 ({percentage:.1f}%)")
        print(f"  平均: {np.mean(phase_times):.2f}秒, 最大: {np.max(phase_times):.2f}秒, 最小: {np.min(phase_times):.2f}秒")
    
    print()
    
    # 最耗时的查询
    print("最耗时的前10个查询:")
    query_data = []
    for key, item in data.items():
        if 'stats' in item and 'timing' in item['stats']:
            timing = item['stats']['timing']
            query_data.append({
                'id': key,
                'total': timing.get('total_s', 0),
                'rollout': timing.get('rollout_s', 0),
                'cte': timing.get('cte_gen_s', 0),
                'sql': timing.get('sql_gen_s', 0),
                'db': timing.get('db_exec_s', 0)
            })
    
    # 按总时间排序
    query_data.sort(key=lambda x: x['total'], reverse=True)
    
    for i, q in enumerate(query_data[:10]):
        print(f"  {i+1}. 查询{q['id']}: {q['total']:.1f}秒 (R:{q['rollout']:.1f}s, C:{q['cte']:.1f}s, S:{q['sql']:.1f}s, D:{q['db']:.1f}s)")

# 使用
detailed_timing_stats("/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/11_11_timing.json")