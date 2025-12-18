"""
Schema动态管理模块

实现"静态初始化 → 动态扩充 → 动态淘汰"的三步走策略：
1. 静态剪枝 (Static Pruning): 使用RSL-SQL进行初始schema简化
2. 动态扩充 (Dynamic Expansion): 当选中某个表时，将其邻居表加入context
3. 动态剪枝 (Dynamic Pruning): 移除多次出现但从未被选中的表

集成到MCTS工作流的建议：

Level 0 (Baseline): 只使用静态剪枝
    - 在solve()开始时，使用RSL-SQL对schema进行简化
    - 将简化后的schema传递给MCTS节点

Level 1 (核心功能): 添加动态扩充
    - 在_mcts_expansion()中，当生成CTE并选中表后：
        expanded_schema = schema_manager.dynamic_expansion(
            current_node.schema_info,
            selected_tables,
            original_schema=original_full_schema
        )
        # 更新子节点的schema_info
        child_node.schema_info = expanded_schema

Level 2 (进阶优化): 添加动态剪枝
    - 在每次rollout后，对schema进行动态剪枝：
        pruned_schema = schema_manager.dynamic_pruning(
            current_schema,
            selected_tables
        )
    - 或者在节点扩展时同时应用：
        processed_schema = schema_manager.process_schema_for_node(
            current_schema,
            cte_text,
            original_schema=original_full_schema,
            enable_expansion=True,
            enable_pruning=True
        )

使用示例：
    from workflows.mcts.utils.schema_dynamic_manager import (
        SchemaDynamicManager, 
        load_relationships_map
    )
    
    # 初始化
    relationships_map = load_relationships_map(db_id=db_id)
    schema_manager = SchemaDynamicManager(
        relationships_map=relationships_map,
        strike_threshold=3
    )
    
    # 在MCTS节点中使用
    # 1. 静态剪枝（在solve开始时）
    simplified_schema = schema_manager.static_pruning(
        original_schema, 
        rsl_simplified_schema
    )
    
    # 2. 动态扩充（在扩展节点时）
    expanded_schema = schema_manager.dynamic_expansion(
        node.schema_info,
        selected_tables,
        original_schema=original_full_schema
    )
    
    # 3. 动态剪枝（在rollout后或节点扩展时）
    pruned_schema = schema_manager.dynamic_pruning(
        expanded_schema,
        selected_tables
    )
"""

from typing import Dict, List, Set, Optional, Tuple, Any
from collections import defaultdict
import re
from pathlib import Path
import json


class SchemaDynamicManager:
    """Schema动态管理器"""
    
    def __init__(self, relationships_map: Optional[Dict[str, Dict[str, Any]]] = None, 
                 strike_threshold: int = 3):
        """
        初始化Schema动态管理器
        
        Args:
            relationships_map: 表关系映射，格式: {f"{table1}<->{table2}": {'type': '1:N', ...}, ...}
            strike_threshold: 动态剪枝的strike阈值，表出现N次但从未被选中则剔除
        """
        self.relationships_map = relationships_map or {}
        self.strike_threshold = strike_threshold
        
        # 构建表邻接图（无向图）
        self._table_graph: Dict[str, Set[str]] = defaultdict(set)
        self._build_table_graph()
        
        # 动态统计：记录每个表在context中的出现次数和是否被选中
        self._table_appearances: Dict[str, int] = defaultdict(int)  # 表在context中出现的次数
        self._table_selections: Set[str] = set()  # 被MCTS选中的表集合
        self._table_strikes: Dict[str, int] = defaultdict(int)  # 表的strike计数（出现但未选中）
        
    def _build_table_graph(self):
        """构建表邻接图（无向图）"""
        for rel_key, rel_info in self.relationships_map.items():
            table1 = rel_info.get('table1', '')
            table2 = rel_info.get('table2', '')
            if table1 and table2:
                # 无向图：两个表互为邻居
                self._table_graph[table1].add(table2)
                self._table_graph[table2].add(table1)
    
    def static_pruning(self, original_schema: str, simplified_schema: str) -> str:
        """
        第一阶段：静态剪枝（Static Pruning）
        
        使用RSL-SQL等方法对原始schema进行简化，移除明显无关的表。
        这个阶段通常在MCTS开始前完成，这里只是记录和返回简化后的schema。
        
        Args:
            original_schema: 原始完整schema
            simplified_schema: 经过RSL-SQL简化后的schema
            
        Returns:
            简化后的schema
        """
        # 这里假设simplified_schema已经通过RSL-SQL等方法处理过
        # 实际实现中，可以在这里调用RSL-SQL的forward/backward剪枝逻辑
        return simplified_schema
    
    def dynamic_expansion(self, current_schema: str, selected_tables: Set[str], 
                         original_schema: Optional[str] = None) -> str:
        """
        第二阶段：动态扩充（Dynamic Expansion）
        
        当MCTS选中某个表时，立即将其所有直接邻居表加入context。
        即使这些邻居在静态剪枝阶段被移除，也要强制加回来。
        
        Args:
            current_schema: 当前节点的schema context
            selected_tables: 当前CTE/SQL中选中的表集合
            original_schema: 原始完整schema（用于提取邻居表的定义），如果为None则只返回标记
            
        Returns:
            扩充后的schema（包含邻居表）
        """
        if not selected_tables:
            return current_schema
        
        # 收集所有需要添加的邻居表
        neighbors_to_add: Set[str] = set()
        for table in selected_tables:
            if table in self._table_graph:
                neighbors = self._table_graph[table]
                neighbors_to_add.update(neighbors)
        
        # 移除已经在current_schema中的表（避免重复）
        existing_tables = self._extract_tables_from_schema(current_schema)
        neighbors_to_add -= existing_tables
        
        if not neighbors_to_add:
            return current_schema
        
        # 从原始schema中提取邻居表的定义
        neighbor_schema_parts = []
        if original_schema:
            # 从原始schema中提取每个邻居表的完整定义
            for neighbor_table in sorted(neighbors_to_add):
                table_def = self._extract_table_definition(original_schema, neighbor_table)
                if table_def:
                    neighbor_schema_parts.append(f"-- [动态扩充] 表 {neighbor_table} (邻居表)\n")
                    neighbor_schema_parts.append(table_def)
                    neighbor_schema_parts.append("\n")
        else:
            # 如果没有原始schema，只添加标记（实际使用时需要传入original_schema）
            for neighbor_table in sorted(neighbors_to_add):
                neighbor_schema_parts.append(
                    f"-- [动态扩充] 需要添加表 {neighbor_table} (邻居表，但缺少schema定义)\n"
                )
        
        # 将邻居表schema追加到当前schema
        expanded_schema = current_schema
        if neighbor_schema_parts:
            expanded_schema += "\n\n-- ========== 动态扩充的邻居表 ==========\n"
            expanded_schema += "".join(neighbor_schema_parts)
        
        return expanded_schema
    
    def _extract_table_definition(self, schema: str, table_name: str) -> Optional[str]:
        """
        从schema中提取指定表的完整定义
        
        Args:
            schema: 完整schema文本
            table_name: 表名
            
        Returns:
            表的CREATE TABLE定义，如果找不到则返回None
        """
        # 更精确的匹配：匹配CREATE TABLE到下一个CREATE TABLE或文件结束
        # 使用非贪婪匹配，确保只匹配到当前表的结束
        pattern = rf'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?{re.escape(table_name)}`?\s*\([^;]+?\);'
        match = re.search(pattern, schema, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(0).strip()
        
        # 如果上面的模式没匹配到，尝试匹配到分号（更宽松的模式）
        pattern2 = rf'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?{re.escape(table_name)}`?[^;]+;'
        match2 = re.search(pattern2, schema, re.IGNORECASE | re.DOTALL)
        if match2:
            return match2.group(0).strip()
        
        return None
    
    def dynamic_pruning(self, current_schema: str, selected_tables: Set[str]) -> str:
        """
        第三阶段：动态剪枝（Dynamic Pruning）
        
        移除在context中出现多次但从未被选中的表（strike机制）。
        随着推理深入，这些"顽固噪声"表应该被剔除以节省token和提升专注度。
        
        Args:
            current_schema: 当前节点的schema context
            selected_tables: 当前CTE/SQL中选中的表集合
            
        Returns:
            剪枝后的schema（移除strike过多的表）
        """
        # 更新表的出现和选中统计
        current_tables = self._extract_tables_from_schema(current_schema)
        
        for table in current_tables:
            self._table_appearances[table] += 1
        
        # 更新选中表
        self._table_selections.update(selected_tables)
        
        # 更新strike计数：出现但未选中
        for table in current_tables:
            if table not in selected_tables:
                self._table_strikes[table] += 1
            else:
                # 如果被选中，重置strike计数
                self._table_strikes[table] = 0
        
        # 找出需要移除的表（strike >= threshold）
        tables_to_remove = {
            table for table, strikes in self._table_strikes.items()
            if strikes >= self.strike_threshold
        }
        
        if not tables_to_remove:
            return current_schema
        
        # 从schema中移除这些表
        pruned_schema = self._remove_tables_from_schema(current_schema, tables_to_remove)
        
        return pruned_schema
    
    def update_selected_tables(self, selected_tables: Set[str]):
        """
        更新被选中的表集合（用于统计）
        
        Args:
            selected_tables: 当前CTE/SQL中选中的表集合
        """
        self._table_selections.update(selected_tables)
        # 重置被选中表的strike计数
        for table in selected_tables:
            self._table_strikes[table] = 0
    
    def get_table_statistics(self) -> Dict[str, Dict[str, int]]:
        """
        获取表的统计信息
        
        Returns:
            统计信息字典: {table_name: {'appearances': int, 'strikes': int, 'selected': bool}}
        """
        stats = {}
        for table in set(self._table_appearances.keys()) | self._table_selections:
            stats[table] = {
                'appearances': self._table_appearances.get(table, 0),
                'strikes': self._table_strikes.get(table, 0),
                'selected': table in self._table_selections
            }
        return stats
    
    def reset_statistics(self):
        """重置所有统计信息（用于新的问题）"""
        self._table_appearances.clear()
        self._table_selections.clear()
        self._table_strikes.clear()
    
    def _extract_tables_from_schema(self, schema: str) -> Set[str]:
        """
        从schema文本中提取所有表名
        
        支持两种格式：
        1. CREATE TABLE格式：CREATE TABLE `table_name` (...)
        2. 注释格式：# table_name(`col1`, `col2`, ...)
        
        Args:
            schema: schema文本
            
        Returns:
            表名集合
        """
        tables = set()
        
        # 1. 匹配 CREATE TABLE 语句
        create_table_pattern = r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?'
        matches = re.finditer(create_table_pattern, schema, re.IGNORECASE)
        for match in matches:
            table_name = match.group(1).strip().strip('`')
            if table_name:
                tables.add(table_name)
        
        # 2. 匹配注释格式：# table_name(`col1`, `col2`, ...)
        # 格式：以#开头，后跟表名（可能带反引号），然后是括号
        comment_table_pattern = r'#\s*`?(\w+)`?\s*\([^)]*\)'
        matches = re.finditer(comment_table_pattern, schema, re.IGNORECASE)
        for match in matches:
            table_name = match.group(1).strip().strip('`')
            if table_name:
                tables.add(table_name)
        
        return tables
    
    def _remove_tables_from_schema(self, schema: str, tables_to_remove: Set[str]) -> str:
        """
        从schema中移除指定的表定义
        
        Args:
            schema: 原始schema文本
            tables_to_remove: 要移除的表名集合
            
        Returns:
            移除后的schema文本
        """
        if not tables_to_remove:
            return schema
        
        # 使用正则表达式直接移除整个CREATE TABLE语句
        result = schema
        for table_name in tables_to_remove:
            # 匹配CREATE TABLE到分号结束的完整语句
            pattern = rf'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?{re.escape(table_name)}`?\s*\([^;]+?\);\s*'
            result = re.sub(pattern, '', result, flags=re.IGNORECASE | re.DOTALL)
            
            # 如果上面的模式没匹配到，尝试更宽松的模式
            pattern2 = rf'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?{re.escape(table_name)}`?[^;]+?;\s*'
            result = re.sub(pattern2, '', result, flags=re.IGNORECASE | re.DOTALL)
        
        # 清理多余的空行（连续3个以上换行符替换为2个）
        result = re.sub(r'\n{3,}', '\n\n', result)
        
        return result.strip()
    
    def extract_tables_from_sql(self, sql: str) -> Set[str]:
        """
        从SQL语句中提取所有真实表名（排除CTE名称）
        
        Args:
            sql: SQL语句
            
        Returns:
            真实表名集合（排除CTE名称）
        """
        tables = set()
        
        # 定义需要忽略的SQL关键字
        KEYWORDS = {
            'SELECT', 'FROM', 'JOIN', 'ON', 'WHERE', 'GROUP', 'ORDER', 'BY', 
            'HAVING', 'LIMIT', 'AS', 'AND', 'OR', 'LEFT', 'RIGHT', 'INNER', 
            'OUTER', 'FULL', 'UNION', 'INTERSECT', 'EXCEPT', 'DISTINCT',
            'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'IS', 'NULL', 'NOT',
            'IN', 'EXISTS', 'LIKE', 'BETWEEN', 'WITH'
        }
        
        # 提取所有CTE名称（用于过滤）
        cte_names = set()
        # 匹配 WITH cte_name AS ( 或 WITH cte1 AS (...), cte2 AS (...)
        cte_pattern = r'WITH\s+(\w+)\s+AS\s*\('
        cte_matches = re.finditer(cte_pattern, sql, re.IGNORECASE | re.DOTALL)
        for match in cte_matches:
            cte_name = match.group(1).strip()
            cte_names.add(cte_name)
        
        # 简单的清洗，移除换行
        sql_clean = sql.replace('\n', ' ')
        
        # 1. 提取 FROM 后的表（排除CTE名称）
        from_matches = re.finditer(r'FROM\s+([^\s(),;]+)', sql_clean, re.IGNORECASE)
        for match in from_matches:
            table = match.group(1).strip().strip('`')
            # 移除可能的AS别名
            table = re.sub(r'\s+AS\s+\w+$', '', table, flags=re.IGNORECASE).strip()
            # 过滤关键字和CTE名称
            if table and table.upper() not in KEYWORDS and table not in cte_names:
                tables.add(table)
        
        # 2. 提取 JOIN 后的表（排除CTE名称）
        join_matches = re.finditer(r'JOIN\s+([^\s(),;]+)', sql_clean, re.IGNORECASE)
        for match in join_matches:
            table = match.group(1).strip().strip('`')
            # 移除可能的AS别名
            table = re.sub(r'\s+AS\s+\w+$', '', table, flags=re.IGNORECASE).strip()
            # 过滤关键字和CTE名称
            if table and table.upper() not in KEYWORDS and table not in cte_names:
                tables.add(table)
        
        return tables
    
    def get_neighbor_tables(self, table_name: str) -> Set[str]:
        """
        获取指定表的所有邻居表
        
        Args:
            table_name: 表名
            
        Returns:
            邻居表集合
        """
        return self._table_graph.get(table_name, set()).copy()
    
    def has_table_in_graph(self, table_name: str) -> bool:
        """
        检查表是否在关系图中
        
        Args:
            table_name: 表名
            
        Returns:
            如果表在图中返回True，否则返回False
        """
        return table_name in self._table_graph
    
    def get_all_tables_in_graph(self) -> Set[str]:
        """
        获取关系图中的所有表
        
        Returns:
            所有表名集合
        """
        return set(self._table_graph.keys())
    
    def process_schema_for_node(self, current_schema: str, cte_or_sql: str, 
                                original_schema: Optional[str] = None,
                                enable_expansion: bool = True,
                                enable_pruning: bool = True) -> str:
        """
        为MCTS节点处理schema（动态扩充 + 动态剪枝的组合方法）
        
        Args:
            current_schema: 当前节点的schema context
            cte_or_sql: CTE或SQL语句（用于提取选中的表）
            original_schema: 原始完整schema（用于动态扩充）
            enable_expansion: 是否启用动态扩充
            enable_pruning: 是否启用动态剪枝
            
        Returns:
            处理后的schema
        """
        # 从CTE/SQL中提取选中的表
        selected_tables = self.extract_tables_from_sql(cte_or_sql)
        
        # 动态扩充
        if enable_expansion and selected_tables:
            current_schema = self.dynamic_expansion(
                current_schema, 
                selected_tables, 
                original_schema=original_schema
            )
        
        # 动态剪枝
        if enable_pruning:
            current_schema = self.dynamic_pruning(current_schema, selected_tables)
        
        return current_schema


def load_relationships_map(relationships_file: Optional[str] = None, 
                          db_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    加载relationships.json并构建关系映射
    
    Args:
        relationships_file: relationships.json文件路径（如果为None，使用默认路径）
        db_id: 数据库ID（如果为None，需要从其他地方获取）
        
    Returns:
        关系映射字典，格式: {f"{table1}<->{table2}": {'type': '1:N', ...}, ...}
    """
    if relationships_file is None:
        # 默认路径：workflows/mcts/data/relationships.json
        relationships_file = Path(__file__).parent.parent / "data" / "relationships.json"
    
    if not Path(relationships_file).exists():
        print(f"[Schema动态管理] ⚠️ relationships.json 文件不存在: {relationships_file}")
        return {}
    
    try:
        with open(relationships_file, 'r', encoding='utf-8') as f:
            all_relationships = json.load(f)
        
        if not db_id:
            print(f"[Schema动态管理] ⚠️ 未提供db_id，返回空关系映射")
            return {}
        
        # 获取当前数据库的关系信息
        db_relationships = all_relationships.get(db_id, {})
        relationships_list = db_relationships.get('relationships', [])
        
        # 构建关系映射：{f"{table1}<->{table2}": {'type': '1:N', ...}}
        relationships_map = {}
        for rel in relationships_list:
            table1 = rel.get('table1', '').strip().strip('`')
            table2 = rel.get('table2', '').strip().strip('`')
            rel_type = rel.get('relationship_type', '')
            
            if table1 and table2:
                # 规范化表名（小的在前）
                key = f"{min(table1, table2)}<->{max(table1, table2)}"
                relationships_map[key] = {
                    'type': rel_type,
                    'table1': table1,
                    'table2': table2,
                    'col1': rel.get('col1', ''),
                    'col2': rel.get('col2', ''),
                    'description': rel.get('description', '')
                }
        
        print(f"[Schema动态管理] ✅ 加载了 {len(relationships_map)} 个表关系（数据库: {db_id}）")
        return relationships_map
    except Exception as e:
        print(f"[Schema动态管理] ⚠️ 加载关系信息失败: {e}")
        return {}


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
        print(f"[Schema动态管理] ⚠️ 从dev_tables.json构建schema失败: {e}")
        return None


# 使用示例
if __name__ == "__main__":
    # 示例：如何使用SchemaDynamicManager
    
    # 1. 加载关系映射
    relationships_map = load_relationships_map(db_id="example_db")
    
    # 2. 创建管理器
    manager = SchemaDynamicManager(
        relationships_map=relationships_map,
        strike_threshold=3
    )
    
    # 3. 静态剪枝（假设已经完成，通常由RSL-SQL等方法完成）
    original_schema = """
    CREATE TABLE students (id INT, name TEXT);
    CREATE TABLE teachers (id INT, name TEXT);
    CREATE TABLE courses (id INT, name TEXT);
    CREATE TABLE enrollments (student_id INT, course_id INT);
    """
    simplified_schema = """
    CREATE TABLE students (id INT, name TEXT);
    CREATE TABLE courses (id INT, name TEXT);
    """
    pruned_schema = manager.static_pruning(original_schema, simplified_schema)
    print("静态剪枝后的schema:", pruned_schema[:100])
    
    # 4. 动态扩充（当选中students表时，自动添加其邻居表）
    selected_tables = {'students'}
    expanded_schema = manager.dynamic_expansion(
        pruned_schema, 
        selected_tables,
        original_schema=original_schema
    )
    print("\n动态扩充后的schema（包含邻居表）:", expanded_schema[:200])
    
    # 5. 动态剪枝（移除strike过多的表）
    # 模拟多次出现但未选中的表
    for _ in range(4):  # 模拟4次出现
        manager.dynamic_pruning(expanded_schema, selected_tables)
    
    final_schema = manager.dynamic_pruning(expanded_schema, selected_tables)
    print("\n动态剪枝后的schema:", final_schema[:200])
    
    # 6. 获取统计信息
    stats = manager.get_table_statistics()
    print("\n表统计信息:")
    for table, info in stats.items():
        print(f"  {table}: appearances={info['appearances']}, "
              f"strikes={info['strikes']}, selected={info['selected']}")
    
    # 7. 使用便捷方法处理节点schema
    cte_sql = "SELECT * FROM students JOIN enrollments ON students.id = enrollments.student_id"
    processed_schema = manager.process_schema_for_node(
        current_schema=pruned_schema,
        cte_or_sql=cte_sql,
        original_schema=original_schema,
        enable_expansion=True,
        enable_pruning=True
    )
    print("\n使用便捷方法处理后的schema:", processed_schema[:200])

