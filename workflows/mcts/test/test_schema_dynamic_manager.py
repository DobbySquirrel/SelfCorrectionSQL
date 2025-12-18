"""
测试Schema动态管理器的功能

测试内容：
1. 从JSON结果文件中提取CTE和SQL
2. 提取表名和列名
3. 测试动态扩充（添加邻居表）
4. 测试动态剪枝（移除未使用的表）
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Set, Any

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from workflows.mcts.utils.schema_dynamic_manager import (
    SchemaDynamicManager,
    load_relationships_map
)


def extract_tables_from_cte_path(cte_path: List[str]) -> Set[str]:
    """从CTE路径中提取所有表名"""
    all_tables = set()
    manager = SchemaDynamicManager()  # 临时创建用于提取表名
    
    for cte in cte_path:
        tables = manager.extract_tables_from_sql(cte)
        all_tables.update(tables)
    
    return all_tables


def extract_tables_from_sql(sql: str) -> Set[str]:
    """从SQL中提取表名"""
    manager = SchemaDynamicManager()
    return manager.extract_tables_from_sql(sql)


def extract_columns_from_cte(cte: str) -> Set[str]:
    """从CTE中提取列名（简化版）"""
    columns = set()
    
    # 匹配SELECT后的列
    import re
    select_pattern = r'SELECT\s+(.*?)\s+FROM'
    match = re.search(select_pattern, cte, re.IGNORECASE | re.DOTALL)
    if match:
        select_clause = match.group(1).strip()
        # 简单的列提取（处理反引号、别名等）
        # 移除DISTINCT等关键字
        select_clause = re.sub(r'\bDISTINCT\b', '', select_clause, flags=re.IGNORECASE)
        # 分割列（按逗号）
        cols = [col.strip().strip('`').split(' AS ')[0].split('.')[-1].strip() 
                for col in select_clause.split(',')]
        columns.update(cols)
    
    return columns


def create_mock_schema(tables: Set[str]) -> str:
    """创建模拟的schema（用于测试）"""
    schema_parts = []
    for table in sorted(tables):
        schema_parts.append(f"""
CREATE TABLE `{table}` (
    `id` INTEGER PRIMARY KEY,
    `name` TEXT,
    `cardKingdomFoilId` INTEGER,
    `cardKingdomId` INTEGER,
    `created_at` TIMESTAMP
);
""")
    return "\n".join(schema_parts)


def test_schema_dynamic_manager(json_file_path: str, db_id: str = None):
    """
    测试Schema动态管理器
    
    Args:
        json_file_path: JSON结果文件路径
        db_id: 数据库ID（用于加载relationships）
    """
    print("=" * 80)
    print("测试Schema动态管理器")
    print("=" * 80)
    
    # 1. 读取JSON文件
    print(f"\n[1] 读取JSON文件: {json_file_path}")
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 获取第一个问题的数据
    first_key = list(data.keys())[0]
    question_data = data[first_key]
    
    print(f"问题ID: {first_key}")
    print(f"最终SQL: {question_data.get('sql', 'N/A')[:100]}...")
    
    # 2. 提取rollout统计信息
    rollout_stats_list = question_data.get('rollout_stats', [])
    if not rollout_stats_list:
        print("⚠️ 没有rollout统计信息")
        return
    
    print(f"\n[2] 找到 {len(rollout_stats_list)} 个rollout")
    
    # 3. 提取所有CTE路径中的表
    all_tables_in_ctes = set()
    all_columns_in_ctes = set()
    cte_paths = []
    
    for idx, rollout_stats in enumerate(rollout_stats_list):
        cte_path = rollout_stats.get('cte_path', [])
        if cte_path:
            cte_paths.append(cte_path)
            tables = extract_tables_from_cte_path(cte_path)
            all_tables_in_ctes.update(tables)
            
            # 提取列
            for cte in cte_path:
                cols = extract_columns_from_cte(cte)
                all_columns_in_ctes.update(cols)
    
    print(f"\n[3] 提取的表名: {sorted(all_tables_in_ctes)}")
    print(f"提取的列名（前20个）: {sorted(list(all_columns_in_ctes))[:20]}")
    
    # 4. 加载关系映射
    print(f"\n[4] 加载关系映射...")
    if db_id is None:
        # 尝试从文件路径推断db_id
        # 或者使用默认值
        db_id = "card_trading"  # 根据cards表推测
        print(f"⚠️ 未提供db_id，使用默认值: {db_id}")
    
    relationships_map = load_relationships_map(db_id=db_id)
    if not relationships_map:
        print("⚠️ 无法加载关系映射，将使用空映射进行测试")
    
    # 5. 创建Schema动态管理器
    print(f"\n[5] 创建Schema动态管理器...")
    manager = SchemaDynamicManager(
        relationships_map=relationships_map,
        strike_threshold=3
    )
    
    # 6. 创建模拟的完整schema
    print(f"\n[6] 创建模拟schema...")
    # 添加一些额外的表用于测试动态扩充
    all_tables_for_schema = all_tables_in_ctes.copy()
    all_tables_for_schema.update(['card_sets', 'card_types', 'card_artists', 'card_prices'])
    original_schema = create_mock_schema(all_tables_for_schema)
    print(f"原始schema包含 {len(all_tables_for_schema)} 个表")
    
    # 7. 静态剪枝（模拟）
    print(f"\n[7] 静态剪枝测试...")
    simplified_tables = all_tables_in_ctes.copy()  # 假设只保留CTE中使用的表
    simplified_schema = create_mock_schema(simplified_tables)
    pruned_schema = manager.static_pruning(original_schema, simplified_schema)
    print(f"静态剪枝后保留 {len(simplified_tables)} 个表: {sorted(simplified_tables)}")
    
    # 8. 测试动态扩充
    print(f"\n[8] 测试动态扩充...")
    current_schema = pruned_schema
    selected_tables_per_rollout = []
    
    for idx, cte_path in enumerate(cte_paths[:3]):  # 只测试前3个rollout
        # 从CTE路径中提取选中的表（使用第一个CTE中的表）
        if cte_path:
            first_cte = cte_path[0]
            selected_tables = extract_tables_from_sql(first_cte)
            selected_tables_per_rollout.append(selected_tables)
            
            print(f"\n  Rollout {idx + 1}:")
            print(f"    选中的表: {sorted(selected_tables)}")
            
            # 获取邻居表
            neighbor_tables = set()
            for table in selected_tables:
                neighbors = manager.get_neighbor_tables(table)
                neighbor_tables.update(neighbors)
                if neighbors:
                    print(f"    表 {table} 的邻居: {sorted(neighbors)}")
            
            if neighbor_tables:
                print(f"    所有邻居表: {sorted(neighbor_tables)}")
                # 动态扩充
                expanded_schema = manager.dynamic_expansion(
                    current_schema,
                    selected_tables,
                    original_schema=original_schema
                )
                
                # 检查是否成功添加了邻居表
                expanded_tables = manager._extract_tables_from_schema(expanded_schema)
                added_tables = expanded_tables - manager._extract_tables_from_schema(current_schema)
                if added_tables:
                    print(f"    ✅ 成功添加邻居表: {sorted(added_tables)}")
                else:
                    print(f"    ⚠️ 未添加新表（可能邻居表已在schema中）")
                
                current_schema = expanded_schema
            else:
                print(f"    ⚠️ 没有找到邻居表（可能关系映射为空）")
    
    # 9. 测试动态剪枝
    print(f"\n[9] 测试动态剪枝...")
    # 模拟多个rollout，某些表被选中，某些表未被选中
    all_selected_tables = set()
    for selected_tables in selected_tables_per_rollout:
        all_selected_tables.update(selected_tables)
    
    print(f"  所有rollout中选中的表: {sorted(all_selected_tables)}")
    
    # 模拟多次出现但未选中的表
    current_tables = manager._extract_tables_from_schema(current_schema)
    unused_tables = current_tables - all_selected_tables
    
    print(f"  当前schema中的表: {sorted(current_tables)}")
    print(f"  未使用的表: {sorted(unused_tables)}")
    
    # 模拟多次rollout，让未使用的表累积strike
    test_schema = current_schema
    for i in range(4):  # 模拟4次rollout
        # 每次rollout都使用相同的选中表
        test_schema = manager.dynamic_pruning(test_schema, all_selected_tables)
    
    # 检查哪些表被移除了
    final_tables = manager._extract_tables_from_schema(test_schema)
    removed_tables = current_tables - final_tables
    
    if removed_tables:
        print(f"  ✅ 动态剪枝移除了 {len(removed_tables)} 个表: {sorted(removed_tables)}")
    else:
        print(f"  ⚠️ 未移除任何表（可能strike阈值未达到）")
    
    # 10. 获取统计信息
    print(f"\n[10] 表统计信息:")
    stats = manager.get_table_statistics()
    for table, info in sorted(stats.items()):
        if info['appearances'] > 0 or info['strikes'] > 0:
            print(f"    {table}: appearances={info['appearances']}, "
                  f"strikes={info['strikes']}, selected={info['selected']}")
    
    # 11. 测试便捷方法
    print(f"\n[11] 测试便捷方法 process_schema_for_node...")
    if cte_paths:
        test_cte = cte_paths[0][0] if cte_paths[0] else ""
        if test_cte:
            processed_schema = manager.process_schema_for_node(
                current_schema=pruned_schema,
                cte_or_sql=test_cte,
                original_schema=original_schema,
                enable_expansion=True,
                enable_pruning=True
            )
            processed_tables = manager._extract_tables_from_schema(processed_schema)
            print(f"  处理前表数: {len(manager._extract_tables_from_schema(pruned_schema))}")
            print(f"  处理后表数: {len(processed_tables)}")
            print(f"  ✅ 便捷方法测试完成")
    
    print("\n" + "=" * 80)
    print("测试完成！")
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="测试Schema动态管理器")
    parser.add_argument(
        "--json-file",
        type=str,
        default="/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/test_q98_347_690_847_864_1158_1268_1527_result.json",
        help="JSON结果文件路径"
    )
    parser.add_argument(
        "--db-id",
        type=str,
        default=None,
        help="数据库ID（用于加载relationships）"
    )
    
    args = parser.parse_args()
    
    test_schema_dynamic_manager(args.json_file, args.db_id)

