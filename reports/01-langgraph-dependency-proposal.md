# 阶段01 LangGraph依赖变更建议

建议时间：2026-09-20。

## 建议版本

建议新增直接依赖：`langgraph==1.2.11`。

理由：这是本次核对时 LangGraph 官方仓库发布列表中的最新 `langgraph` 稳定版本，PyPI 将项目标记为 Production/Stable，支持 Python 3.12，满足最小 StateGraph、条件路由、checkpointer、interrupt 和 resume 合同所需能力。本阶段不新增 PostgreSQL 或 SQLite 专用 checkpoint 包，测试使用内存 checkpointer；正式持久化方案留给后续数据库阶段。

官方来源：

- https://pypi.org/project/langgraph/
- https://github.com/langchain-ai/langgraph/releases/tag/1.2.11
- https://docs.langchain.com/oss/python/langgraph/interrupts

## 预演方法

将 `backend/pyproject.toml` 和 `backend/uv.lock` 复制到工作区临时目录，只在副本的 dependencies 中加入 `langgraph==1.2.11`，执行 `uv lock`。没有修改 `D:\TeachingAgent` 的清单、锁文件或虚拟环境。

临时解析成功，共解析383个包。uv 使用 Python 3.12 解释器，符合项目 `>=3.12,<3.13` 约束。解析器对既有第三方元数据中的不规范版本范围给出规范化警告，但锁定成功。

## 预计直接文件变化

- `backend/pyproject.toml`：dependencies 增加一行 `langgraph==1.2.11`。
- `backend/uv.lock`：预计增加297行，大小从476387字节变为511135字节。
- 当前锁文件 SHA-256：`82299A4E7A475434F6D71F11509B6F39F5D8A5CE729929BB545814E0AF5C9CAF`。
- 临时预览锁 SHA-256：`4231E632D3FDFA87BD9CCB3CDF1AB7E366D2D0EE41BF1C53A3B610FA3D2DFBC7`。

## 预计新增锁定包

| 包 | 版本 |
| --- | --- |
| langgraph | 1.2.11 |
| langgraph-checkpoint | 4.2.0 |
| langgraph-prebuilt | 1.1.0 |
| langgraph-sdk | 0.4.4 |
| langchain-core | 1.6.3 |
| langchain-protocol | 0.0.19 |
| langsmith | 0.13.0 |
| httpcore2 | 2.13.0 |
| httpx2 | 2.13.0 |
| httpx2-jsfetch | 1.0 |
| jsonpatch | 1.33 |
| jsonpointer | 3.1.1 |
| ormsgpack | 1.12.2 |
| requests-toolbelt | 1.0.0 |
| truststore | 0.10.4 |
| uuid-utils | 0.17.1 |
| xxhash | 4.0.1 |
| zstandard | 0.25.0 |

没有预计删除的包，也没有任何现有包版本变化。`httpx` 0.28.1 会保留；`httpx2` 是并存的新分发。

## OpenHands保护检查

临时锁预览中以下版本保持不变：

- `openhands-sdk==1.49.2`
- `openhands-tools==1.49.2`
- `openhands-workspace==1.49.2`
- `openhands-agent-server==1.49.2`

## 获批后的安装与验证计划

1. 在 `backend/pyproject.toml` 精确固定 `langgraph==1.2.11`。
2. 使用 uv 更新项目锁文件并检查实际差异仍只有上述新增项。
3. 使用项目 Python 3.12.13 同步锁定依赖，不升级 OpenHands。
4. 运行 `uv pip check`、现有依赖检查、OpenHands 1.49.2 版本断言和 LangGraph 最小导入检查。
5. 只有上述检查通过后开始教学控制合同和最小流程实现。

## 当前状态

`approval_required`。尚未修改项目依赖或虚拟环境，等待负责人明确确认。
