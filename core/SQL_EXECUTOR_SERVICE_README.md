# SQL执行器服务使用说明

## 概述

SQL执行器服务是一个独立的Flask服务，用于执行SQL查询。参考Reward-SQL的实现，提供以下优势：

1. **避免重复加载数据库连接**：服务启动时加载一次，后续请求复用
2. **支持并发请求**：Flask的threaded模式支持多线程并发
3. **可扩展性**：可以部署到多台机器，实现负载均衡
4. **统一管理**：集中管理数据库连接和超时控制

## 快速开始

### 1. 启动服务

```bash
# 默认端口5887
python core/sql_executor_service.py

# 指定端口
python core/sql_executor_service.py --port 5888

# 指定地址和端口
python core/sql_executor_service.py --host 0.0.0.0 --port 5887
```

### 2. 在代码中使用

#### 方式1：使用客户端（推荐）

```python
from core.sql_executor_client import SQLExecutorClient

# 创建客户端
client = SQLExecutorClient("http://localhost:5887")

# 执行SQL
result, error = client.execute_sql("SELECT * FROM table1 LIMIT 10", "database_name")
if error:
    print(f"执行失败: {error}")
else:
    print(result)

# 比较SQL
match, message = client.compare_sql(
    predicted_sql="SELECT * FROM table1",
    ground_truth="SELECT * FROM table1",
    db_name="database_name"
)
print(f"匹配: {match}, 消息: {message}")
```

#### 方式2：使用服务包装器（兼容现有代码）

```python
from core.database_connector_service import DatabaseConnectorService

# 使用服务模式
connector = DatabaseConnectorService(
    db_name="database_name",
    service_url="http://localhost:5887",
    use_service=True
)

# 使用方式与DatabaseConnector完全相同
result, error = connector.execute_query("SELECT * FROM table1 LIMIT 10")
if error:
    print(f"执行失败: {error}")
else:
    print(result)
```

#### 方式3：回退到传统模式

```python
from core.database_connector_service import DatabaseConnectorService

# 如果服务不可用，自动回退到传统模式
connector = DatabaseConnectorService(
    db_name="database_name",
    use_service=False  # 或服务不可用时自动回退
)

# 使用方式完全相同
result, error = connector.execute_query("SELECT * FROM table1 LIMIT 10")
```

## API接口

### POST /execute_sql

执行SQL查询

**请求体：**
```json
{
    "sql": "SELECT * FROM table1 LIMIT 10",
    "db_name": "database_name",
    "timeout": 60
}
```

**响应：**
```json
{
    "status": "success",
    "result": [
        {"column1": "value1", "column2": "value2"},
        ...
    ],
    "columns": ["column1", "column2"],
    "row_count": 10
}
```

### POST /compare_sql

比较两个SQL的执行结果

**请求体：**
```json
{
    "predicted_sql": "SELECT * FROM table1",
    "ground_truth": "SELECT * FROM table1",
    "db_name": "database_name",
    "timeout": 60
}
```

**响应：**
```json
{
    "status": "success",
    "match": true,
    "message": "Results match (rows: 10)"
}
```

### POST /compact_result

压缩执行结果（参考Reward-SQL的实现）

**请求体：**
```json
{
    "result": [
        {"column1": "value1", "column2": "value2"},
        ...
    ],
    "max_length": 500
}
```

**响应：**
```json
{
    "status": "success",
    "compacted_result": "{'value1': 2, 'value2': 3, ...}"
}
```

### GET /health

健康检查

**响应：**
```json
{
    "status": "healthy",
    "service": "SQL Executor Service"
}
```

## 在MCTS中使用

### 修改MCTS Workflow

在`mcts_workflow.py`中，可以选择使用服务模式：

```python
from core.database_connector_service import DatabaseConnectorService

# 在初始化时
if use_sql_service:
    self.db_connector = DatabaseConnectorService(
        db_name=sample['db'],
        service_url="http://localhost:5887",
        use_service=True
    )
else:
    from core.database_connector import DatabaseConnector
    self.db_connector = DatabaseConnector(sample['db'])
```

### 环境变量配置

可以通过环境变量控制是否使用服务：

```python
import os

USE_SQL_SERVICE = os.getenv('USE_SQL_SERVICE', 'false').lower() == 'true'
SQL_SERVICE_URL = os.getenv('SQL_SERVICE_URL', 'http://localhost:5887')
```

## 性能优势

### 1. 避免重复连接
- **传统模式**：每次创建DatabaseConnector都要连接数据库
- **服务模式**：服务启动时连接一次，后续请求复用

### 2. 并发支持
- **传统模式**：多线程共享连接可能有问题
- **服务模式**：Flask的threaded模式天然支持并发

### 3. 可扩展性
- **传统模式**：单机处理
- **服务模式**：可以部署多实例，实现负载均衡

## 注意事项

1. **服务必须启动**：使用服务模式前，确保SQL执行器服务已启动
2. **网络延迟**：服务模式会有网络请求开销，但通常可以忽略
3. **自动回退**：如果服务不可用，`DatabaseConnectorService`会自动回退到传统模式
4. **超时控制**：服务模式支持超时控制，避免长时间阻塞

## 部署建议

### 开发环境
```bash
# 单机运行
python core/sql_executor_service.py --port 5887
```

### 生产环境
```bash
# 使用gunicorn等WSGI服务器
gunicorn -w 4 -b 0.0.0.0:5887 core.sql_executor_service:app

# 或使用tmux管理
tmux new-session -d -s sql_service 'python core/sql_executor_service.py --port 5887'
```

## 故障排查

### 1. 服务无法启动
- 检查端口是否被占用：`lsof -i :5887`
- 检查数据库路径是否正确

### 2. 客户端连接失败
- 检查服务是否运行：`curl http://localhost:5887/health`
- 检查防火墙设置
- 检查服务地址是否正确

### 3. 执行超时
- 增加timeout参数
- 检查SQL查询是否过于复杂
- 检查数据库文件是否损坏

## 与Reward-SQL的对比

| 特性 | Reward-SQL | 我们的实现 |
|------|-----------|-----------|
| 服务架构 | Flask服务 | Flask服务 ✅ |
| 并发支持 | threaded=True | threaded=True ✅ |
| 结果压缩 | Counter压缩 | Counter压缩 ✅ |
| SQL缓存 | 无 | 有（在客户端）✅ |
| 自动回退 | 无 | 有 ✅ |
| 超时控制 | 有 | 有 ✅ |

## 总结

SQL执行器服务提供了更好的并发性能和可扩展性，特别适合：
- 多线程/多进程环境
- 需要处理大量SQL查询的场景
- 需要部署到多台机器的场景

如果只是单机单线程使用，传统模式可能更简单直接。

