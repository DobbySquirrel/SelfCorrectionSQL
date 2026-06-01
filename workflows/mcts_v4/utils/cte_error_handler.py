"""
CTE错误处理工具

处理CTE执行中的错误，特别是列名引用错误和CTE别名问题
"""

import re
from typing import Dict, Any, Optional, List
from .mcts_helpers import MCTSUtils


class CTEErrorHandler:
    """CTE错误处理器"""
    
    # 常见SQL错误类型及其修复提示
    ERROR_PATTERNS = {
        'no_such_column': {
            'patterns': [r'no such column', r'unknown column', r'column .* not found'],
            'hint_template': '列名错误: {detail}。请检查列名是否正确，确保列名存在于所引用的表或CTE中。'
        },
        'no_such_table': {
            'patterns': [r'no such table', r'table .* doesn\'t exist', r'unknown table'],
            'hint_template': '表名错误: {detail}。请检查表名是否正确，确保表存在于数据库中。'
        },
        'syntax_error': {
            'patterns': [r'syntax error', r'near ".*":', r'incomplete input'],
            'hint_template': 'SQL语法错误: {detail}。请检查SQL语法，确保括号匹配、关键字正确。'
        },
        'ambiguous_column': {
            'patterns': [r'ambiguous column', r'column reference .* is ambiguous'],
            'hint_template': '列名歧义: {detail}。多个表中存在同名列，请使用表别名限定列名（如 t.column_name）。'
        },
        'aggregate_error': {
            'patterns': [r'misuse of aggregate', r'aggregate .* not allowed', r'must appear in.*group by'],
            'hint_template': '聚合函数错误: {detail}。请检查GROUP BY子句，确保非聚合列都在GROUP BY中。'
        },
        'type_mismatch': {
            'patterns': [r'type mismatch', r'cannot compare', r'incompatible types'],
            'hint_template': '类型不匹配: {detail}。请检查数据类型，确保比较和操作的类型一致。'
        },
        'cte_error': {
            'patterns': [r'duplicate with table name', r'recursive cte', r'cte .* not found'],
            'hint_template': 'CTE定义错误: {detail}。CTE名称与已有CTE重复，请使用不同的名称（如添加_v2后缀）。',
            'requires_full_cte_chain': True  # 需要重新生成完整CTE链
        },
        'join_error': {
            'patterns': [r'join.*on', r'cannot join', r'join condition'],
            'hint_template': 'JOIN错误: {detail}。请检查JOIN条件是否正确，确保连接列存在且类型匹配。'
        },
        'subquery_error': {
            'patterns': [r'subquery', r'more than one row', r'scalar subquery'],
            'hint_template': '子查询错误: {detail}。子查询返回多行但预期单值，请使用聚合函数或LIMIT。'
        },
        'timeout': {
            'patterns': [r'timeout', r'timed out', r'execution time exceeded'],
            'hint_template': '查询超时: {detail}。请优化查询，考虑添加索引条件或简化查询逻辑。'
        },
        'generic': {
            'patterns': [],  # 默认匹配所有其他错误
            'hint_template': 'SQL执行错误: {detail}。请检查SQL语法和逻辑是否正确。'
        }
    }
    
    @staticmethod
    def classify_error(error: str) -> str:
        """
        分类错误类型
        
        Args:
            error: 错误信息
            
        Returns:
            错误类型名称
        """
        error_lower = error.lower()
        for error_type, config in CTEErrorHandler.ERROR_PATTERNS.items():
            if error_type == 'generic':
                continue  # 跳过通用类型，最后处理
            for pattern in config['patterns']:
                if re.search(pattern, error_lower):
                    return error_type
        return 'generic'
    
    @staticmethod
    def generate_repair_hint(error: str, error_type: str = None) -> str:
        """
        生成修复提示
        
        Args:
            error: 原始错误信息
            error_type: 错误类型（如果未提供，会自动分类）
            
        Returns:
            修复提示字符串
        """
        if error_type is None:
            error_type = CTEErrorHandler.classify_error(error)
        
        config = CTEErrorHandler.ERROR_PATTERNS.get(error_type, CTEErrorHandler.ERROR_PATTERNS['generic'])
        hint_template = config['hint_template']
        
        # 截断过长的错误信息
        detail = error[:200] if len(error) > 200 else error
        return hint_template.format(detail=detail)
    
    @staticmethod
    def process_error(failed_item: Dict[str, Any], current_node, root_node) -> Dict[str, Any]:
        """
        处理所有类型的错误，添加修复提示
        
        这是通用的错误处理入口，会根据错误类型调用不同的处理逻辑
        
        Args:
            failed_item: 失败信息字典
            current_node: 当前MCTS节点
            root_node: 根节点（用于获取schema_info）
            
        Returns:
            处理后的失败信息字典（添加了repair_hint等字段）
        """
        error = failed_item.get('error', '').strip()
        if not error:
            error = '未知错误'
            failed_item['error'] = error
        
        # 分类错误类型
        error_type = CTEErrorHandler.classify_error(error)
        failed_item['error_type'] = error_type
        
        # 生成通用修复提示
        repair_hint = CTEErrorHandler.generate_repair_hint(error, error_type)
        failed_item['repair_hint'] = repair_hint
        
        # 对于列名错误，调用专门的处理逻辑（添加列名映射）
        if error_type == 'no_such_column' or CTEErrorHandler.is_column_error(error):
            failed_item = CTEErrorHandler.process_column_error(failed_item, current_node, root_node)
        
        # 对于表名错误，尝试提取表名并提供可能的表名建议
        elif error_type == 'no_such_table':
            failed_item = CTEErrorHandler._process_table_error(failed_item, current_node, root_node)
        
        # 对于语法错误，提取错误位置
        elif error_type == 'syntax_error':
            failed_item = CTEErrorHandler._process_syntax_error(failed_item)
        
        # 对于歧义列名错误，提取歧义列名
        elif error_type == 'ambiguous_column':
            failed_item = CTEErrorHandler._process_ambiguous_column_error(failed_item, current_node, root_node)
        
        # 对于CTE错误（如duplicate with table name），标记需要重新生成
        elif error_type == 'cte_error':
            failed_item['requires_full_cte_chain'] = True
            failed_item['is_cte_name_error'] = True
            # 提取重复的CTE名称
            dup_match = re.search(r'duplicate with table name[:\s]+(\w+)', error, re.IGNORECASE)
            if dup_match:
                failed_item['duplicate_cte_name'] = dup_match.group(1)
        
        return failed_item
    
    @staticmethod
    def _process_table_error(failed_item: Dict[str, Any], current_node, root_node) -> Dict[str, Any]:
        """处理表名错误"""
        error = failed_item.get('error', '')
        
        # 提取错误中的表名
        table_match = re.search(r'no such table[:\s]+(\w+)', error, re.IGNORECASE)
        if not table_match:
            table_match = re.search(r'table\s+"?(\w+)"?\s+doesn\'t exist', error, re.IGNORECASE)
        
        if table_match:
            table_name = table_match.group(1)
            failed_item['table_name'] = table_name
            
            # 从schema_info中提取所有可用的表名
            schema_info = None
            if root_node and hasattr(root_node, 'schema_info'):
                schema_info = root_node.schema_info
            elif current_node and hasattr(current_node, 'schema_info'):
                schema_info = current_node.schema_info
            
            if schema_info:
                # 提取CREATE TABLE语句中的表名
                table_names = re.findall(r'CREATE\s+TABLE\s+[`"]?(\w+)[`"]?', schema_info, re.IGNORECASE)
                if table_names:
                    # 查找相似的表名（简单的字符串相似度）
                    similar_tables = [t for t in table_names if table_name.lower() in t.lower() or t.lower() in table_name.lower()]
                    if similar_tables:
                        failed_item['table_hint'] = f"可能的表名: {', '.join(similar_tables)}"
                    else:
                        failed_item['table_hint'] = f"可用的表: {', '.join(table_names[:10])}"  # 最多显示10个
        
        return failed_item
    
    @staticmethod
    def _process_syntax_error(failed_item: Dict[str, Any]) -> Dict[str, Any]:
        """处理语法错误"""
        error = failed_item.get('error', '')
        
        # 提取语法错误的位置
        near_match = re.search(r'near\s+"([^"]+)"', error, re.IGNORECASE)
        if near_match:
            failed_item['syntax_near'] = near_match.group(1)
            failed_item['syntax_hint'] = f"语法错误位置在 '{near_match.group(1)}' 附近，请检查此处的SQL语法"
        
        return failed_item
    
    @staticmethod
    def _process_ambiguous_column_error(failed_item: Dict[str, Any], current_node, root_node) -> Dict[str, Any]:
        """处理歧义列名错误"""
        error = failed_item.get('error', '')
        
        # 提取歧义的列名
        col_match = re.search(r'column\s+[`"]?(\w+)[`"]?\s+is\s+ambiguous', error, re.IGNORECASE)
        if not col_match:
            col_match = re.search(r'ambiguous\s+column[:\s]+[`"]?(\w+)[`"]?', error, re.IGNORECASE)
        
        if col_match:
            column_name = col_match.group(1)
            failed_item['ambiguous_column'] = column_name
            
            # 从schema_info中查找包含该列的所有表
            schema_info = None
            if root_node and hasattr(root_node, 'schema_info'):
                schema_info = root_node.schema_info
            elif current_node and hasattr(current_node, 'schema_info'):
                schema_info = current_node.schema_info
            
            if schema_info:
                # 查找包含该列的表
                tables_with_column = MCTSUtils.find_tables_with_column(column_name, schema_info)
                if tables_with_column:
                    failed_item['ambiguous_hint'] = f"列 '{column_name}' 存在于以下表中: {', '.join(tables_with_column)}。请使用表别名限定。"
        
        return failed_item
    
    @staticmethod
    def is_column_error(error: str) -> bool:
        """
        检查是否是列名错误
        
        Args:
            error: 错误信息
            
        Returns:
            如果是列名错误返回True
        """
        return 'no such column' in error.lower()
    
    @staticmethod
    def detect_cte_column_error(error: str, cte: str, current_node) -> bool:
        """
        检测是否是CTE列名引用错误
        
        Args:
            error: 错误信息
            cte: CTE文本
            current_node: 当前MCTS节点
            
        Returns:
            如果是CTE列名引用错误返回True
        """
        if not cte:
            return False
        
        # 首先从错误信息中提取完整的列名（可能包含别名，如 "c.atom_id2"）
        # 匹配 "no such column: ..." 格式，保留别名信息
        error_column_match = re.search(r'no\s+such\s+column[:\s]+([^\s,;]+)', error, re.IGNORECASE)
        if not error_column_match:
            return False
        
        full_column_ref = error_column_match.group(1).strip().strip('`"\'')
        column_name = MCTSUtils.extract_column_from_error(error)  # 提取纯列名（去掉别名）
        
        if not column_name:
            return False
        
        # 检查列名是否包含CTE别名（如 e.colour, c.order_id）
        if '.' in full_column_ref:
            alias_part = full_column_ref.split('.')[0]
            # 检查CTE中是否使用了别名（FROM cte1 c 或 FROM cte1 AS c）
            # 匹配 FROM cte_name alias 或 FROM cte_name AS alias
            # 注意：需要匹配所有可能的别名位置（可能在FROM后，也可能在JOIN中）
            cte_alias_patterns = [
                r'FROM\s+(\w+)(?:\s+AS\s+(\w+))?(?:\s+(\w+))?',  # FROM cte1 c 或 FROM cte1 AS c
                r'JOIN\s+(\w+)(?:\s+AS\s+(\w+))?(?:\s+(\w+))?',  # JOIN cte1 c 或 JOIN cte1 AS c
            ]
            
            for pattern in cte_alias_patterns:
                cte_alias_matches = list(re.finditer(pattern, cte, re.IGNORECASE))
                for cte_alias_match in cte_alias_matches:
                    cte_name = cte_alias_match.group(1)
                    alias = cte_alias_match.group(2) or cte_alias_match.group(3)
                    # 如果错误中的别名匹配CTE别名，说明是CTE别名问题
                    if alias and alias.lower() == alias_part.lower():
                        return True
            
            # 如果CTE中使用了别名但列名引用错误，也认为是CTE列名引用错误
            # 检查CTE中是否有任何别名使用（即使没有完全匹配）
            if re.search(r'FROM\s+\w+\s+\w+|JOIN\s+\w+\s+\w+', cte, re.IGNORECASE):
                return True
        else:
            # 检查列名是否在前序CTE中存在
            # 向上遍历父节点链，检查列名是否在成功的CTE中被选择
            check_node = current_node.parent
            column_found = False
            while check_node is not None:
                if hasattr(check_node, 'cte') and check_node.cte and check_node.cte != "<END>":
                    exec_results = check_node.execution_results.get('cte_result', {})
                    if exec_results.get('valid', False):
                        # 检查列名是否在CTE的SELECT子句中
                        cte_text = check_node.cte
                        # 提取SELECT子句中的列
                        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', cte_text, re.IGNORECASE | re.DOTALL)
                        if select_match:
                            select_clause = select_match.group(1)
                            # 检查列名是否在SELECT子句中
                            if re.search(rf'\b{re.escape(column_name)}\b', select_clause, re.IGNORECASE):
                                column_found = True
                                break
                check_node = check_node.parent
            
            # 如果列名不在前序CTE中，可能是CTE列名引用错误
            if not column_found:
                # 进一步检查：如果CTE中引用了其他CTE，但列名不在那些CTE中，则是CTE列名引用错误
                if re.search(r'FROM\s+cte\d+|FROM\s+\w+.*cte', cte, re.IGNORECASE):
                    return True
        
        return False
    
    @staticmethod
    def process_column_error(failed_item: Dict[str, Any], current_node, root_node) -> Dict[str, Any]:
        """
        处理列名错误，添加列名映射和CTE错误标记
        
        Args:
            failed_item: 失败信息字典
            current_node: 当前MCTS节点
            root_node: 根节点（用于获取schema_info）
            
        Returns:
            处理后的失败信息字典
        """
        error = failed_item.get('error', '').strip()
        cte = failed_item.get('cte', '').strip()
        
        if not CTEErrorHandler.is_column_error(error):
            return failed_item
        
        # 使用根节点的schema_info（确保使用最新的schema）
        schema_info = None
        if hasattr(current_node, 'schema_info') and current_node.schema_info:
            schema_info = current_node.schema_info
        elif root_node and hasattr(root_node, 'schema_info'):
            schema_info = root_node.schema_info
        
        if not schema_info:
            return failed_item
        
        # 检测是否是CTE列名引用错误
        is_cte_column_error = CTEErrorHandler.detect_cte_column_error(error, cte, current_node)
        
        # 传入CTE以便生成更准确的提示（检测是否在CTE上下文中）
        column_mapping = MCTSUtils.find_column_table_mapping(error, schema_info, cte=cte)
        
        if column_mapping:
            # 在失败信息中添加列名到表的映射提示
            if 'column_hint' not in failed_item:
                failed_item['column_hint'] = column_mapping['hint']
                failed_item['column_name'] = column_mapping['column']
                failed_item['column_tables'] = column_mapping['tables']
            
            # 如果是CTE列名引用错误，标记需要重新生成完整CTE链
            if is_cte_column_error:
                failed_item['is_cte_column_error'] = True
                failed_item['requires_full_cte_chain'] = True
        
        return failed_item
