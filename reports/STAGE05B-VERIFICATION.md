# Stage 05B Verification Gate

日期：2026-09-21
范围：现有 05A PostgreSQL 实例、真实 Microsoft Edge、FAQ-001-v1。完成后停止，不进入下一阶段。

## 1. Alembic 真实迁移

实例：`teachingagent-postgres-02b`，PostgreSQL 17.11，绑定 `127.0.0.1:59453`，复用 05A/02B 测试 volume。

- 原始在线升级：`20260920_0003 -> 20260920_0004`，退出码 0；迁移前后的 `attempts=26`、`snapshots=28`、`requirement_results=39`、`teaching_events=64`、`interventions=7` 均未改变。
- Gate 最终幂等复跑：升级前后 revision 均为 `20260920_0004`，退出码 0；当时已有 `35 attempts / 52 snapshots / 76 requirement_results / 64 teaching_events / 7 interventions / 8 submissions / 4 formal_grades`，前后计数完全一致。
- 新表：`rubric_definitions`、`submissions`、`reviews`、`review_items`、`formal_grades`；`teaching_app` 对五表均具备预期 CRUD 权限。

原始证据：`stage05b-migration.json`、`stage05b-gate-migration.json`。

## 2. 旧数据兼容与恢复链路

- 05A Attempt `ef885ba8-cd80-4946-a7a0-65c23f303039` 可读：`state_version=11`、`status=active`、4 个 Snapshot、5 条 RequirementResult、11 条 TeachingEvent。
- Intervention `5aa75d37-349d-4ae7-ada8-dbdbea6a5287` 仍为 `RESOLVED`、`allow_l2=true`。
- `langgraph_checkpoint` 的四张表未被 Alembic 修改，`checkpoints=807`。
- 使用 `AsyncPostgresSaver` 实际恢复最新 checkpoint：thread `attempt:69fdcd0e-580d-47fb-9e6a-3492bbde113f:faq-001-v1`，channel values 存在，checkpoint version 为 4。

## 3. Submission B -> 编辑 C -> 评价仍绑定 B

最终真实浏览器运行：

| 对象 | ID / 结果 |
|---|---|
| Attempt | `143cc20e-e2ac-4eac-aaf5-838696b50da6` |
| Submission | `0a578c24-4c14-482b-a40f-42378adbb338` |
| Snapshot B | `c1edf46a-a6c7-404f-a8a0-044d46b8dac4`，sequence 2，提交冻结版本 |
| Snapshot C | `a097bfe4-4171-43a9-9a76-8ffba1101fe6`，sequence 3，提交后继续编辑 |

真实数据库中 Submission 的 `snapshot_id` 为 B，而该 Attempt 最新 Snapshot 为 C。B 上的 `retrieval_public_tests` 与 `retrieval_observation` 均为 `SATISFIED`；C 上对应结果均为 `NOT_SATISFIED`。教师评价页和正式成绩继续使用 B，C 没有覆盖 B 的证据。

两条 `TEACHER_REVIEW`（`source_quality_review`、`delivery_review`）均绑定 B，并以 Submission 和教师引用作为 evidence refs。

## 4. 教师完整评价与发布

教师在真实 Edge 中打开待复核 Submission，逐项确认教师复核要求，为四项量规填写分数和理由，随后发布。四项得分为 `40 / 25 / 20 / 10`，合计 95。Review：

```text
id                   = 89879b59-9180-426b-a42e-8067ae3ecc48
status               = published
publish_operation_id = ui-publish:42a0a731-625f-4000-b750-c8d4dcdf6014
teacher_id           = d10924f3-b028-4823-936e-a9d3b784705c
```

## 5. formal_grades 真实记录

```text
id            = 1cfe0234-fcb7-41da-bcb1-af3c516a4b96
submission_id = 0a578c24-4c14-482b-a40f-42378adbb338
review_id     = 89879b59-9180-426b-a42e-8067ae3ecc48
total_score   = 95
max_score     = 100
published_by  = d10924f3-b028-4823-936e-a9d3b784705c
count(*)      = 1
```

AI 文案中即使包含分数建议，也没有正式成绩写权限；负向验证中的 AI “100 分”文本没有改变数据库中的 95 分。

## 6. 学生复盘

学生用真实浏览器打开复盘页，顶栏显示“教师已确认 95/100”，并明确评价针对 Snapshot B。四项量规均分成“自动检查 / AI 建议 / 教师确认”三条信息；AI `score` 均为 `null`。

## 7. 负向测试

| # | 场景 | 实测结果 |
|---:|---|---|
| 1 | 同 Snapshot + operation_id 重复提交 | 200，`duplicate=true`，返回同一 Submission |
| 2 | 部分量规未确认发布 | 409 `unconfirmed_rubric_item` |
| 3 | 缺少教师理由发布 | 409 `teacher_reason_required` |
| 4 | AI 输出包含分数文本 | 数据库正式成绩仍为 95 |
| 5 | 重复发布 | 200，正式成绩仍仅 1 行 |
| 6 | 非任课教师读取/发布 | 403 / 403 |
| 7 | 学生伪造 `formal_grade` | 422 `extra_forbidden` |
| 8 | 提交旧 Snapshot | 409 `stale_snapshot` |
| 9 | TEACHER_REVIEW 传入错误 Snapshot | 422；服务端实际写入仍绑定 Submission Snapshot B |

独立负向验证 Attempt：`7bef6b81-9fe4-4dac-bc7f-2fe5a5e829a4`。`freeze_pass=true`，`all_negatives_pass=true`。

## 8. 新页面四视口截图

浏览器：真实 Microsoft Edge（Playwright `channel=msedge`）。视口：`1440x900`、`1366x768`、`1280x800`、`390x844`。

- `stage05b-screenshots/student-submit-*.png`
- `stage05b-screenshots/student-recap-*.png`
- `stage05b-screenshots/teacher-review-*.png`
- `stage05b-screenshots/teacher-review-draft-1440x900.png`

所有截图的 `documentElement.scrollWidth` 均不大于 viewport 宽度；三条复盘信息、Snapshot B 和已发布 95/100 均由浏览器断言通过。未修改 05A 学生三栏骨架、教师课堂台主骨架或 Evidence Drawer 信息架构。

## 9. 测试与构建

- 后端：`46 passed in 6.37s`。
- Ruff：应用、测试和 Gate 脚本通过。
- 前端依赖/构建检查：student、teacher 均通过。
- 学生生产构建：通过；teacher 生产构建：通过。
- 真实 Edge E2E：通过；真实 PostgreSQL Gate record：`passed=true`。

## 10. FAQ-001 量规建议（等待确认，未修改）

FAQ-001-v1 当前是个人任务：每个 Attempt 只有一个 `learner_id`，没有小组、共同编辑或协作贡献事实表。本次评价也没有可用于评价协作的真实证据。

建议将 **“规范与协作 15”** 调整为 **“工程规范与可复现性 15”**，保持 15 分不变，并映射：

- `delivery_static_check`：README、交付文件与可复现说明。
- `source_quality_review`：资料质量、来源组织和可核查性。
- `delivery_review`：教师对最终交付规范的复核。

本 Gate 未自行修改量规名称，等待负责人确认。

## 11. 风险与未完成项

- Gate 为验证冻结、评价与成绩权限，Snapshot B 的通过结果由业务服务写入隔离 Attempt；没有在本 Gate 重新触发 OpenHands。Stage 03/04A 的真实 OpenHands 闭环保持原验证结论。
- 多次排查浏览器测试选择器留下了隔离测试 Attempt/Submission；均位于现有测试实例，不涉及生产数据。正式课堂试用前应提供测试数据清理流程。
- 当前量规第四项名称仍是“规范与协作”；没有负责人确认前不改。
- 仅完成阻断性视觉 QA，Final UI Polish 仍待 05B 验收后另行安排。
- 未进入下一阶段。

## 12. 证据文件

- `stage05b-browser-e2e.json`：真实浏览器动作、四视口和冻结断言。
- `stage05b-gate-record.json`：最终 Submission、Review、formal_grade、B/C RequirementResult。
- `stage05b-real-verification.json`：九项负向验证。
- `stage05b-gate-migration.json`：非空库迁移幂等、旧数据和 checkpoint 恢复。
