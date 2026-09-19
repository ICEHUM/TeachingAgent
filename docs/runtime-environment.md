# 本机运行环境

2026-09-19 已完成实际运行验证，无需再次重启。

## 当前配置

- WSL 2.7.14.0；Windows 虚拟化引擎已加载。
- Docker Desktop 4.91.0，Linux Engine / CLI 29.8.0，WSL2 后端。
- Docker 镜像与容器数据：`D:\TeachingAgent\.runtime\docker-desktop`，已生成 `disk` 与 `main` 目录。
- OpenHands 官方镜像：`ghcr.io/openhands/agent-server:1.49.2-python-amd64`，已下载并核对摘要。固定摘要见 `workspaces/agent-server-image.json`，工作区启动使用摘要，不随 latest 改变。
- 模型配置和测试详见 [DeepSeek API 说明](model-api.md)。模型密钥保存在后端，不挂载进本次工作区测试容器。

## 启动与检查

PowerShell 中运行：

```powershell
Set-Location D:\TeachingAgent
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-runtime.ps1
```

脚本在需要时启动 Docker Desktop，并运行官方 hello-world 容器验证 Linux 引擎。只检查状态可运行 `scripts/check-runtime.ps1`。

验证两个 OpenHands 工作区：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-workspaces.ps1
```

这会创建两个短期容器，执行测试后移除容器；测试文件保留在 `workspaces/runtime/teachingagent-*`。不调用模型 API。执行策略参数只影响当前 PowerShell 进程，不修改系统策略。

## 工作区接入

`backend/app/agent/workspace.py` 使用 Docker Desktop 创建独立容器，通过 OpenHands SDK 的 `RemoteWorkspace` 连接 Agent Server。每次调用分配独立目录、随机访问凭据和本机端口，每个容器限制 2 CPU、2 GiB 内存和 512 个进程。

```python
from app.agent.workspace import isolated_workspace

with isolated_workspace() as handle:
    result = handle.workspace.execute_command(
        "python -c \"print(1 + 1)\"", cwd="/workspace"
    )
    print(result.stdout)
```

容器只挂载本次分配的实训目录，API 仅绑定 127.0.0.1，不挂载 Docker socket。退出上下文后回收容器，保留目录文件。该模块目前针对本机 Windows Docker Desktop，课堂多用户账号、任务授权和网络策略需在业务开发时补充。

## 实测结果

- hello-world Linux 容器运行通过。
- 两个 Agent Server 容器启动通过，远端 SDK 版本均为 1.49.2。
- SDK 远程执行 Python 和 shell 命令通过。
- A、B 工作区文件分离及宿主目录持久化通过。
- 未携带访问凭据，以及携带另一工作区凭据时，API 拒绝访问。
- API 仅绑定本机地址；两个测试容器退出后均已移除。

记录：`docs/runtime-check.json`、`docs/workspace-check.json`。这些是运行环境验证；学生／教师页面、自研教学控制和完整模型智能体业务流程尚未实现。

## 安装修复记录

首次重启后 VirtualMachinePlatform 已启用但虚拟化引擎未加载。已补全 WSL 系统组件，将当前启动项 hypervisorlaunchtype 设为 Auto，第二次重启后检查通过。备份与日志在 `.cache/runtime-setup/`；原启动配置备份为 `bcd-backup-20260919-232138`。

官方参考：[Docker Windows 安装](https://docs.docker.com/desktop/setup/install/windows-install/)、[OpenHands Docker 工作区](https://docs.openhands.dev/sdk/guides/agent-server/docker-sandbox)。
