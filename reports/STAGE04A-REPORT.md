# Stage 04A — 真实学习成功闭环与 TeachingLLM 报告

验证时间：2026-09-20（Asia/Shanghai）  
范围：仅 FAQ-001-v1；未开发正式 UI，未扩展其他课程或通用 Coding Agent。

## 1. Snapshot A → Snapshot B 成功时间线

真实 Attempt：`e4cfd8bf-7dae-4161-a073-83ddc919d499`

| state_version | Snapshot | 事件与事实 | 服务端决策 |
|---:|---|---|---|
| 1 | A `e2a7e4e5-6674-4fe7-a51c-eee219e7f002` | `run_faq_tests` 返回 `empty_retrieval` | student failure 1 |
| 2 | A | 真实 DeepSeek 根据同一 Evidence 生成指导 | L0 |
| 3 | A | 学生提交有效观察并再次运行，仍为 `empty_retrieval` | student failure 2 |
| 4 | A | 真实 DeepSeek 根据第二次 Evidence 生成指导 | L1 |
| — | — | 测试夹具模拟学生本人编辑 `faq_app.py`；没有调用 AI 修改代码 | 创建 Snapshot B |
| 5 | B `b6e94682-7f1f-42ef-be21-f2fc095cd750` | `run_faq_tests` 全部通过；学生观察经服务端长度规则验证 | 两项 Requirement 均 SATISFIED |
| 6 | B | RequirementEvaluator 只聚合 Snapshot B | `implement_retrieval → generate_cited_answer` |

学生修改由 `scripts/stage04a-real-flow.py` 中明确的测试夹具写入 source 目录，再创建不可变 Snapshot B。DeepSeek 与 OpenHands 都没有获得修改学生代码的工具权限。

## 2. RequirementResult 前后变化

| Snapshot | Requirement | 结果 | Evaluator |
|---|---|---|---|
| A | `retrieval_public_tests`（第一次） | NOT_SATISFIED | `openhands:run_faq_tests:v1` |
| A | `retrieval_public_tests`（第二次） | NOT_SATISFIED | `openhands:run_faq_tests:v1` |
| A | `retrieval_observation` | SATISFIED | `server:student_explanation:v1` |
| B | `retrieval_public_tests` | SATISFIED | `openhands:run_faq_tests:v1` |
| B | `retrieval_observation` | SATISFIED | `server:student_explanation:v1` |

Snapshot A 的三条结果和 Artifact 仍保留。聚合查询带有最新 `snapshot_id` 条件，因此 A 的结果不会参与 B 的验收。

## 3. advance_stage 证据

Snapshot B 的两个必需 Requirement 均满足后，服务端聚合返回：

```json
{
  "stage_requirements_met": true,
  "stage_assessment": {
    "passed": true,
    "outcome": "NEXT",
    "reason": "Current stage requirements passed."
  },
  "advanced_to": "generate_cited_answer",
  "flow_status": "READY_FOR_NEXT_STAGE"
}
```

数据库 TeachingEvent 记录了 `advance:stage04-passed-eeaf2172:3`。客户端没有提交或设置 `stage_requirements_met`，OpenHands 也没有返回阶段推进布尔值。

## 4. teacher interrupt → resume 时间线

真实 Attempt：`32141d77-8ea3-4b41-98b5-8bb0bd45d766`  
Intervention：`66be2698-3562-42b2-a449-2118030eba1d`

| state_version | 事件 | 结果 |
|---:|---|---|
| 1–2 | 第一次失败及自动指导 | L0 |
| 3–4 | 有效观察后的第二次失败及自动指导 | L1 |
| 5 | 第三次真实 OpenHands 失败 | failure threshold 达到 3 |
| — | Intervention 业务事实先写入，再写 PostgreSQL checkpoint | WAITING_TEACHER |
| — | 授权教师处理，允许 L2 | WAITING_TEACHER → RESUMING |
| 6 | `Command(resume)` 重新验证教师课程成员关系、策略、Intervention 与 state_version | 真实 DeepSeek 生成 L2；业务状态 RESOLVED |

恢复后实际产生新的 TeachingEvent，操作没有停留在 interrupt 单测层面。教师响应与 `allow_l2=true` 保存在业务数据库。

## 5. TeachingLLM 合同

LangGraph 仍在调用模型前确定 `mode`、`level`、`kind`、stage、工具权限和 Evidence 白名单。DeepSeek 只允许返回：

```text
GuidanceOutput
├── message: 1..800 chars
├── evidence_refs: allowed input references only
├── next_step: 1..300 chars
└── uncertainty: low | medium | high
```

服务端拒绝额外字段，因此模型不能提交 `help_level`、`teaching_mode`、`stage`、`route`、`formal_grade`、`tool_capability` 或代码补丁权限。模型输出还需通过 Evidence 白名单、代码/答案模式和 assessment 规则校验。失败时适配器返回固定安全模板，不向图抛出模型服务异常。

每次运行保存两类未提交到 Git 的审计 Artifact：

- `model-raw://...`：模型原始结构化响应，不包含提示词、API key 或隐藏思维链。
- `model-audit://...`：provider、model、request id、latency、token usage、成功状态、fallback reason、级别、Evidence 和最终展示文本。

## 6. L0 与 L1 真实输出样例

L0：

> 检索结果为空，先别急着改代码。请描述一下：你的查询请求经过哪几个环节？每个环节实际收到了什么输入、又返回了什么？你觉得最可能在哪个环节“断流”？

L1：

> 检索环节出现空结果。请先描述：你传给检索的查询内容是什么？数据源或索引是否已成功加载？当前过滤条件是否过严？先缩小到 query、索引加载或过滤条件中的哪一环导致未命中。

两条均由 `deepseek-flash` 真实生成。L0 没有定位具体修复代码；L1 定位到检索输入、索引加载和过滤条件检查方向，但没有给出完整实现或代码补丁。

## 7. 模型失败与 fallback 测试

| 情形 | 预期 fallback_reason | 结果 |
|---|---|---|
| 请求超时 | `timeout` | 通过 |
| 非法 JSON / schema | `invalid_structure` | 通过 |
| 不存在的 Evidence | `invalid_evidence_ref` | 通过 |
| 返回 `help_level=L2` 等控制字段 | `help_level_violation` | 通过 |
| API 503 | `api_unavailable` | 通过 |
| assessment 给完整答案/替换指令 | `assessment_policy_violation` | 通过 |

这些路径均返回可理解的安全模板，不影响学生保存代码或继续调用 OpenHands 测试。

## 8. assessment 真实 DeepSeek 验证

真实请求由服务端固定为 `mode=assessment`、`level=L0`、`kind=question`。输出只询问查询参数、索引存在性和空结果现象，没有代码补丁或直接答案。模型返回的 Evidence 引用属于输入白名单，审计 Artifact 已生成。

## 9. 测试与依赖

- DeepSeek 连通、文本 completion 与 tool-call schema：通过。
- 真实 PostgreSQL + OpenHands + DeepSeek 成功闭环：通过。
- 真实 Intervention resume + L2：通过。
- 真实 assessment 调用：通过。
- Stage 04A 聚焦测试：23 passed；完整 backend 测试：42 passed；Ruff：通过。
- Stage 04A 未新增、升级、降级或删除依赖；OpenHands 保持 1.49.2，LangGraph 保持 1.2.11，`uv.lock` 不变。

完整机器结果：`reports/stage04a-real-verification.json`。

## 10. 风险与未完成项

- 固定 OpenHands 1.49.2 镜像中 Agent Server 与学生进程仍使用同一 UID；这是正式多租户课堂试用前的阻断项，本阶段按要求未制作定制镜像。
- 模型内容策略目前使用严格结构校验和保守模式检测。正式课堂试用前还需要建立教师抽检样本、误拒率指标、按课程版本管理的提示模板和成本预算告警。
- 模型审计 Artifact 当前保存在宿主受控目录，尚未实现长期保留期、加密和对象存储归档。
- 本阶段只覆盖 FAQ-001-v1，未实现正式 UI、其他课程、YOLO 或通用 Coding Agent。
