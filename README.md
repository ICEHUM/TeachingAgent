# 智训工坊

面向 Python 基础编程课堂的 AI 实训系统。学生编写并运行代码，系统依据当前代码和检查结果提供分层指导；教师管理班级、查看学习证据并完成评价。

## 智能体与角色

- **学生 AI 教练**：学生主动请求时，结合已保存代码、运行报错和检查结果，实时调用大模型生成下一步提示。提示级别由教学策略控制，不直接替学生改代码。
- **教师教学助手**：与教师自由对话；分析班级学情时读取已保存的课堂数据，并可在对话中生成可交互图表。
- **教学流程控制器**：LangGraph 负责证据收集、工具调用、分层指导和教师介入后的流程恢复。Python 基础题由隔离的 Python Runner 执行；保留的旧 FAQ 任务使用 OpenHands。
- **真人教师**：创建班级和课程、查看学生过程、介入指导、复核作品并发布正式成绩。AI 不替代教师确认。

## 本地启动（Windows）

先按[开发环境说明](docs/development-environment.md)准备 Python、Node.js、PostgreSQL 和 Docker。首次建立演示数据时运行前三条初始化命令；以后只运行最后一条启动命令。

```powershell
Set-Location D:\TeachingAgent
$env:TEACHING_ENV = "DEMO"
& .\backend\.venv\Scripts\python.exe .\backend\scripts\stage06_demo.py init --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\backend\scripts\seed_python_basics_demo.py --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\backend\scripts\prepare_python_classroom_demo.py --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\scripts\stage05b-start-stack.py
```

学生端：<http://127.0.0.1:5173/>；教师端：<http://127.0.0.1:5174/>。演示账号为 `demo_student`、`demo_teacher`、`demo_teacher2`、`demo_teacher3`，演示密码均为 `123456`。

## 云服务器试运行

服务器需有 Python 3.12、Node.js、PostgreSQL 和 Docker。先创建数据库并执行 `deploy/bootstrap-postgres.sql`，在服务器环境中设置 `TEACHING_DATABASE_URL`、`LANGGRAPH_CHECKPOINT_DATABASE_URL`、`LLM_API_KEY` 等变量。用数据库管理员连接设置 `TEACHING_ALEMBIC_DATABASE_URL`，在 `backend/` 运行 `uv sync --frozen` 和 `uv run alembic upgrade head`；随后运行 `uv run python scripts/stage06_demo.py init` 与 `uv run python scripts/seed_python_basics_demo.py` 初始化演示课堂。演示数据脚本要求 PostgreSQL 位于服务器本机。

后端在 `backend/` 运行 `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`。前端在仓库根目录运行 `npm ci`，设置 `VITE_STUDENT_URL`、`VITE_TEACHER_URL` 为两个公开 HTTPS 地址，再分别运行 `npm run build --workspace @teachingagent/student` 和 `npm run build --workspace @teachingagent/teacher`。把两个 `dist` 目录作为静态站点发布，并将两站点的 `/api` 转发到后端。具体配置与限制见[云服务器试运行准备](docs/CLOUD_TRIAL_DEPLOY.md)。

当前试运行使用开发身份机制，只应在 VPN、IP 白名单等访问控制后面使用虚拟数据；正式开放前需要完善会话鉴权。
