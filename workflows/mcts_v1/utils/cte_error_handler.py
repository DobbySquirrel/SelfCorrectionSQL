"""
CTE错误处理工具

处理CTE执行中的错误，特别是列名引用错误和CTE别名问题
"""

import re
from typing import Dict, Any, Optional, List
from .mcts_helpers import MCTSUtils


class CTEErrorHandler:
    """CTE错误处理器"""
    
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
                        print(f"[错误处理] ⚠️ 检测到CTE别名问题: {full_column_ref} (CTE {cte_name} 使用了别名 {alias})")
                        return True
            
            # 如果CTE中使用了别名但列名引用错误，也认为是CTE列名引用错误
            # 检查CTE中是否有任何别名使用（即使没有完全匹配）
            if re.search(r'FROM\s+\w+\s+\w+|JOIN\s+\w+\s+\w+', cte, re.IGNORECASE):
                print(f"[错误处理] ⚠️ 检测到CTE别名使用但列名引用错误: {full_column_ref} (CTE中使用了别名但列名引用不正确)")
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
                    print(f"[错误处理] ⚠️ 检测到CTE列名引用错误: {column_name} 不在前序CTE中")
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
        print(f"[错误处理] 开始检测CTE列名引用错误: error={error[:100]}, cte={cte[:200] if cte else 'None'}")
        is_cte_column_error = CTEErrorHandler.detect_cte_column_error(error, cte, current_node)
        print(f"[错误处理] detect_cte_column_error 返回: {is_cte_column_error}")
        
        # 传入CTE以便生成更准确的提示（检测是否在CTE上下文中）
        # 注意：确保使用正确的schema_info（从当前节点或根节点获取，确保数据库名称匹配）
        column_mapping = MCTSUtils.find_column_table_mapping(error, schema_info, cte=cte)
        
        # 调试：打印schema_info中的db_name，确保使用正确的数据库schema
        db_name_match = re.search(r'db_name[:\s]+(\w+)', schema_info, re.IGNORECASE)
        if db_name_match:
            db_name = db_name_match.group(1)
            print(f"[错误处理] 使用数据库schema: {db_name}")
        else:
            print(f"[错误处理] ⚠️ 未找到db_name in schema_info")
        if column_mapping:
            # 在失败信息中添加列名到表的映射提示
            if 'column_hint' not in failed_item:
                failed_item['column_hint'] = column_mapping['hint']
                failed_item['column_name'] = column_mapping['column']
                failed_item['column_tables'] = column_mapping['tables']
                print(f"[错误处理] ✅ 找到列名映射: {column_mapping['column']} -> {column_mapping['tables']}")
            
            # 如果是CTE列名引用错误，标记需要重新生成完整CTE链
            if is_cte_column_error:
                failed_item['is_cte_column_error'] = True
                failed_item['requires_full_cte_chain'] = True
                print(f"[错误处理] ⚠️ 标记为CTE列名引用错误，需要重新生成完整CTE链")
        
        return failed_item
