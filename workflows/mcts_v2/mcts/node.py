# mcts/node.py
import math
from enum import Enum
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from utils.execution_trace import StepRecord

class ActionType(Enum):
    """
    定义 MCTS 中的动作类型，对应不同的思维层级。
    """
    STRATEGIZE = "strategize"  # Layer 1: 根节点策略选择 (S1/S2/S3/S4)
    PROBE = "probing"          # Layer 2: 探索信息 (不修改 accumulated_sql)
    BUILD = "building"         # Layer 2: 构建 CTE (修改 accumulated_sql)
    REFINE = "refining"        # Layer 2: 修正逻辑 (替换/修改 accumulated_sql)
    FINISH = "finishing"       # Layer 3: 封顶提交 (终止节点)

@dataclass
class KnowledgeState:
    """
    随着路径流动的“认知快照”。
    解决了 LLM 容易遗忘前序步骤查到的具体值的问题。
    """
    # 已验证的实体映射，例如: {"Hero": "Hero (2002)", "HK": "Hong Kong"}
    verified_values: Dict[str, str] = field(default_factory=dict)
    
    # 已确认的 Schema 路径，例如: ["schools.id <-> frpm.cds_code"]
    confirmed_joins: List[str] = field(default_factory=list)
    
    # 备注/思考片段
    notes: List[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """将知识转换为 Prompt 文本"""
        if not self.verified_values and not self.confirmed_joins:
            return "No verified external knowledge yet."
        
        txt = "Verified Knowledge (Facts):\n"
        for k, v in self.verified_values.items():
            txt += f"- The term '{k}' refers to value '{v}' in the database.\n"
        for join in self.confirmed_joins:
            txt += f"- Confirmed Join Path: {join}\n"
        return txt
    
    def clone(self) -> 'KnowledgeState':
        """深拷贝知识状态（避免 MCTS 分支污染）"""
        return KnowledgeState(
            verified_values=self.verified_values.copy(),
            confirmed_joins=self.confirmed_joins.copy(),
            notes=self.notes.copy()
        )

class MCTSNode:
    def __init__(self, parent: Optional['MCTSNode'] = None, action_type: ActionType = ActionType.STRATEGIZE):
        # --- 树结构关系 ---
        self.parent = parent
        self.children: List['MCTSNode'] = []
        self.depth: int = 0 if parent is None else parent.depth + 1
        
        # --- MCTS 统计值 (用于 UCT) ---
        self.visits: int = 0        # N (访问次数)
        self.value_sum: float = 0.0 # Q (总价值)
        
        # --- 节点核心状态 (Context) ---
        self.action_type = action_type
        # 策略：根节点（parent=None）时为空，让模型选择；子节点继承父节点策略
        self.strategy: Optional[str] = None if parent is None else (parent.strategy if parent else None)
        
        # SQL 状态 (CoCTE 核心)
        # accumulated_sql: 截止到当前节点，完整的、可执行的 SQL 序列
        self.accumulated_sql: str = "" 
        
        # 知识状态
        self.knowledge: KnowledgeState = KnowledgeState()
        
        # --- 执行反馈 (Observation) ---
        # 存储这一步动作执行后的结果摘要
        self.observation: Dict[str, Any] = {
            'status': 'init',      # success, error, empty
            'row_count': -1,
            'result_hash': None,   # 用于 Self-Consistency
            'error_message': None,
            'sc_score': 0.0        # 这一步得到的自洽性评分 (Instant Reward)
        }
        
        # --- Execution Trace ---
        # 存储从根节点到当前节点的所有步骤记录（用于 prompt）
        self.execution_trace: List[StepRecord] = []
        if parent:
            # 继承父节点的 trace
            self.execution_trace = parent.execution_trace.copy()
        
        self.is_terminal: bool = False

    @property
    def q_value(self) -> float:
        """平均价值 Q/N"""
        return self.value_sum / self.visits if self.visits > 0 else 0.0

    def uct_score(self, exploration_weight: float = 1.414) -> float:
        """
        计算 UCT 分数。
        Selection 阶段使用此分数决定走哪条路。
        """
        if self.visits == 0:
            return float('inf')  # 鼓励探索未访问节点
        
        exploitation = self.q_value
        # 加上一个小量防止 log(0)
        parent_visits = self.parent.visits if self.parent else 1
        exploration = exploration_weight * math.sqrt(math.log(parent_visits) / self.visits)
        
        return exploitation + exploration

    def add_child(self, child_node: 'MCTSNode'):
        self.children.append(child_node)

    def update_knowledge(self, new_facts: Dict):
        """用于 Probe 节点更新知识"""
        if 'verified_values' in new_facts:
            self.knowledge.verified_values.update(new_facts['verified_values'])
        if 'confirmed_joins' in new_facts:
            self.knowledge.confirmed_joins.extend(new_facts['confirmed_joins'])

    def __repr__(self):
        return f"<Node D={self.depth} Type={self.action_type.value} Visits={self.visits} Q={self.q_value:.2f}>"