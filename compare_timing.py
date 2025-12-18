import json
import os
import statistics

# 文件路径
file_path = '/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/12_11.json'

def analyze_performance(path):
    if not os.path.exists(path):
        print(f"错误: 找不到文件 {path}")
        return

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 定义我们需要收集的指标字段
        metrics_map = {
            "total_s": [],      # 总耗时
            "rollout_s": [],    # 蒙特卡洛搜索/推理耗时
            "cte_gen_s": [],    # CTE (Common Table Expression) 生成耗时
            "sql_gen_s": [],    # SQL 最终生成耗时
            "db_exec_s": []     # 数据库执行耗时
        }
        
        valid_count = 0

        # 遍历数据收集数值
        for key, value in data.items():
            timing_data = value.get('stats', {}).get('timing', {})
            
            if not timing_data:
                continue

            valid_count += 1
            for metric_name in metrics_map.keys():
                # 如果某个字段不存在，默认记为 0，防止报错
                val = timing_data.get(metric_name, 0.0)
                metrics_map[metric_name].append(val)

        # 打印统计表格
        if valid_count > 0:
            print(f"\n成功读取条目数: {valid_count}")
            print("-" * 75)
            # 表头（单位：分钟）
            print(f"{'指标 (Metric)':<15} | {'平均耗时 (Avg, 分钟)':<20} | {'最大耗时 (Max, 分钟)':<20} | {'最小耗时 (Min, 分钟)':<20}")
            print("-" * 75)

            for metric, values in metrics_map.items():
                avg_val = statistics.mean(values) / 60.0  # 转换为分钟
                max_val = max(values) / 60.0  # 转换为分钟
                min_val = min(values) / 60.0  # 转换为分钟
                
                print(f"{metric:<15} | {avg_val:<20.4f} | {max_val:<20.4f} | {min_val:<20.4f}")
            
            print("-" * 75)
            
            # 显示总时长（所有条目的 total_s 之和）
            total_s_sum = sum(metrics_map["total_s"])  # 只计算 total_s 的总和（秒）
            total_sum_hours = total_s_sum / 3600.0  # 转换为小时
            total_sum_minutes = total_s_sum / 60.0
            print(f"\n总时长（所有条目 total_s 之和）: {total_sum_hours:.4f} 小时 ({total_sum_minutes:.4f} 分钟, {total_s_sum:.4f} 秒)")
            
            # 简单的瓶颈分析
            max_avg_metric = max(metrics_map, key=lambda k: statistics.mean(metrics_map[k]))
            max_avg_value_minutes = statistics.mean(metrics_map[max_avg_metric]) / 60.0
            print(f"\n[分析]: 根据平均值，最耗时的环节是 '{max_avg_metric}' ({max_avg_value_minutes:.4f} 分钟)")
            
        else:
            print("未找到有效数据。")

    except json.JSONDecodeError:
        print("错误: JSON 文件格式不正确")
    except Exception as e:
        print(f"发生错误: {e}")

if __name__ == "__main__":
    analyze_performance(file_path)