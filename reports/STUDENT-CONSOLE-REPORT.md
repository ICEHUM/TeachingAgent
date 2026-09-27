# 学生控制台与报错定位 完成报告

日期：2026-09-21
范围：学生端"运行我的程序 / 原始输出面板 / 报错定位跳转"三项，以及为此需要的服务端自运行契约
边界：未新增页面、未改动已确认的三栏信息架构与 Evidence Drawer 层级、未改量规与成绩合同、未加交互式终端

## 1 为什么做这三项

`DESIGN.md` 与 `docs/functional-design-v2.md` 5.1 已经写明"结果区切换**检查、输出和 FAQ 预览**"，实现只做了"运行结果 / 验收"两栏。`backend/app/agent/tools.py` 里 `run_student_program` 与 `inspect_runtime_error` 早已在服务端工具目录和策略白名单中，但产品 API 从不请求它们：`stage05_api.py` 把学生的唯一运行动作硬编码为 `run_faq_tests`。因此学生写成什么样都只能拿到布尔验收结论，没有"自己跑一次看输出"的路径，第一反应只能是问教练。

本次补的是**运行时**，不是新设计语言。

## 2 服务端契约变更

| 变更 | 位置 | 说明 |
|---|---|---|
| `failure_counting_capabilities: ["EVALUATION"]` | `teaching_control/state.py`、`business/faq.py` | 只有评分类工具（`run_faq_tests`、`validate_*`）的失败才计入重复失败阈值；DIAGNOSTIC 自运行失败只记录状态，不计数 |
| `self_service_tools: ["run_student_program", "inspect_runtime_error"]` | `business/faq.py` | 服务端拥有的自运行白名单，同时用于 API 校验与前端按钮 |
| 两个新策略键由服务端强制 | `business/stage03.py` | 与 `allowed_tools`、`tool_capabilities` 一样，任务版本里存的值不能覆盖 |
| 事件负载增加 `tool_name` | `teaching_control/graph.py` | 供教学时间线区分自运行与验收检查 |
| 时间线文案 | `business/stage05_api.py` | 自运行显示"学生自己运行程序（正常结束/报错）"，不再冒充"运行检查未通过" |
| 非结构化工具记录退出码 | `agent/tools.py` | 证据 artifact 的 `details.exit_code`，控制台显示真实退出码 |

自运行的三条安全性质（均由测试与浏览器实测覆盖）：

1. 不写 `RequirementResult`：`REQUIREMENT_TOOL` 映射不含自运行工具，成绩类事实不会被污染。
2. 不增加 `student_failure_count`：学生跑崩自己的程序不会被送进教师介入队列。
3. 不触发自动模型追问：端点以 `automatic_followup=False` 调用教学运行时，失败不会自动生成 AI 建议。

## 3 API

| 动作 | 路径 | 说明 |
|---|---|---|
| 自运行 | `POST /api/product/attempts/{id}/program-runs` | 入参 `operation_id`、`snapshot_id`、`expected_state_version`、`tool`；`tool` 必须在服务端白名单内，否则 422 `tool_not_allowed` |
| 工作台暴露白名单 | `GET /api/product/attempts/{id}/workbench` | 新增 `self_service_tools: [{name,label}]`，前端不硬编码工具名 |

返回体为有界控制台载荷：`tool`、`label`、`status`、`code`、`exit_code`、`stdout`（8192 字符上限）、`stderr`（4096 字符上限）、截断标记、`location`（文件与行号）、`snapshot`、`operation`、`recorded`、`state_version`。

## 4 前端

- 结果区新增**输出**标签（`运行结果 / 输出 / 验收`），工具栏新增次级主操作"运行我的程序"。
- 控制台原样显示 stdout / stderr，不做润色；无输出时明确说明原因并给出 `if __name__ == "__main__":` 的写法提示；失败时显示真实退出码与可达失败态样式。
- 运行结果与证据抽屉里的"定位 文件 第 N 行"改为可点击：切换到出错文件、把光标放到该行并滚动到视野内、焦点回到编辑器。
- 复用 `frontend/shared/tokens.css` 变量与既有控件样式，未引入第二套色板或圆角。

## 5 测试与构建

| 命令 | 结果 |
|---|---|
| `uv run pytest -q`（backend，PYTHONPATH=backend，TMP/TEMP 指向 `.runtime/pytest-tmp`） | 71 passed in 13.41s（原 60，新增 11） |
| `uv run ruff check app tests` | 本次改动文件全部通过；`tests/business/test_real_scenario_import.py` 的 I001 为改动前既有问题，未处理 |
| `cmd /c npm run check:frontend` | 学生端、教师端 Vite 生产构建均通过 |

新增测试：

- `tests/teaching_control/test_graph.py`：DIAGNOSTIC 自运行失败不计数且不触发指导；EVALUATION 失败照常计数；旧策略缺少新键时按默认规则计数。
- `tests/business/test_student_program_runs.py`：控制台载荷与 `automatic_followup=False`；白名单外工具 422；非本人 Attempt 403；过期 Snapshot 409；多余字段（`formal_grade`）422；工作台白名单与标签一致性；自运行工具必须属于不计数的能力。

## 6 真实浏览器验证

脚本：`scripts/student-console-browser-qa.py`，真实 Microsoft Edge（Playwright `channel=msedge`），演示学生账号登录，**32 项检查全部通过**，记录见 `reports/student-console/qa-notes.json`。

关键实测：

| 检查 | 实测值 |
|---|---|
| 自运行正常结束 | "程序正常结束"，退出码 0，stdout 为 `sources: 5 / hits: 5` |
| 成功自运行不改计数 | 6/0 → 6/0 |
| 失败自运行不改计数 | 6 → 6（同一次会话内 3 次自运行） |
| 报错原样可见 | `NameError: name 'retrieve_answer' is not defined` |
| 定位跳转 | 按钮"跳到 faq_app.py 第 55 行"；点击后活动文件切换为 `faq_app.py`，光标进入编辑器，行号 55 出现在可见行内 |
| 时间线区分 | "学生自己运行程序（正常结束） / （报错）"，不再显示为运行检查 |
| 验收链路未回归 | "检查未通过"＋五项检查列表；计数器 6 → 7（评分失败照常计数） |
| 键盘可达与可激活 | Tab 序列 `提交 > 收起任务 > 运行我的程序`，回车启动运行并到达终态 |
| 四视口 | 1440×900、1366×768、1024×768、390×844 均无页面级横向溢出，输出面板正常渲染 |

截图（`reports/student-console/`）：`console-empty-1440x900.png`、`console-run-1440x900.png`、`console-error-1440x900.png`、`console-located-1440x900.png`、`acceptance-run-1440x900.png`、`console-1440x900.png`、`console-1366x768.png`、`console-1024x768.png`、`console-390x844.png`。

## 7 验证过程的环境与数据影响

- 学生端 Vite dev server 未感知外部写入的源文件，改动后必须重启 dev server，否则会服务旧模块（本次踩到一次，表现为运行时 `ConsoleView is not defined`）。
- 演示 Attempt `d9486e06-7e74-5862-bd71-8f96bfc64359` 被验证写入：`student_failure_count` 由 3 增至 8（来自 5 次评分检查失败，自运行未产生影响）、`state_version` 到 34、Snapshot 由 2 个增至 10 个，另有多条 TeachingEvent 与证据 artifact。
- 为让验证可重复，脚本以演示教师身份两次处理了 `failure_threshold_reached` 介入（等价于课堂中的真实教师动作）。验证结束时没有遗留 `WAITING_TEACHER` 介入。
- 未执行 `stage06_demo.py reset`：它会清空 `DEMO-FAQ-001-RC06` 命名空间，连 06B 导入的 POLICY 场景 Attempt 一起删除。是否复位、是否需要单独的 QA 数据清理流程，请负责人决定。

## 8 未完成项与风险

1. 输出面板高度受结果区限制，1440×900 下 stdout 与 stderr 需要面板内滚动；建议列入 UI Polish 收尾（P-06），不改信息架构。
2. `FAQ-001-v1` 的旧起始代码（`scripts/stage05a-seed.py`、`fixtures/faq-001-transfer-v1`）只有函数定义，没有 `__main__` 演示块，"运行我的程序"会显示空输出。演示用的 POLICY 场景自带 `__main__`，开箱可用。是否给旧起始代码补演示块属于任务内容变更，等待负责人确认。
3. 自运行不计入失败阈值，意味着"反复自己跑崩"不会进入教师"需要关注"队列；如需该信号，应另设独立的诊断失败信号，本次未做。
4. 交互式终端/REPL 与断点调试未做：前者会突破当前"固定工具目录"的试点安全边界，需要单独评估容器与资源合同。
5. 浏览器矩阵仅 Chromium/Edge；未做 Firefox、Safari 实机；未做真实教室投影可读性确认。
6. 自运行每次都会固定一个新的检查版本（Snapshot），一节 40 人的课会积累大量 Snapshot 目录，尚无保留与清理策略（沿用既有风险）。
