"""
完整SQL生成器智能体

基于现有CTE生成完整的SQL查询，实现从CTE到最终SQL的补全过程
"""

import autogen
from typing import Dict, List, Any
import Levenshtein
import concurrent.futures
import threading
from openai import OpenAI


class CompleteSQLGenerator:
    """完整SQL生成器智能体"""
    
    def __init__(self, llm_config: Dict, multi_model_configs: List[Dict] = None, cte_probe_limit: int = 15):
        """
        初始化完整SQL生成器
        
        Args:
            llm_config: LLM配置
            multi_model_configs: 多个模型配置列表（用于多模型并行加速）
            cte_probe_limit: CTE探针查询的LIMIT值（默认15）
        """
        self.llm_config = llm_config
        self.multi_model_configs = multi_model_configs or []
        self.cte_probe_limit = cte_probe_limit
        # 线程锁：保护 agent 创建过程（避免并行环境下的 Pydantic 冲突）
        self._agent_lock = threading.Lock()
        # 用于轮询选择模型的计数器（线程安全）
        self._model_counter_lock = threading.Lock()
        self._model_counter = 0
        self.setup_agent()
    
    def setup_agent(self, temperature: float = 0.2):
        """
        设置完整SQL生成智能体（线程安全）
        
        Args:
            temperature: 温度参数
        """
        # 使用锁保护 agent 创建，避免并行环境下的冲突
        with self._agent_lock:
            # 创建带有指定temperature的llm_config
            llm_config_with_temp = self.llm_config.copy()
            if 'config_list' in llm_config_with_temp:
                for config in llm_config_with_temp['config_list']:
                    config['temperature'] = temperature
            
            self.sql_agent = autogen.AssistantAgent(
                name="CompleteSQLGenerator",
                llm_config=llm_config_with_temp,
                system_message=self._get_sql_system_message()
            )
            
            self.user_proxy = autogen.UserProxyAgent(
                name="SQLUserProxy",
                human_input_mode="NEVER",
                max_consecutive_auto_reply=0,
                code_execution_config=False
            )
    
    def _get_sql_system_message(self) -> str:
        """获取完整SQL生成器的系统消息"""
        return """You are a professional SQL query generator.

**Task**: Based on the provided natural language question, database schema, and existing CTE, generate a complete SQL query.

**Input Information**:
- Natural language question
- Database schema
- Existing CTE (if any)
- Additional context

**Output Format**:
```sql
```

**Output Requirements**:
1. Generate a complete SQL query with necessary clauses like SELECT, FROM, WHERE, ORDER BY, etc.
2. If CTE is provided, incorporate it as a subquery or part of WITH clause
3. Ensure SQL syntax is correct and executable
4. **CRITICAL: Only return the EXACT columns asked in the question. Do NOT add extra columns. The CTEs may contain intermediate data - IGNORE them in the final SELECT unless explicitly asked.**

**Database Admin Instructions (Must Strictly Adhere):**
1. **SELECT Clause & Output Schema (CRITICAL)**:
   - **NO Over-Selection**: Strictly SELECT ONLY the columns explicitly requested in the question. Do NOT include auxiliary columns, computed flags, or extra information not asked for. If the question asks for ONE thing, return ONLY that ONE column.
   - **NO Under-Selection**: If the question implies multiple intents, you MUST select ALL requested columns.
   - **Column Order**: Arrange columns in the SELECT clause in the same order as they appear in the question.
2.  **Aggregation (MAX/MIN):** Only use `MAX()` or `MIN()` when the question explicitly asks for the "highest", "maximum", "lowest", "minimum", "best", or "worst" value. If the question does not explicitly request an aggregated value, return all matching values without using aggregation functions. Always perform JOINs before using `MAX()` or `MIN()`.
3.  **ORDER BY with Distinct Values:** Use `GROUP BY <column>` before `ORDER BY <column> ASC|DESC` to ensure distinct values.
4.  **Handling NULLs:** If a column may contain NULL values (indicated by "None" in value examples or explicitly stated), use `JOIN` or `WHERE <column> IS NOT NULL`.
5.  **FROM/JOIN Clauses:** Only include tables essential to answer the question.
6.  **Strictly Follow Hints:** Adhere to all provided hints.
7.  **Thorough Question Analysis:** Address all conditions mentioned in the question.
8.  **DISTINCT Keyword:** Use `SELECT DISTINCT` when the question requires unique values or when selecting columns that may have duplicates (e.g., IDs, URLs, names, nationalities, categories). If the question asks for a list of unique items or the result may contain duplicate values, always use `SELECT DISTINCT`. Refer to column statistics ("Value Statics") to determine if `DISTINCT` is necessary. When in doubt, use `DISTINCT` to ensure unique results.
9.  **COUNT with DISTINCT:** When using `COUNT()` after JOINs, especially with N:1 or M:N relationships, you MUST use `COUNT(DISTINCT column)` instead of `COUNT(*)` to avoid counting duplicate rows. If the relationship is N:1 (Many-to-One), the child table may have multiple rows per parent, so always use `COUNT(DISTINCT column)` when counting entities from the parent table. **Even if the preceding CTE uses `COUNT(*)`, you must check if the data involves JOINs with N:1 relationships and use `COUNT(DISTINCT column)` if counting entities.**
10. **Column Selection:** When similar columns exist across tables, carefully analyze column descriptions and hints to choose the correct column.
11. **String Concatenation:** Never use `|| ' ' ||` or any other method to concatenate strings in the `SELECT` clause.
12. **JOIN Preference:** Prioritize `INNER JOIN` over nested `SELECT` statements. Do not use `CROSS JOIN` or `LEFT JOIN` or `RIGHT JOIN`.
13. **Schema Syntax:** Use `table_name`.`column_name` to refer to columns from different tables when table name or column name contains whitespace or special characters.
14. **Multiple Columns from Different Tables:** When you need to select different columns from different tables, use `JOIN` to combine them horizontally (side by side), NOT `UNION ALL` to stack them vertically. 
    - CORRECT: `SELECT T1.col1, T2.col2 FROM table1 AS T1 INNER JOIN table2 AS T2 ON T1.id = T2.id`
    - WRONG: `SELECT col1 FROM table1 UNION ALL SELECT col2 FROM table2` (This stacks rows vertically, not pairing columns horizontally)
    - **Rule**: If the question asks for multiple columns from different tables, they should appear in the same row (use JOIN), not in separate rows (avoid UNION ALL).
15. **SQLite Functions Only:** Use only functions available in SQLite.
16. **Date Processing:** Utilize `STRFTIME()` for date manipulation (e.g., `STRFTIME('%Y', SOMETIME)` to extract the year)."""

    def _find_relevant_values(self, values: List[Any], question: str, top_n: int = 3) -> List[Any]:
        """
        找到与自然语言问题最相关的数据值
        
        Args:
            values: 数据值列表
            question: 自然语言问题
            top_n: 返回的top值数量
            
        Returns:
            最相关的top_n个值
        """
        if not values or not question:
            return values[:top_n]
        
        # 去重
        unique_values = list(set(values))
        
        # 限制计算数量，避免太慢
        if len(unique_values) > 100:
            import random
            unique_values = random.sample(unique_values, 100)
        
        # 计算每个值与问题的相似度
        similarities = []
        question_lower = question.lower()
        
        for value in unique_values:
            # 转换为字符串
            value_str = str(value).lower()
            
            # 计算相似度分数（多个维度）
            score = 0.0
            
            # 1. 子串匹配（最重要）
            if value_str in question_lower or question_lower in value_str:
                score += 100.0
            
            # 2. 单词级别匹配
            value_words = set(value_str.split())
            question_words = set(question_lower.split())
            common_words = value_words & question_words
            if common_words:
                score += len(common_words) * 50.0
            
            # 3. Levenshtein距离（相似度）
            # 对较短的字符串计算编辑距离
            if len(value_str) < 50 and len(question_lower) < 100:
                # 计算最小编辑距离（与问题中的每个单词）
                min_distance = min([Levenshtein.distance(value_str, word) for word in question_words] + [999])
                # 距离越小，分数越高
                if min_distance < 10:
                    score += (10 - min_distance) * 2.0
            
            similarities.append((value, score))
        
        # 按分数降序排序
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        # 返回top_n个值
        return [value for value, score in similarities[:top_n]]
    
    def _get_preceding_cte_info(self, node) -> str:
        """
        通过node.parent向上追溯，获取所有前序CTE及其执行结果
        
        Args:
            node: 当前MCTS节点
            
        Returns:
            格式化的前序CTE信息字符串
        """
        # 收集从根节点到当前节点的所有节点（包括当前节点）
        cte_path = []
        current = node
        
        while current is not None:
            if current.cte and current.cte != "" and current.cte != "<END>":
                cte_info = {
                    'cte': current.cte,
                    'execution_result': current.execution_results.get('cte_result', {})
                }
                cte_path.insert(0, cte_info)  # 插入到开头，保持从根到叶的顺序
            current = current.parent
        
        # 如果没有CTE，返回提示信息
        if not cte_path:
            return "None"
        
        # 获取问题文本（用于相似度计算）
        question = node.question
        
        # 格式化前序CTE信息
        formatted_info = []
        for idx, cte_info in enumerate(cte_path, 1):
            formatted_info.append(f"### Step {idx}:")
            formatted_info.append(f"```sql\n{cte_info['cte']}\n```")
            
            # 添加执行结果信息
            exec_result = cte_info['execution_result']
            if exec_result.get('valid', False):
                query_result = exec_result.get('query_result', [])
                # 确保query_result是列表格式（可能需要转换）
                try:
                    query_result = MCTSUtils.safe_to_dict(query_result)
                except Exception:
                    pass
                if not isinstance(query_result, list):
                    try:
                        query_result = list(query_result)
                    except Exception:
                        query_result = []
                if query_result:
                    # 限制只展示前20行数据
                    total_rows = len(query_result)
                    query_result_limited = query_result[:20]
                    formatted_info.append(f"**Execution Result**: Successfully returned {total_rows} rows")
                    # 显示列信息
                    if len(query_result_limited) > 0:
                        columns = list(query_result_limited[0].keys())
                        # 显示与问题相关的示例数据值（每列Top3最相关的值）
                        formatted_info.append("**Relevant Sample Values**:")
                        for col in columns:
                            # 收集该列的所有值（仅从前20行）
                            col_values = [row[col] for row in query_result_limited if row.get(col) is not None]
                            if col_values:
                                # 判断列的数据类型：如果是数字类型，直接排序；如果是字符串，使用相似度
                                is_numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in col_values)
                                
                                if is_numeric:
                                    # 数字类型：按顺序排序，取前3个
                                    unique_values = list(set(col_values))
                                    sorted_values = sorted(unique_values)[:3]
                                    relevant_values = sorted_values
                                else:
                                    # 字符串类型：使用相似度计算选择最相关的值
                                    relevant_values = self._find_relevant_values(col_values, question, top_n=3)
                                
                                # 如果是字符串类型，用单引号括起来
                                formatted_values = [f"'{v}'" if isinstance(v, str) else str(v) for v in relevant_values]
                                values_str = ", ".join(formatted_values)
                                formatted_info.append(f"  '{col}': {values_str}")
                else:
                    formatted_info.append("**Execution Result**: Successfully executed, returned empty result set")
            else:
                error = exec_result.get('error', 'Unknown error')
                formatted_info.append(f"**Execution Result**: Failed - {error}")
            formatted_info.append("")  # 空行分隔
        
        return "\n".join(formatted_info)
    
    def generate_multiple_complete_sqls_parallel(self, node, num_variants: int = 3, max_workers: int = 3) -> List[str]:
        """
        使用OpenAI的n参数一次性生成多个完整SQL变体（不需要并行调用）
        
        Args:
            node: MCTS节点
            num_variants: 变体数量
            max_workers: 最大并行工作线程数（已废弃，保留以兼容接口）
            
        Returns:
            完整SQL变体列表
        """
        # 使用多个temperature值增加多样性
        # 将变体分成多个temperature组：[0.3, 0.6, 0.9]
        temperature_groups = [0.3, 0.6, 0.9]
        num_groups = len(temperature_groups)
        
        # 计算每组应该生成多少个变体
        variants_per_group = num_variants // num_groups
        remainder = num_variants % num_groups
        
        # 通过node.parent向上追溯获取前序CTE信息（包括当前节点的CTE）
        preceding_cte_info = self._get_preceding_cte_info(node)
        
        # 构建用户输入
        user_input = f"""
**Natural language question**: {node.question}
**Database schema**: {node.schema_info}
**Additional context**: {node.additional_context}

**Existing CTE and Results (Quick verification with LIMIT {self.cte_probe_limit})**: 
{preceding_cte_info}/no_think
"""
        # 从llm_config中提取OpenAI配置
        config = self.llm_config.get('config_list', [{}])[0]
        model = config.get('model')
        base_url = config.get('base_url')
        api_key = config.get('api_key')
        
        # 构建系统消息
        system_message = self._get_sql_system_message()
        
        # 打印SQL生成的prompt（用于调试）- 包含system message和user input
        print(f"[SQL生成] User Input:")
        print(f"  {'='*80}")
        # print(f"[System Message]:")
        # print(system_message)
        print(f"\n[User Input]:")
        print(user_input)
        print(f"  {'='*80}")
        
        try:
            # 并行为每个temperature组生成变体
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import time
            
            total_start_time = time.time()
            sql_variants = []
            
            def get_model_config_for_group(group_idx):
                """为当前组选择模型配置（轮询方式）"""
                if self.multi_model_configs:
                    # 使用轮询方式选择模型
                    with self._model_counter_lock:
                        selected_idx = self._model_counter % len(self.multi_model_configs)
                        self._model_counter += 1
                        return self.multi_model_configs[selected_idx]
                else:
                    # 使用默认配置
                    return {'model': model, 'base_url': base_url, 'api_key': api_key}
            
            def generate_group(group_idx, temperature, group_size):
                """生成单个temperature组的SQL变体（每个线程使用独立的client）"""
                if group_size == 0:
                    return []
                try:
                    # 为当前组选择模型配置（轮询方式）
                    model_config = get_model_config_for_group(group_idx)
                    selected_base_url = model_config['base_url']
                    selected_api_key = model_config.get('api_key', api_key)
                    selected_model = model_config.get('model', model)
                    
                    # 每个线程创建独立的client（OpenAI client不是线程安全的）
                    # 设置超时为120秒，避免LLM调用卡住
                    client = OpenAI(base_url=selected_base_url, api_key=selected_api_key, timeout=120.0)
                    start_time = time.time()
                    response = client.chat.completions.create(
                        model=selected_model,
                        messages=[
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_input}
                        ],
                        temperature=temperature,
                        n=group_size  # 当前组生成group_size个响应
                    )
                    elapsed = time.time() - start_time
                    endpoint_info = f" (端点: {selected_base_url.split('/')[-2]})" if self.multi_model_configs else ""
                    print(f"[SQL生成] temperature={temperature}, group_size={group_size}, 耗时={elapsed:.2f}s{endpoint_info}")
                    
                    # 提取当前组的SQL
                    group_sqls = []
                    for choice in response.choices:
                        content = choice.message.content
                        sql = self._extract_sql_from_response(content)
                        if sql:
                            group_sqls.append(sql)
                    return group_sqls
                except Exception as e:
                    error_type = type(e).__name__
                    error_msg = str(e)
                    print(f"[SQL生成] temperature={temperature} 失败: {error_type}: {error_msg}")
                    # 打印更详细的调试信息
                    print(f"  端点: {selected_base_url}, 模型: {selected_model}")
                    return []
            
            # 并行执行所有temperature组
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {}
                for group_idx, temperature in enumerate(temperature_groups):
                    group_size = variants_per_group + (1 if group_idx < remainder else 0)
                    if group_size > 0:
                        future = executor.submit(generate_group, group_idx, temperature, group_size)
                        futures[future] = (group_idx, temperature)
                
                # 收集结果
                for future in as_completed(futures):
                    group_idx, temperature = futures[future]
                    try:
                        group_sqls = future.result()
                        sql_variants.extend(group_sqls)
                    except Exception as e:
                        print(f"[SQL生成] temperature={temperature} 执行异常: {e}")
            
            total_elapsed = time.time() - total_start_time
            print(f"[SQL生成] 完成！共生成 {len(sql_variants)} 个SQL变体，总耗时={total_elapsed:.2f}s")
            return sql_variants
            
        except Exception as e:
            print(f"❌ 使用n参数生成SQL变体失败: {e}")
            # 如果失败，回退到单次调用（但只生成一个）
            try:
                self.setup_agent(0.6)  # 使用默认temperature
                messages = [
                    {"role": "user", "content": user_input}
                ]
                response = self.sql_agent.generate_reply(messages)
                sql = self._extract_sql_from_response(response)
                return [sql] if sql else []
            except Exception as e2:
                print(f"❌ 回退方案也失败: {e2}")
                return []
    
    def _shuffle_schema(self, schema_info: str) -> str:
        """
        随机打乱database schema中表和列的顺序，增加生成多样性
        
        Args:
            schema_info: 原始schema信息
            
        Returns:
            打乱后的schema信息
        """
        try:
            # 分离数据库名称和schema主体
            parts = schema_info.split('\n', 1)
            if len(parts) < 2:
                return schema_info
            
            db_name_line = parts[0]  # 例如: "db_name:codebase_community\n#"
            rest = parts[1]
            
            # 分离表定义和外键定义
            if 'foreign_key:' in rest:
                schema_part, fk_part = rest.split('foreign_key:', 1)
            else:
                schema_part = rest
                fk_part = None
            
            # 解析表定义（每行格式: # table_name(`col1`, `col2`, ...)）
            table_lines = []
            for line in schema_part.split('\n'):
                line = line.strip()
                if line.startswith('#') and '`' in line:
                    table_lines.append(line)
                elif line:  # 保留其他非空行
                    table_lines.append(line)
            
            # 随机打乱表的顺序
            import random
            random.shuffle(table_lines)
            
            # 重建schema
            new_schema = db_name_line + '\n' + '\n'.join(table_lines)
            
            # 处理外键（如果有）
            if fk_part:
                new_schema += '\nforeign_key:' + fk_part
            
            return new_schema
            
        except Exception as e:
            # 如果解析失败，返回原始schema
            print(f"⚠️  Schema打乱失败: {e}，使用原始schema")
            return schema_info
    
    def _extract_sql_from_response(self, response) -> str:
        """从响应中提取SQL代码"""
        import re
        
        # 处理autogen返回的不同类型
        if isinstance(response, dict):
            # 如果是字典，尝试提取content字段
            response = response.get('content', '') or str(response)
        elif not isinstance(response, str):
            # 如果不是字符串也不是字典，转换为字符串
            response = str(response)
        
        # 尝试从代码块中提取
        code_block_match = re.search(r'```sql\s*(.*?)\s*```', response, re.DOTALL)
        if code_block_match:
            return code_block_match.group(1).strip()
        
        # 如果没有代码块，尝试直接提取SQL语句
        lines = response.split('\n')
        sql_lines = []
        in_sql = False
        
        for line in lines:
            # 检测SQL开始
            if line.strip().upper().startswith('SELECT'):
                in_sql = True
                sql_lines.append(line)
            elif in_sql:
                sql_lines.append(line)
                # 检测SQL结束
                if line.strip().endswith(';'):
                    break
        
        result = '\n'.join(sql_lines).strip()
        
        # 如果提取到了内容，返回结果
        if result and 'SELECT' in result.upper():
            return result
        
        # 如果没找到SQL，返回空字符串
        return ""
  