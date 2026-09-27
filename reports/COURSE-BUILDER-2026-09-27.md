# 教师建课与 AI 出题本地验收（2026-09-27）

本阶段让三位教师使用独立 DEMO 账号进入同一教师端，并完成“创建课程 → 上传课程设计 → AI 生成题目草稿 → 教师审阅保存”的流程。新题仍为草稿；任意题目的 Runner、公开/隐藏测试和学生工作区尚未自动生成，因此没有发布到学生端。

## 交付内容

- 演示账号：`demo_teacher`、`demo_teacher2`、`demo_teacher3`，密码均为 `123456`。`stage06_demo.py init` 幂等创建后两位教师，并将三人加入既有演示课程；`seed_python_basics_demo.py` 将后两位教师加入 Python 演示课程。演示登录只在 DEMO/TEST 开发身份机制下开放。
- 教师“课程设计”工作区：创建课程时自动取得 owner 身份；上传 DOCX/TXT/MD（2 MB 内）并保存原文件与提取文字；按上传文档调用已配置的大模型，生成三道可编辑题目草稿；支持下载原文件、预览提取文字、保存教师修改。未入课教师无法访问该课程资料和草稿。
- 后端新增 `CourseDesign`、`QuestionDraft` 模型和 Alembic `20260927_0006` 迁移；迁移授予应用数据库角色新表的 CRUD 权限。模型失败或输出格式不合法时返回明确错误，不保存伪题目。
- 登录页列出新增账号。教师顶栏在课程设计页显示当前工作区，避免沿用课堂示例课程名。

主要文件：`backend/app/business/course_builder.py`、`backend/app/business/models.py`、`backend/migrations/versions/20260927_0006_course_design_drafts.py`、`backend/scripts/stage06_demo.py`、`backend/scripts/seed_python_basics_demo.py`、`backend/app/business/stage06_api.py`、`frontend/teacher/src/CourseBuilder.tsx`、`frontend/teacher/src/App.tsx`、`frontend/teacher/src/styles.css`、`frontend/student/src/App.tsx`、`scripts/stage05b-start-stack.py`。

## 本地实际验证

- 本地 PostgreSQL 升级至 `20260927_0006`；应用角色对两张新表具备 CRUD 权限。
- 三个教师账号分别通过真实 `/api/product/auth/demo-login` 登录，角色均为 teacher。教师三通过真实 API 建课和上传文本课程设计；教师二读取教师三的新课程资料得到 HTTP 403。
- 配置的外部文本模型根据该课程设计实际返回并保存 3 道题目草稿。教师二又在浏览器中独立创建“Python 基础循环（界面验证）”，上传课程设计，生成 3 道关于 `for` 循环的题目，修改一题标题后保存；刷新页面后修改仍在。这个过程暴露并修复了前端将只读字段一同提交、导致保存失败的问题。
- 教师页面在 1440×900、1366×768、1024×768、390×844 四种视口下无横向溢出；课程名称输入框、创建按钮与课程列表支持连续 Tab 导航。浏览器截图已现场检查，未另存文件。
- 全量后端测试：`130 passed, 14 skipped`；师生端构建通过；修改的 Python 文件 Ruff 检查通过。设计检测器只报告现有图表样式的 `stroke-width` 过渡（将其识别为宽度动画），本次新增课程页面没有相应问题。

## 使用方式与边界

打开 `http://127.0.0.1:5174/`，任选一位教师账号登录，点击“课程设计”。上传的文件需包含至少 50 字可提取文字；模型当前读取前 2 万字，界面显示实际使用字数。生成的是教师可修改草稿，必须核对题意、样例和起始代码。当前不能把草稿直接作为学生可运行任务发布；下一阶段需要为教师提供测试用例配置、沙箱预运行、学生任务发布及回滚流程。
