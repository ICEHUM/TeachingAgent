# 教学智能体实训项目

面向高职人工智能技术应用专业的 AI 应用开发实训，采用 LangGraph 教学流程控制、OpenHands SDK 实训执行、DeepSeek 模型服务、学生与教师网页，以及 Docker 独立实训工作区。自研教学模块负责阶段规则、分层指导、教师介入和评价证据。

## 目录

- backend/app/agent/：模型与 OpenHands SDK 执行适配。
- backend/app/teaching_control/：计划实现 LangGraph 状态、教学节点、分层指导策略和过程记录。
- frontend/student/：学生实训页面。
- frontend/teacher/：教师任务配置、过程查看与指导页面。
- workspaces/：实训工作区相关配置。已验证独立 Docker 容器的基本执行与回收；跨请求课堂会话管理待开发。
- docs/：需求、架构设计和参赛材料。

## 当前状态

已安装并验证 OpenHands SDK、后端和前端应用依赖，生成版本锁文件和环境重装脚本。WSL 2.7.14、Docker Desktop 4.91.0 与 OpenHands 独立容器工作区均已通过实际运行验证。已提供本地工作区创建、SDK 命令执行与容器回收模块。LangGraph 尚未接入业务，学生／教师页面、自研教学控制、持久化状态和完整课堂业务流程尚未实现。

详见 [开发环境说明](docs/development-environment.md)。

运行 `scripts/start-runtime.ps1`，启动并验证本地 Docker Linux 引擎。详见 [运行环境说明](docs/runtime-environment.md)。

DeepSeek 模型 API 已配置并完成 SDK 文本与 API 工具调用格式验证。详见 [模型配置说明](docs/model-api.md)。

## 当前设计方案

2026-09-20 更新为 v2，以 LangGraph 为教学流程主框架，OpenHands 为受限实训执行层。首个任务为构建带来源引用的 FAQ 问答服务。

- [功能设计文档 v2](docs/functional-design-v2.md)：评分对应、10项功能、页面、流程、数据接口和验收清单。
- [功能设计文档 Word](docs/AI应用开发实训教练功能设计文档_v2.docx)：可阅读和评审的排版版本。
- v1 保留为历史资料；后续开发以 v2 为准。本次文档更新不代表业务功能已经实现。
