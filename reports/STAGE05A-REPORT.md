# Stage 05A 完成报告

日期：2026-09-20
范围：FAQ-001-v1 学生实训工作台、教师课堂工作台
边界：未开发 Stage 05B、任务设计器、成绩系统、其他课程、YOLO、通用 Agent、语音或完整移动 IDE。

## 1. 截图

- 学生工作台（发布前修正后，1440×900）：`stage05a-screenshots/student-workbench-post-critique-1440x900.jpg`
- 学生工作台（1366×768）：`stage05a-screenshots/student-workbench-1366x768.png`
- 学生工作台（1280×800）：`stage05a-screenshots/student-workbench-1280x800.png`
- 教师课堂台（发布前修正后，1440×900）：`stage05a-screenshots/teacher-classroom-post-critique-1440x900.jpg`
- Evidence Drawer：`stage05a-screenshots/evidence-drawer-1440x900.jpg`
- Intervention 侧栏：`stage05a-screenshots/intervention-panel-1440x900.jpg`
- 智能体运行轨迹：`stage05a-screenshots/agent-trace-1440x900.jpg`
- L0 真实指导：`stage05a-screenshots/student-l0-1440x900.jpg`

## 2. 页面信息架构

### 学生工作台

```text
课程 / 任务 / 连接与保存状态
├─ 左：任务目标、六阶段、当前验收进度
├─ 中：文件树、Monaco、运行结果 / 验收 / 成果预览
└─ 右：当前观察、证据、本轮指导、学生观察、下一步行动
                         └─ Evidence Drawer（脱敏、有界、可追溯）
```

右栏是结构化实训教练，不是通用聊天界面。运行、指导和验收全部引用当前 Attempt、Stage、Snapshot 和 Evidence。

### 教师课堂台

```text
课程 / 任务 / 自动更新时间
├─ 左：需要关注 / 进行中 / 已完成
│      └─ 学生、阶段、原因、失败次数、帮助级别、等待时间
└─ 右：Requirement、Evidence、学生观察、指导历史、Snapshot 差异
       ├─ 教学过程
       ├─ 智能体运行轨迹
       └─ 继续 / 允许 L2 / 暂停 AI / 恢复流程
```

教师列表只读业务数据库；教师操作调用真实 Intervention/resume API。

## 3. Design tokens

| Token family | Values |
|---|---|
| spacing | 4 / 8 / 12 / 16 / 24 / 32 / 48 px |
| radius | control 6px / panel 10px / dialog 14px |
| border | `#d8dee8` / strong `#b7c0ce` |
| surface | canvas `#f6f8fb` / panel `#fff` / subtle `#f1f4f8` / selected `#e8f0ff` |
| text | primary `#0f172a` / secondary `#475569` / muted `#64748b` |
| accent | `#1d4ed8`, hover `#1e40af`, soft `#e8f0ff` |
| semantic | success `#18794e` / warning `#9a6700` / danger `#b42318` |
| focus | 2px `#2563eb`, 2px offset |
| shadow | 仅浮层：`0 12px 30px rgba(15,23,42,.14)` |
| motion | 120–180ms；`prefers-reduced-motion` 降至 0.01ms |

## 4. 真实 API 映射

| UI action | API | Authoritative source |
|---|---|---|
| 打开学生工作台 | `GET /api/product/attempts/{attempt_id}/workbench` | PostgreSQL Attempt/Stage/RequirementResult/TeachingEvent/Intervention |
| 读取文件 | `GET /api/product/attempts/{attempt_id}/files/{path}` | Attempt 独立 Workspace |
| 保存文件 | `PUT /api/product/attempts/{attempt_id}/files/{path}` | Workspace + hash CAS |
| 创建 Snapshot | `POST /api/product/attempts/{attempt_id}/snapshots` | Workspace snapshot + OperationLedger |
| 运行检查 | `POST /api/product/attempts/{attempt_id}/runs` | LangGraph → OpenHands → RequirementEvaluator |
| 请求指导 | `POST /api/product/attempts/{attempt_id}/guidance` | LangGraph 决策 + TeachingLLM 表达 |
| 打开证据 | `GET /api/product/attempts/{attempt_id}/evidence/{operation_id}` | RequirementResult + Evidence artifact |
| 学生事件流 | `GET /api/product/attempts/{attempt_id}/stream` | TeachingEvent SSE，断线 2 秒重连 |
| 教师课堂列表 | `GET /api/product/teacher/classroom` | 业务数据库，5 秒自动刷新 |
| 教师学生详情 | `GET /api/product/teacher/attempts/{attempt_id}` | 业务事实与脱敏事件时间线 |
| 教师处理/恢复 | `POST /api/product/teacher/interventions/{id}/action` | Intervention + 权限/策略/state_version + Command(resume) |

前端不保存模型 key、完整 stdout、宿主路径、容器 token 或完整大日志。

## 5. 浏览器 E2E

真实浏览器、真实 PostgreSQL、真实 LangGraph checkpoint、真实 OpenHands Workspace 和真实 DeepSeek 已完成：

1. Snapshot A 运行失败，得到 `empty_retrieval` Evidence 与 L0“引导”。
2. 学生提交有效观察后再次运行，仍失败并得到 L1“定位”。
3. 在 Monaco 中由浏览器真实编辑 `faq_app.py`，使用 Ctrl+S 保存。
4. 创建 Snapshot B；界面明确把 Snapshot A 标为旧证据。
5. Snapshot B 运行真实 OpenHands FAQ tests，两项必需 RequirementResult 均为 `SATISFIED`。
6. RequirementEvaluator 只聚合 Snapshot B，服务端写入 `stage_advanced`，阶段从 `implement_retrieval` 推进到 `generate_cited_answer`。
7. 独立学生第三次失败后产生 WAITING_TEACHER；教师在真实课堂台授权“局部示例”，Graph resume 后 Intervention 为 RESOLVED，并生成真实 L2 指导。

持久化断言文件：`stage05a-e2e-verification.json`。关键结果：

- Snapshot A：`255ecbcd-fefb-49db-8d0d-fabf4a924dbb`
- Snapshot B：`90c3fbbb-7981-4de7-80fd-b7352756aa67`
- 当前阶段：`generate_cited_answer`
- 学生 Attempt state_version：6
- 教师 Intervention：RESOLVED、`allow_l2=true`
- 5 项 E2E 持久化断言全部 PASS。

## 6. 状态与异常 QA

| State | Verification |
|---|---|
| Workspace 启动中 / loading | 浏览器初始载入看到 Monaco loading；骨架与编辑器占位均存在 |
| empty | 教师“需要关注 0”真实空状态；自动刷新后仍保持列表/详情一致 |
| save / saving / failed / conflict | 真实 Ctrl+S；hash CAS 测试；失败状态持久显示且可重试 |
| queued / running / timeout / OpenHands unavailable | UI 独立文案与状态；后端 timeout/infrastructure 分类回归测试 |
| slow model / model timeout / fallback | 非阻塞生成状态与安全 fallback 文案；Stage 04A 模型失败合同继续通过 |
| old Snapshot | 浏览器真实 Snapshot A→B 显示“旧证据不参与当前验收” |
| Intervention / resume / resume failure | 真实 WAITING_TEACHER→RESOLVED；失败保留并可幂等重试 |
| network offline / SSE reconnect | 学生 SSE 重连状态与 2 秒重试；教师自动刷新显示最近更新时间 |
| long text | 指导、观察、operation、artifact 均有 wrap/scroll 上限，真实长 L2 文本已检查 |

入口兼容 `user/attempt` 与 `user_id/attempt_id`，两种形式均已在真实浏览器进入完整页面。

## 7. Accessibility

- Monaco 真实键盘路径与 Ctrl+S 已验证。
- 交互控件均使用原生 button/input/textarea；教师提示有显式 label。
- Evidence Drawer 使用 `role=dialog`、`aria-modal`、自动聚焦关闭按钮和 Escape 关闭。
- 运行/保存/等待/成功/错误均同时使用文字或符号，不只依靠颜色。
- 全局 `:focus-visible` 为 2px 高对比焦点环。
- `prefers-reduced-motion` 禁止持续动效。
- 学生与教师身份头像均有 aria-label；Tab 和分组有 ARIA 当前状态。
- 1440×900、1366×768、1280×800 无页面级横向溢出。

已知后续项：tablist 方向键模式、少量 9–11px 元数据、复杂术语就地解释和更多高频快捷键。

## 8. Impeccable / Taste

- 编码前记录：`STAGE05A-VISUAL-REVIEW.md`
- Impeccable 双审查：`STAGE05A-IMPECCABLE-CRITIQUE.md`
- Taste 复核：`STAGE05A-TASTE-REVIEW.md`
- Impeccable 存档：`.impeccable/critique/2026-09-20T09-51-48Z__frontend-student.md`、`.impeccable/critique/2026-09-20T09-51-48Z__frontend-teacher.md`

修正了 P0 路由兼容和三个 P1：教师自动刷新承诺、空分组详情同步、保存失败持久恢复。Detector：教师端 0；学生端 1 条 Evidence footer 中性边界误报。

## 9. 依赖与验证

新增直接前端依赖：

- `@monaco-editor/react==4.7.0`
- `monaco-editor==0.56.0`

新增锁定传递依赖：`@monaco-editor/loader 1.7.0`、`state-local 1.0.7`、`dompurify 3.4.8`、`marked 14.0.0`、`@types/trusted-types 2.0.7`。Python `pyproject.toml` 和 `uv.lock` 无变化；OpenHands 四组件仍为 1.49.2，LangGraph 仍为 1.2.11。

| Check | Result |
|---|---|
| Backend pytest | 44 passed |
| Ruff | All checks passed |
| `uv pip check` | 220 packages compatible |
| Student production build | passed |
| Teacher production build | passed |
| Existing `check:frontend` | both workspaces passed |
| OpenHands/LangGraph version assertion | passed |
| Real DB E2E assertions | 5/5 passed |

## 10. 风险与未完成项

- `X-User-Id` 仍只允许 DEV/TEST；正式课堂需要接入可信身份认证。
- Agent Server 与学生进程不同 UID 仍是多租户试用前阻断项。
- 教师端采用 5 秒业务数据库轮询；学生教学事件使用 SSE。正式高并发课堂需评估教师级事件流与退避策略。
- 当前只实现 FAQ-001-v1；不包含任务设计器、成绩系统和其他课程。
- 1280px 以下只保证任务、指导和求助可用，不提供完整移动 IDE。
- DeepSeek 或 OpenHands 不可用时产品可继续保存/编辑，但指导或运行进入明确降级状态。

Questions skipped: 0 unresolved Stage 05A decisions; implementation and required QA are complete.
