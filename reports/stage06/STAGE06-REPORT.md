# Stage 06 — Demo Hardening + Classroom Pilot Readiness

## Release Candidate

- RC：`TA-FAQ-2026.09-RC1`
- 冻结 UI 基线：`2d3baf9a0e56d199f6bb97d9b0aeaf5e0708efec`
- Python lock SHA-256：`23CCDBD6A2A032BE733D4798C2DBF1DB697CA67F3E3873A045EEB460F73C787E`
- Frontend lock SHA-256：`6DA2B77DF3EC24241B02483D0861F7D1EE2E4068E03831ADDC3F93E9A54FB095`
- OpenHands 四组件：`1.49.2`
- LangGraph：`1.2.11`
- PostgreSQL migration head：`20260921_0005`
- Design System：`TA-DS-1.0-final`
- 新增依赖：无；两个锁文件未修改。

变更均记录为 `BUG`、`SECURITY`、`PILOT_FEEDBACK` 或 `DEMO_BLOCKER`。核心教学决策、成绩合同与七个正式页面结构未重构。

## DEMO 初始化与复位

`backend/scripts/stage06_demo.py` 在 `TEACHING_ENV=TEST/DEMO`、本机 PostgreSQL、非 production 命名数据库上运行。确定性命名空间创建演示教师、演示学生、课程/班级、FAQ-001-v1 六阶段、量规、初始错误代码和三条资料。连续初始化两次得到相同 ID；复位先以完整一次性探针生成并实测删除 1 个 Attempt、1 个 Snapshot、1 个 RequirementResult、1 个 Intervention、1 个 Submission、1 个 Review、1 个 FormalGrade 及 workspace/evidence，再重建相同场景。生产环境实测拒绝执行。

复位只提供 CLI，并要求 `--confirm RESET_DEMO_RC06`；会清理该命名空间的 Attempt、Snapshot、RequirementResult、Intervention、Submission、Review、FormalGrade、Evidence、OperationLedger 与对应 checkpoint thread。没有 HTTP 复位接口。

## Preflight 与健康页

真实 Preflight：`PASS`，`demo_ready=true`。PostgreSQL、migration head、807 条既有 checkpoint 的可读性、固定 OpenHands 1.49.2 镜像、真实 Docker workspace、FAQ 六工具、DeepSeek、学生/教师前端、两账号、FAQ 数据、362.6 GiB 磁盘以及 8000/5173/5174 端口全部 PASS。workspace 实测只读 Snapshot、internal network、无 Docker socket、CPU/内存/PID 限制。

教师健康页真实浏览器验证：业务数据库、执行环境、教学流程、模型服务均显示“正常”。演示教师 API 200，学生 API 403；返回合同仅含四个状态，不含 key、token、宿主路径或数据库凭据。

## 课堂试用

- 数据合同：`docs/stage06/pilot-data-dictionary.md`
- 匿名导出：HMAC-SHA256 假名化，不导出姓名、邮箱、原始 ID、代码全文或密钥。
- DEMO 样例明确标记 `DEMO_NOT_OUTCOME`，不作为教学成效。
- 教师记录：`docs/stage06/pilot-teacher-log-template.md`
- 操作手册：`docs/stage06/pilot-operation-manual.md`
- 独立迁移任务：图书馆设备借用 FAQ，资料与问法不同，最大帮助 L0，默认 `independent_completed=null`。

## 降级与投影

DeepSeek、OpenHands、PostgreSQL 与 SSE 四条降级路径均通过故障注入验证，详见 `degradation-verification.md`。70%/1080p 投影为模拟检查，五张核心页面核心状态可读；未声称真实投影设备通过。

## 验证结果

- Backend：52 passed。
- Stage 06 降级专项：6 passed。
- Ruff：All checks passed。
- Student / Teacher Vite production build：均通过。
- `uv pip check`：220 个包兼容。
- OpenHands 四组件 1.49.2、LangGraph 1.2.11 版本断言通过。

## 安全阻断

Agent Server 与学生进程同 UID 仍是正式开放多租户任意代码课堂前的阻断项。Stage 06 继续限定固定 FAQ、固定工具目录、只读 Snapshot、无主模型密钥、internal network 及 CPU/内存/PID/时间/输出限制。
