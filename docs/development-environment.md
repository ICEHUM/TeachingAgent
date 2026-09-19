# 开发依赖与环境

安装日期：2026-09-19。项目位置：`D:\TeachingAgent`。

## 已安装并锁定

| 组件 | 版本 / 位置 |
| --- | --- |
| Python | 3.12.13；解释器在 `.tools/python/`，虚拟环境在 `backend/.venv/` |
| OpenHands SDK / Tools / Workspace / Agent Server | 四个包统一为 1.49.2 |
| FastAPI | 0.141.1 |
| Uvicorn | 0.53.0 |
| Node.js | 使用机器现有的 22.14.0 |
| React / React DOM | 19.3.0 |
| TypeScript | 7.0.2 |
| Vite | 8.3.0 |
| Vite React 插件 | 6.1.1 |

后端另含配置、HTTP、表单、SQLAlchemy、SQLite 异步驱动，以及 pytest、pytest-asyncio、Ruff 开发工具。全部 Python 解析结果保存在 `backend/uv.lock`。两个前端使用 npm workspaces，共享根目录 `node_modules` 与 `package-lock.json`。

## 重装与验证

在 PowerShell 中进入项目目录：

```powershell
Set-Location D:\TeachingAgent
.\scripts\install-dependencies.ps1
.\scripts\check-dependencies.ps1
```

脚本需要现有的 `uv`、`node`、`npm.cmd` 可从 PATH 访问。重装使用现有锁文件；Python 与安装缓存保存在项目内部。日常运行后端可直接使用 `backend/.venv/Scripts/python.exe`。

本次已通过：Python 包依赖一致性检查，四个 OpenHands 包及后端组件导入，终端工具、任务工具与 DockerWorkspace 导入，FastAPI 本地 HTTP 冒烟检查，以及学生端和教师端依赖解析与临时 React 页面生产构建。

前端构建验证生成的临时文件位于 `.cache/frontend-verification/`；这些是验证夹具，不是业务页面。当前未实现学生端、教师端和业务 API。FastAPI 冒烟检查出现上游 Starlette 关于未来迁移 httpx2 的弃用提示，但检查通过。

## 运行环境与业务接入状态

- WSL 2.7.14、Docker Desktop 4.91.0、Linux 容器和 OpenHands 双工作区均已实测通过。Docker 数据目录为 `D:\TeachingAgent\.runtime\docker-desktop`。启动及检查见 [运行环境说明](runtime-environment.md)。
- DeepSeek API 已配置：`deepseek-flash`，地址 `https://api.deepseek.com`。已通过 SDK 文本调用和 API 工具调用格式验证，详见 [模型配置说明](model-api.md)。完整容器智能体端到端运行仍待验证。
- 当前安装的是固定版本发行包。后续修改 SDK 源码时，应另设源码目录并切换为可编辑安装，避免直接改动 `.venv` 中的包文件。

官方参考：

- [OpenHands SDK 安装文档](https://docs.openhands.dev/sdk/getting-started)
- [OpenHands SDK 架构](https://docs.openhands.dev/sdk/arch/overview)
- [Vite 环境要求](https://vite.dev/guide/)

## 框架方案更新

2026-09-20 的 [功能设计 v2](functional-design-v2.md) 将 LangGraph 作为教学流程主框架，OpenHands 保留为受限实训执行层。LangGraph、持久检查点及教学业务尚未接入；现有依赖锁文件和环境验证结果继续描述已安装组件，实施时再添加并验证新依赖。
