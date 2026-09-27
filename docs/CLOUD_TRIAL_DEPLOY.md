# 云服务器试运行准备

本仓库可以作为云服务器试运行的代码来源；本页记录目前代码实际需要的服务和限制。云端部署及端到端验收尚未执行。请先在服务器上完成部署，再用真实地址验证登录、学生运行与教师记录。

## 服务组成

- Python 3.12 后端：在 `backend/` 用锁文件 `uv sync --frozen` 安装，应用入口为 `app.main:app`。服务器使用 `uvicorn app.main:app --host 127.0.0.1 --port 8000`，由反向代理提供 HTTPS 和 `/api`、`/health`。
- PostgreSQL：分别配置 `TEACHING_DATABASE_URL` 与 `LANGGRAPH_CHECKPOINT_DATABASE_URL`。角色及 schema 的基线见 `deploy/bootstrap-postgres.sql`；数据库迁移见 `backend/migrations/versions/`。云端首次试运行按 README 执行 `stage06_demo.py init` 和 `seed_python_basics_demo.py`；`prepare_python_classroom_demo.py` 只用于本机样例姓名整理。不要在已有试运行数据上重复执行重置命令。
- Docker：后端主机必须能运行受限 Python Runner 镜像；旧 FAQ 任务还依赖 OpenHands 镜像。运行用户需要可用的 Docker CLI 和容器权限。先按 `docs/runtime-environment.md` 验证镜像与工作区。
- 模型：在服务器本地环境设置 `LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL` 等变量。`backend/.env.example` 只提供字段示例，不包含密钥。要验证实时 AI 回答，必须提供有效模型密钥并实际调用模型。
- 前端：在仓库根目录 `npm ci`，分别运行 `npm run build --workspace @teachingagent/student` 和 `npm run build --workspace @teachingagent/teacher`。构建后的 `frontend/student/dist`、`frontend/teacher/dist` 是两个静态站点；它们访问各自同源的 `/api`，因此两个站点的反向代理都要转发 `/api` 到后端。

## 登录跳转地址

构建两端前设置 `VITE_STUDENT_URL` 和 `VITE_TEACHER_URL` 为各自公开的 HTTPS 入口，例如 `https://student.example.org/` 与 `https://teacher.example.org/`。这两个地址会嵌入前端构建，登录后按用户身份跳转。本地开发未设置时仍使用 5173/5174 端口。改动地址后重新构建两个前端。

## 试运行边界

当前 DEMO/TEST 仍使用开发身份和 URL 中的 `user` 标识，部分 API 依赖 `X-User-Id`，没有正式会话鉴权。请仅在 VPN、IP 白名单或其他访问控制后面使用虚拟数据进行试运行；不要作为开放互联网的正式教学系统，也不要填入真实学生信息。前端展示的演示账号密码只服务于测试。正式对外开放前，需要实现服务端会话、授权校验、凭据轮换和完整云端验收。

本地发布前验证记录：后端 `134 passed, 14 skipped`，Ruff 全量通过，学生端与教师端生产构建通过。跳过的用例包含需要真实外部服务的测试；这些能力须在部署后的环境再次验证。
