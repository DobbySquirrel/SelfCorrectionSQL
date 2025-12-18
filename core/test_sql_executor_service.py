#!/usr/bin/env python3
"""
测试SQL执行器服务

使用方法：
1. 启动服务：
   python core/sql_executor_service.py --port 5887

2. 运行测试：
   python core/test_sql_executor_service.py
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from core.sql_executor_client import SQLExecutorClient
import time


def test_health_check(client: SQLExecutorClient):
    """测试健康检查"""
    print("测试健康检查...")
    is_healthy = client.health_check()
    if is_healthy:
        print("✅ 服务健康")
    else:
        print("❌ 服务不健康")
    return is_healthy


def test_execute_sql(client: SQLExecutorClient, db_name: str = "flight_company"):
    """测试执行SQL"""
    print(f"\n测试执行SQL (数据库: {db_name})...")
    
    # 测试简单查询
    sql = "SELECT COUNT(*) as count FROM flight"
    print(f"SQL: {sql}")
    
    start_time = time.time()
    result, error = client.execute_sql(sql, db_name)
    elapsed = time.time() - start_time
    
    if error:
        print(f"❌ 执行失败: {error}")
        return False
    else:
        print(f"✅ 执行成功 (耗时: {elapsed:.3f}秒)")
        print(f"结果:\n{result}")
        return True


def test_compare_sql(client: SQLExecutorClient, db_name: str = "flight_company"):
    """测试比较SQL"""
    print(f"\n测试比较SQL (数据库: {db_name})...")
    
    sql1 = "SELECT COUNT(*) as count FROM flight"
    sql2 = "SELECT COUNT(*) as count FROM flight"
    
    print(f"SQL1: {sql1}")
    print(f"SQL2: {sql2}")
    
    start_time = time.time()
    match, message = client.compare_sql(sql1, sql2, db_name)
    elapsed = time.time() - start_time
    
    if match:
        print(f"✅ SQL结果匹配 (耗时: {elapsed:.3f}秒)")
        print(f"消息: {message}")
    else:
        print(f"❌ SQL结果不匹配 (耗时: {elapsed:.3f}秒)")
        print(f"消息: {message}")
    
    return match


def test_compact_result(client: SQLExecutorClient, db_name: str = "flight_company"):
    """测试压缩结果"""
    print(f"\n测试压缩结果 (数据库: {db_name})...")
    
    # 先执行SQL获取结果
    sql = "SELECT * FROM flight LIMIT 10"
    result, error = client.execute_sql(sql, db_name)
    
    if error:
        print(f"❌ 无法获取结果: {error}")
        return False
    
    print(f"原始结果行数: {len(result)}")
    
    # 压缩结果
    start_time = time.time()
    compacted = client.compact_result(result, max_length=500)
    elapsed = time.time() - start_time
    
    print(f"✅ 压缩成功 (耗时: {elapsed:.3f}秒)")
    print(f"压缩后长度: {len(compacted)}")
    print(f"压缩结果预览: {compacted[:200]}...")
    
    return True


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="测试SQL执行器服务")
    parser.add_argument("--service_url", type=str, default="http://localhost:5887", 
                       help="SQL执行器服务地址")
    parser.add_argument("--db_name", type=str, default="flight_company", 
                       help="测试数据库名称")
    args = parser.parse_args()
    
    print("=" * 60)
    print("SQL执行器服务测试")
    print("=" * 60)
    print(f"服务地址: {args.service_url}")
    print(f"测试数据库: {args.db_name}")
    print("=" * 60)
    
    # 创建客户端
    client = SQLExecutorClient(args.service_url)
    
    # 运行测试
    all_passed = True
    
    # 1. 健康检查
    if not test_health_check(client):
        print("\n❌ 服务不可用，请先启动服务:")
        print(f"   python core/sql_executor_service.py --port {args.service_url.split(':')[-1]}")
        return
    
    # 2. 执行SQL
    all_passed &= test_execute_sql(client, args.db_name)
    
    # 3. 比较SQL
    all_passed &= test_compare_sql(client, args.db_name)
    
    # 4. 压缩结果
    all_passed &= test_compact_result(client, args.db_name)
    
    # 总结
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ 所有测试通过！")
    else:
        print("❌ 部分测试失败")
    print("=" * 60)


if __name__ == "__main__":
    main()

