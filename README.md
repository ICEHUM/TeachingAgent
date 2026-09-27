# 教学智能体实训项目

这是一个面向高职人工智能技术应用专业的 AI 应用开发实训系统。学生在浏览器中完成真实代码任务，系统通过 LangGraph 教学流程、隔离代码执行和 DeepSeek 分层指导提供有证据的下一步；教师查看课堂过程、介入指导并发布正式成绩。

当前学生演示登录默认进入 **PYB-01-v1「两数相加」**，可在任务栏切换 **PYB-02「判断是否及格」** 和 **PYB-03「遍历列表求和」**；旧 FAQ-001-v1 保留为回归与教师演示数据。登录页支持教师和学生注册；教师可创建班级并关联课程，学生注册时选择班级。教师端可创建课程、上传课程设计并让 AI 生成可编辑题目草稿；自由教学对话可按问题查询课堂数据并生成可交互图表。师生端和登录页支持浅色/深色主题切换。三道 Python 题的公开/隐藏检查默认使用独立 Python Runner，旧 FAQ 仍使用 OpenHands。

## 快速入口

- [项目总说明](docs/PROJECT_GUIDE_CN.md)：产品定位、智能体角色、学生/教师流程、启动登录、演示数据、API、边界和验证记录。
- [阶段 05 界面验收](docs/WORKSHOP_STAGE05_UI.md)：师生端信息收敛、课堂信号与本地浏览器验收。
- [功能设计 v2](docs/functional-design-v2.md)：内部设计基线，包含规划中的功能和验收条件；不能替代当前实现说明。
- [Stage 06 操作手册](docs/stage06/pilot-operation-manual.md)：小规模试用、预检和演示复位。
- [开发环境说明](docs/development-environment.md)：Python、Node、Docker 和依赖安装。
- [运行环境说明](docs/runtime-environment.md)：Docker Desktop 与 OpenHands 工作区检查。
- [云服务器试运行准备](docs/CLOUD_TRIAL_DEPLOY.md)：构建入口、地址配置与当前访问边界。

## 当前启动方式

```powershell
Set-Location D:\TeachingAgent
$env:TEACHING_ENV = "DEMO"
& .\backend\.venv\Scripts\python.exe .\backend\scripts\stage06_demo.py init --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\backend\scripts\seed_python_basics_demo.py --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\backend\scripts\prepare_python_classroom_demo.py --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\scripts\stage05b-start-stack.py
```

打开：

- 学生端：<http://127.0.0.1:5173/>
- 教师端：<http://127.0.0.1:5174/>
- API 健康检查：<http://127.0.0.1:8000/health>

当前演示账号：`demo_student`、`demo_teacher`、`demo_teacher2`、`demo_teacher3`，密码均为 `123456`。密码来自当前 DEMO 配置文件；新环境初始化后请以 `%TEMP%\teachingagent-stage06-demo-login.json` 为准。打开教师端后点击“课程设计”即可建课、上传 DOCX/TXT/MD 文件和生成题目草稿。草稿尚未进入学生端，发布前仍需配置可运行测试。

## 当前实现状态

已落地：学生和教师双端、三道 Python 基础题的任务包与提交检查、旧 FAQ 任务、Snapshot/证据/提交/评价、LangGraph 持久流程和 interrupt/resume、独立 Python Runner 与旧 FAQ 的 OpenHands 隔离检查、DeepSeek 安全降级、教师课堂刷新、教师分析助手和五类交互图表，以及教师建课、课程设计上传和 AI 题目草稿。学生端以编辑、运行、检查结果和按需 AI 教练为主；教师端默认显示 Python 示例课程、公开检查共性问题及可下钻的样例证据。三道 Python 题已通过真实容器及业务 API 端到端验收；建课出题已通过本地 API 与浏览器验证。

演示环境仍使用 DEMO/TEST 开发身份和 `X-User-Id`，没有正式 OAuth/JWT 会话、持久任务队列或开放多租户任意代码课堂。正式成绩由教师确认发布。完整边界和验证结果见 [项目总说明](docs/PROJECT_GUIDE_CN.md)。
