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
        
        # MASTER 框架：即时评分和信心
        self.immediate_score: Optional[float] = None  # r0: 初始评分，范围 0-1（None 表示未评估）
        self.confidence: Optional[float] = None  # c0: 信心分数，范围 0-1（None 表示未评估）
        # MASTER 框架：backup 奖励统计（来自 Simulation 阶段的奖励）
        self.backup_reward_sum = 0.0  # 累积的 backup 奖励总和
        self.backup_visits = 0  # backup 奖励的访问次数
        
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
            use_average_reward: 是否使用average_reward而不是q_value（None时自动判断：如果没有LLM打分则用average_reward）
            
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
            # 未访问节点：尽量使用 LLM 打的分（如果启用且不使用average_reward）
            if not use_average_reward and self.immediate_score is not None and self.confidence is not None:
                base_Q = self.confidence * self.immediate_score
            else:
                base_Q = 0.0
            # 返回 +∞ 确保每个孩子至少被选一次
            return float('inf')
        
        # 已访问节点：根据配置选择使用q_value或average_reward
        if use_average_reward:
            exploitation = self.average_reward  # 使用average_reward（基于SQL执行结果回传）
        else:
            exploitation = self.q_value  # 使用q_value（MASTER框架，包含LLM打分）
        
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
        更新奖励值（MASTER 框架：只更新 backup 统计和 visit_count）
        Q 值通过 property 动态计算
        """
        self.visit_count += 1
        self.backup_reward_sum += reward
        self.backup_visits += 1
        # 保持向后兼容：同时更新 total_reward 和 average_reward
        self.total_reward += reward
        self.average_reward = self.total_reward / self.visit_count
    
    @property
    def q_backup(self) -> float:
        """
        MASTER 框架：计算 Q_backup（来自 Simulation 阶段的平均奖励）
        
        Returns:
            Q_backup 值
        """
        if self.backup_visits == 0:
            return 0.0
        return self.backup_reward_sum / self.backup_visits
    
    @property
    def q_value(self) -> float:
        """
        MASTER 框架的 Q 值计算：Q = c0 * r0 + (1 - c0) * Q_backup
        
        优化：根据深度动态调整confidence权重
        - 深度 >= 5 时，CTE评分更可靠，提高confidence权重
        - 深度 < 5 时，CTE评分不太准确，降低confidence权重，更多依赖Q_backup
        
        Returns:
            Q 值
        """
        # 如果没有即时评分和信心，直接使用 Q_backup
        if self.immediate_score is None or self.confidence is None:
            return self.q_backup
        
        # 确保 confidence 在 [0, 1] 范围内
        base_c0 = max(0.0, min(1.0, self.confidence))
        r0 = self.immediate_score
        
        # 根据深度动态调整confidence权重（基于相关性分析：后期CTE评分更可靠）
        # 深度 >= 5: 提高权重（后期CTE，评分更可靠）
        # 深度 < 5: 降低权重（早期CTE，评分不太准确）
        if self.depth >= 5:
            # 后期CTE：提高confidence权重（最多提高到1.0）
            adjusted_c0 = min(1.0, base_c0 * 1.2)  # 提高20%
        elif self.depth >= 3:
            # 中期CTE：保持原权重
            adjusted_c0 = base_c0
        else:
            # 早期CTE：降低confidence权重，更多依赖Q_backup
            adjusted_c0 = base_c0 * 0.6  # 降低40%
        
        # MASTER 公式: Q = c0 * r0 + (1 - c0) * Q_backup
        return adjusted_c0 * r0 + (1 - adjusted_c0) * self.q_backup
    
    def should_use_average_reward_for_ucb(self) -> bool:
        """
        判断UCB计算时是否应该使用average_reward而不是q_value
        如果节点没有LLM打分（immediate_score为None），则使用average_reward
        """
        return self.immediate_score is None or self.confidence is None
    
    def get_best_child(self) -> Optional['MCTSNode']:
        """获取最佳子节点（MASTER 框架：使用 q_value）"""
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
            'q_value': self.q_value,  # MASTER 框架
            'q_backup': self.q_backup,  # MASTER 框架
            'immediate_score': self.immediate_score,  # MASTER 框架 (r0)
            'confidence': self.confidence,  # MASTER 框架 (c0)
            'backup_reward_sum': self.backup_reward_sum,  # MASTER 框架
            'backup_visits': self.backup_visits,  # MASTER 框架
            'depth': self.depth,
            'is_expanded': self.is_expanded,
            'is_terminal': self.is_terminal,
            'cte': self.cte,
            'full_sql': self.full_sql,
            'children_count': len(self.children)
        }
