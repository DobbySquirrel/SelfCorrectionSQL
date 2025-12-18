#!/usr/bin/env python3
"""
SQL执行器性能对比测试

对比服务模式和传统模式的速度

使用方法：
1. 启动服务：
   python core/sql_executor_service.py --port 5887

2. 运行性能测试：
   python core/benchmark_sql_executor.py --db_name california_schools
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import time
import statistics
from typing import List, Tuple
import argparse

# 传统模式
from core.database_connector import DatabaseConnector

# 服务模式
from core.database_connector_service import DatabaseConnectorService


def benchmark_traditional_mode(db_name: str, sqls: List[str], num_runs: int = 5) -> Tuple[float, List[float]]:
    """测试传统模式"""
    print(f"\n【传统模式测试】")
    print(f"数据库: {db_name}")
    print(f"SQL数量: {len(sqls)}")
    print(f"运行次数: {num_runs}")
    
    all_times = []
    
    for run in range(num_runs):
        print(f"\n第 {run + 1}/{num_runs} 轮...")
        run_times = []
        
        for i, sql in enumerate(sqls):
            # 每次创建新的连接器（模拟实际使用场景）
            connector = DatabaseConnector(db_name)
            
            start_time = time.time()
            result, error = connector.execute_query(sql)
            elapsed = time.time() - start_time
            
            run_times.append(elapsed)
            
            if error:
                print(f"  SQL {i+1} 失败: {error}")
            else:
                print(f"  SQL {i+1}: {elapsed:.3f}秒 (行数: {len(result) if result is not None else 0})")
            
            connector.disconnect()
        
        total_time = sum(run_times)
        all_times.append(total_time)
        print(f"  本轮总耗时: {total_time:.3f}秒")
    
    avg_time = statistics.mean(all_times)
    std_time = statistics.stdev(all_times) if len(all_times) > 1 else 0.0
    
    return avg_time, all_times


def benchmark_service_mode(db_name: str, sqls: List[str], service_url: str, num_runs: int = 5) -> Tuple[float, List[float]]:
    """测试服务模式"""
    print(f"\n【服务模式测试】")
    print(f"数据库: {db_name}")
    print(f"服务地址: {service_url}")
    print(f"SQL数量: {len(sqls)}")
    print(f"运行次数: {num_runs}")
    
    # 检查服务是否可用
    connector = DatabaseConnectorService(db_name, service_url=service_url, use_service=True)
    if not connector.use_service:
        print("❌ 服务不可用，无法进行服务模式测试")
        return None, []
    
    all_times = []
    
    for run in range(num_runs):
        print(f"\n第 {run + 1}/{num_runs} 轮...")
        run_times = []
        
        for i, sql in enumerate(sqls):
            # 复用同一个连接器（服务模式下连接器很轻量）
            start_time = time.time()
            result, error = connector.execute_query(sql)
            elapsed = time.time() - start_time
            
            run_times.append(elapsed)
            
            if error:
                print(f"  SQL {i+1} 失败: {error}")
            else:
                print(f"  SQL {i+1}: {elapsed:.3f}秒 (行数: {len(result) if result is not None else 0})")
        
        total_time = sum(run_times)
        all_times.append(total_time)
        print(f"  本轮总耗时: {total_time:.3f}秒")
    
    avg_time = statistics.mean(all_times)
    std_time = statistics.stdev(all_times) if len(all_times) > 1 else 0.0
    
    return avg_time, all_times


def main():
    parser = argparse.ArgumentParser(description="SQL执行器性能对比测试")
    parser.add_argument("--db_name", type=str, default="california_schools", help="测试数据库名称")
    parser.add_argument("--service_url", type=str, default="http://localhost:5887", help="SQL执行器服务地址")
    parser.add_argument("--num_runs", type=int, default=3, help="运行次数")
    parser.add_argument("--num_sqls", type=int, default=10, help="测试SQL数量")
    args = parser.parse_args()
    
    print("=" * 60)
    print("SQL执行器性能对比测试")
    print("=" * 60)
    print(f"测试数据库: {args.db_name}")
    print(f"服务地址: {args.service_url}")
    print(f"运行次数: {args.num_runs}")
    print(f"每轮SQL数量: {args.num_sqls}")
    print("=" * 60)
    
    # 准备测试SQL（使用california_schools数据库的表）
    test_sqls = [
        "SELECT COUNT(*) as count FROM schools",
        "SELECT * FROM schools LIMIT 5",
        "SELECT COUNT(*) as count FROM frpm",
        "SELECT * FROM frpm LIMIT 5",
        "SELECT COUNT(*) as count FROM satscores",
        "SELECT * FROM satscores LIMIT 5",
        "SELECT COUNT(*) as count FROM schools WHERE County = 'Los Angeles'",
        "SELECT * FROM schools WHERE County = 'Los Angeles' LIMIT 5",
        "SELECT COUNT(*) as count FROM schools s JOIN frpm f ON s.CDSCode = f.CDSCode",
        "SELECT * FROM schools s JOIN frpm f ON s.CDSCode = f.CDSCode LIMIT 5"
    ]
    
    # 如果指定的SQL数量少于准备的SQL，只取前N个
    test_sqls = test_sqls[:args.num_sqls]
    
    # 测试传统模式
    print("\n" + "=" * 60)
    print("开始测试传统模式...")
    print("=" * 60)
    traditional_avg, traditional_times = benchmark_traditional_mode(args.db_name, test_sqls, args.num_runs)
    
    # 测试服务模式
    print("\n" + "=" * 60)
    print("开始测试服务模式...")
    print("=" * 60)
    service_avg, service_times = benchmark_service_mode(args.db_name, test_sqls, args.service_url, args.num_runs)
    
    # 对比结果
    print("\n" + "=" * 60)
    print("性能对比结果")
    print("=" * 60)
    
    if service_avg is not None:
        print(f"传统模式:")
        print(f"  平均耗时: {traditional_avg:.3f}秒")
        print(f"  标准差: {statistics.stdev(traditional_times) if len(traditional_times) > 1 else 0.0:.3f}秒")
        print(f"  各轮耗时: {[f'{t:.3f}' for t in traditional_times]}")
        
        print(f"\n服务模式:")
        print(f"  平均耗时: {service_avg:.3f}秒")
        print(f"  标准差: {statistics.stdev(service_times) if len(service_times) > 1 else 0.0:.3f}秒")
        print(f"  各轮耗时: {[f'{t:.3f}' for t in service_times]}")
        
        speedup = traditional_avg / service_avg if service_avg > 0 else 0
        improvement = (traditional_avg - service_avg) / traditional_avg * 100 if traditional_avg > 0 else 0
        
        print(f"\n性能提升:")
        print(f"  加速比: {speedup:.2f}x")
        print(f"  提升百分比: {improvement:.1f}%")
        
        if speedup > 1:
            print(f"  ✅ 服务模式更快！")
        elif speedup < 1:
            print(f"  ⚠️ 传统模式更快（可能是网络延迟）")
        else:
            print(f"  ➡️ 性能相当")
    else:
        print("❌ 服务模式测试失败，无法对比")
    
    print("=" * 60)


if __name__ == "__main__":
    main()

