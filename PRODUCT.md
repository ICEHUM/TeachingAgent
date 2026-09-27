# 产品事实与边界

## 下一版产品决策（2026-09-23）

下一版按《智训工坊 自主研发版 开发设计文档 V1.0》增量改造现有工程。首版只建设一门 Python 基础编程课程和三项完整实验，从输入输出与运算、条件判断、列表遍历与 `for` 循环开始；不以文件处理、CSV 或异常处理作为首版题目。另外两门课程待负责人提供教学材料后接入。学生主流程为阅读任务、编辑与保存代码、运行公开检查、获取基于当前证据的分层辅导、修改重试、提交并接受隐藏测试。教师主流程为查看真实课堂卡点、下钻学生证据、与教学助手对话并生成可交互数据可视化、进行人工介入和正式成绩确认。

保留 React、TypeScript、FastAPI、Python LangGraph、PostgreSQL、Snapshot、证据记录、教师介入和现有对话能力。PYB-01/02/03 已由通过隔离、限额、公开/隐藏测试验收的独立 Runner 执行；OpenHands 暂为历史 FAQ 回归场景保留。FAQ 不作为新版默认课程。四个智能体按 Guard、Assessment、Tutor、Teaching Insight 四项可观察职责实现，不要求四个独立聊天模型。

当前代码默认进入 PYB-01，学生可切换 PYB-02/03，同时保留 FAQ 演示回归。学生工作台聚焦读题、编码、试运行和公开检查；AI 教练按需打开。教师课堂默认聚焦 Python 示例课程，先看公开检查信号，再下钻到学生与样例证据；其他课程可切换。新版范围与验收见[改造阶段 01](docs/WORKSHOP_MIGRATION_STAGE01.md)、[Runner 阶段 03](docs/WORKSHOP_STAGE03_RUNNER.md)、[三题阶段 04](docs/WORKSHOP_STAGE04_AGENT_TASKS.md)和[界面阶段 05](docs/WORKSHOP_STAGE05_UI.md)。

## 当前实现

本文件记录产品定位和不可越过的边界；当前实现状态以 [项目总说明](docs/PROJECT_GUIDE_CN.md) 和现行代码为准，早期基线报告只作为历史记录。

产品是面向高职人工智能技术应用专业的 AI 应用开发实训教练。当前默认演示任务是 Python 基础编程“两数相加”，带来源引用的 FAQ 问答服务作为历史回归场景保留。学生在独立工作区编辑、试运行和接受阶段验收，系统通过 LangGraph 控制教学流程，通过独立 Runner 执行 Python 检查，通过 DeepSeek 生成有证据的分层指导；教师确认正式成绩。

当前技术组合包含 React、TypeScript、FastAPI、LangGraph、Docker Python Runner、旧 FAQ 使用的 OpenHands SDK 与 Agent Server、DeepSeek 和独立工作区。教师可在试运行环境注册、创建班级并关联课程；学生注册时选择班级，已发布且受支持的 Python 基础练习会建立独立 Attempt 与工作区。教师助手支持自由对话，话题不限；涉及班级数据时可读取真实课程数据库并生成交互式桑基、柱状、折线、表格和热力图。它不修改作品、成绩或教学策略。

已落地的能力包括：Python 基础任务和旧 FAQ 演示、师生双端、Snapshot 与证据、提交与教师量规复核、LangGraph checkpoint interrupt/resume、SSE 事件补读、独立 Runner 与旧 FAQ OpenHands 受限执行、DeepSeek 实时指导和教师课堂分析。模型调用失败时明确显示不可用，不用预写建议冒充 AI 回答。正式成绩只能由教师确认并发布。

演示环境仍使用 DEMO/TEST 开发身份和 `X-User-Id`；新注册账号的密码以加盐哈希保存，但尚无正式 OAuth/JWT 会话，因此注册只在 DEMO/TEST 开放。系统没有持久长任务队列或开放多租户任意代码课堂。旧 FAQ 的 OpenHands UID 隔离、跨请求工作区会话、并发/负载、备份恢复、Runner 基础镜像漏洞处理和安全渗透仍是生产化前的工作项。不得把演示虚拟数据描述为真实课堂成效，不得把教师助手描述为自动授课或自动评分系统。
