# DeepSeek 模型配置

配置与实测日期：2026-09-19。

## 已配置

凭据只保存在后端 `backend/.env`，该文件已被项目 `.gitignore` 排除。模型为 `deepseek-flash`，接口为 `https://api.deepseek.com`，使用 OpenAI 兼容协议。前端不读取或持有 API 密钥。

OpenHands 通过 LiteLLM 接入时，模型标识为 `deepseek/deepseek-flash`。其中 `deepseek/` 是 SDK 的提供商路由前缀，发给服务端的模型名仍是 `deepseek-flash`。

后端从 `app.agent.llm` 导入 `build_llm` 即可加载本地配置。此模块使用绝对文件位置读取 `.env`，不依赖当前工作目录。密钥用 `SecretStr` 保存，未启用完整对话日志。

```python
from app.agent.llm import build_llm
from openhands.sdk import Message, TextContent

llm = build_llm()
response = llm.completion(messages=[
    Message(role="user", content=[TextContent(text="你好")])
])
```

## 实测结果

- 账户模型列表中可访问 `deepseek-flash`。
- OpenHands SDK 发起短文本请求，成功收到预期回复。
- DeepSeek 返回正确的函数调用结构及参数；测试未执行任何实际工具。
- 用量兼容回归测试 3 项通过。

验证脚本：`backend/.venv/Scripts/python.exe scripts/check-model-api.py`。再次执行会发起少量真实模型请求。验证报告在 `docs/model-api-check.json`，不包含密钥。冒烟测试关闭思考、将输出上限设为 128 tokens；正常 `build_llm()` 保留 SDK 默认思考设置，输出上限暂设 4096，可按任务通过参数调整。

## 接入层兼容处理

当前 OpenHands 1.49.2 与 LiteLLM 1.101.0 存在返回用量字段不一致：LiteLLM 在缓存写入计数为空时删除字段，但字段集合中仍保留标记，导致 SDK 统计用量时触发 `AttributeError`。

`TeachingLLM` 在 SDK 校验回复前补齐缺失的缓存写入计数，保留已有缓存读写计数。该处理位于项目源码，未改动第三方安装包；覆盖同步和异步 Chat Completions 共用路径。升级 SDK 后应重新运行回归测试再评估移除。测试文件：`backend/tests/test_llm_compat.py`。

模型调用验证与 Docker 运行环境独立；Docker 引擎和 OpenHands 独立容器工作区现已通过实际运行验证。完整模型智能体业务流程及学生／教师页面尚未实现。

官方接口说明：[DeepSeek 首次调用](https://api-docs.deepseek.com/zh-cn/)。
