class Node:
    """推理树的节点类，包含问题、执行结果、证据和动作"""
    
    def __init__(self, question, parent=None):
        self.question = question  # 节点的自然语言问题
        self.cell_value = None    # actions执行的结果
        self.evidences = []       
        self.actions = None       # Python代码
        self.parent = parent      # 父节点
        self.children = []        # 子节点列表
        self.is_solved = False    # 节点是否已解决
    
    def add_child(self, child_node):
        """添加子节点"""
        self.children.append(child_node)
        return child_node
    
    def update_cell_value(self, value):
        """更新cell value"""
        self.cell_value = value
        
    def update_evidences(self, evidences):
        """更新evidences"""
        self.evidences = evidences
        
    def update_actions(self, actions):
        """更新actions"""
        self.actions = actions
        
    def mark_as_solved(self):
        """标记节点为已解决"""
        self.is_solved = True
