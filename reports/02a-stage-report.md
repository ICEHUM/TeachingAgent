# 阶段 02A 完成报告

日期：2026-09-20  
实现提交：`d44373fd5e6fa00a93f823c78f7b74dd4cb866ec`

## 交付结果

- 新增 12 类业务事实模型：User、Course、CourseMembership、Task、TaskVersion、TaskStage、RequirementDefinition、Attempt、Snapshot、TeachingEvent、RequirementResult、Intervention、OperationLedger（正式成绩复杂业务未加入）。
- `teaching_business` 是事实权威；`langgraph_checkpoint` 只恢复流程。教师介入列表与教学时间线只查询业务表。
- Graph 新增 `persist_intervention -> interrupt` 顺序，以及 `persist_event -> evaluate_requirements -> stage_assessment`。
- 客户端 `stage_requirements_met` 和 `formal_grade` 入库前丢弃；阶段通过由 RequirementEvaluator 聚合全部必需 RequirementResult。
- 默认 `allow_auto_l2=false`；L2 自动生成前需要任务策略或教师授权。
- 工具能力分为 DIAGNOSTIC、EVALUATION、MODIFICATION、GENERATION、ADMIN；assessment 只接受策略允许的安全 DIAGNOSTIC/EVALUATION 工具。
- FastAPI 提供任务版本、Attempt、教学事件、Requirement、Intervention/resume 和时间线的最小接口，并校验数据库用户、课程成员关系和对象归属。

完整 ER 图、权限矩阵、FAQ-001-v1、API 表、Graph 和 interrupt/resume 时序见 `docs/stage02a-business-facts.md`。

## 实际依赖 diff

直接新增：`alembic==1.20.0`、`psycopg[binary,pool]==3.3.6`、`langgraph-checkpoint-postgres==3.1.2`。

锁记录新增：`langgraph-checkpoint-postgres 3.1.2`、`psycopg 3.3.6`、`psycopg-binary 3.3.6`、`psycopg-pool 3.3.2`、`tzdata 2026.4`。`uv.lock +81/-0`，升级 0、降级 0、删除 0，SHA-256 为 `23CCDBD6A2A032BE733D4798C2DBF1DB697CA67F3E3873A045EEB460F73C787E`。OpenHands 四组件保持 1.49.2，LangGraph 保持 1.2.11。

## PostgreSQL 与 Alembic

- `deploy/bootstrap-postgres.sql` 幂等创建两个 role/schema，设置固定 search_path 和交叉 REVOKE。
- 业务连接使用 SQLAlchemy AsyncEngine/AsyncSession。
- checkpoint 使用独立 Psycopg AsyncConnectionPool，参数为 `autocommit=True`、`prepare_threshold=0`、`row_factory=dict_row`。
- `LANGGRAPH_STRICT_MSGPACK=true` 为强制启动条件。
- `backend/scripts/init_checkpoint.py` 是独立部署初始化命令；FastAPI worker 不调用 `setup()`。
- Alembic version table 位于 `teaching_business`；`include_schemas=True` 且只包含业务 schema。迁移包含 downgrade，不引用 checkpoint schema。
- PostgreSQL 离线 DDL 成功生成，所有表名均限定为 `teaching_business`。

## 测试

最终完整后端：`24 passed in 12.79s`。新增核心测试覆盖：

1. Intervention 在 interrupt 前持久化：通过。
2. 文件数据库重启后仍可查询 pending Intervention：通过。
3. 未授权教师处理 Intervention：拒绝。
4. 客户端伪造阶段通过：被忽略。
5. RequirementResult 未满足不能推进：通过。
6. 全部必需 Requirement 满足后才推进：通过。
7. assessment 的 MODIFICATION capability：拒绝。
8. L2 默认进入已持久化教师介入：通过。
9. OperationLedger 同 scope/operation_id 去重：通过。
10. stale state_version CAS：拒绝。
11. checkpoint 版本与业务 Attempt 冲突：以业务版本拒绝恢复。
12. 不同学生 Attempt/事件相互隔离：通过。

其他验证：目标代码 Ruff 通过；`uv pip check` 检查 220 个包通过；既有 dependency checks、FastAPI smoke、师生前端 production build 通过；Alembic PostgreSQL offline upgrade DDL 通过；业务 metadata 的空库创建、重复创建和 downgrade 范围测试通过。

## 风险与未完成项

1. 当前主机没有可用的 Docker CLI 或 PostgreSQL 服务，因此 role/schema GRANT、Alembic online upgrade/downgrade、`AsyncPostgresSaver.setup()` 两次执行尚未在真实 PostgreSQL 实例验证；代码、离线 DDL与 SQLite 业务集成测试已通过。
2. Checkpoint 初始化采用官方幂等 `setup()`，但生产部署仍需加单实例 migration job 和失败告警。
3. API 使用 `X-User-Id` 作为已认证主体输入；正式部署需由可信认证中间件注入，不能直接信任公网请求头。
4. Graph resume runtime 以应用注入接口保留；本阶段没有接真实 OpenHands，也没有启动真实课堂 worker。
5. Requirement evaluator 已实现聚合与版本校验；AUTO_TEST、STATIC_CHECK 等具体执行器仍是后续阶段工作。
6. 迁移当前以业务 SQLAlchemy metadata 快照执行 `create_all/drop_all`；在后续模型演进前应将新增变更拆成独立显式 revision，避免修改首个 revision。
7. 未实现正式 UI、真实 OpenHands 执行、复杂成绩表、生产认证和部署监控。

阶段 02A 在此停止，不进入真实 OpenHands 或正式 UI。
