"""
MCTS Expansion阶段的辅助函数

将 _mcts_expansion 函数拆分成更小的功能单元，提高代码可读性和可维护性
"""
from typing import Dict, List, Optional, Tuple, Any
from ..core.mcts_node import MCTSNode
from ..utils.mcts_helpers import MCTSUtils


def check_parent_has_empty_result(node: MCTSNode) -> bool:
    """
    检测前序CTE是否返回空结果（用于判断是否提示了模糊匹配）
    
    Args:
        node: 当前节点
        
    Returns:
        如果父节点返回空结果返回True，否则返回False
    """
    if not node.parent or not hasattr(node.parent, 'execution_results'):
        return False
    
    parent_exec_res = node.parent.execution_results.get('cte_result', {})
    if not parent_exec_res.get('valid', False):
        return False
    
    parent_query_result = parent_exec_res.get('query_result', [])
    try:
        parent_query_result = MCTSUtils.safe_to_dict(parent_query_result)
    except Exception:
        parent_query_result = []
    
    if not isinstance(parent_query_result, list):
        try:
            parent_query_result = list(parent_query_result)
        except Exception:
            parent_query_result = []
    
    return not parent_query_result or len(parent_query_result) == 0


def print_cte_variants_info(unique_cte_variants: List[Dict[str, Any]], 
                           parent_has_empty_result: bool):
    """
    打印去重后的CTE内容
    
    Args:
        unique_cte_variants: 去重后的CTE变体列表
        parent_has_empty_result: 父节点是否返回空结果
    """
    for idx, info in enumerate(unique_cte_variants, 1):
        cte_text = info.get('cte', '')
        count = info.get('count', 0)
        exec_res = info.get('execution_result', {})
        
        if cte_text == "<END>":
            print(f"  [{idx}] <END> (出现{count}次)")
        else:
            valid = exec_res.get('valid', False) if exec_res else False
            if valid:
                query_result = exec_res.get('query_result', [])
                try:
                    query_result = MCTSUtils.safe_to_dict(query_result)
                except Exception:
                    query_result = []
                if not isinstance(query_result, list):
                    try:
                        query_result = list(query_result)
                    except Exception:
                        query_result = []
                result_count = len(query_result) if query_result else 0
                status = f"✅ 有效，返回{result_count}行" if result_count > 0 else "⚠️ 有效但结果为空"
            else:
                error = exec_res.get('error', '未知错误') if exec_res else '未知错误'
                status = f"❌ 失败: {error}"
            
            print(f"  [{idx}] CTE (出现{count}次, {status}):")
            print(f"      {cte_text[:200]}{'...' if len(cte_text) > 200 else ''}")
            
            # 如果前序CTE返回空结果（提示了模糊匹配），打印详细的CTE和执行结果
            if parent_has_empty_result:
                # 检查CTE是否包含模糊匹配函数
                has_fuzzy_match = ('levenshtein' in cte_text.lower() or 
                                 'similarity' in cte_text.lower() or 
                                 'pg_trgm' in cte_text.lower() or
                                 ' % ' in cte_text)
                
                if has_fuzzy_match:
                    print(f"      🔍 [模糊匹配CTE] 检测到使用了模糊匹配函数")
                
                print(f"      完整CTE内容:")
                print(f"      {cte_text}")
                # 打印执行结果
                if valid and query_result:
                    print(f"      📊 执行结果（前{min(10, result_count)}行）:")
                    for row_idx, row in enumerate(query_result[:10], 1):
                        if isinstance(row, dict):
                            # 如果是字符串类型，用单引号括起来
                            formatted_items = []
                            for k, v in row.items():
                                if isinstance(v, str):
                                    formatted_items.append(f"{k}='{v}'")
                                else:
                                    formatted_items.append(f"{k}={v}")
                            row_str = ", ".join(formatted_items)
                        else:
                            row_str = str(row)
                        print(f"        行{row_idx}: {row_str}")
                    if result_count > 10:
                        print(f"        ... (还有 {result_count - 10} 行未显示)")
                elif valid and not query_result:
                    print(f"      📊 执行结果: 空结果集")
                elif not valid:
                    error = exec_res.get('error', '未知错误') if exec_res else '未知错误'
                    print(f"      📊 执行错误: {error}")
                print(f"      {'-'*100}")


def calculate_cte_variants_stats(cte_variants: List[str], 
                                unique_cte_variants: List[Dict[str, Any]],
                                failed_info: List[Dict[str, str]]) -> Dict[str, int]:
    """
    统计所有CTE变体的状态
    
    Args:
        cte_variants: 原始CTE变体列表
        unique_cte_variants: 去重后的CTE变体列表
        failed_info: 失败信息列表
        
    Returns:
        包含统计信息的字典
    """
    total_variants = len(cte_variants)
    successful_count = 0
    empty_count = 0
    
    for info in unique_cte_variants:
        exec_res = info.get('execution_result', {})
        if exec_res and exec_res.get('valid', False):
            qr = exec_res.get('query_result', [])
            try:
                qr = MCTSUtils.safe_to_dict(qr)
            except Exception:
                qr = []
            if not isinstance(qr, list):
                try:
                    qr = list(qr)
                except Exception:
                    qr = []
            if qr and len(qr) > 0:
                successful_count += info.get('count', 1)
            else:
                empty_count += info.get('count', 1)
    
    failed_count = len(failed_info)
    
    # 打印统计信息
    if total_variants > len(unique_cte_variants) or failed_count > 0 or empty_count > 0:
        print(f"\n  📊 [CTE变体统计] 共 {total_variants} 个变体:")
        if successful_count > 0:
            print(f"      ✅ 有效结果: {successful_count} 个")
        if empty_count > 0:
            print(f"      ⚠️ 空结果: {empty_count} 个")
        if failed_count > 0:
            print(f"      ❌ 执行失败: {failed_count} 个")
    
    return {
        'total': total_variants,
        'successful': successful_count,
        'empty': empty_count,
        'failed': failed_count
    }


def get_cte_priority(info: Dict[str, Any]) -> int:
    """
    获取CTE变体的优先级（用于排序）
    
    Args:
        info: CTE变体信息字典
        
    Returns:
        优先级值（越小优先级越高）
    """
    cte_text = info.get('cte', '')
    exec_res = info.get('execution_result')
    
    if cte_text == "<END>":
        return 2  # <END> 优先级为2
    
    # 判定是否有效且非空
    is_valid_nonempty = False
    if exec_res and exec_res.get('valid', False):
        qr = exec_res.get('query_result', [])
        try:
            qr = MCTSUtils.safe_to_dict(qr)
        except Exception:
            qr = []
        if not isinstance(qr, list):
            try:
                qr = list(qr)
            except Exception:
                qr = []
        is_valid_nonempty = bool(qr)
    
    if is_valid_nonempty:
        return 0  # 有效非空优先级最高（0）
    elif exec_res and exec_res.get('valid', False):
        return 3  # 有效但为空优先级最低（3）
    else:
        return 4  # 执行失败/超时（不会创建子节点，但保留在列表中用于排序）

