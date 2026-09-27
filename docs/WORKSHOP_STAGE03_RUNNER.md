# 智训工坊 · 阶段 03 独立 Runner 实施记录

日期：2026-09-23
状态：PYB-01 已默认路由到独立 Python Runner；本地 DEMO 和 Windows Docker Linux Engine 验收通过。云服务器部署、安全基线更新仍待单独验收。

## 已实现

- 新增 `DockerPythonRunnerExecutor`。PYB-01 的样例、公开测试和隐藏测试直接使用 Docker CLI 启动短期 Python 容器，不调用 OpenHands Agent Server。学生程序只在容器内执行；测试输入由服务端经 stdin 提供，预期输出留在服务端，隐藏检查仅返回汇总状态。
- 每次执行使用不可变摘要镜像、非特权 UID/GID `65534:65534`、`--network none`、只读根文件系统与只读 Snapshot、无新增 capability、禁止提权、CPU/内存/PID 限额、宿主侧有界 stdout/stderr 采集和超时强制回收。容器不挂载 Docker socket，不传递模型密钥。
- 复用现有 Snapshot 与证据持久化合同；结果仍绑定 Attempt、任务版本、Snapshot 和 operation ID，证据中记录实际镜像摘要。PYB-01 默认使用固定摘要官方 Python 镜像；进程环境变量 `TEACHING_PYTHON_RUNNER_IMAGE` 可覆盖，显式空值是回退旧执行器的开关。FAQ 暂保留 OpenHands 运行路径。
- 执行结果和证据记录实际执行器来源；教师端有对应验收结果时展示“Python Runner 运行证据”或“OpenHands 实训证据”，没有来源证据的自运行事件显示中性名称，避免误标。
- Docker CLI 连接增加 Linux 本机 Unix socket 路径，拒绝相对路径和 TCP 端点；Windows 仍固定使用本机 Docker Desktop Linux Engine 命名管道。当前只在 Windows Docker Linux Engine 上实测，Linux 宿主端到端仍待云部署阶段核验。

## 实测

- 使用本机已有、固定摘要的 OpenHands 镜像作为**临时 Python 根文件系统**，对相同 Snapshot 在旧执行器和新 Runner 上运行公开与隐藏检查。正确程序均通过；故意遗漏负数边界的程序均为公开通过、隐藏失败。新 Runner 的隐藏反馈不包含具体输入和预期输出。
- 在临时本地 `127.0.0.1:8001` DEMO API（不替换现有 8000 服务）上运行 `backend/scripts/verify_python_runner_live.py --local-stage02b-config`：两名明确标记为 Runner 验收的演示学生走通保存、Snapshot、公开 Run、隐藏 Submit、教师详情。错误样本公开 `SATISFIED`、隐藏 `NOT_SATISFIED` 且无 Submission；正确样本两项均 `SATISFIED` 且生成 Submission。教师验收记录的 evaluator 均为 `docker_runner:*`，教学过程显示 Runner，学生接口不暴露隐藏输入。临时服务验收后已关闭。
- 负责人确认新增镜像后拉取 `python:3.12.14-slim-bookworm` 的同一 `linux/amd64` 摘要；`docker image inspect` 显示镜像 ID 为 `sha256:1aaa65…a1a`、展开大小约 186 MB，离线启动报告 Python 3.12.14。正式配置使用 Docker 标准的 `python@sha256:…` 引用，因为 Docker Scout 扫描后本地标签映射消失，而固定摘要仍存在。
- 对官方镜像重新运行同快照 Runner/OpenHands 对照与容器隔离测试；在临时 DEMO API 无镜像环境变量覆盖的情况下运行 `backend/scripts/verify_python_runner_live.py --local-stage02b-config --suite official`。新演示样本再次证明错误程序公开通过、隐藏失败且无 Submission，正确程序两项通过并生成 Submission；教师详情、隐藏结果脱敏和证据内的官方镜像摘要均核对通过。
- 仅重启现有 8000 API 后，对官方 Runner 验收样本执行一次非评分 `program-runs`：返回 `succeeded`、退出码 0，证据标记 `docker_runner` 与上述官方镜像摘要，容器网络为 `none`、用户为 `65534:65534`；5173/5174 前端服务没有重启。阶段 06 预检全部 `PASS`，`demo_ready=true`，包含新加的 Python Runner 镜像检查及旧 FAQ/OpenHands 回归工作区检查。验收后 8001 临时 API 已关闭，没有遗留 Runner 容器。
- 容器 `inspect` 证实非特权用户、无网络、只读根目录和 Snapshot、无端口绑定、内存 1 GiB、CPU 1 核、PID 128，Docker socket 未挂载。死循环在限时内结束并回收容器；20 万字符输出被截断至服务端配置的 16 KiB。
- 以官方镜像运行 `python -m pytest -q --basetemp <唯一目录> backend/tests`：**113 passed**。Ruff 对本阶段改动文件检查通过，`git diff --check` 通过，两个依赖锁文件差异为零。真实 API 的阶段 02 全链路也已通过，见[阶段 02 记录](WORKSHOP_STAGE02_IMPLEMENTATION.md)。本阶段未修改师生页面，因此无新增界面截图。

## 固定镜像和部署边界

已按负责人确认下载并默认使用 Docker Official Image `python:3.12.14-slim-bookworm` 的 `linux/amd64` 固定摘要 `sha256:1aaa65a85fda306ffb8b910824d4e93bdce61e212c7e87168123ea3073b41a1a`。其来源为 Debian Bookworm slim 和 Python 3.12.14；官方 Dockerfile 列出的运行层依赖包括 `ca-certificates`、`netbase`、`tzdata` 及 Python 所需的系统共享库。它未向后端 Python 或前端 npm 环境安装包，`backend/uv.lock` 与 `package-lock.json` 的差异均为零。

Docker Scout v1.24.0 在 2026-09-23 对该本地镜像识别 156 个包，报告 **3 Critical、12 High**，涉及 Perl、OpenSSL、util-linux 和 zlib；明细留在 `.runtime/python-runner-scout.txt`。Debian 安全跟踪器当日仍将相关 Bookworm 版本标为易受影响，例如 [Perl CVE-2026-13221](https://security-tracker.debian.org/tracker/CVE-2026-13221)、[Perl CVE-2026-12087](https://security-tracker.debian.org/tracker/CVE-2026-12087) 和 [OpenSSL CVE-2026-75803](https://security-tracker.debian.org/tracker/CVE-2026-75803)。这些扫描项不能直接等同于容器逃逸；本项目的无网络、非 root、只读和资源限制降低暴露面，但不消除基础包漏洞。**云服务器对外运行前必须更新镜像安全基线、重新扫描并在目标 Linux 主机复测**；本阶段只声称本地 DEMO 通过。

官方来源：[Python 官方镜像清单](https://github.com/docker-library/official-images/blob/master/library/python)、[Python slim-bookworm Dockerfile](https://github.com/docker-library/python/blob/master/3.12/slim-bookworm/Dockerfile)、[Docker 固定镜像摘要建议](https://docs.docker.com/build/building/best-practices/)。

项目 [AGENTS.md](../AGENTS.md) 要求“新增或升级依赖前先报告建议版本、传递依赖和锁文件差异，获得负责人确认后再修改”；负责人已在本任务中明确同意上述镜像。OpenHands 仅在旧 FAQ 回归路径保留，直到该路径正式归档或替换。Linux 宿主端到端、云端 Docker 权限、并发负载和安全基线仍属于部署阶段验证，不包含在本地 DEMO 结论中。

## 变更文件与复现

- 执行与证据：`backend/app/agent/runner.py`、`tools.py`、`workspace.py`、`backend/app/main.py`、`backend/app/business/stage03.py`、`stage05_api.py`、`backend/app/teaching_control/protocols.py`。
- 验收与运行：`backend/tests/agent/test_docker_runner_contract.py`、`test_docker_runner_live.py`、`backend/scripts/verify_python_runner_live.py`、`verify_python_basics_live.py`、`stage06_preflight.py`、`scripts/stage05a-server.py`。
- 说明：`README.md`、`PRODUCT.md`、`docs/PROJECT_GUIDE_CN.md`、本记录。仓库已有其他未提交改动，未在本阶段清理或覆盖。

实际执行：`TEACHING_RUNNER_TEST_IMAGE=python@sha256:1aaa65a85fda306ffb8b910824d4e93bdce61e212c7e87168123ea3073b41a1a` 下运行 `python -m pytest -q --basetemp <本地唯一目录> backend/tests`（113 passed）；`python -m ruff check` 检查本节列出的 Python 文件（通过）；`python backend/scripts/verify_python_runner_live.py --local-stage02b-config --suite official`（两样本通过）；`python backend/scripts/stage06_preflight.py --local-stage02b-config`（全部 PASS）；`git diff --check`（通过）。页面代码未改，无新增截图。
