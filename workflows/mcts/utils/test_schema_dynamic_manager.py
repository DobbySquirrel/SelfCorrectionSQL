"""
测试SchemaDynamicManager的功能

测试内容：
1. 从JSON结果中提取tables和columns
2. 测试动态扩充（增加相邻的表）
3. 测试动态剪枝（去掉后几轮rollout没有使用的表）
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Set, Any, Optional
from collections import defaultdict

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from workflows.mcts.utils.schema_dynamic_manager import (
        SchemaDynamicManager,
        load_relationships_map
    )
except ImportError as e:
    # 如果导入失败，尝试直接导入
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "schema_dynamic_manager",
        Path(__file__).parent / "schema_dynamic_manager.py"
    )
    schema_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema_module)
    SchemaDynamicManager = schema_module.SchemaDynamicManager
    load_relationships_map = schema_module.load_relationships_map


def extract_tables_from_cte_path(cte_path: List[str]) -> Set[str]:
    """从CTE路径中提取所有真实表名（排除CTE名称）"""
    import re
    tables = set()
    
    # 收集所有CTE名称（用于过滤）
    cte_names = set()
    for cte in cte_path:
        # 提取WITH后的CTE名称
        with_match = re.search(r'WITH\s+(\w+)\s+AS', cte, re.IGNORECASE)
        if with_match:
            cte_names.add(with_match.group(1))
    
    KEYWORDS = {
        'SELECT', 'FROM', 'JOIN', 'ON', 'WHERE', 'GROUP', 'ORDER', 'BY', 
        'HAVING', 'LIMIT', 'AS', 'AND', 'OR', 'LEFT', 'RIGHT', 'INNER', 
        'OUTER', 'FULL', 'UNION', 'INTERSECT', 'EXCEPT', 'DISTINCT',
        'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'IS', 'NULL', 'NOT',
        'IN', 'EXISTS', 'LIKE', 'BETWEEN', 'WITH'
    }
    
    for cte in cte_path:
        sql_clean = cte.replace('\n', ' ')
        
        # 提取FROM后的表（排除CTE名称）
        from_matches = re.finditer(r'FROM\s+([^\s(),;]+)', sql_clean, re.IGNORECASE)
        for match in from_matches:
            table = match.group(1).strip().strip('`')
            table = re.sub(r'\s+AS\s+\w+$', '', table, flags=re.IGNORECASE).strip()
            # 排除CTE名称和关键字
            if table and table.upper() not in KEYWORDS and table not in cte_names:
                tables.add(table)
        
        # 提取JOIN后的表（排除CTE名称）
        join_matches = re.finditer(r'JOIN\s+([^\s(),;]+)', sql_clean, re.IGNORECASE)
        for match in join_matches:
            table = match.group(1).strip().strip('`')
            table = re.sub(r'\s+AS\s+\w+$', '', table, flags=re.IGNORECASE).strip()
            # 排除CTE名称和关键字
            if table and table.upper() not in KEYWORDS and table not in cte_names:
                tables.add(table)
    
    return tables


def extract_tables_from_sql(sql: str) -> Set[str]:
    """从SQL中提取真实表名（排除CTE名称）"""
    import re
    
    # 提取所有CTE名称（用于过滤）
    cte_names = set()
    cte_pattern = r'WITH\s+(\w+)\s+AS\s*\('
    cte_matches = re.finditer(cte_pattern, sql, re.IGNORECASE | re.DOTALL)
    for match in cte_matches:
        cte_name = match.group(1).strip()
        cte_names.add(cte_name)
    
    KEYWORDS = {
        'SELECT', 'FROM', 'JOIN', 'ON', 'WHERE', 'GROUP', 'ORDER', 'BY', 
        'HAVING', 'LIMIT', 'AS', 'AND', 'OR', 'LEFT', 'RIGHT', 'INNER', 
        'OUTER', 'FULL', 'UNION', 'INTERSECT', 'EXCEPT', 'DISTINCT',
        'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'IS', 'NULL', 'NOT',
        'IN', 'EXISTS', 'LIKE', 'BETWEEN', 'WITH'
    }
    
    sql_clean = sql.replace('\n', ' ')
    tables = set()
    
    # 提取FROM后的表（排除CTE名称）
    from_matches = re.finditer(r'FROM\s+([^\s(),;]+)', sql_clean, re.IGNORECASE)
    for match in from_matches:
        table = match.group(1).strip().strip('`')
        table = re.sub(r'\s+AS\s+\w+$', '', table, flags=re.IGNORECASE).strip()
        # 排除CTE名称和关键字
        if table and table.upper() not in KEYWORDS and table not in cte_names:
            tables.add(table)
    
    # 提取JOIN后的表（排除CTE名称）
    join_matches = re.finditer(r'JOIN\s+([^\s(),;]+)', sql_clean, re.IGNORECASE)
    for match in join_matches:
        table = match.group(1).strip().strip('`')
        table = re.sub(r'\s+AS\s+\w+$', '', table, flags=re.IGNORECASE).strip()
        # 排除CTE名称和关键字
        if table and table.upper() not in KEYWORDS and table not in cte_names:
            tables.add(table)
    
    return tables


def analyze_rollout_stats(rollout_stats_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    分析rollout统计信息，提取：
    1. 每个rollout中使用的表
    2. 表的出现频率
    3. 哪些表从未被选中
    """
    table_usage = defaultdict(int)  # 表在rollout中出现的次数
    table_selections = defaultdict(int)  # 表被选中的次数
    rollout_tables = []  # 每个rollout使用的表
    
    for idx, rollout_stats in enumerate(rollout_stats_list):
        # 从CTE路径中提取表
        cte_path = rollout_stats.get('cte_path', [])
        tables_in_cte = extract_tables_from_cte_path(cte_path)
        
        # 从selected_sql中提取表
        selected_sql = rollout_stats.get('selected_sql', '')
        tables_in_sql = extract_tables_from_sql(selected_sql) if selected_sql else set()
        
        # 合并所有表
        all_tables = tables_in_cte | tables_in_sql
        
        rollout_tables.append({
            'rollout_id': rollout_stats.get('rollout_id', idx + 1),
            'tables': all_tables,
            'tables_in_cte': tables_in_cte,
            'tables_in_sql': tables_in_sql
        })
        
        # 统计表的使用情况
        for table in all_tables:
            table_usage[table] += 1
            if table in tables_in_sql or table in tables_in_cte:
                table_selections[table] += 1
    
    return {
        'table_usage': dict(table_usage),
        'table_selections': dict(table_selections),
        'rollout_tables': rollout_tables
    }


def load_db_id_from_dev_json(dev_json_file: str, question_id: int) -> Optional[str]:
    """
    从dev.json中根据question_id查找对应的db_id
    
    Args:
        dev_json_file: dev.json文件路径
        question_id: 问题ID
        
    Returns:
        数据库ID，如果找不到则返回None
    """
    try:
        with open(dev_json_file, 'r', encoding='utf-8') as f:
            dev_data = json.load(f)
        
        # dev.json是一个数组
        for item in dev_data:
            if item.get('question_id') == question_id:
                return item.get('db_id')
        
        return None
    except Exception as e:
        print(f"  ⚠️ 加载dev.json失败: {e}")
        return None


def build_schema_from_dev_tables(dev_tables_file: str, db_id: str) -> Optional[str]:
    """
    从dev_tables.json中根据db_id构建完整的CREATE TABLE schema
    
    Args:
        dev_tables_file: dev_tables.json文件路径
        db_id: 数据库ID
        
    Returns:
        完整的CREATE TABLE schema字符串，如果找不到则返回None
    """
    try:
        with open(dev_tables_file, 'r', encoding='utf-8') as f:
            dev_tables_data = json.load(f)
        
        # 查找对应的数据库
        for db_info in dev_tables_data:
            if db_info.get('db_id') == db_id:
                table_names_original = db_info.get('table_names_original', [])
                column_names_original = db_info.get('column_names_original', [])
                
                # 构建CREATE TABLE语句
                schema_parts = []
                
                # 按表分组列
                table_columns = {}
                for col_info in column_names_original:
                    if isinstance(col_info, list) and len(col_info) >= 2:
                        table_idx = col_info[0]
                        col_name = col_info[1]
                        
                        if table_idx >= 0:  # 排除-1（*）
                            if table_idx not in table_columns:
                                table_columns[table_idx] = []
                            table_columns[table_idx].append(col_name)
                
                # 为每个表生成CREATE TABLE语句
                for table_idx, table_name in enumerate(table_names_original):
                    columns = table_columns.get(table_idx, [])
                    
                    # 构建列定义（简化版，使用TEXT类型）
                    col_defs = []
                    for col in columns:
                        # 处理特殊字符，用反引号包裹
                        col_name_escaped = f"`{col}`" if ' ' in col or '-' in col or '(' in col else col
                        col_defs.append(f"    {col_name_escaped} TEXT")
                    
                    # 如果没有列，至少添加一个占位符
                    if not col_defs:
                        col_defs.append("    id TEXT")
                    
                    create_table = f"CREATE TABLE `{table_name}` (\n" + ",\n".join(col_defs) + "\n);"
                    schema_parts.append(create_table)
                
                return "\n\n".join(schema_parts)
        
        return None
    except Exception as e:
        print(f"  ⚠️ 从dev_tables.json构建schema失败: {e}")
        return None


def test_schema_dynamic_manager(test_file: str, dev_json_file: str = None, 
                                dev_tables_file: str = None, db_id: str = None):
    """
    测试SchemaDynamicManager的功能
    
    Args:
        test_file: 测试JSON文件路径
        dev_json_file: dev.json文件路径（用于查找db_id）
        dev_tables_file: dev_tables.json文件路径（用于获取完整schema）
        db_id: 数据库ID（如果为None，尝试从dev.json中查找）
    """
    print("=" * 80)
    print("测试SchemaDynamicManager - 处理所有问题")
    print("=" * 80)
    
    # 1. 加载测试数据
    print(f"\n[1] 加载测试文件: {test_file}")
    with open(test_file, 'r', encoding='utf-8') as f:
        test_data = json.load(f)
    
    question_ids = list(test_data.keys())
    print(f"  找到 {len(question_ids)} 个问题: {question_ids}")
    
    # 2. 加载dev.json（如果提供）
    dev_data_map = {}
    if dev_json_file and Path(dev_json_file).exists():
        print(f"\n[2] 加载dev.json: {dev_json_file}")
        try:
            with open(dev_json_file, 'r', encoding='utf-8') as f:
                dev_data = json.load(f)
            # 构建question_id -> db_id的映射
            for item in dev_data:
                qid = item.get('question_id')
                if qid is not None:
                    dev_data_map[qid] = item.get('db_id')
            print(f"  成功加载 {len(dev_data_map)} 个question_id -> db_id映射")
        except Exception as e:
            print(f"  ⚠️ 加载dev.json失败: {e}")
    
    # 2.5 检查dev_tables.json
    if not dev_tables_file:
        dev_tables_file = "/home/shenshuyu/SQL_tool_multiAgent/data/dev_tables.json"
    
    if not Path(dev_tables_file).exists():
        print(f"  ⚠️  dev_tables.json文件不存在: {dev_tables_file}")
        dev_tables_file = None
    else:
        print(f"  ✅ dev_tables.json文件存在: {dev_tables_file}")
    
    # 3. 处理每个问题
    all_results = []
    for question_id_str in question_ids:
        question_id = int(question_id_str)
        question_data = test_data[question_id_str]
        rollout_stats_list = question_data.get('rollout_stats', [])
        
        print(f"\n{'='*80}")
        print(f"处理问题 ID: {question_id} ({len(rollout_stats_list)} 个rollout)")
        print(f"{'='*80}")
        
        # 获取db_id
        if db_id:
            current_db_id = db_id
        elif question_id in dev_data_map:
            current_db_id = dev_data_map[question_id]
        else:
            # 尝试从表名推断
            analysis = analyze_rollout_stats(rollout_stats_list)
            tables = list(analysis['table_usage'].keys())
            if 'cards' in tables or 'rulings' in tables:
                current_db_id = 'card_trading'
            elif 'bond' in tables or 'molecule' in tables:
                current_db_id = 'chemistry'
            else:
                current_db_id = 'unknown'
            print(f"  ⚠️ 未找到db_id映射，推断为: {current_db_id}")
        
        print(f"  使用数据库ID: {current_db_id}")
    
        # 3. 分析rollout统计信息
        print(f"\n[3] 分析rollout统计信息...")
        analysis = analyze_rollout_stats(rollout_stats_list)
        
        print(f"  使用的表: {sorted(analysis['table_usage'].keys())}")
        print(f"  表使用频率:")
        for table, count in sorted(analysis['table_usage'].items(), key=lambda x: -x[1]):
            selections = analysis['table_selections'].get(table, 0)
            print(f"    {table}: 出现{count}次, 被选中{selections}次")
        
        # 4. 加载关系映射
        print(f"\n[4] 加载关系映射 (db_id={current_db_id})...")
        relationships_map = load_relationships_map(db_id=current_db_id)
        
        if not relationships_map:
            print("  ⚠️ 未找到关系映射，将使用空映射进行测试")
            print("  注意：动态扩充功能需要关系映射才能正常工作")
        else:
            print(f"  ✅ 成功加载 {len(relationships_map)} 个表关系")
        
        # 5. 创建SchemaDynamicManager（为每个问题创建新的实例）
        print(f"\n[5] 创建SchemaDynamicManager...")
        manager = SchemaDynamicManager(
            relationships_map=relationships_map,
            strike_threshold=2  # 降低阈值以便测试
        )
        
        # 6. 测试动态扩充
        print(f"\n[6] 测试动态扩充（Dynamic Expansion）...")
        print("  模拟：当选中某个表时，自动添加其邻居表")
        
        # 收集所有rollout中使用的表
        all_tables_in_all_rollouts = set()
        for rollout_info in analysis['rollout_tables']:
            all_tables_in_all_rollouts.update(rollout_info['tables'])
        
        print(f"  所有rollout使用的表: {sorted(all_tables_in_all_rollouts)}")
        
        # 测试每个表的邻居
        neighbor_info = {}
        for table in all_tables_in_all_rollouts:
            neighbors = manager.get_neighbor_tables(table)
            if neighbors:
                neighbor_info[table] = neighbors
                print(f"    表 {table} 的邻居表: {sorted(neighbors)}")
        
        if not neighbor_info:
            print("    ℹ️  没有找到任何表的邻居（可能关系映射为空）")
        
        # 测试动态扩充（使用第一个rollout）
        if analysis['rollout_tables']:
            first_rollout_tables = analysis['rollout_tables'][0]['tables']
            print(f"\n  测试第一个rollout的动态扩充...")
            print(f"    第一个rollout使用的表: {sorted(first_rollout_tables)}")
            
            # 从dev_tables.json获取完整schema
            original_full_schema = None
            if dev_tables_file:
                original_full_schema = build_schema_from_dev_tables(dev_tables_file, current_db_id)
                if original_full_schema:
                    print(f"    ✅ 成功从dev_tables.json加载完整schema（包含所有表）")
                else:
                    print(f"    ⚠️  未能从dev_tables.json加载完整schema")
            
            # 构建当前schema（只包含第一个rollout使用的表）
            if original_full_schema:
                # 从完整schema中提取使用的表的定义
                current_schema_parts = []
                for table in first_rollout_tables:
                    table_def = manager._extract_table_definition(original_full_schema, table)
                    if table_def:
                        current_schema_parts.append(table_def)
                current_schema = "\n\n".join(current_schema_parts)
            else:
                # 如果没有完整schema，使用简化版
                current_schema = "\n".join([f"CREATE TABLE {t} (...);" for t in first_rollout_tables])
                original_full_schema = current_schema
            
            # 测试动态扩充
            expanded_schema = manager.dynamic_expansion(
                current_schema,
                first_rollout_tables,
                original_schema=original_full_schema
            )
            
            # 检查是否添加了邻居表
            expanded_tables = manager._extract_tables_from_schema(expanded_schema)
            added_tables = expanded_tables - first_rollout_tables
            if added_tables:
                print(f"    ✅ 动态扩充成功：添加了 {len(added_tables)} 个邻居表: {sorted(added_tables)}")
                # 显示添加的表的部分定义
                for added_table in sorted(added_tables):
                    table_def = manager._extract_table_definition(expanded_schema, added_table)
                    if table_def:
                        print(f"      添加的表 {added_table} 的定义（前100字符）: {table_def[:100]}...")
            else:
                print(f"    ℹ️  未添加新表（可能没有邻居表或邻居表已在schema中）")
        
        # 7. 测试动态剪枝
        print(f"\n[7] 测试动态剪枝（Dynamic Pruning）...")
        print("  模拟：移除在多个rollout中出现但从未被选中的表")
        
        # 构建模拟schema（包含所有rollout中使用的表）
        mock_schema = "\n".join([f"CREATE TABLE {t} (...);" for t in all_tables_in_all_rollouts])
        
        # 模拟多个rollout的处理
        print(f"  处理 {len(analysis['rollout_tables'])} 个rollout...")
        
        current_schema = mock_schema
        removed_tables_summary = []
        
        for idx, rollout_info in enumerate(analysis['rollout_tables']):
            selected_tables = rollout_info['tables']
            
            # 应用动态剪枝
            pruned_schema = manager.dynamic_pruning(current_schema, selected_tables)
            
            # 检查被移除的表
            before_tables = manager._extract_tables_from_schema(current_schema)
            after_tables = manager._extract_tables_from_schema(pruned_schema)
            removed_tables = before_tables - after_tables
            
            if removed_tables:
                print(f"    Rollout {idx + 1}: 移除了 {len(removed_tables)} 个表: {sorted(removed_tables)}")
                removed_tables_summary.extend(removed_tables)
            
            current_schema = pruned_schema
        
        if not removed_tables_summary:
            print("    ℹ️  没有表被移除（所有表都被选中或strike阈值未达到）")
        
        # 8. 获取统计信息
        print(f"\n[8] 获取最终统计信息...")
        stats = manager.get_table_statistics()
        
        print(f"  表统计信息:")
        for table, info in sorted(stats.items()):
            print(f"    {table}: appearances={info['appearances']}, "
                  f"strikes={info['strikes']}, selected={info['selected']}")
        
        # 9. 测试便捷方法
        print(f"\n[9] 测试便捷方法 process_schema_for_node...")
        if analysis['rollout_tables'] and rollout_stats_list:
            # 获取第一个CTE
            cte_path = rollout_stats_list[0].get('cte_path', [])
            if cte_path:
                first_cte = cte_path[0]
                print(f"  使用第一个CTE进行测试:")
                print(f"    {first_cte[:100]}...")
                
                # 获取完整schema（如果可用）
                original_full_schema_for_process = None
                if dev_tables_file:
                    original_full_schema_for_process = build_schema_from_dev_tables(dev_tables_file, current_db_id)
                
                # 构建当前schema（只包含第一个rollout使用的表）
                first_rollout_tables = analysis['rollout_tables'][0]['tables']
                if original_full_schema_for_process:
                    current_schema_parts = []
                    for table in first_rollout_tables:
                        table_def = manager._extract_table_definition(original_full_schema_for_process, table)
                        if table_def:
                            current_schema_parts.append(table_def)
                    current_schema_for_process = "\n\n".join(current_schema_parts)
                else:
                    current_schema_for_process = "\n".join([f"CREATE TABLE {t} (...);" for t in first_rollout_tables])
                    original_full_schema_for_process = current_schema_for_process
                
                processed_schema = manager.process_schema_for_node(
                    current_schema=current_schema_for_process,
                    cte_or_sql=first_cte,
                    original_schema=original_full_schema_for_process,
                    enable_expansion=True,
                    enable_pruning=True
                )
                
                processed_tables = manager._extract_tables_from_schema(processed_schema)
                print(f"  ✅ 处理完成，schema中包含 {len(processed_tables)} 个表")
                if len(processed_tables) > len(first_rollout_tables):
                    added = processed_tables - set(first_rollout_tables)
                    print(f"    添加了 {len(added)} 个表: {sorted(added)}")
        
        # 保存结果
        all_results.append({
            'question_id': question_id,
            'db_id': current_db_id,
            'num_rollouts': len(rollout_stats_list),
            'tables_used': sorted(analysis['table_usage'].keys()),
            'neighbor_info': {k: sorted(v) for k, v in neighbor_info.items()},
            'removed_tables': sorted(set(removed_tables_summary)),
            'stats': stats
        })
    
    # 10. 总结
    print(f"\n{'='*80}")
    print("测试总结")
    print(f"{'='*80}")
    print(f"总共处理了 {len(all_results)} 个问题")
    for result in all_results:
        print(f"\n问题 {result['question_id']} (db_id: {result['db_id']}):")
        print(f"  - Rollout数量: {result['num_rollouts']}")
        print(f"  - 使用的表: {result['tables_used']}")
        if result['neighbor_info']:
            print(f"  - 找到邻居关系: {len(result['neighbor_info'])} 个表有邻居")
        if result['removed_tables']:
            print(f"  - 被移除的表: {result['removed_tables']}")
    
    print("\n" + "=" * 80)
    print("所有测试完成！")
    print("=" * 80)
    
    return all_results


if __name__ == "__main__":
    import sys
    print("Python version:", sys.version)
    print("Starting test...")
    
    # 测试文件路径
    test_file = "/home/shenshuyu/SQL_tool_multiAgent/workflows/mcts/test/out/test_q98_347_690_847_864_1158_1268_1527_result.json"
    dev_json_file = "/home/shenshuyu/SQL_tool_multiAgent/data/dev.json"
    dev_tables_file = "/home/shenshuyu/SQL_tool_multiAgent/data/dev_tables.json"
    
    # 检查文件是否存在
    if not Path(test_file).exists():
        print(f"❌ 测试文件不存在: {test_file}")
        sys.exit(1)
    
    if not Path(dev_json_file).exists():
        print(f"⚠️  dev.json文件不存在: {dev_json_file}")
        print("  将尝试从表名推断db_id")
        dev_json_file = None
    
    if not Path(dev_tables_file).exists():
        print(f"⚠️  dev_tables.json文件不存在: {dev_tables_file}")
        print("  将使用简化的schema进行测试")
        dev_tables_file = None
    
    print(f"测试文件存在: {test_file}")
    if dev_json_file:
        print(f"dev.json文件存在: {dev_json_file}")
    if dev_tables_file:
        print(f"dev_tables.json文件存在: {dev_tables_file}")
    
    # 可以手动指定db_id（会覆盖从dev.json中查找的结果）
    db_id = None  # 设置为None让脚本从dev.json中查找，或手动指定如 'card_trading'
    
    try:
        results = test_schema_dynamic_manager(
            test_file, 
            dev_json_file=dev_json_file,
            dev_tables_file=dev_tables_file,
            db_id=db_id
        )
        print(f"\n✅ 测试成功完成，处理了 {len(results)} 个问题")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

