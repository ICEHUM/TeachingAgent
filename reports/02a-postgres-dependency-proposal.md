# 阶段 02A PostgreSQL 依赖变更提案

日期：2026-09-20  
基线提交：`331eb9788352c8777a9cd704127a8f0d4795e671`  
状态：仅完成临时副本锁文件预演；尚未修改 `D:\TeachingAgent\backend\pyproject.toml`、正式 `uv.lock` 或虚拟环境。

## 1. 建议方案

业务事实层使用 SQLAlchemy 2 的异步 ORM 与 PostgreSQL；数据库迁移使用 Alembic；LangGraph 流程恢复使用官方 PostgreSQL checkpointer。业务表和 checkpoint 表使用独立连接池与独立 PostgreSQL schema，数据库查询只读取业务表，checkpoint 不作为教师课堂列表或业务时间线的数据源。

不新增 `asyncpg`。Psycopg 3 同时支持 SQLAlchemy 异步引擎和 LangGraph PostgreSQL checkpointer，可以避免维护两套 PostgreSQL 驱动。

## 2. 建议新增的直接依赖

```toml
"alembic==1.20.0",
"psycopg[binary,pool]==3.3.6",
"langgraph-checkpoint-postgres==3.1.2",
```

| 直接依赖 | 用途 | 选择依据 |
| --- | --- | --- |
| `alembic==1.20.0` | 业务表迁移与回滚 | 当前锁文件已由 `openhands-agent-server` 间接锁定该版本；提升为直接依赖不会改变现有版本 |
| `psycopg[binary,pool]==3.3.6` | SQLAlchemy PostgreSQL 驱动、异步连接和连接池 | Psycopg 3 稳定版，支持 Python 3.12；`binary` 避免本地编译，`pool` 满足 checkpointer 与业务连接池需求 |
| `langgraph-checkpoint-postgres==3.1.2` | 持久化 LangGraph interrupt/resume checkpoint | LangGraph 官方 PostgreSQL saver；与现有 `langgraph-checkpoint==4.2.0` 解析兼容 |

保留：

- `langgraph==1.2.11`
- `sqlalchemy==2.0.54`（当前锁定结果；直接声明仍保持原形式）
- `aiosqlite`（保留既有开发兼容性，本阶段正式业务方案使用 PostgreSQL）
- OpenHands 四组件 `1.49.2`

## 3. 临时锁文件预演

预演方法：复制正式 `backend/pyproject.toml` 与 `backend/uv.lock` 到工作区临时目录，只在副本中加入上述三个精确依赖，然后执行 `uv lock`。没有安装包，也没有修改 D 盘项目。

| 项目 | 结果 |
| --- | --- |
| 当前锁文件 SHA-256 | `4231E632D3FDFA87BD9CCB3CDF1AB7E366D2D0EE41BF1C53A3B610FA3D2DFBC7` |
| 预演锁文件 SHA-256 | `23CCDBD6A2A032BE733D4798C2DBF1DB697CA67F3E3873A045EEB460F73C787E` |
| `uv.lock` 行差异 | `+81 / -0` |
| 新增包记录 | 5 |
| 已有包升级 | 0 |
| 已有包降级 | 0 |
| 已有包删除 | 0 |

新增锁记录：

| 包 | 版本 | 来源 |
| --- | --- | --- |
| `langgraph-checkpoint-postgres` | 3.1.2 | 新增直接依赖 |
| `psycopg` | 3.3.6 | 新增直接依赖 |
| `psycopg-binary` | 3.3.6 | `psycopg[binary]` |
| `psycopg-pool` | 3.3.2 | `psycopg[pool]` 和 PostgreSQL checkpointer |
| `tzdata` | 2026.4 | Psycopg 在 Windows 上的时区数据 |

以下 checkpointer 所需包已经存在于锁文件，因此没有新增或变更：

- `langgraph-checkpoint==4.2.0`
- `orjson==3.12.0`
- `ormsgpack==1.12.2`
- `typing-extensions==4.16.0`

## 4. OpenHands 与核心依赖断言

预演前后版本一致：

| 包 | 当前 | 预演后 |
| --- | --- | --- |
| `openhands-sdk` | 1.49.2 | 1.49.2 |
| `openhands-tools` | 1.49.2 | 1.49.2 |
| `openhands-workspace` | 1.49.2 | 1.49.2 |
| `openhands-agent-server` | 1.49.2 | 1.49.2 |
| `langgraph` | 1.2.11 | 1.2.11 |
| `langgraph-checkpoint` | 4.2.0 | 4.2.0 |
| `sqlalchemy` | 2.0.54 | 2.0.54 |
| `alembic` | 1.20.0 | 1.20.0 |

## 5. 数据库与 checkpoint 物理边界建议

- 业务连接使用 `teaching_business` schema，Alembic 只管理该 schema 中的 User、Course、Task、Attempt、Requirement、Intervention、TeachingEvent 和 OperationLedger 等业务表。
- checkpoint 连接使用 `langgraph_checkpoint` schema，由 `PostgresSaver.setup()` 管理其专用表；Alembic 不接管这些表。
- 两个连接池使用不同 PostgreSQL role。API 的普通业务连接不授予 checkpoint schema 查询权限；教师课堂列表只能查询业务 `interventions` 表。
- 设置 `LANGGRAPH_STRICT_MSGPACK=true`，或向 checkpointer 提供显式允许模块列表，限制 checkpoint 反序列化范围。
- checkpoint 写入成功不能替代 Intervention、TeachingEvent 或 OperationLedger 的业务事务；恢复时必须重新读取业务 Attempt、权限、Intervention 状态和 `state_version`。

## 6. 获得确认后的安装与验证计划

1. 确认 Git 工作区干净并记录当前提交。
2. 在正式 `backend/pyproject.toml` 中加入三个精确依赖并生成 `uv.lock`，暂不升级其他依赖。
3. 将正式锁文件与本预演比较；要求 SHA-256 等于 `23CCDBD6A2A032BE733D4798C2DBF1DB697CA67F3E3873A045EEB460F73C787E`，新增记录和版本必须完全一致。
4. 如果出现预演外新增、升级、降级或删除，立即停止，不执行 `uv sync`。
5. 锁文件一致后执行 `uv sync --locked`。
6. 运行 `uv pip check`、原有 dependency checks、OpenHands 1.49.2 断言。
7. 运行 `alembic`、`psycopg`、`AsyncPostgresSaver` 导入测试；检查业务连接与 checkpoint 连接分别指向独立 schema。
8. 再开始阶段 02A 数据模型、迁移、服务、Graph 调整和 API 实现。

## 7. 预演中的实际异常

受限沙箱首次执行 `uv lock` 无法建立到 PyPI 的套接字连接并失败。以获准联网权限在同一临时副本中重跑后解析成功。正式项目没有因此发生任何变更。

## 8. 依据

- LangGraph 官方 PostgreSQL checkpointer：https://pypi.org/project/langgraph-checkpoint-postgres/
- LangGraph 官方持久化说明：https://docs.langchain.com/oss/python/langgraph/add-memory
- Psycopg：https://pypi.org/project/psycopg/
- Alembic：https://pypi.org/project/alembic/

等待负责人确认后再安装；确认前不修改正式依赖。
