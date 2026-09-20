# Stage 05B 完成报告

日期：2026-09-20
范围：FAQ-001-v1 提交、复盘、教师评价与正式成绩
边界：未开发任务设计器、成效中心、匿名导出、其他课程；未做 Final UI Polish。

## 1. 锁定结构

学生工作台三栏、教师课堂台列表+详情+tabs+介入区、Evidence Drawer 层级均未重构。05B 以独立提交/复盘页和独立评价页接入，入口分别为学生顶栏「提交 / 查看提交」和教师详情「打开评价页」。

书面锁定见 `reports/STAGE05B-SCOPE.md`，polish backlog 见 `reports/UI-POLISH-BACKLOG.md`。

## 2. 功能

- 学生将当前最新 Snapshot 冻成提交；同一 Snapshot / 同一 operation_id 幂等。
- 提交后仍可继续编辑；评价只读取被提交 Snapshot。
- 复盘页分栏：自动检查、AI 建议、教师确认。AI 建议没有分数字段。
- 教师评价页：左作品/检查/解释/变式，右量规。未确认项保持待评价。
- 发布前必须四项均确认且填写理由；正式成绩只在发布后写入 `formal_grades`。
- `TEACHER_REVIEW` 由教师确认写入，绑定提交 Snapshot，不经过学生 runner。
- 课堂「需要关注」增加 `pending_review`。

量规：功能与边界测试 40、调试解释 25、方案与迁移 20、规范与协作 15。

## 3. API

| 动作 | 路径 | 权威来源 |
|---|---|---|
| 提交预览 / 最新提交 | `GET /api/product/attempts/{id}/submissions/latest` | Snapshot + RequirementResult |
| 创建提交 | `POST /api/product/attempts/{id}/submissions` | Submission + OperationLedger |
| 读取提交 | `GET /api/product/submissions/{id}` | 提交 Snapshot，不是当前编辑 |
| 提交文件 | `GET /api/product/submissions/{id}/files/{path}` | 只读 Snapshot 目录 |
| 保存评价草稿 | `PUT /api/product/teacher/reviews/{id}` | ReviewItem，忽略 formal_grade |
| 发布成绩 | `POST /api/product/teacher/reviews/{id}/publish` | FormalGrade，幂等 |
| 教师复核验收项 | `POST /api/product/teacher/submissions/{id}/requirement-reviews` | TEACHER_REVIEW RequirementResult |

## 4. 测试

| Check | Result |
|---|---|
| Backend pytest | 46 passed |
| Ruff（05B 相关文件） | All checks passed |
| Student `tsc -b` | passed |
| Teacher `tsc -b` | passed |
| 现有 `check:frontend` | both workspaces passed |
| Impeccable detect（05B 前端文件） | `[]` |

实际命令：

```text
uv run pytest -q --tb=line
uv run ruff check app/business/reviews.py app/business/models.py app/business/service.py app/business/faq.py app/business/stage05_api.py tests/business/test_stage05b_reviews.py
npx tsc -b  （frontend/student 与 frontend/teacher）
impeccable detect --json <05B 前端文件>
```

未运行：真实浏览器四视口、真实 PostgreSQL alembic upgrade、真实 OpenHands/DeepSeek 提交链路。本环境没有浏览器自动化工具；Alembic 迁移文件已写好，需在现有 PostgreSQL 实例上执行 `alembic upgrade head` 后才能用于 05A 课堂数据。

## 5. 变更文件

新增：`backend/app/business/reviews.py`、`backend/migrations/versions/20260920_0004_stage05b_submission_review.py`、`backend/tests/business/test_stage05b_reviews.py`、`frontend/student/src/SubmitViews.tsx`、`frontend/teacher/src/ReviewView.tsx`、`reports/STAGE05B-SCOPE.md`、`reports/UI-POLISH-BACKLOG.md`、`reports/STAGE05B-REPORT.md`

更新：`backend/app/business/models.py`、`faq.py`、`service.py`、`stage05_api.py`、学生/教师 `App.tsx`、`types.ts`、`styles.css`

未修改：`uv.lock`、`package-lock.json`、`.env`、OpenHands / LangGraph 版本。

## 6. 未完成项

- Final UI Polish backlog（P-01 至 P-05）
- 真实浏览器 1440/1366/1024/390 与键盘验收
- PostgreSQL 上执行 0004 迁移并用 05A 课堂数据联调
- 任务设计器、成效导出、独立隐藏测试 runner
