"""
SQL执行器智能体（轻量实现）
"""
from typing import Dict, Any, Optional, List, Tuple
from core.database_connector import DatabaseConnector
import re
import Levenshtein
import time


class SQLExecutor:
    def __init__(self, db_connector: DatabaseConnector):
        self.db_connector = db_connector
        self.cte_generator = None  # 将在初始化后设置
        # 关闭“相似 value”带来的值级相似度计算，避免潜在卡顿
        self.enable_value_similarity = False

    def set_cte_generator(self, cte_generator):
        """设置CTE生成器，用于错误恢复"""
        self.cte_generator = cte_generator

    def execute_queries(self, node, cte: str) -> Dict[str, Any]:
        results = {
            'cte_result': {'valid': False, 'query_result': [], 'error': None},
            'final_sql_result': {'valid': False, 'query_result': [], 'error': None},
            'execution_time': 0.0
        }
        if cte:
            # 从 node 获取 schema_info
            schema_info = getattr(node, 'schema_info', None) if node else None
            results['cte_result'] = self._execute_single_query(cte, schema_info=schema_info)
            # CTE生成器已经输出完整的WITH语句，直接使用
            results['final_sql_result'] = self._execute_single_query(cte, schema_info=schema_info)
            node.full_sql = cte
        return results

    def _execute_single_query(self, sql: str, timeout_s: Optional[float] = None, schema_info: Optional[str] = None) -> Dict[str, Any]:
        start_ts = time.time()
        try:
            # 使用带缓存的执行函数（优化1和2：SQL缓存和标准化）
            from workflows.mcts.utils.sql_exec_helpers import _execute_with_cache
            df, err = _execute_with_cache(self.db_connector, sql, timeout_s=timeout_s)
            duration = time.time() - start_ts
            # print(f"[监控] SQL执行耗时: {duration:.3f}s, rows={len(df) if df is not None else 0}")
            if df is not None:
                return {'valid': True, 'query_result': df.to_dict(orient='records'), 'error': None}
            
            # 如果执行失败，检查是否是列名错误，添加列名建议
            error_msg = err if err else "未知错误"
            enhanced_error = self._enhance_column_error_message(error_msg, schema_info, sql)
            return {'valid': False, 'query_result': [], 'error': enhanced_error}
        except Exception as e:
            duration = time.time() - start_ts
            # print(f"[监控] SQL执行异常，耗时: {duration:.3f}s, error={e}")
            error_msg = str(e)
            enhanced_error = self._enhance_column_error_message(error_msg, schema_info, sql)
            return {'valid': False, 'query_result': [], 'error': enhanced_error}
    
    def execute_with_auto_fix(self, node, cte: str, schema_info: str, timeout_s: Optional[float] = None) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        执行CTE，如果遇到列名错误则自动尝试修复（仅一次）
        
        Args:
            node: MCTS节点
            cte: 待执行的CTE
            schema_info: 数据库schema信息
            
        Returns:
            (执行结果, 修复后的CTE或None)
        """
        # 第一次执行
        result = self._execute_single_query(cte, timeout_s=timeout_s, schema_info=schema_info)
        
        # 如果执行失败，检查是否是列名错误
        if not result['valid']:
            error_msg = result.get('error', '')
            unknown_columns = self._extract_unknown_columns(error_msg)
            
            if unknown_columns and self.cte_generator:
                # 尝试修复列名错误
                column_fix_result = self._try_fix_column_error(node, cte, schema_info, error_msg, unknown_columns, timeout_s=timeout_s)
                if column_fix_result:
                    return column_fix_result
        
        # 无法修复，返回原始结果
        return result, None
    
    def _try_fix_column_error(self, node, cte: str, schema_info: str, error_msg: str, unknown_columns: List[str], timeout_s: Optional[float] = None) -> Optional[Tuple[Dict[str, Any], str]]:
        """尝试修复列名错误"""

        # 为每个错误列名找到最相似的列名
        suggestions = self._find_similar_columns(unknown_columns, schema_info, cte)
        
        if not suggestions:

            return None
         
        # 构建修复提示
        fix_prompt = self._build_column_fix_prompt(
            original_cte=cte,
            error_msg=error_msg,
            suggestions=suggestions,
            question=node.question,
            schema_info=schema_info
        )
        
        # 调用CTE生成器重新生成
        print(fix_prompt)
        fixed_cte = self._regenerate_cte_with_fix(node, fix_prompt)
        
        if not fixed_cte:
            print(f"   ❌ 重新生成失败")
            return None
        
        print(f"修复后的CTE: {fixed_cte}")
        
        # 执行修复后的CTE
        fixed_result = self._execute_single_query(fixed_cte, timeout_s=timeout_s, schema_info=schema_info)
        
        if fixed_result['valid']:
            print(f"   ✅ 修复成功！")
            return (fixed_result, fixed_cte)
        else:
            print(f"   ❌ 修复后仍然失败: {fixed_result.get('error', '')}")
            return None
    
    
    
    def _enhance_column_error_message(self, error_msg: str, schema_info: Optional[str] = None, sql: str = "") -> str:
        """
        增强错误消息，如果是列名错误则添加相似列名建议
        
        Args:
            error_msg: 原始错误消息
            schema_info: schema信息（可选）
            sql: SQL语句（可选，用于提取表信息）
            
        Returns:
            增强后的错误消息
        """
        # 检查是否是 "no such column" 错误
        if not schema_info or "no such column" not in error_msg.lower():
            return error_msg
        
        # 提取未知列名
        unknown_columns = self._extract_unknown_columns(error_msg)
        if not unknown_columns:
            return error_msg
        
        # 找到相似的列名
        try:
            suggestions = self._find_similar_columns(unknown_columns, schema_info, sql)
        except Exception as e:
            # 如果查找相似列名失败，返回原始错误
            return error_msg
        
        if not suggestions:
            return error_msg
        
        # 构建增强的错误消息
        enhanced_msg = error_msg
        
        # 为每个未知列添加建议
        for wrong_col, col_suggestions in suggestions.items():
            if col_suggestions:
                # 取前3个最相似的建议
                top_suggestions = col_suggestions[:3]
                suggestion_texts = []
                tables_with_column = set()  # 收集所有包含相似列的表
                
                for suggested_col, table_name, similarity, _ in top_suggestions:
                    if table_name:
                        suggestion_texts.append(f"表 `{table_name}` 中的列 `{suggested_col}` (相似度: {similarity:.2f})")
                        tables_with_column.add(table_name)
                    else:
                        suggestion_texts.append(f"列 `{suggested_col}` (相似度: {similarity:.2f})")
                
                if suggestion_texts:
                    enhanced_msg += f"\n\n列名建议: '{wrong_col}' 不存在。您是否想要使用以下列名之一?"
                    enhanced_msg += f"\n   {', '.join(suggestion_texts)}"
                            
        return enhanced_msg
    
    def _extract_unknown_columns(self, error_msg: str) -> List[str]:
        """
        从错误消息中提取未知的列名（保留表前缀信息）
        
        Args:
            error_msg: 错误消息
            
        Returns:
            未知列名列表（格式：可能包含表前缀 "table.column" 或只有 "column"）
        """
        unknown_columns = []
        
        # SQLite错误格式: no such column: T1.`Charter School (Y/N)` 或 T1.Charter School (Y/N)
        # 策略：提取 "no such column: " 后面到行尾的所有内容（贪婪匹配）
        sqlite_match = re.search(r'no such column:\s*(.+)', error_msg, re.IGNORECASE)
        if sqlite_match:
            col_text = sqlite_match.group(1).strip()
            # 移除反引号（处理 T1.`col name` 格式）
            col_text = re.sub(r'`', '', col_text)
            unknown_columns.append(col_text)
        
        # MySQL错误格式: unknown column 'xxx'
        mysql_matches = re.findall(r'unknown column\s*[\'"]([^\'"]+)[\'"]', error_msg, re.IGNORECASE)
        unknown_columns.extend(mysql_matches)
        
        # PostgreSQL错误格式: column 'xxx' does not exist
        postgres_matches = re.findall(r'column\s*[\'"]([^\'"]+)[\'"]\s*does not exist', error_msg, re.IGNORECASE)
        unknown_columns.extend(postgres_matches)
        
        # 去重并清理
        cleaned_columns = []
        for col in unknown_columns:
            col = col.strip()
            if col and col not in cleaned_columns:
                cleaned_columns.append(col)
        
        return cleaned_columns
    
    def _extract_table_from_sql(self, sql: str, table_alias: str) -> Optional[str]:
        """
        从SQL中提取表别名对应的真实表名
        
        Args:
            sql: SQL语句
            table_alias: 表别名
            
        Returns:
            真实表名或None
        """
        # 匹配 FROM/JOIN table_name alias 或 FROM/JOIN table_name AS alias
        patterns = [
            rf'FROM\s+(\w+)\s+(?:AS\s+)?{re.escape(table_alias)}\b',
            rf'JOIN\s+(\w+)\s+(?:AS\s+)?{re.escape(table_alias)}\b',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, sql, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def _find_similar_columns(self, unknown_columns: List[str], schema_info: str, sql: str = "") -> Dict[str, Tuple[str, str, float, str]]:
        """
        为每个未知列名找到schema中最相似的列名（智能考虑表上下文）
        
        Args:
            unknown_columns: 未知列名列表（可能包含表前缀，如 "t1.colname"）
            schema_info: schema信息
            sql: 原始SQL（用于提取表别名映射）
            
        Returns:
            {错误列名: (推荐列名, 表名, 相似度分数, 修复说明)}
        """
        # 从schema中提取所有列名和表名
        all_columns = self._extract_all_columns_from_schema(schema_info)
        
        suggestions = {}
        
        for wrong_col_full in unknown_columns:
            # 分离表别名和列名
            table_alias = None
            wrong_col = wrong_col_full
            
            if '.' in wrong_col_full:
                table_alias, wrong_col = wrong_col_full.split('.', 1)
            
            # 如果有表别名，尝试找出真实表名
            target_table = None
            if table_alias and sql:
                target_table = self._extract_table_from_sql(sql, table_alias)
            
            # 收集所有候选列及其相似度
            candidates = []
            wrong_col_lower = wrong_col.lower()
            
            # 遍历所有表和列
            for table_name, columns_with_values in all_columns.items():
                for col, values in columns_with_values.items():
                    # 计算列名相似度
                    col_clean = col.strip('`').lower()
                    
                    # 1. 列名Levenshtein距离
                    distance = Levenshtein.distance(wrong_col_lower, col_clean)
                    max_len = max(len(wrong_col_lower), len(col_clean))
                    col_lev_similarity = 1.0 - (distance / max_len) if max_len > 0 else 0.0
                    
                    # 2. 列名子串匹配
                    col_substring_score = 0.0
                    if wrong_col_lower in col_clean or col_clean in wrong_col_lower:
                        col_substring_score = 0.3
                    
                    # 3. 列名+列值组合相似度（默认关闭以避免卡顿）
                    value_similarities = []
                    if self.enable_value_similarity and values:
                        for value in values:
                            if value and value != 'None':
                                col_value_combined = f"{col_clean} {str(value).lower()}"
                                combined_distance = Levenshtein.distance(wrong_col_lower, col_value_combined)
                                combined_max_len = max(len(wrong_col_lower), len(col_value_combined))
                                combined_sim = 1.0 - (combined_distance / combined_max_len) if combined_max_len > 0 else 0.0
                                if wrong_col_lower in col_value_combined or col_value_combined in wrong_col_lower:
                                    combined_sim += 0.2
                                value_similarities.append(combined_sim)
                    
                    # 取所有列名+列值组合相似度的最大值
                    value_similarity = max(value_similarities) if value_similarities else 0.0
                    
                    # 综合分数：列名相似度 + 列值相似度
                    similarity = col_lev_similarity + col_substring_score + value_similarity * 0.5
                    
                    # 生成修复说明（默认不展示示例值，避免无关噪音与卡顿）
                    fix_note = f"来自表 {table_name}"
                    if self.enable_value_similarity and values:
                        sample_values = values[:3]
                        fix_note += f" (示例值: {', '.join(sample_values)})"
                    
                    # 只保留相似度大于阈值的候选
                    if similarity > 0.3:
                        candidates.append((col, table_name, similarity, fix_note))
            
            # 按相似度排序，取前3个最佳候选
            candidates.sort(key=lambda x: x[2], reverse=True)
            top_candidates = candidates[:3]
            
            if top_candidates:
                suggestions[wrong_col_full] = top_candidates
        
        return suggestions
    
    def _extract_all_columns_from_schema(self, schema_info: str) -> Dict[str, Dict[str, List[str]]]:
        """
        从schema中提取所有表名、列名和列值
        
        Args:
            schema_info: schema信息
            
        Returns:
            {表名: {列名: [值列表]}}
        """
        tables = {}
        
        # 匹配表定义：# table_name(`col1`[val1,val2], `col2`[val3,val4], ...)
        table_pattern = r'#\s*(\w+)\((.*?)\)'
        matches = re.findall(table_pattern, schema_info, re.DOTALL)
        
        for table_name, columns_str in matches:
            # 解析列名和列值
            columns_with_values = self._parse_columns_with_values(columns_str)
            tables[table_name] = columns_with_values
        
        return tables
    
    def _parse_columns_with_values(self, columns_str: str) -> Dict[str, List[str]]:
        """
        解析列名和列值：`col1`[val1,val2], `col2`[val3,val4]
        
        Args:
            columns_str: 列定义字符串
            
        Returns:
            {列名: [值列表]}
        """
        columns_with_values = {}
        current_col = ""
        current_values = []
        in_backtick = False
        in_bracket = False
        current_value = ""
        
        i = 0
        while i < len(columns_str):
            char = columns_str[i]
            
            if char == '`' and not in_bracket:
                in_backtick = not in_backtick
                current_col += char
            elif char == '[' and not in_backtick:
                in_bracket = True
            elif char == ']' and in_bracket:
                in_bracket = False
                if current_value.strip():
                    current_values.append(current_value.strip())
                if current_col.strip():
                    col_name = current_col.strip()
                    columns_with_values[col_name] = current_values.copy()
                current_col = ""
                current_values = []
                current_value = ""
            elif char == ',' and in_bracket:
                if current_value.strip():
                    current_values.append(current_value.strip())
                current_value = ""
            elif in_bracket:
                current_value += char
            elif not in_bracket:
                current_col += char
            
            i += 1
        
        return columns_with_values
    
    def _parse_columns_simple(self, columns_str: str) -> List[str]:
        """简单解析列名（保留反引号）"""
        columns = []
        current_col = ""
        in_backtick = False
        
        for char in columns_str:
            if char == '`':
                in_backtick = not in_backtick
                current_col += char
            elif char == ',' and not in_backtick:
                if current_col.strip():
                    columns.append(current_col.strip())
                current_col = ""
            else:
                current_col += char
        
        if current_col.strip():
            columns.append(current_col.strip())
        
        return columns
    
    def _build_column_fix_prompt(self, original_cte: str, error_msg: str, suggestions: Dict[str, Tuple[str, str, float, str]], 
                          question: str, schema_info: str) -> str:
        """
        构建列名修复提示（包含完整上下文）
        
        Args:
            original_cte: 原始CTE
            error_msg: 错误消息
            suggestions: 推荐的列名 {错误列名: (推荐列名, 表名, 相似度, 修复说明)}
            question: 自然语言问题
            schema_info: 数据库schema
            
        Returns:
            修复提示文本
        """
        suggestion_text = "\n".join([
            f"- 将 `{wrong_col}` 改为以下选项之一：\n" + 
            "\n".join([
                f"  • `{correct_col}` ({fix_note}, 相似度: {similarity:.2f})"
                for correct_col, table, similarity, fix_note in candidates
            ])
            for wrong_col, candidates in suggestions.items()
        ])
        
        prompt = f"""
**CTE执行失败，需要修复！**

**自然语言问题**: {question}

**Database Schema**:
{schema_info}

**错误信息**: {error_msg}

**问题诊断**: 使用了不存在的列名

**推荐的修正**:
{suggestion_text}

**原始CTE（有错误）**:
```sql
{original_cte}
```

**请重新生成CTE，确保：**
1. 使用schema中实际存在的列名（见上面的Database Schema）
2. 注意每个列属于哪个表，不要混淆不同表的列
3. 如果某个表中没有你需要的列，考虑：
   - JOIN其他包含该列的表
   - 或者使用该表中实际存在的、语义相近的列
4. 仔细检查列名的拼写和大小写，使用schema中提供的完整列名
"""
        return prompt
    
    
    
    def _regenerate_cte_with_fix(self, node, fix_prompt: str) -> Optional[str]:
        """
        使用修复提示重新生成CTE
        
        Args:
            node: MCTS节点
            fix_prompt: 修复提示
            
        Returns:
            重新生成的CTE或None
        """
        if not self.cte_generator:
            return None
        
        try:
            # 临时修改节点的additional_context，添加修复提示
            original_context = node.additional_context
            node.additional_context = f"{original_context}\n\n{fix_prompt}"
            
            # 调用CTE生成器
            fixed_cte = self.cte_generator.generate_cte(node)
            
            # 恢复原始context
            node.additional_context = original_context
            
            if fixed_cte and fixed_cte != "<END>":
                return fixed_cte
            
            return None
        except Exception as e:
            print(f"   ⚠️ 重新生成CTE时出错: {e}")
            return None


    # ===== CTE 构建辅助：从历史CTE与当前CTE拼装可执行SQL =====
    def build_executable_cte_sql(self, node, current_cte: str) -> str:
        """
        构建可执行的完整CTE SQL：拼接路径上的所有历史CTE + 当前CTE，保留最后SELECT。
        自动处理<END>与空输入。
        """
        if not current_cte or current_cte is None:
            print(f"⚠️  build_executable_cte_sql: current_cte 为 None 或空")
            return ""

        current_cte_stripped = current_cte.strip()

        # 收集路径上所有历史CTE
        cte_sequence: List[str] = []
        current_node = node
        while current_node is not None:
            if getattr(current_node, 'cte', None) and current_node.cte != "" and current_node.cte != "<END>":
                cte_sequence.insert(0, current_node.cte)
            current_node = current_node.parent

        if current_cte_stripped and current_cte_stripped != "<END>":
            cte_sequence.append(current_cte_stripped)

        return self.combine_cte_sequence(cte_sequence)

    def combine_cte_sequence(self, cte_sequence: List[str]) -> str:
        """组合CTE序列为完整的WITH语句。"""
        if not cte_sequence:
            return ""
        if len(cte_sequence) == 1:
            return cte_sequence[0]

        cte_definitions: List[str] = []
        for cte in cte_sequence:
            cte_def = self.extract_cte_definition(cte)
            if cte_def:
                cte_definitions.append(cte_def)
        if not cte_definitions:
            return cte_sequence[-1]

        combined_ctes = ",\n".join(cte_definitions)
        last_cte_name = self.extract_cte_name(cte_sequence[-1])
        if last_cte_name:
            return f"WITH {combined_ctes}\nSELECT * FROM {last_cte_name};"
        else:
            return f"WITH {combined_ctes}"

    def extract_cte_definition(self, cte: str) -> str:
        """
        从CTE文本中提取定义部分，避免带出最终SELECT。
        
        策略：
        1. 先删除末尾的 SELECT * FROM ... 语句（如果存在）
        2. 然后提取 WITH name AS (...) 部分，去掉WITH关键字
        3. 处理嵌套括号的情况
        """
        if not cte or not cte.strip():
            return ""
        
        # 步骤1: 删除末尾的 SELECT * FROM ... 语句
        # 匹配最后的 SELECT * FROM cte_name; 或 SELECT * FROM cte_name
        # 使用 $ 确保匹配到末尾
        cte_cleaned = re.sub(r'\s*SELECT\s+\*\s+FROM\s+\w+\s*;?\s*$', '', cte, flags=re.IGNORECASE | re.DOTALL)
        cte_cleaned = cte_cleaned.strip()
        
        if not cte_cleaned:
            return ""
        
        # 步骤2: 提取 WITH name AS (...) 部分
        # 使用平衡括号匹配来处理嵌套括号
        # 先找到 WITH name AS 的位置
        with_match = re.search(r'WITH\s+(\w+)\s+AS\s*\(', cte_cleaned, re.IGNORECASE)
        if not with_match:
            # 如果没有WITH，可能已经是纯定义，直接返回（去掉可能的WITH前缀）
            result = re.sub(r'^\s*WITH\s+', '', cte_cleaned, flags=re.IGNORECASE).strip()
            return result
        
        cte_name = with_match.group(1)
        start_pos = with_match.end() - 1  # AS 后面的 ( 的位置
        
        # 步骤3: 使用平衡括号算法找到匹配的右括号
        # 从 start_pos 开始，找到匹配的右括号
        paren_count = 0
        i = start_pos
        while i < len(cte_cleaned):
            if cte_cleaned[i] == '(':
                paren_count += 1
            elif cte_cleaned[i] == ')':
                paren_count -= 1
                if paren_count == 0:
                    # 找到了匹配的右括号
                    cte_body = cte_cleaned[start_pos + 1:i].strip()
                    # 返回 name AS (body) 格式（不包含WITH）
                    return f"{cte_name} AS ({cte_body})"
            i += 1
        
        # 如果没有找到匹配的右括号，说明CTE不完整
        # 回退：尝试提取到末尾
        # 但这种情况应该被标记为错误
        print(f"⚠️ extract_cte_definition: CTE括号不匹配，可能不完整: {cte}...")
        # 尝试提取到末尾（不推荐，但作为回退）
        cte_body = cte_cleaned[start_pos + 1:].strip()
        if cte_body:
            return f"{cte_name} AS ({cte_body})"
        
        return ""

    def extract_cte_name(self, cte: str) -> str:
        """从CTE中提取名称。"""
        match = re.search(r'WITH\s+(\w+)\s+AS', cte, re.IGNORECASE)
        return match.group(1) if match else ""