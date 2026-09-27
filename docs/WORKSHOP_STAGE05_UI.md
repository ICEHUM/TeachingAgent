# 智训工坊 · 阶段 05 师生端收敛验收

日期：2026-09-24
状态：本地 DEMO 界面、课堂证据和四视口验收已完成；未进入云部署。

## 本阶段结果

学生端把一轮练习压缩成“读题 → 编辑 → 试运行或公开检查 → 看结果 → 按需找 AI 教练 → 提交”。Python 单阶段任务不再重复显示阶段进度与左栏要求清单；结果区在首次进入、尚无检查时收起，运行后自动展开。编辑现有运行结果对应的代码时，顶部会提示该结果仍属于旧版本。右侧“AI 教练”明确是按需分析已保存代码和检查结果，不暗示敲键时实时生成；学生观察默认收起，教师消息仍与 AI 建议分开。1024px 宽度下文件栏收成带可访问名称的图标栏，编辑区实测宽 766px；390px 宽度下收起的结果区仅占 41px。试运行和公开检查保持不同按钮，编辑器仍提供 stdout、stderr 和错误行定位。

教师课堂默认选择 Python 基础编程示例课程，也可切换旧 FAQ。名单、分组数和首选学生均跟随当前课程。首屏在名单前展示每名学生每题最近尝试的公开检查失败信号和未检查人次；点击信号能查看代表学生。教师详情的证据页直接展示公开样例的输入、预期、实际和诊断，以及样例级技能证据；原始执行输出仍可展开。等待时间改为分钟、小时或天显示。课程和顶栏明确标注示例数据，虚拟验收学生由受限的本地脚本改为易读示例姓名。

课堂信号来自业务库里已有的 Snapshot/RequirementResult，不由模型臆造。它描述当前已检查样例，不代表完整掌握度；隐藏测试仍只向学生提供汇总状态。教师对话与交互图表保留，教师仍有最终成绩确认权。

## 本地演示

```powershell
Set-Location D:\TeachingAgent
$env:TEACHING_ENV = "DEMO"
& .\backend\.venv\Scripts\python.exe .\backend\scripts\stage06_demo.py init --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\backend\scripts\seed_python_basics_demo.py --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\backend\scripts\prepare_python_classroom_demo.py --local-stage02b-config
& .\backend\.venv\Scripts\python.exe .\scripts\stage05b-start-stack.py
```

学生端 `http://127.0.0.1:5173/`，教师端 `http://127.0.0.1:5174/`。当前本地演示账号是 `demo_student / 123456` 与 `demo_teacher / 123456`。虚拟姓名脚本只处理固定验收 UUID 和旧验收前缀；本地运行结果为 `renamed_demo_learners=12`、`already_curated=0`。重新运行不会覆盖已经整理的姓名。

## 实测

- 后端完整测试：`python -m pytest -q --basetemp %TEMP%\teaching-stage05-full tests`，**119 passed, 9 skipped**。跳过项需要显式容器镜像变量；上一阶段真实容器用例已经单独通过。
- 师生 TypeScript `tsc --noEmit` 均通过；两端 `npm run build` 生产构建通过；本阶段后端和测试文件 Ruff 通过。
- 真实浏览器逐一查看师生端 **1440×900、1366×768、1024×768、390×844**。各视口的页面根节点均无横向溢出。1024px 学生编辑区宽 766px；390px 学生编辑区宽 327px，文件栏 48px，收起的结果区 41px。截图在浏览器验收时查看，未另存独立图片文件。
- 键盘验收：学生“AI 教练”和窄屏教师详情均可用 Esc 关闭，并把焦点返回入口；教师课程下拉可用键盘在 Python 与 FAQ 间切换。
- 现场数据核对：教师点击“判断是否及格”公开检查信号能进入对应学生，在证据页看到 60 分样例的预期“及格”、实际“不及格”和“分数边界判断不符”；技能证据显示“边界比较：需检查”，没有虚构掌握度。

## 交付边界

学生 AI 教练当前是基于已保存版本和证据的按需分层指导，还不是持续自由对话。当前调试能力是程序试运行、输出和错误定位，没有断点、单步执行或变量监视。演示课堂记录是脚本建立的虚拟样本，不是课堂试用结论。教师任务创建/发布后台、正式认证、云部署和真实课堂调研不属于本阶段；进入真实试用前仍需任课教师核对三题题面、时长和量规。
