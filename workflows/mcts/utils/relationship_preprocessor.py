#!/usr/bin/env python3
"""
表关系预处理器 (修复版 + 增强版)

1. 修复 NoneType 排序报错
2. 批量处理所有数据库，计算表间连接关系类型（1:1, 1:N, N:1, M:N）
3. 增加简单的枚举值和空值率探测接口 (预留)
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple
from collections import defaultdict

# 添加项目路径
sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from core.database_connector import DatabaseConnector
# 确保引用的是我们修正过 Bug 的 analyze_join_relationships
from workflows.mcts.test.analyze_join_relationships import (
    determine_relationship_type,
    extract_tables_from_sql
)

class RelationshipPreprocessor:
    """表关系预处理器"""
    
    def __init__(self, db_root_dir: str = None):
        if db_root_dir:
            self.db_root_dir = Path(db_root_dir)
        else:
            self.db_root_dir = Path("/ssd/shenshuyu/work/bird/dev_20240627/dev_databases")
        
        self.relationships_cache = {}
    
    def process_all_databases(self, 
                             output_file: str = "relationships.json",
                             db_ids: Optional[List[str]] = None,
                             skip_existing: bool = True) -> Dict[str, Any]:
        """批量处理所有数据库"""
        # 加载已存在的关系信息
        existing_relationships = {}
        output_path = Path(output_file)
        if output_path.exists() and skip_existing:
            try:
                with open(output_path, 'r', encoding='utf-8') as f:
                    existing_relationships = json.load(f)
                print(f"✅ 加载了 {len(existing_relationships)} 个已存在的数据库关系信息")
            except Exception as e:
                print(f"⚠️ 加载已有关系信息失败: {e}")
        
        if db_ids is None:
            db_ids = self._get_all_database_ids()
        
        all_relationships = existing_relationships.copy()
        processed_count = 0
        skipped_count = 0
        error_count = 0
        
        print(f"\n{'='*80}")
        print(f"开始批量处理 {len(db_ids)} 个数据库")
        print(f"{'='*80}\n")
        
        for i, db_id in enumerate(db_ids, 1):
            if skip_existing and db_id in existing_relationships:
                # 即使跳过，也要确保没有错误标记，如果有错误标记则重试
                if "error" not in existing_relationships[db_id].get("metadata", {}):
                    skipped_count += 1
                    # print(f"[{i}/{len(db_ids)}] ⏭️  跳过 {db_id} (已存在)")
                    continue
            
            print(f"[{i}/{len(db_ids)}] 🔍 处理 {db_id}...")
            try:
                relationships = self.process_database(db_id)
                if relationships:
                    all_relationships[db_id] = relationships
                    processed_count += 1
                    print(f"      ✅ 完成: 发现 {len(relationships['relationships'])} 个关系")
                else:
                    print(f"      ⚠️  未发现关系")
            except Exception as e:
                error_count += 1
                print(f"      ❌ 错误: {e}")
                all_relationships[db_id] = {
                    "relationships": [],
                    "metadata": {
                        "error": str(e),
                        "processed_at": None
                    }
                }
            
            if i % 10 == 0:
                self._save_relationships(all_relationships, output_file)
                print(f"      💾 已保存进度 ({i}/{len(db_ids)})")
        
        self._save_relationships(all_relationships, output_file)
        
        print(f"\n{'='*80}")
        print(f"处理完成!")
        print(f"  ✅ 成功处理: {processed_count} 个数据库")
        print(f"  ⏭️  跳过: {skipped_count} 个数据库")
        print(f"  ❌ 错误: {error_count} 个数据库")
        print(f"  📁 输出文件: {output_file}")
        print(f"{'='*80}\n")
        
        return all_relationships
    
    def process_database(self, db_id: str) -> Optional[Dict[str, Any]]:
        """处理单个数据库"""
        db_connector = None
        try:
            db_path = self.db_root_dir / db_id / f"{db_id}.sqlite"
            
            db_connector = DatabaseConnector(str(db_path))
            if not db_connector.connect():
                raise Exception(f"无法连接到数据库: {db_path}")
            
            schema_info = db_connector.get_schema_info()
            if not schema_info:
                return None
            
            tables = list(schema_info.keys())
            if len(tables) < 2:
                return {
                    "relationships": [],
                    "metadata": {
                        "processed_at": self._get_timestamp(),
                        "total_relationships": 0,
                        "total_tables": len(tables)
                    }
                }
            
            potential_joins = self._detect_potential_joins(db_connector, schema_info, tables)
            
            relationships = []
            for join_info in potential_joins:
                table1 = join_info['table1']
                col1 = join_info['col1']
                table2 = join_info['table2']
                col2 = join_info['col2']
                
                # --- 核心：确定关系类型 (使用之前的修复版逻辑) ---
                rel_type = determine_relationship_type(db_connector, table1, col1, table2, col2)
                
                if rel_type:
                    description = self._generate_relationship_description(
                        table1, col1, table2, col2, rel_type
                    )
                    
                    relationships.append({
                        "table1": table1,
                        "col1": col1,
                        "table2": table2,
                        "col2": col2,
                        "relationship_type": rel_type,
                        "description": description,
                        "method": join_info.get('detection_method', 'unknown')
                    })
            
            return {
                "relationships": relationships,
                "metadata": {
                    "processed_at": self._get_timestamp(),
                    "total_relationships": len(relationships),
                    "total_tables": len(tables)
                }
            }
            
        except Exception as e:
            raise Exception(f"处理数据库 {db_id} 失败: {e}")
        finally:
            if db_connector:
                db_connector.disconnect()
    
    def _detect_potential_joins(self, 
                                db_connector: DatabaseConnector,
                                schema_info: Dict[str, Any],
                                tables: List[str]) -> List[Dict[str, str]]:
        """检测可能的JOIN关系 (已修复 NoneType 排序错误)"""
        potential_joins = []
        processed_pairs = set()
        
        # 策略1: 基于外键关系
        for table1 in tables:
            if table1 not in schema_info: continue
            
            foreign_keys = schema_info[table1].get('foreign_keys', [])
            for fk in foreign_keys:
                table2 = fk.get('references_table')
                col1 = fk.get('column')
                col2 = fk.get('references_column')
                
                # --- 修复 1: 空值检查 ---
                if not table2 or not col1 or not col2:
                    continue
                    
                if table2 in tables and table2 != table1:
                    # --- 修复 2: 强制转换为字符串进行排序，防止 None 报错 ---
                    sort_key = sorted([str(table1), str(table2), str(col1), str(col2)])
                    pair_key = tuple(sort_key)
                    
                    if pair_key not in processed_pairs:
                        potential_joins.append({
                            'table1': table1,
                            'col1': col1,
                            'table2': table2,
                            'col2': col2,
                            'detection_method': 'foreign_key'
                        })
                        processed_pairs.add(pair_key)
        
        # 策略2: 基于列名相似性
        # (只有当外键很少时才启用，避免过多误判)
        if len(potential_joins) < len(tables):
            for i, table1 in enumerate(tables):
                if table1 not in schema_info: continue
                cols1 = [c['name'] for c in schema_info[table1].get('columns', [])]
                
                for table2 in tables[i+1:]:
                    if table2 not in schema_info: continue
                    cols2 = [c['name'] for c in schema_info[table2].get('columns', [])]
                    
                    for col1 in cols1:
                        for col2 in cols2:
                            # --- 修复 3: 空值检查 ---
                            if not col1 or not col2: continue
                            
                            if self._are_columns_similar(col1, col2):
                                # --- 修复 4: 强制字符串排序 ---
                                sort_key = sorted([str(table1), str(table2), str(col1), str(col2)])
                                pair_key = tuple(sort_key)
                                
                                if pair_key not in processed_pairs:
                                    potential_joins.append({
                                        'table1': table1,
                                        'col1': col1,
                                        'table2': table2,
                                        'col2': col2,
                                        'detection_method': 'name_similarity'
                                    })
                                    processed_pairs.add(pair_key)
        
        return potential_joins
    
    def _are_columns_similar(self, col1: str, col2: str) -> bool:
        """检查列名相似性 (已修复 NoneType 错误)"""
        if not col1 or not col2:
            return False
            
        def normalize(name: str) -> str:
            # 增加健壮性
            if not name: return ""
            return str(name).lower().replace('_', '').replace(' ', '').replace('-', '')
        
        norm1 = normalize(col1)
        norm2 = normalize(col2)
        
        if not norm1 or not norm2:
            return False

        if norm1 == norm2:
            return True
        
        # 包含关系逻辑：避免短词误判 (如 id 包含在 idea 中)
        if (norm1 in norm2 or norm2 in norm1):
            # 长度限制：长度差不能太大，且必须包含 id, code, key 等关键词才算
            keywords = ['id', 'code', 'key', 'no', 'num']
            has_keyword = any(k in norm1 for k in keywords) or any(k in norm2 for k in keywords)
            
            if has_keyword and abs(len(norm1) - len(norm2)) <= 4:
                return True
        
        return False
    
    def _generate_relationship_description(self, t1, c1, t2, c2, rel_type):
        """生成描述 (保持原样，可根据需要优化 Prompt)"""
        # 这里使用你之前的逻辑，或者简化为符号表示以节省 Token
        # 比如直接返回 "{t1}.{c1} ({rel_type}) {t2}.{c2}"
        if rel_type == "1:1":
            return f"One-to-One relationship. Vertically partitioned data."
        elif rel_type == "1:N":
            return f"One-to-Many relationship (Parent: {t1}, Child: {t2}). Aggregation needed on {t2}."
        elif rel_type == "N:1":
            return f"Many-to-One relationship (Child: {t1}, Parent: {t2})."
        elif rel_type == "M:N":
            return f"Many-to-Many relationship. Cartesian product risk."
        return f"Relationship: {rel_type}"
    
    def _get_all_database_ids(self) -> List[str]:
        db_ids = []
        if self.db_root_dir.exists():
            for item in self.db_root_dir.iterdir():
                if item.is_dir():
                    db_file = item / f"{item.name}.sqlite"
                    if db_file.exists():
                        db_ids.append(item.name)
        return sorted(db_ids)
    
    def _save_relationships(self, relationships: Dict[str, Any], output_file: str):
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(relationships, f, indent=2, ensure_ascii=False)
    
    def _get_timestamp(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="批量处理数据库表关系")
    parser.add_argument("--db_root_dir", type=str, default=None)
    parser.add_argument("--output_file", type=str, default="workflows/mcts/data/relationships.json")
    parser.add_argument("--db_ids", type=str, nargs="*")
    parser.add_argument("--skip_existing", action="store_true", default=False, help="是否跳过已存在的")
    
    args = parser.parse_args()
    
    preprocessor = RelationshipPreprocessor(args.db_root_dir)
    preprocessor.process_all_databases(
        output_file=args.output_file,
        db_ids=args.db_ids,
        skip_existing=args.skip_existing
    )