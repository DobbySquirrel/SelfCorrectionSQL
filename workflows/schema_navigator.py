# workflows/schema_navigator.py

import networkx as nx
from core.database_connector import DatabaseConnector
from utils.agent_helpers import AgentHelpers
from utils.prompts import Prompts # 我们会在这里添加新的提示
import re
import json # 用于处理 Agent 输出的结构化数据，例如 JSON

class SchemaNavigator:
    """
    SchemaNavigator: 智能地导航数据库 Schema 以寻找最优的 Join 路径，
    利用 LLM Agent 进行查询的语义理解和路径评估。
    """

    def __init__(self, user_proxy, schema_agent):
        """
        初始化 SchemaNavigator。
        
        参数:
            user_proxy: 用于协调与其他 Agent 对话的用户代理。
            schema_agent: 专门用于 Schema 理解和 Join 路径语义推理的 LLM Agent。
        """
        self.user_proxy = user_proxy
        self.schema_agent = schema_agent
        self.db_name = None # 将在 solve() 被调用时动态设置
        self.db_connector = None # 将使用 db_name 初始化
        self.schema_graph = None # 表和外键的 networkx 图
        self.raw_schema_info = None # 来自 database_connector 的原始 Schema 字典

    def set_db_name(self, db_name):
        """
        设置或更新导航器的数据库名称，如果数据库发生变化，则重新初始化数据库连接器并清除缓存的 Schema 信息。
        """
        if self.db_name != db_name:
            print(f"SchemaNavigator: 将数据库设置为 {db_name}")
            self.db_name = db_name
            self.db_connector = DatabaseConnector(self.db_name)
            self.schema_graph = None # 清除缓存的图
            self.raw_schema_info = None # 清除缓存的原始 Schema

    def _get_database_schema(self):
        """获取数据库 Schema 信息"""
        try:
            if self.raw_schema_info:
                return self.raw_schema_info
            
            if not self.db_connector:
                raise ValueError("DatabaseConnector 未初始化")
            
            if self.db_connector.connect():
                try:
                    schema_info = self.db_connector.get_schema_info()
                    if schema_info:
                        self.raw_schema_info = schema_info
                        return schema_info
                    else:
                        raise ValueError("无法获取 Schema 信息")
                finally:
                    self.db_connector.disconnect()  # 确保在任何情况下都断开连接
            else:
                raise ConnectionError(f"无法连接到数据库 {self.db_name}")
        except Exception as e:
            print(f"获取数据库 Schema 时出错: {e}")
            return None

    def _build_schema_graph(self, schema_info):
        """
        从原始 Schema 信息构建 NetworkX 图。
        节点是表，边代表外键关系。
        
        参数:
            schema_info (dict): 来自 DatabaseConnector 的详细 Schema 信息。
        
        返回:
            networkx.Graph: 代表表关系的图。
        """
        if self.schema_graph and self.raw_schema_info == schema_info: # 基本缓存检查
            print("DEBUG: 使用缓存的 Schema 图。")
            return self.schema_graph

        graph = nx.Graph()
        if not schema_info:
            print("WARNING: 未提供 Schema 信息来构建图。")
            return graph

        for table_name, details in schema_info.items():
            graph.add_node(table_name)
            if 'foreign_keys' in details and isinstance(details['foreign_keys'], list):
                for fk in details['foreign_keys']:
                    from_table = table_name
                    to_table = fk.get('references_table')
                    from_col = fk.get('column')
                    to_col = fk.get('references_column')
                    
                    if from_table and to_table and from_col and to_col:
                        # 在添加边之前，确保两个表都作为节点存在
                        if from_table not in graph: graph.add_node(from_table)
                        if to_table not in graph: graph.add_node(to_table)

                        graph.add_edge(from_table, to_table, 
                                       fk_from_col=from_col, 
                                       fk_to_col=to_col,
                                       relationship=f"{from_table}.{from_col} = {to_table}.{to_col}")
        self.schema_graph = graph
        print("DEBUG: Schema 图已构建。")
        return graph

    def _identify_key_elements_with_agent(self, natural_language_query, tables_schema_str, additional_context):
        """
        使用 schema_agent 识别与查询相关的关键表和列。
        
        参数:
            natural_language_query (str): 用户的提问。
            tables_schema_str (str): 完整的表 Schema 字符串。
            additional_context (str): 提供的任何额外上下文。
            
        返回:
            tuple: (关键表列表, 关键列列表, Agent 的推理过程)
        """
        print("DEBUG: 正在使用 Schema Agent 识别关键元素...")
        # 提示词的占位符，将要求 Agent 识别关键元素
        prompt = Prompts.SCHEMA_NAVIGATOR_IDENTIFY_ELEMENTS.format(
            question=natural_language_query,
            tables_schema=tables_schema_str,
            additional_context=additional_context if additional_context else "None"
        )
        
        self.user_proxy.initiate_chat(self.schema_agent, message=prompt)
        response = self.user_proxy.last_message(self.schema_agent)
        
        # 解析 Agent 的响应以获取表、列和推理过程
        # 期望的 XML 标签包括 <tables>、<columns>、<reasoning>
        key_tables_str = AgentHelpers.extract_xml_tag(response, "tables")
        key_columns_str = AgentHelpers.extract_xml_tag(response, "columns")
        reasoning = AgentHelpers.extract_xml_tag(response, "reasoning")

        key_tables = [t.strip() for t in key_tables_str.split(',') if t.strip()] if key_tables_str else []
        key_columns = [c.strip() for c in key_columns_str.split(',') if c.strip()] if key_columns_str else []
        
        print(f"DEBUG: Agent 识别的表: {key_tables}, 列: {key_columns}")
        return key_tables, key_columns, reasoning


    def _semantically_filter_paths_with_agent(self, natural_language_query, potential_join_paths):
        """
        使用 schema_agent 对 Join 路径进行语义评估并选择最佳路径。
        
        参数:
            natural_language_query (str): 用户的提问。
            potential_join_paths (list): 格式化的 Join 路径描述列表。
            
        返回:
            list: 选定的 Join 路径（字符串）列表，或空列表。
        """
        print("DEBUG: 正在使用 Schema Agent 语义过滤 Join 路径...")
        if not potential_join_paths:
            return []

        # 为 Agent 提示准备路径
        formatted_paths = "\n".join([f"{i+1}. {path}" for i, path in enumerate(potential_join_paths)])
        
        prompt = Prompts.SCHEMA_NAVIGATOR_FILTER_PATHS.format(
            question=natural_language_query,
            potential_join_paths=formatted_paths
        )
        
        self.user_proxy.initiate_chat(self.schema_agent, message=prompt)
        response = self.user_proxy.last_message(self.schema_agent)
        
        # 期望的 XML 标签如 <selected_paths>（逗号分隔的索引或完整路径）
        # 或 <selected_path_indices> 和 <reasoning>
        selected_paths_str = AgentHelpers.extract_xml_tag(response, "selected_paths")
        reasoning = AgentHelpers.extract_xml_tag(response, "reasoning")
        
        selected_paths = []
        if selected_paths_str:
            # Agent 可能返回索引（例如 "1,3"）或完整的路径字符串
            # 优先尝试解析为索引，如果需要则作为字面字符串
            selected_indices = []
            try:
                selected_indices = [int(idx.strip()) - 1 for idx in selected_paths_str.split(',') if idx.strip().isdigit()]
                for idx in selected_indices:
                    if 0 <= idx < len(potential_join_paths):
                        selected_paths.append(potential_join_paths[idx])
            except ValueError:
                # 如果不是索引，则假定它是直接提供的路径（不太理想，但更健壮）
                selected_paths = [p.strip() for p in selected_paths_str.split(';') if p.strip()] # 使用分号分割，以防有逗号在路径描述中
                # 更健壮的解决方案可能要求 Agent 以特定的 JSON 格式输出路径，
                # 或始终通过索引引用以避免解析歧义。
        
        print(f"DEBUG: Agent 选定的路径: {selected_paths}")
        print(f"DEBUG: Agent 选择的理由: {reasoning}")
        return selected_paths


    def find_best_join_paths(self, natural_language_query, tables_schema_str, additional_context):
        """
        协调 Schema 导航过程以查找和选择最优的 Join 路径。
        
        参数:
            natural_language_query (str): 用户的提问。
            tables_schema_str (str): 完整的表 Schema 字符串。
            additional_context (str): 查询的任何额外上下文。
            
        返回:
            list: 最相关的 Join 路径描述（字符串）列表。
        """
        # 1. 获取数据库 Schema 并构建图
        schema_info = self._get_database_schema()
        if not schema_info:
            return ["无法检索数据库 Schema。"]
        
        self._build_schema_graph(schema_info)
        if not self.schema_graph or not self.schema_graph.nodes():
            return ["无法从数据库构建功能性 Schema 图。"]

        # 2. Agent 识别关键表和列
        key_tables, key_columns, _ = self._identify_key_elements_with_agent(
            natural_language_query, tables_schema_str, additional_context
        )
        
        if not key_tables:
            print("WARNING: Schema Agent 无法识别关键表。无法继续 Join 路径搜索。")
            return ["Schema 导航器无法从查询中识别关键表。"]

        # 3. 查找多个潜在 Join 路径
        potential_join_paths = self._find_multiple_join_paths(key_tables, key_columns)
        
        if not potential_join_paths or \
           (len(potential_join_paths) == 1 and "没有 " in potential_join_paths[0]): # 检查是否是默认的"无路径"消息
            print("WARNING: 根据已识别的关键表，未找到潜在的 Join 路径。")
            return ["Schema 导航器未找到合适的 Join 路径。"]

        # 4. Agent 语义过滤路径
        final_selected_paths = self._semantically_filter_paths_with_agent(
            natural_language_query, potential_join_paths
        )
        
        if not final_selected_paths:
            print("WARNING: Schema Agent 无法语义过滤并选择最佳路径。返回所有潜在路径。")
            return potential_join_paths # 回退：如果 Agent 无法决定，则返回所有找到的路径
        
        return final_selected_paths
    
    def _find_multiple_join_paths(self, key_tables, key_columns):
            """
            查找已识别关键表之间的多个潜在 Join 路径。
            此方法旨在 self.schema_graph 中查找路径并描述它们。
            
            参数:
                key_tables (list): 识别为相关的表名列表。
                key_columns (list): 识别为相关的列名列表（用于覆盖）。
                
            返回:
                list: 描述潜在 Join 路径的格式化字符串列表。
            """
            print("DEBUG: 正在查找多个 Join 路径...")
            if not self.schema_graph or not self.schema_graph.nodes():
                print("WARNING: Schema 图不可用或为空。无法查找 Join 路径。")
                return ["没有可用于 Join 路径推荐的 Schema 图。"]

            # 存储 (路径描述, 评分) 的元组
            scored_paths = [] 

            # 将 key_columns 转换为集合以提高查找效率
            key_columns_set = set(key_columns)

            # 策略：查找所有关键表对之间的路径
            for i in range(len(key_tables)):
                for j in range(i + 1, len(key_tables)):
                    source_table = key_tables[i]
                    target_table = key_tables[j]

                    if source_table not in self.schema_graph or target_table not in self.schema_graph:
                        print(f"WARNING: 关键表 '{source_table}' 或 '{target_table}' 不在 Schema 图中。")
                        continue

                    try:
                        # 查找所有简单路径（无重复节点），限制路径长度
                        # cutoff 可以调整，例如 3 或 4
                        for path_nodes in nx.all_simple_paths(self.schema_graph, source=source_table, target=target_table, cutoff=4): 
                            path_description_parts = []
                            current_tables_in_path = set(path_nodes)
                            
                            # --- 路径评分逻辑开始 ---
                            path_score = self._calculate_path_score(path_nodes, key_columns_set)
                            
                            # --- 路径评分逻辑结束 ---

                            # 构建路径描述
                            for k in range(len(path_nodes) - 1):
                                node1 = path_nodes[k]
                                node2 = path_nodes[k+1]
                                edge_data = self.schema_graph.get_edge_data(node1, node2)
                                if edge_data and 'relationship' in edge_data:
                                    path_description_parts.append(edge_data['relationship'])
                                else:
                                    path_description_parts.append(f"JOIN {node1} 和 {node2} (关系未在图中完全定义)")
                            
                            if path_description_parts:
                                full_path_desc = f"表: {', '.join(path_nodes)} | Join 路径: {' -> '.join(path_description_parts)}"
                                scored_paths.append((full_path_desc, path_score))

                    except nx.NetworkXNoPath:
                        # print(f"DEBUG: 未在 {source_table} 和 {target_table} 之间找到路径。")
                        pass # 不打印太多无路径信息，保持日志简洁
                    except Exception as e:
                        print(f"ERROR: 在 {source_table} 和 {target_table} 之间查找路径时发生错误: {e}")

            if not scored_paths:
                return ["在已识别的关键表之间未找到潜在的 Join 路径。"]
            
            # 按照评分排序，分数越低越好 (成本)
            # 然后去除重复路径，只取前 N 条
            sorted_unique_paths = sorted(list(set(scored_paths)), key=lambda x: x[1]) # 按评分排序
            
            # 只取描述部分，并限制数量
            final_paths = [desc for desc, score in sorted_unique_paths[:10]] # 限制为前 10 条路径
            
            return final_paths

    def _calculate_path_score(self, path_nodes, key_columns_set):
        """计算路径得分"""
        path_score = 0.0
        
        # 1. Join 数量成本
        num_joins = len(path_nodes) - 1
        path_score += num_joins * 1.0
        
        # 2. 列覆盖率
        covered_columns = set()
        for table in path_nodes:
            table_cols = self.raw_schema_info.get(table, {}).get('columns', [])
            covered_columns.update(col['name'] for col in table_cols 
                                 if col['name'] in key_columns_set)
        
        uncovered_penalty = len(key_columns_set - covered_columns) * 5.0
        path_score += uncovered_penalty
        
        # 3. 路径直接性（额外的中间表惩罚）
        intermediate_tables = set(path_nodes[1:-1])  # 排除起点和终点
        path_score += len(intermediate_tables) * 0.5
        
        # 4. 主键/外键关系质量
        for i in range(len(path_nodes) - 1):
            table1, table2 = path_nodes[i], path_nodes[i+1]
            edge_data = self.schema_graph.get_edge_data(table1, table2)
            if edge_data and 'relationship' in edge_data:
                # 如果是直接外键关系，给予奖励（减少成本）
                path_score -= 0.3
            
        return path_score