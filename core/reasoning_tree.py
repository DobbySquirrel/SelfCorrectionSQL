from .node import Node

class ReasoningTree:
    """推理树类，管理整个推理过程"""
    
    def __init__(self, root_question):
        """初始化推理树"""
        self.root = Node(root_question)
        self.current_node = self.root
        self.max_depth = 2  # 默认最大深度为2层
        
    def set_max_depth(self, depth):
        """设置最大深度"""
        self.max_depth = depth
        
    def get_current_depth(self, node=None):
        """获取当前节点的深度"""
        if node is None:
            node = self.current_node
            
        depth = 0
        current = node
        while current.parent is not None:
            depth += 1
            current = current.parent
        return depth
    
    def add_child_node(self, question):
        """添加子节点"""
        child = Node(question, parent=self.current_node)
        self.current_node.add_child(child)
        return child
    
    def move_to_child(self, index):
        """移动到指定的子节点"""
        if 0 <= index < len(self.current_node.children):
            self.current_node = self.current_node.children[index]
            return True
        return False
    
    def move_to_parent(self):
        """移动到父节点"""
        if self.current_node.parent is not None:
            self.current_node = self.current_node.parent
            return True
        return False
    
    def is_root_solved(self):
        """检查根节点是否已解决"""
        return self.root.is_solved
