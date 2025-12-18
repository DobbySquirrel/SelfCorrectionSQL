#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化版SQL到MQL转换器
专门处理california_schools和financial数据库的SQL查询
"""

import re
import json
from typing import Dict, List, Tuple


class SimpleSQLToMQLConverter:
    """简化版SQL到MQL转换器"""
    
    def __init__(self):
        self.collection_mappings = {
            'california_schools': {
                'frpm': 'frpm',
                'schools': 'schools',
                'satscores': 'satscores'
            },
            'financial': {
                'account': 'account',
                'client': 'client', 
                'district': 'district',
                'loan': 'loan',
                'disp': 'disp'
            }
        }
    
    def parse_sql_file(self, file_path: str) -> List[Tuple[str, str]]:
        """解析SQL文件"""
        queries = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    parts = line.split('\t')
                    if len(parts) == 2:
                        sql_query = parts[0].strip()
                        database = parts[1].strip()
                        queries.append((sql_query, database, line_num))
        
        return queries
    
    def convert_sql_to_mql(self, sql_query: str, database: str) -> Dict:
        """转换单个SQL查询为MQL"""
        try:
            # 基础解析
            parsed = self._parse_basic_sql(sql_query)
            
            # 构建MongoDB查询
            mql_query = self._build_mongo_query(parsed, database)
            
            return {
                "success": True,
                "mql_query": mql_query,
                "parsed_sql": parsed
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "original_sql": sql_query
            }
    
    def _parse_basic_sql(self, sql_query: str) -> Dict:
        """基础SQL解析"""
        # 清理SQL
        sql = re.sub(r'\s+', ' ', sql_query.strip())
        
        # 提取SELECT
        select_match = re.search(r'SELECT\s+(.+?)\s+FROM', sql, re.IGNORECASE)
        if not select_match:
            raise ValueError("无法解析SELECT子句")
        
        select_fields = select_match.group(1).strip()
        
        # 提取FROM
        from_match = re.search(r'FROM\s+(.+?)(?:\s+WHERE|\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)', sql, re.IGNORECASE)
        if not from_match:
            raise ValueError("无法解析FROM子句")
        
        from_clause = from_match.group(1).strip()
        
        # 提取WHERE
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)', sql, re.IGNORECASE)
        where_clause = where_match.group(1).strip() if where_match else ""
        
        # 提取ORDER BY
        order_match = re.search(r'ORDER\s+BY\s+(.+?)(?:\s+LIMIT|$)', sql, re.IGNORECASE)
        order_clause = order_match.group(1).strip() if order_match else ""
        
        # 提取LIMIT
        limit_match = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
        limit_value = int(limit_match.group(1)) if limit_match else None
        
        # 提取GROUP BY
        group_match = re.search(r'GROUP\s+BY\s+(.+?)(?:\s+HAVING|\s+ORDER\s+BY|\s+LIMIT|$)', sql, re.IGNORECASE)
        group_clause = group_match.group(1).strip() if group_match else ""
        
        return {
            "select": select_fields,
            "from": from_clause,
            "where": where_clause,
            "order_by": order_clause,
            "limit": limit_value,
            "group_by": group_clause
        }
    
    def _build_mongo_query(self, parsed: Dict, database: str) -> Dict:
        """构建MongoDB查询"""
        pipeline = []
        
        # 处理FROM子句 (包括JOIN)
        collections = self._parse_from_clause(parsed["from"])
        
        # 如果有JOIN，添加$lookup阶段
        if len(collections) > 1:
            for i, collection in enumerate(collections[1:], 1):
                lookup_stage = {
                    "$lookup": {
                        "from": collection["name"],
                        "localField": collection["join_field"],
                        "foreignField": collection["join_field"],
                        "as": collection["alias"]
                    }
                }
                pipeline.append(lookup_stage)
        
        # 处理WHERE条件
        if parsed["where"]:
            match_stage = self._build_match_stage(parsed["where"])
            if match_stage:
                pipeline.append(match_stage)
        
        # 处理GROUP BY
        if parsed["group_by"]:
            group_stage = self._build_group_stage(parsed["group_by"], parsed["select"])
            if group_stage:
                pipeline.append(group_stage)
        
        # 处理ORDER BY
        if parsed["order_by"]:
            sort_stage = self._build_sort_stage(parsed["order_by"])
            if sort_stage:
                pipeline.append(sort_stage)
        
        # 处理LIMIT
        if parsed["limit"]:
            pipeline.append({"$limit": parsed["limit"]})
        
        # 处理SELECT (投影)
        project_stage = self._build_project_stage(parsed["select"])
        if project_stage:
            pipeline.append(project_stage)
        
        return {
            "database": database,
            "collection": collections[0]["name"] if collections else "unknown",
            "pipeline": pipeline
        }
    
    def _parse_from_clause(self, from_clause: str) -> List[Dict]:
        """解析FROM子句"""
        collections = []
        
        # 检查是否有JOIN
        join_pattern = r'(\w+)\s+AS\s+(\w+)\s+INNER\s+JOIN\s+(\w+)\s+AS\s+(\w+)\s+ON\s+(.+?)(?:\s+WHERE|\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)'
        join_match = re.search(join_pattern, from_clause, re.IGNORECASE)
        
        if join_match:
            # 有JOIN的情况
            table1 = join_match.group(1)
            alias1 = join_match.group(2)
            table2 = join_match.group(3)
            alias2 = join_match.group(4)
            join_condition = join_match.group(5)
            
            # 解析JOIN条件
            on_match = re.search(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', join_condition)
            if on_match:
                join_field = on_match.group(2)  # 假设字段名相同
            
            collections = [
                {"name": table1, "alias": alias1, "join_field": join_field},
                {"name": table2, "alias": alias2, "join_field": join_field}
            ]
        else:
            # 简单表名
            table_match = re.search(r'(\w+)(?:\s+AS\s+(\w+))?', from_clause)
            if table_match:
                table_name = table_match.group(1)
                alias = table_match.group(2) if table_match.group(2) else table_name
                collections = [{"name": table_name, "alias": alias, "join_field": None}]
        
        return collections
    
    def _build_match_stage(self, where_clause: str) -> Dict:
        """构建$match阶段"""
        match_conditions = {}
        
        # 分割AND条件
        and_conditions = re.split(r'\s+AND\s+', where_clause, flags=re.IGNORECASE)
        
        for condition in and_conditions:
            # 处理各种操作符
            if '=' in condition:
                field, value = self._parse_equality_condition(condition)
                if field and value:
                    match_conditions[field] = value
            elif '>' in condition:
                field, value = self._parse_comparison_condition(condition, '>')
                if field and value:
                    match_conditions[field] = {"$gt": value}
            elif '<' in condition:
                field, value = self._parse_comparison_condition(condition, '<')
                if field and value:
                    match_conditions[field] = {"$lt": value}
            elif 'LIKE' in condition.upper():
                field, pattern = self._parse_like_condition(condition)
                if field and pattern:
                    match_conditions[field] = {"$regex": pattern}
            elif 'BETWEEN' in condition.upper():
                field, min_val, max_val = self._parse_between_condition(condition)
                if field and min_val and max_val:
                    match_conditions[field] = {"$gte": min_val, "$lte": max_val}
            elif 'IS NOT NULL' in condition.upper():
                field = self._parse_null_condition(condition)
                if field:
                    match_conditions[field] = {"$exists": True}
            elif 'IS NULL' in condition.upper():
                field = self._parse_null_condition(condition)
                if field:
                    match_conditions[field] = {"$exists": False}
        
        return {"$match": match_conditions} if match_conditions else None
    
    def _parse_equality_condition(self, condition: str) -> Tuple[str, str]:
        """解析等值条件"""
        pattern = r'(\w+\.?\w*)\s*=\s*[\'"]([^\'"]+)[\'"]'
        match = re.search(pattern, condition)
        if match:
            return match.group(1), match.group(2)
        return None, None
    
    def _parse_comparison_condition(self, condition: str, operator: str) -> Tuple[str, str]:
        """解析比较条件"""
        pattern = rf'(\w+\.?\w*)\s*\{operator}\s*[\'"]([^\'"]+)[\'"]'
        match = re.search(pattern, condition)
        if match:
            return match.group(1), match.group(2)
        return None, None
    
    def _parse_like_condition(self, condition: str) -> Tuple[str, str]:
        """解析LIKE条件"""
        pattern = r'(\w+\.?\w*)\s+LIKE\s+[\'"]([^\'"]+)[\'"]'
        match = re.search(pattern, condition, re.IGNORECASE)
        if match:
            field = match.group(1)
            pattern_value = match.group(2)
            # 转换LIKE模式为正则表达式
            regex_pattern = pattern_value.replace('%', '.*').replace('_', '.')
            return field, regex_pattern
        return None, None
    
    def _parse_between_condition(self, condition: str) -> Tuple[str, str, str]:
        """解析BETWEEN条件"""
        pattern = r'(\w+\.?\w*)\s+BETWEEN\s+([^\s]+)\s+AND\s+([^\s]+)'
        match = re.search(pattern, condition, re.IGNORECASE)
        if match:
            field = match.group(1)
            min_val = match.group(2).strip("'\"")
            max_val = match.group(3).strip("'\"")
            return field, min_val, max_val
        return None, None, None
    
    def _parse_null_condition(self, condition: str) -> str:
        """解析NULL条件"""
        pattern = r'(\w+\.?\w*)\s+IS\s+(?:NOT\s+)?NULL'
        match = re.search(pattern, condition, re.IGNORECASE)
        return match.group(1) if match else None
    
    def _build_group_stage(self, group_by: str, select: str) -> Dict:
        """构建$group阶段"""
        group_fields = [field.strip() for field in group_by.split(',')]
        
        group_id = {}
        for field in group_fields:
            group_id[field] = f"${field}"
        
        return {"$group": {"_id": group_id}}
    
    def _build_sort_stage(self, order_by: str) -> Dict:
        """构建$sort阶段"""
        sort_fields = {}
        
        for field in order_by.split(','):
            field = field.strip()
            if 'DESC' in field.upper():
                field_name = field.replace('DESC', '').strip()
                sort_fields[field_name] = -1
            elif 'ASC' in field.upper():
                field_name = field.replace('ASC', '').strip()
                sort_fields[field_name] = 1
            else:
                sort_fields[field] = 1
        
        return {"$sort": sort_fields}
    
    def _build_project_stage(self, select: str) -> Dict:
        """构建$project阶段"""
        # 这里可以添加投影逻辑
        # 暂时返回None，因为大多数情况下不需要额外的投影
        return None
    
    def convert_all_queries(self, file_path: str) -> List[Dict]:
        """转换所有查询"""
        queries = self.parse_sql_file(file_path)
        results = []
        
        for sql_query, database, line_num in queries:
            print(f"正在转换第{line_num}行查询...")
            
            result = self.convert_sql_to_mql(sql_query, database)
            result["line_number"] = line_num
            result["database"] = database
            result["original_sql"] = sql_query
            
            results.append(result)
        
        return results
    
    def save_results(self, results: List[Dict], output_file: str):
        """保存结果"""
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        print(f"转换结果已保存到: {output_file}")
    
    def print_examples(self, results: List[Dict], num_examples: int = 5):
        """打印转换示例"""
        print(f"\n前{num_examples}个转换示例:")
        
        for i, result in enumerate(results[:num_examples]):
            print(f"\n=== 查询 {i+1} ===")
            print(f"行号: {result['line_number']}")
            print(f"数据库: {result['database']}")
            print(f"原始SQL: {result['original_sql']}")
            
            if result["success"]:
                print(f"MongoDB集合: {result['mql_query']['collection']}")
                print(f"MongoDB管道: {json.dumps(result['mql_query']['pipeline'], ensure_ascii=False, indent=2)}")
            else:
                print(f"转换失败: {result['error']}")


def main():
    """主函数"""
    converter = SimpleSQLToMQLConverter()
    
    # 输入文件
    input_file = "/home/shenshuyu/SQL_tool/work/bird/dev_20240627/dev_100.sql"
    output_file = "/home/shenshuyu/SQL_tool/work/bird/dev_20240627/mql_conversion_results.json"
    
    print("开始SQL到MQL转换...")
    print(f"输入文件: {input_file}")
    
    # 转换查询
    results = converter.convert_all_queries(input_file)
    
    # 保存结果
    converter.save_results(results, output_file)
    
    # 打印示例
    converter.print_examples(results, 5)
    
    # 统计
    successful = len([r for r in results if r["success"]])
    total = len(results)
    print(f"\n转换统计: {successful}/{total} 成功")


if __name__ == "__main__":
    main()