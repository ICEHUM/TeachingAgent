# Stage 02B 完成报告

日期：2026-09-20
实现提交：`653f89ee92dcfd2e642d24f0bd8e032a6b2a7b8f`

## 1. PostgreSQL版本与启动方式

- 固定镜像：`docker.io/library/postgres:17.11-alpine3.24@sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995`
- 拉取后 RepoDigest：与上述 OCI digest 一致。
- `linux/amd64` manifest：`sha256:aa90e97ee862e558111d34cfb8b2c4bec768c2b039fb791341686928560263b3`，与提案一致。
- 容器内 `postgres --version`：PostgreSQL 17.11。
- `SELECT version()`：PostgreSQL 17.11，x86_64-pc-linux-musl，64-bit。
- 容器：`teachingagent-postgres-02b`；独立volume：`teachingagent-postgres-02b-data`。
- Docker随机分配端口并只绑定 `127.0.0.1`；本次实测为 `127.0.0.1:59453`。
- Docker Desktop 4.91.0、Engine 29.8.0、Linux/amd64；未安装或重装Docker。

## 2. role/schema/search_path验证

`deploy/bootstrap-postgres.sql` 在空schema连续执行两次成功，并在业务表/checkpoint表存在后再次执行成功。

| 验证 | teaching_app | langgraph_cp |
|---|---|---|
| search_path | `teaching_business, public` | `langgraph_checkpoint, public` |
| teaching_business USAGE | 允许 | 拒绝 |
| 业务表 CRUD | INSERT/UPDATE/DELETE/SELECT事务实测通过 | SELECT和INSERT均拒绝 |
| langgraph_checkpoint USAGE/CREATE | 拒绝 | 允许 |
| checkpoint表访问 | 拒绝 | setup和查询通过 |

两个schema均撤销PUBLIC权限。教师列表、教学时间线和Intervention服务只查询业务表。

## 3. Alembic真实验证

首个revision已从运行时 `Base.metadata.create_all/drop_all` 改为显式：

- `op.create_table`
- `op.create_index`
- `op.create_unique_constraint`
- 明确的PK/FK和反向downgrade

实测：

| 步骤 | 结果 |
|---|---|
| 空业务schema `upgrade head` | 成功，13张业务表＋`alembic_version` |
| 再次 `upgrade head` | 成功，无新DDL |
| `downgrade base` | 成功，仅保留空版本表 |
| downgrade期间checkpoint schema | 4张表保持不变 |
| 再次 `upgrade head` | 成功，revision=`20260920_0001` |
| `alembic check` | `No new upgrade operations detected` |

首次online尝试暴露URL中百分号被ConfigParser插值的问题；`migrations/env.py`现对环境URL中的百分号做Alembic所需转义。显式索引名也已与metadata对齐，消除了autogenerate drift。

## 4. AsyncPostgresSaver

使用独立 `langgraph_cp` AsyncConnectionPool，固定 `autocommit=True`、`prepare_threshold=0`、`row_factory=dict_row`，且 `LANGGRAPH_STRICT_MSGPACK=true`。

`python scripts/init_checkpoint.py` 连续执行两次均成功。生成表仅位于 `langgraph_checkpoint`：

- `checkpoint_migrations`
- `checkpoints`
- `checkpoint_blobs`
- `checkpoint_writes`

业务schema中不存在checkpoint表。脚本修正了直接运行时的模块路径，并在Windows使用Selector event loop；此前真实错误分别为 `ModuleNotFoundError: app` 和 Psycopg不支持Proactor event loop，均已保留为本阶段验证记录。

## 5. 跨进程interrupt/resume

进程A创建Attempt与Intervention，先提交业务状态 `CREATING`，再执行真实LangGraph `interrupt()`并写PostgreSQL checkpoint，成功后改为 `WAITING_TEACHER`，随后进程退出。

全新进程B不使用进程A内存对象：先从 `teaching_business` 查询到该事项，重新校验教师课程成员关系、TaskVersion策略、Intervention状态和Attempt `state_version`；随后以相同thread_id执行 `Command(resume=...)`，从PostgreSQL checkpoint恢复，最终业务状态为 `RESOLVED`。最终显式迁移结构上复测通过。

完整时序与状态图见 `docs/stage02b-recovery-gate.md`。

## 6. 双写故障与恢复

| 场景 | 真实PostgreSQL结果 |
|---|---|
| 业务DB成功、checkpoint模拟失败 | `CHECKPOINT_FAILED` |
| 失败事项进入正常教师列表 | 否 |
| 相同创建operation_id重试 | 同一Intervention，数据库仅1行，转`WAITING_TEACHER` |
| stale state_version resume | 拒绝，状态保持`WAITING_TEACHER` |
| resume模拟失败 | `RESUME_FAILED` |
| resume失败后的教师操作 | teacher、response、allow_l2全部保留 |
| 相同resume operation重试 | 成功转`RESOLVED`，OperationLedger仅1行 |
| 已完成resume operation再次重放 | 直接返回`RESOLVED`，不重复调用Graph |

## 7. 认证边界

`DEV_AUTH_ENABLED`默认为关闭。仅DEV/TEST环境可启用 `X-User-Id`；production配置启用时应用拒绝启动。角色、课程关系和对象归属仍由业务数据库校验。本阶段未实现OAuth/JWT。

## 8. 测试与依赖

- 完整后端：`28 passed in 5.16s`。
- Stage 02B修改文件Ruff：通过。
- `uv pip check`：220个包兼容。
- 既有dependency checks：通过；FastAPI smoke、学生端和教师端production build通过。
- OpenHands四组件：均为1.49.2。
- LangGraph：1.2.11；Alembic：1.20.0；Psycopg：3.3.6；checkpoint-postgres：3.1.2。
- `uv.lock`未修改，SHA-256仍为 `23CCDBD6A2A032BE733D4798C2DBF1DB697CA67F3E3873A045EEB460F73C787E`。
- 本阶段新增直接/传递依赖：0；升级/降级/删除：0。

全仓Ruff仍报告阶段前既有的 `app/agent/llm.py`、`app/agent/workspace.py` 和 `tests/test_llm_compat.py` 风格项；本阶段没有改动这些OpenHands相关文件。Stage 02B目标文件全部通过。

## 9. 风险与未完成项

1. 测试PostgreSQL容器和独立volume仍在本机运行，供用户复核；它们不含生产数据。是否清理由用户后续决定。
2. `PostgresInterventionRecoveryRuntime`已通过真实跨进程验证，但尚未作为正式课堂worker部署或注入生产FastAPI生命周期。
3. OAuth/JWT、正式UI、真实OpenHands教学执行仍未实现，均属于后续阶段。
4. checkpoint初始化仍应作为单实例部署job运行；FastAPI worker不得自动setup。
5. Windows Selector event loop约束已落实到Stage 02B命令行入口；未来若更换运行服务器，需要在部署环境验证事件循环策略。

Stage 02B在此停止，不进入真实OpenHands或正式UI。
