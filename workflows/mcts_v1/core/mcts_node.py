"""
MCTS节点类

表示MCTS搜索树中的一个节点，包含：
- 问题状态
- 统计信息（访问次数、奖励值等）
- 子节点管理
- CTE和SQL信息
"""

from typing import List, Optional, Dict, Any
import math


class MCTSNode:
    """MCTS搜索树节点"""
    _global_node_counter = 0
    
    def __init__(self, question: str, schema_info: str, additional_context: str = "", 
                 parent: Optional['MCTSNode'] = None):
        """
        初始化MCTS节点
        
        Args:
            question: 自然语言问题
            schema_info: 数据库模式信息
            additional_context: 额外上下文
            parent: 父节点
        """
        # 分配唯一节点编号
        self.node_id = MCTSNode._global_node_counter
        MCTSNode._global_node_counter += 1

        self.question = question
        self.schema_info = schema_info
        self.additional_context = additional_context
        self.parent = parent
        
        # 统计信息
        self.visit_count = 0
        self.total_reward = 0.0
        self.average_reward = 0.0
        
        # 子节点
        self.children: List['MCTSNode'] = []
        self.untried_actions: List[str] = []  # 未尝试的动作（CTE变体）
        
        # SQL相关信息
        self.cte = ""
        self.full_sql = ""
        self.execution_results: Dict[str, Any] = {}
        
        # 节点状态
        self.is_expanded = False
        self.is_expanding = False  # 用于并行场景：标记节点正在扩展中（防止重复扩展）
        self.is_terminal = False
        self.depth = 0 if parent is None else parent.depth + 1
        
        # 跟踪连续空结果次数（用于模糊匹配剪枝）
        self.consecutive_empty_count = 0  # 连续空结果次数（模糊匹配后）
    
    def add_child(self, child: 'MCTSNode'):
        """添加子节点"""
        self.children.append(child)
        child.parent = self
        child.depth = self.depth + 1
    
    def is_fully_expanded(self) -> bool:
        """检查节点是否完全展开"""
        return len(self.untried_actions) == 0 and len(self.children) > 0
    
    def is_leaf(self) -> bool:
        """检查是否为叶子节点"""
        return len(self.children) == 0
    
    def get_ucb1_value(self, exploration_constant: float = 1.414, use_average_reward: bool = None) -> float:
        """
        计算UCB1值
        
        Args:
            exploration_constant: 探索常数，默认为sqrt(2)
            use_average_reward: 是否使用average_reward而不是q_value（现在总是使用average_reward，此参数保留用于兼容性）
            
        Returns:
            UCB1值
            
        Note:
            UCB1的exploration项 c * sqrt(log(N) / n) 本身就会鼓励探索访问较少的节点：
            - visit_count越小，exploration项越大
            - 未访问节点（visit_count=0）返回+∞，确保优先选择
            增加exploration_constant可以增强探索，通常不需要额外的bonus机制。
        """
        # 如果未指定，自动判断：如果没有LLM打分，则使用average_reward
        if use_average_reward is None:
            use_average_reward = self.should_use_average_reward_for_ucb()
        
        if self.visit_count == 0:
            # 未访问节点：返回 +∞ 确保每个孩子至少被选一次
            return float('inf')
        
        # 已访问节点：使用average_reward（基于SQL执行结果回传）
        exploitation = self.average_reward
        
        if self.parent:
            parent_visits = max(1, self.parent.visit_count)  # 避免log(0)
            exploration = exploration_constant * math.sqrt(
                math.log(parent_visits) / self.visit_count
            )
        else:
            exploration = 0
        
        return exploitation + exploration
    
    def update_reward(self, reward: float):
        """
        更新奖励值
        
        Args:
            reward: 奖励值
        """
        self.visit_count += 1
        self.total_reward += reward
        self.average_reward = self.total_reward / self.visit_count
    
    @property
    def q_value(self) -> float:
        """
        Q 值计算：直接使用 average_reward（基于SQL执行结果回传的平均奖励）
        
        Returns:
            Q 值（等于 average_reward）
        """
        return self.average_reward
    
    def should_use_average_reward_for_ucb(self) -> bool:
        """
        判断UCB计算时是否应该使用average_reward而不是q_value
        现在总是返回True，因为q_value已经等于q_backup（即average_reward）
        """
        return True
    
    def get_best_child(self) -> Optional['MCTSNode']:
        """获取最佳子节点（使用 q_value，即 average_reward）"""
        if not self.children:
            return None
        
        return max(self.children, key=lambda child: child.q_value)
    
    def get_most_visited_child(self) -> Optional['MCTSNode']:
        """获取访问次数最多的子节点"""
        if not self.children:
            return None
        
        return max(self.children, key=lambda child: child.visit_count)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'question': self.question,
            'visit_count': self.visit_count,
            'total_reward': self.total_reward,
            'average_reward': self.average_reward,
            'q_value': self.q_value,  # Q值（等于average_reward）
            'depth': self.depth,
            'is_expanded': self.is_expanded,
            'is_terminal': self.is_terminal,
            'cte': self.cte,
            'full_sql': self.full_sql,
            'children_count': len(self.children)
        }
