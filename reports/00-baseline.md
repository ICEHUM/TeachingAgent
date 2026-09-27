# 阶段00基线审计报告

审计时间：2026-09-20 02:07 +08:00  
审计对象：`D:\TeachingAgent`  
执行范围：仅阶段00。未重建项目、未升级依赖、未修改业务代码、未覆盖治理文件、未启用或自动批准 hooks。

## 1 审计依据与证据边界

已读取执行包中的 `AGENTS.md`、`PRODUCT.md`、`DESIGN.md`、`steps/00_基线审计与技能安装.md`，以及设计文档第1、2、13、14节和上一阶段报告。包内“已有验证”只作为线索；本报告将本轮实测、既有报告和未复测事项分开记录。

本轮未打开或输出 API 密钥。只确认 `backend/.env` 存在、所需配置可由 `ModelSettings` 加载、密钥字段非空。

## 2 仓库与锁文件

- `D:\TeachingAgent` 不是 Git 仓库，无法用 commit 或 `git status` 建立可回滚基线。
- 项目根目录没有 `AGENTS.md`、`PRODUCT.md`、`DESIGN.md`。缓存依赖中存在一个第三方 `AGENTS.md`，不属于本项目。
- 根目录没有既有 `.agents/skills`、`.git/hooks` 或 `.agents/hooks`；技能安装前未发现同名目标。
- Python 锁文件：`backend/uv.lock`，476387 字节，SHA-256 `82299A4E7A475434F6D71F11509B6F39F5D8A5CE729929BB545814E0AF5C9CAF`。
- 前端锁文件：`package-lock.json`，42653 字节，SHA-256 `11E345EE510FE928DEDCE4D4BD971F7377EAEAB409CC442608AFEA872AFEEE7D`。
- 完成测试和技能安装后两个锁文件哈希不变。

## 3 现有模块映射

| 范围 | 当前文件或能力 | 审计结论 |
| --- | --- | --- |
| 模型适配 | `backend/app/agent/llm.py` | 可加载 DeepSeek 配置并构造 OpenHands `LLM`；含对 OpenHands 私有方法 `_validate_chat_response` 的兼容覆盖 |
| 工作区 | `backend/app/agent/workspace.py` | 提供短期 Docker Agent Server 上下文；创建独立目录和令牌，绑定本机端口，退出时删除容器并保留目录 |
| 教学控制 | `backend/app/teaching_control/` | 目录存在但为空；无 LangGraph 状态、节点、策略或 checkpoint |
| 后端业务 | 无业务 API、数据模型、迁移和服务层 | FastAPI 仅在验证脚本中做 HTTP 冒烟测试 |
| 学生前端 | `frontend/student/package.json` | 只有 React 依赖清单，无业务页面源码 |
| 教师前端 | `frontend/teacher/package.json` | 只有 React 依赖清单，无业务页面源码 |
| 验证脚本 | `scripts/verify_backend.py`、`check-workspaces.py` 等 | 能检查依赖、临时前端构建、Docker 工作区和模型接口 |
| 历史证据 | `docs/*-check.json` | 包含依赖、运行时、工作区和模型接口的既有检查结果；本轮仅重跑依赖和工作区检查 |

## 4 依赖与运行环境实测

运行项目现有 `scripts/check-dependencies.ps1`，退出码 0：

- Python 3.12.13 环境中 198 个包依赖一致。
- `openhands-sdk`、`openhands-tools`、`openhands-workspace`、`openhands-agent-server` 均为 1.49.2。
- FastAPI 0.141.1、Uvicorn 0.53.0、SQLAlchemy 2.0.54、aiosqlite 0.22.1 等导入成功。
- FastAPI 本地 HTTP 冒烟检查通过。
- 学生端和教师端临时 React/Vite 生产构建通过；这是构建夹具，不代表业务前端存在。
- `npm ls --depth=0` 通过，React/ReactDOM 19.3.0、TypeScript 7.0.2、Vite 8.3.0 与锁定清单一致。
- 出现 Starlette 关于未来改用 `httpx2` 的弃用警告，不影响本次退出码。

## 5 OpenHands版本与已验证接口

### 5.1 版本和镜像

- `backend/pyproject.toml` 明确锁定四个 OpenHands 包为 1.49.2，实际安装版本一致。
- Docker Linux 引擎实测为 29.8.0。
- 固定镜像可用：`ghcr.io/openhands/agent-server@sha256:591264fb1cacf06447d66fb69a2596ef736417633dcc81fbc01d3b1d2aa19f2b`。
- 镜像实际 ID 与摘要一致，平台为 `linux/amd64`。

### 5.2 本地版本对应接口

本轮通过 `inspect.signature` 核对当前安装包：

- `RemoteWorkspace(*, working_dir, host, api_key=None, read_timeout=600.0, max_connections=None, runtime_conversation_id=None)`
- `RemoteWorkspace.get_server_info(self) -> dict`
- `RemoteWorkspace.execute_command(self, command, cwd=None, timeout=30.0) -> CommandResult`
- `RemoteWorkspace.reset_client(self) -> None`
- `LLM.completion(self, messages, tools=None, add_security_risk_prediction=False, on_token=None, call_context=None, **kwargs) -> LLMResponse`
- `LLM._validate_chat_response(...)` 存在，但以下划线开头，属于升级敏感的私有接口。

### 5.3 双工作区复测

运行项目现有 `scripts/check-workspaces.ps1`，退出码 0。实际创建两个临时容器并验证：

- Agent Server 报告 SDK 版本 1.49.2；
- `RemoteWorkspace.execute_command` 执行 Python 命令成功；
- 两个宿主目录相互隔离，文件持久化通过；
- 无凭据请求和使用另一工作区令牌的请求均被拒绝；
- 服务只绑定 `127.0.0.1`；
- 上下文退出后两个容器均被删除，测试后没有遗留 `teachingagent.managed=true` 容器；
- 测试没有模型调用，证据目录保留在 `workspaces/runtime`。

更新后的原始结果位于 `docs/workspace-check.json`。

## 6 LangGraph、DeepSeek、数据库和队列

- LangGraph：未安装，`uv.lock` 无 `langgraph`，`teaching_control` 无实现。
- DeepSeek：`backend/.env` 存在；当前配置可加载 `deepseek-flash`、provider `deepseek`、base URL `https://api.deepseek.com`，密钥字段非空。本轮未发起模型调用。既有 `docs/model-api-check.json` 记录 2026-09-19 的模型列表、OpenHands 文本完成和工具调用格式通过，但不能替代本轮实时 API 复测。
- 数据库：SQLAlchemy 2.0.54 和 aiosqlite 0.22.1 已安装；没有业务数据模型、迁移脚本、数据库文件或应用引用。
- 队列：Celery、RQ、Dramatiq、ARQ 均未安装；Redis 8.1.0 是锁文件中的现有依赖，但应用没有引用，不能视为已经存在任务队列。
- checkpoint：没有实现或配置。

## 7 设计技能安装

安装详情见 `reports/00-skill-install-verification.json`：

- 离线辅助测试运行 4 项，3 项通过，1 项因当前 Windows 符号链接权限跳过；总体退出码 0。
- dry-run 退出码 0，确认只复制两个项目级技能目录，不启用 hooks。
- 第一次实际安装失败：第三方输出触发 Python 子进程 GBK 解码错误，安装脚本最终记录 `NoneType` 错误；verify-only 证实没有半安装文件。
- 设置 `PYTHONUTF8=1` 后使用同一脚本重试成功；Impeccable 安装器版本 4.1.0，skills CLI 版本 1.7.0。
- 最终 verify-only 退出码 0；`impeccable` 和 `design-taste-frontend` 的 `SKILL.md` 均存在且名称匹配，树哈希已记录。
- 安装前后均没有 hooks 目录，脚本报告 `hooks_activated=false`。
- 本轮开始时开发工具的技能目录已完成初始化，且当前任务工作目录不是 `D:\TeachingAgent`，所以“项目技能已被发现并激活”尚未验证。应在以 `D:\TeachingAgent` 为项目根目录的新任务中确认。

## 8 本轮变更文件

新增：

- `.agents/skills/impeccable/**`
- `.agents/skills/design-taste-frontend/SKILL.md`
- `reports/skill-install-20260919T180232Z.json`，首次失败记录
- `reports/skill-install-20260919T180319Z.json`，重试成功记录
- `reports/00-baseline.md`
- `reports/00-skill-install-verification.json`
- `reports/00-risks.md`

更新：

- `docs/workspace-check.json`，由本轮双工作区复测刷新时间和证据目录
- `workspaces/runtime/teachingagent-450c2287c9c342f7b5dc771b1ec0d8a0/**`，工作区 A 的保留证据
- `workspaces/runtime/teachingagent-b33e5e7b64fb41c284e0e6c1b469b6db/**`，工作区 B 的保留证据

未修改：业务 Python、前端清单、依赖锁文件、`.env`、已有文档、数据库、AGENTS、PRODUCT、DESIGN 和 hooks。

## 9 实际测试命令与结果

| 命令 | 结果 |
| --- | --- |
| `python -B tools/test_installer_offline.py` | 退出码0；4项中1项跳过 |
| `python -B tools/install_design_skills.py --project-root D:\TeachingAgent` | dry-run退出码0 |
| `python -B tools/install_design_skills.py --project-root D:\TeachingAgent --apply` | 第一次退出码1；GBK解码失败，已记录 |
| `python -B tools/install_design_skills.py --project-root D:\TeachingAgent --verify-only` | 失败后退出码2，确认没有半安装 |
| `PYTHONUTF8=1 ... --apply` | 重试退出码0，技能文件已复制 |
| `PYTHONUTF8=1 ... --verify-only` | 最终退出码0 |
| `scripts/check-dependencies.ps1` | 退出码0；依赖、导入、HTTP和临时前端构建通过 |
| OpenHands Python 签名与分发版本检查 | 退出码0；四包1.49.2，LangGraph缺失 |
| Docker info和固定镜像 inspect | 退出码0；Linux 29.8.0，摘要及平台一致 |
| `scripts/check-workspaces.ps1` | 退出码0；版本、执行、隔离、认证和回收通过 |
| DeepSeek配置存在性检查 | 退出码0；未输出密钥，未调用模型 |
| 锁文件前后哈希 | 一致 |

UI截图：本阶段不涉及界面修改，未运行浏览器检查，也没有新增截图。

## 10 未执行或未完成项

- 未实时调用 DeepSeek；只核对配置和既有 2026-09-19 报告。
- 未运行完整业务端到端流程，因为业务 API、前端、LangGraph、数据库和队列尚不存在。
- 未做负载、安全渗透或课堂并发测试；双工作区通过不能推导全班并发能力。
- 未在新的项目会话中确认项目技能可发现；文件安装已验证，激活待验证。
- 未运行 UI 测试，因为阶段00没有业务界面变更。

## 11 进入阶段01的条件

1. 负责人审阅并确认本报告和 `00-risks.md`，明确允许进入阶段01。
2. 在以 `D:\TeachingAgent` 为项目根目录的新任务中确认 Impeccable 和 Taste 可发现；不能发现时先修正项目技能加载，不重复安装或启用 hooks。
3. 明确执行包治理文件的落位方式：项目根目前没有 AGENTS、PRODUCT、DESIGN，阶段01不能在未比较的情况下直接覆盖或假定已经合并。
4. 在开始写业务代码前建立可回滚基线：优先初始化 Git；若暂不使用 Git，负责人需明确接受文件级快照方案。
5. 阶段01继续遵守当前锁文件，不安装 LangGraph、不升级 OpenHands，除非阶段01指令明确要求并由负责人确认。

阶段00至此停止，未自动进入阶段01。
