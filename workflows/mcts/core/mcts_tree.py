"""
MCTS搜索树

管理整个MCTS搜索过程，包括：
- 树结构管理
- 节点选择策略
- 回溯更新
- 最优解选择
"""

from typing import List, Optional, Dict, Any
import threading
from .mcts_node import MCTSNode


class MCTSTree:
    """MCTS搜索树（支持并行访问）"""
    
    def __init__(self):
        """初始化MCTS搜索树"""
        self.root: Optional[MCTSNode] = None
        self.max_depth = 10
        # self.termination_threshold = 0.95  # 已废弃：不再使用reward提前终止
        # 线程锁：保护共享树结构（主要用于 backpropagation 和节点扩展）
        self.lock = threading.Lock()
    
    def set_root(self, root_node: MCTSNode):
        """设置根节点"""
        self.root = root_node
    
    def select_node(self, exploration_constant: float = 1.414) -> MCTSNode:
        """
        选择节点（UCB1算法）
        
        Args:
            exploration_constant: 探索常数
            
        Returns:
            选择的节点
        """
        if not self.root:
            raise ValueError("Root node not set")

        # print(f"[节点选择 - Selection Phase]")  # 关闭节点选择打印

        
        current = self.root
        path = []
        
        # 从根节点开始，使用UCB1选择路径
        while not current.is_terminal and current.depth < self.max_depth:
            if not current.is_fully_expanded():
                # 未完全展开，返回该节点
                print(f"[UCB选择] 选中未完全展开节点 #{getattr(current, 'node_id', -1)} (深度={current.depth}, 访问={current.visit_count}, c={exploration_constant})")
                return current
            else:
                # 过滤掉执行失败的子节点
                valid_children = []
                for child in current.children:
                    # 检查节点是否执行失败
                    if hasattr(child, 'execution_results') and child.execution_results:
                        cte_result = child.execution_results.get('cte_result', {})
                        if not cte_result.get('valid', True):  # 如果执行失败
                            continue
                    valid_children.append(child)
                
                # 如果没有有效的子节点，返回当前节点
                if not valid_children:
                    # print(f"⚠️ 所有子节点都执行失败，返回当前节点")  # 关闭打印
                    return current
                
                # print(f"📊 当前节点 (深度={current.depth}, 已完全展开, {len(valid_children)}个有效子节点)")  # 关闭打印
                
                # 打印所有有效子节点的UCB1值
                child_ucbs = []
                for child in valid_children:
                    u = child.get_ucb1_value(exploration_constant)
                    child_ucbs.append((getattr(child, 'node_id', -1), u, child.visit_count, child.average_reward))
                child_ucbs.sort(key=lambda x: x[1], reverse=True)
                print(f"[UCB候选] (c={exploration_constant})")
                for nid, u, v, avg in child_ucbs:
                    print(f"  节点#{nid}: UCB={u:.4f}, 访问={v}, 平均奖励={avg:.4f}")
                
                # 选择最大UCB子节点，并打印并列最优
                ucbs = [(child, child.get_ucb1_value(exploration_constant)) for child in valid_children]
                max_ucb = max(u for _, u in ucbs)
                eps = 1e-9
                tie_nodes = [child for child, u in ucbs if abs(u - max_ucb) <= eps]
                best_child = tie_nodes[0]
                ids = [f"#{getattr(n, 'node_id', -1)}" for n in tie_nodes]
                if len(tie_nodes) > 1:
                    print(f"[UCB并列最优] {', '.join(ids)} (UCB={max_ucb:.4f}, c={exploration_constant})")
                print(f"[UCB选择] 选择子节点 #{getattr(best_child, 'node_id', -1)} (UCB={max_ucb:.4f})")
                current = best_child
                path.append(current)
        
        # print(f"✅ 最终选中节点 (深度={current.depth})")  # 关闭打印

        return current
    
    def expand_node(self, node: MCTSNode, new_actions: List[str]):
        """
        展开节点
        
        Args:
            node: 要展开的节点
            new_actions: 新的动作列表
        """
        for action in new_actions:
            child = MCTSNode(
                question=node.question,
                schema_info=node.schema_info,
                additional_context=node.additional_context,
                parent=node
            )
            child.cte = action  # 将动作作为CTE
            node.add_child(child)
        
        node.is_expanded = True
        node.untried_actions = []
    
    def backpropagate(self, node: MCTSNode, reward: float, exploration_constant: float = 1.414):
        """
        回溯更新（线程安全）
        
        Args:
            node: 要更新的节点
            reward: 奖励值
            exploration_constant: 探索常数（用于打印）
        """
        # 使用锁保护回溯更新过程
        with self.lock:
            # print(f"[回溯更新 - Backpropagation Phase]")
            # print(f"   初始奖励: {reward:.4f}")

            
            current = node
            depth = current.depth
            step = 0

            # 打印从叶到根的回溯路径（节点编号与深度），便于理解顺序
            path_preview = []
            _tmp = current
            while _tmp is not None:
                path_preview.append(f"#{getattr(_tmp, 'node_id', -1)}(d{_tmp.depth})")
                _tmp = _tmp.parent
            print("[回溯路径] " + " -> ".join(path_preview))
            
            while current is not None:
                old_visits = current.visit_count
                old_avg_reward = current.average_reward
                
                current.update_reward(reward)
                
                # 可视化当前节点及其UCB（相对父节点）
                # 在回溯过程中，当前节点的父节点尚未更新访问次数。
                # 为符合直觉，日志里的父访问使用 (parent.visit_count + 1) 的"即将更新"值。
                if current.parent is not None:
                    parent_visits_effective = max(1, current.parent.visit_count + 1)
                    # 手动按公式计算UCB用于打印（避免get_ucb1内使用未加1的父访问）
                    try:
                        import math
                        exploitation = current.average_reward
                        exploration = exploration_constant * math.sqrt(
                            math.log(parent_visits_effective) / max(1, current.visit_count)
                        )
                        ucb_dbg = exploitation + exploration
                    except Exception:
                        ucb_dbg = current.get_ucb1_value(exploration_constant)
                        parent_visits_effective = max(1, getattr(current.parent, 'visit_count', 0))
                    print(
                        f"  节点#{getattr(current, 'node_id', -1)} UCB={ucb_dbg:.4f} "
                        f"(c={exploration_constant}, 父访问={parent_visits_effective})"
                    )
                
                print(
                    f"  [{step}] 节点#{getattr(current, 'node_id', -1)} 深度={current.depth}: "
                    f"访问 {old_visits}→{current.visit_count}, 奖励 {old_avg_reward:.4f}→{current.average_reward:.4f}"
                )
                
                current = current.parent
                step += 1

    
    def get_final_statistics(self) -> Dict[str, Any]:
        """获取最终统计信息"""
        if not self.root:
            return {}
        
        return {
            'total_visits': self.root.visit_count,
            'average_reward': self.root.average_reward,
            'tree_depth': self._get_tree_depth(),
            'total_nodes': self._count_nodes()
        }
    
    def get_tree_info(self) -> Dict[str, Any]:
        """获取树结构信息"""
        if not self.root:
            return {}
        
        return {
            'root_info': self.root.to_dict(),
            'tree_depth': self._get_tree_depth(),
            'total_nodes': self._count_nodes(),
            'expanded_nodes': self._count_expanded_nodes()
        }
    
    def _get_tree_depth(self) -> int:
        """获取树的最大深度"""
        if not self.root:
            return 0
        
        return self._get_max_depth_recursive(self.root)
    
    def _get_max_depth_recursive(self, node: MCTSNode) -> int:
        """递归获取最大深度"""
        if not node.children:
            return node.depth
        
        return max(self._get_max_depth_recursive(child) for child in node.children)
    
    def _count_nodes(self) -> int:
        """计算总节点数"""
        if not self.root:
            return 0
        
        return self._count_nodes_recursive(self.root)
    
    def _count_nodes_recursive(self, node: MCTSNode) -> int:
        """递归计算节点数"""
        count = 1
        for child in node.children:
            count += self._count_nodes_recursive(child)
        return count
    
    def _count_expanded_nodes(self) -> int:
        """计算已展开的节点数"""
        if not self.root:
            return 0
        
        return self._count_expanded_recursive(self.root)
    
    def _count_expanded_recursive(self, node: MCTSNode) -> int:
        """递归计算已展开节点数"""
        count = 1 if node.is_expanded else 0
        for child in node.children:
            count += self._count_expanded_recursive(child)
        return count
