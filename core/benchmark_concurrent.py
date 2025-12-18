#!/usr/bin/env python3
"""
SQL执行器并发性能对比测试

对比服务模式和传统模式在并发场景下的性能

使用方法：
1. 启动服务：
   python core/sql_executor_service.py --port 5887

2. 运行并发测试：
   python core/benchmark_concurrent.py --db_name california_schools --num_queries 20 --num_workers 5
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import time
import statistics
from typing import List, Tuple
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# 传统模式
from core.database_connector import DatabaseConnector

# 服务模式
from core.database_connector_service import DatabaseConnectorService


def execute_sql_traditional(db_name: str, sql: str) -> Tuple[float, bool]:
    """传统模式执行SQL"""
    start_time = time.time()
    try:
        connector = DatabaseConnector(db_name)
        result, error = connector.execute_query(sql)
        connector.disconnect()
        elapsed = time.time() - start_time
        return elapsed, error is None
    except Exception as e:
        elapsed = time.time() - start_time
        return elapsed, False


def execute_sql_service(connector: DatabaseConnectorService, sql: str) -> Tuple[float, bool]:
    """服务模式执行SQL"""
    start_time = time.time()
    try:
        result, error = connector.execute_query(sql)
        elapsed = time.time() - start_time
        return elapsed, error is None
    except Exception as e:
        elapsed = time.time() - start_time
        return elapsed, False


def benchmark_traditional_concurrent(db_name: str, sqls: List[str], num_workers: int) -> Tuple[float, List[float], int]:
    """测试传统模式并发执行"""
    print(f"\n【传统模式并发测试】")
    print(f"数据库: {db_name}")
    print(f"SQL数量: {len(sqls)}")
    print(f"并发数: {num_workers}")
    
    start_time = time.time()
    times = []
    success_count = 0
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(execute_sql_traditional, db_name, sql) for sql in sqls]
        
        for i, future in enumerate(as_completed(futures)):
            elapsed, success = future.result()
            times.append(elapsed)
            if success:
                success_count += 1
            print(f"  SQL {i+1}/{len(sqls)}: {elapsed:.3f}秒 {'✅' if success else '❌'}")
    
    total_time = time.time() - start_time
    return total_time, times, success_count


def benchmark_service_concurrent(db_name: str, sqls: List[str], service_url: str, num_workers: int) -> Tuple[float, List[float], int]:
    """测试服务模式并发执行"""
    print(f"\n【服务模式并发测试】")
    print(f"数据库: {db_name}")
    print(f"服务地址: {service_url}")
    print(f"SQL数量: {len(sqls)}")
    print(f"并发数: {num_workers}")
    
    # 创建连接器（服务模式下可以复用）
    connector = DatabaseConnectorService(db_name, service_url=service_url, use_service=True)
    if not connector.use_service:
        print("❌ 服务不可用，无法进行服务模式测试")
        return None, [], 0
    
    start_time = time.time()
    times = []
    success_count = 0
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(execute_sql_service, connector, sql) for sql in sqls]
        
        for i, future in enumerate(as_completed(futures)):
            elapsed, success = future.result()
            times.append(elapsed)
            if success:
                success_count += 1
            print(f"  SQL {i+1}/{len(sqls)}: {elapsed:.3f}秒 {'✅' if success else '❌'}")
    
    total_time = time.time() - start_time
    return total_time, times, success_count


def main():
    parser = argparse.ArgumentParser(description="SQL执行器并发性能对比测试")
    parser.add_argument("--db_name", type=str, default="california_schools", help="测试数据库名称")
    parser.add_argument("--service_url", type=str, default="http://localhost:5887", help="SQL执行器服务地址")
    parser.add_argument("--num_queries", type=int, default=20, help="测试SQL数量")
    parser.add_argument("--num_workers", type=int, default=5, help="并发线程数")
    args = parser.parse_args()
    
    print("=" * 60)
    print("SQL执行器并发性能对比测试")
    print("=" * 60)
    print(f"测试数据库: {args.db_name}")
    print(f"服务地址: {args.service_url}")
    print(f"SQL数量: {args.num_queries}")
    print(f"并发数: {args.num_workers}")
    print("=" * 60)
    
    # 准备测试SQL
    base_sqls = [
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
    
    # 重复SQL以达到指定数量
    test_sqls = (base_sqls * ((args.num_queries // len(base_sqls)) + 1))[:args.num_queries]
    
    # 测试传统模式
    print("\n" + "=" * 60)
    print("开始测试传统模式（并发）...")
    print("=" * 60)
    traditional_total, traditional_times, traditional_success = benchmark_traditional_concurrent(
        args.db_name, test_sqls, args.num_workers
    )
    
    # 测试服务模式
    print("\n" + "=" * 60)
    print("开始测试服务模式（并发）...")
    print("=" * 60)
    service_total, service_times, service_success = benchmark_service_concurrent(
        args.db_name, test_sqls, args.service_url, args.num_workers
    )
    
    # 对比结果
    print("\n" + "=" * 60)
    print("并发性能对比结果")
    print("=" * 60)
    
    if service_total is not None:
        print(f"传统模式:")
        print(f"  总耗时: {traditional_total:.3f}秒")
        print(f"  平均单SQL耗时: {statistics.mean(traditional_times):.3f}秒")
        print(f"  成功数: {traditional_success}/{len(test_sqls)}")
        print(f"  吞吐量: {len(test_sqls)/traditional_total:.2f} SQL/秒")
        
        print(f"\n服务模式:")
        print(f"  总耗时: {service_total:.3f}秒")
        print(f"  平均单SQL耗时: {statistics.mean(service_times):.3f}秒")
        print(f"  成功数: {service_success}/{len(test_sqls)}")
        print(f"  吞吐量: {len(test_sqls)/service_total:.2f} SQL/秒")
        
        speedup = traditional_total / service_total if service_total > 0 else 0
        improvement = (traditional_total - service_total) / traditional_total * 100 if traditional_total > 0 else 0
        
        print(f"\n性能提升:")
        print(f"  总耗时加速比: {speedup:.2f}x")
        print(f"  总耗时提升百分比: {improvement:.1f}%")
        
        if speedup > 1:
            print(f"  ✅ 服务模式更快！")
        elif speedup < 1:
            print(f"  ⚠️ 传统模式更快（可能是网络延迟或连接开销）")
        else:
            print(f"  ➡️ 性能相当")
        
        # 分析并发效果
        traditional_avg = statistics.mean(traditional_times)
        service_avg = statistics.mean(service_times)
        
        print(f"\n并发效果分析:")
        print(f"  传统模式平均单SQL: {traditional_avg:.3f}秒")
        print(f"  服务模式平均单SQL: {service_avg:.3f}秒")
        print(f"  传统模式理论串行时间: {traditional_avg * len(test_sqls):.3f}秒")
        print(f"  服务模式理论串行时间: {service_avg * len(test_sqls):.3f}秒")
        print(f"  传统模式并发效率: {(traditional_avg * len(test_sqls)) / traditional_total:.2f}x")
        print(f"  服务模式并发效率: {(service_avg * len(test_sqls)) / service_total:.2f}x")
    else:
        print("❌ 服务模式测试失败，无法对比")
    
    print("=" * 60)


if __name__ == "__main__":
    main()

