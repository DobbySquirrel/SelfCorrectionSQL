{
  "probe_templates": [
    {
      "id": "value_fuzzy_match",
      "category": "value_linking",
      "description": "模糊匹配探测：检查关键词是否是列中值的子串",
      "sql_template": "SELECT DISTINCT {column} FROM {table} WHERE {column} LIKE '%{keyword}%' LIMIT 5",
      "parameters": ["table", "column", "keyword"],
      "default_values": {},
      "return_type": "list",
      "usage_scenario": "当自然语言实体与数据库值可能存在缩写或别名差异时使用。"
    },
    {
      "id": "value_existence_check",
      "category": "value_linking",
      "description": "精确存在性探测：验证某个特定值是否存在",
      "sql_template": "SELECT 1 FROM {table} WHERE {column} = '{value}' LIMIT 1",
      "parameters": ["table", "column", "value"],
      "default_values": {},
      "return_type": "boolean",
      "usage_scenario": "当LLM推测了一个具体的值（如状态码、固定搭配）时进行验证。"
    },
    {
      "id": "value_column_ambiguity",
      "category": "value_linking",
      "description": "列歧义消解：统计关键词在某一列的出现次数",
      "sql_template": "SELECT COUNT(*) FROM {table} WHERE {column} LIKE '%{keyword}%'",
      "parameters": ["table", "column", "keyword"],
      "default_values": {},
      "return_type": "integer",
      "usage_scenario": "当一个词（如Apple）可能属于多个列（品牌或产品名）时，对比Count大小。"
    },
    {
      "id": "semantic_sampling",
      "category": "semantic_understanding",
      "description": "语义采样：获取非空样本数据以理解列含义",
      "sql_template": "SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT {limit}",
      "parameters": ["table", "column", "limit"],
      "default_values": {"limit": 5},
      "return_type": "list",
      "usage_scenario": "当列名含糊不清（如type, status）时，通过样本值推断含义。"
    },
    {
      "id": "semantic_distinct_enum",
      "category": "semantic_understanding",
      "description": "枚举值获取：获取分类字段的所有选项",
      "sql_template": "SELECT DISTINCT {column} FROM {table} LIMIT {limit}",
      "parameters": ["table", "column", "limit"],
      "default_values": {"limit": 50},
      "return_type": "list",
      "usage_scenario": "用于低基数字段（类别、状态），获取所有可能的选项供LLM选择。"
    },
    {
      "id": "struct_join_validity",
      "category": "structural_join",
      "description": "关联可行性探测：验证两个表是否可以通过指定键关联",
      "sql_template": "SELECT 1 FROM {table_a} A JOIN {table_b} B ON A.{key_a} = B.{key_b} LIMIT 1",
      "parameters": ["table_a", "key_a", "table_b", "key_b"],
      "default_values": {},
      "return_type": "boolean",
      "usage_scenario": "最重要的探针。用于在生成 JOIN CTE 前验证路径是否通畅。"
    },
    {
      "id": "struct_join_coverage",
      "category": "structural_join",
      "description": "外键覆盖率探测：计算左连接的匹配比例",
      "sql_template": "SELECT COUNT(A.{key_a}) as total_a, COUNT(B.{key_b}) as matched_b FROM {table_a} A LEFT JOIN {table_b} B ON A.{key_a} = B.{key_b}",
      "parameters": ["table_a", "key_a", "table_b", "key_b"],
      "default_values": {},
      "return_type": "dictionary",
      "usage_scenario": "判断应该使用 Inner Join 还是 Left Join，防止数据大量丢失。"
    },
    {
      "id": "integrity_relationship_type",
      "category": "data_integrity",
      "description": "关系类型探测（1:1 vs 1:N）：探测两表关联后是否存在数据膨胀",
      "sql_template": "SELECT COUNT(*) FROM {table_a} A JOIN {table_b} B ON A.{key_a} = B.{key_b}",
      "parameters": ["table_a", "table_b", "key_a", "key_b"],
      "default_values": {},
      "return_type": "integer",
      "usage_scenario": "在计算总金额时，如果发生 1:N 关联导致订单行膨胀，SUM() 结果会错误倍增。此探针用于预警。"
    },
    {
      "id": "dist_min_max",
      "category": "distribution_range",
      "description": "极值探测：获取数值列的范围",
      "sql_template": "SELECT MIN({column}) as min_val, MAX({column}) as max_val FROM {table}",
      "parameters": ["table", "column"],
      "default_values": {},
      "return_type": "dictionary",
      "usage_scenario": "用于理解数值量级（如金额单位）或时间跨度。"
    },
    {
      "id": "dist_null_density",
      "category": "distribution_range",
      "description": "空值率探测：计算列的空值比例",
      "sql_template": "SELECT CAST(COUNT({column}) AS FLOAT) / COUNT(*) as not_null_ratio FROM {table}",
      "parameters": ["table", "column"],
      "default_values": {},
      "return_type": "float",
      "usage_scenario": "评估列的数据质量，决定是否适合作为过滤条件。"
    },
    {
      "id": "temporal_format_check",
      "category": "temporal_logic",
      "description": "时间格式探测：确认日期列的存储格式（时间戳、字符串还是日期对象）",
      "sql_template": "SELECT {column}, TYPEOF({column}) FROM {table} WHERE {column} IS NOT NULL LIMIT 1",
      "parameters": ["table", "column"],
      "default_values": {},
      "return_type": "list",
      "usage_scenario": "用户问'2023年'，LLM需要知道是用 STRFTIME('%Y') 还是 date_part，或者处理 Unix 时间戳。"
    },
    {
      "id": "temporal_range_granularity",
      "category": "temporal_logic",
      "description": "时间跨度与粒度：获取时间的最早/最晚点，辅助相对时间计算",
      "sql_template": "SELECT MIN({column}), MAX({column}) FROM {table}",
      "parameters": ["table", "column"],
      "default_values": {},
      "return_type": "dictionary",
      "usage_scenario": "用户问'最近一个月'。如果数据库最新数据是2年前的，直接用 NOW() 会得到空集，必须用 MAX(date) 推算。"
    },
    {
      "id": "integrity_uniqueness",
      "category": "data_integrity",
      "description": "主键/唯一性探测：判断某列是否唯一，决定是否需要 DISTINCT 或 GROUP BY",
      "sql_template": "SELECT COUNT(*) as total, COUNT(DISTINCT {column}) as distinct_cnt FROM {table}",
      "parameters": ["table", "column"],
      "default_values": {},
      "return_type": "dictionary",
      "usage_scenario": "用户问'有多少学生'。如果 student_id 在表中不唯一（如选课表），直接 Count(*) 是错的，必须 Count(Distinct)。"
    }
  ]
}