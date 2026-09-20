# FINAL UI POLISH 验收报告

## 范围与结果

本阶段未新增产品功能，未改变 7 个正式页面的核心信息架构。完成统一设计系统、7 页面视觉精修、FAQ-001-v1 第四量规改名、四视口真实浏览器 QA，以及 TEST 专用数据清理机制。

FAQ-001-v1 第四项已由“规范与协作 15分”改为“工程规范与可复现性 15分”，证据仍映射到 `delivery_static_check`、`source_quality_review`、`delivery_review` 和 README/交付文件/可复现说明。迁移 revision 为 `20260921_0005`。

## 7 个核心页面

| 页面 | 1440×900 主截图 |
|---|---|
| 学生工作台 | `student-workbench-1440x900.png` |
| Evidence Drawer | `evidence-drawer-1440x900.png` |
| 教师课堂台 | `teacher-classroom-1440x900.png` |
| Intervention 面板 | `intervention-panel-1440x900.png` |
| 学生提交页 | `student-submit-1440x900.png` |
| 教师评价页 | `teacher-review-pending-1440x900.png`；发布态为 `teacher-review-1440x900.png` |
| 学生复盘页 | `student-recap-1440x900.png` |

每个页面另有 1366×768、1280×800、390×844 截图；教师评价待评价态额外保留 1440×900 证据。

## UI Polish backlog

| ID | 状态 | 处理结果 |
|---|---|---|
| P-01 教师低样本空白感 | 关闭 | 真实清理后仅 1 名待介入学生；增加优先级排序说明，列表与详情维持 46/54 平衡 |
| P-02 Drawer 顶部减重 | 关闭 | 顶部降至 68px；关闭按钮透明化；教学结论成为第一视觉层 |
| P-03 学生右栏长文本 | 关闭 | 指导拆为当前判断、检查方向/建议、下一步行动，并限制阅读行宽 |
| P-04 状态与元信息统一 | 关闭 | 状态色、Badge、Snapshot、待评价/已确认和 12px 元信息下限统一 |
| P-05 间距/字号/边框收敛 | 关闭 | 使用统一 spacing/radius/control-height，去除普通内容阴影和重复边框 |

## Accessibility 与四视口 QA

- 1440×900、1366×768、1280×800、390×844 均无页面横向溢出。
- 7 个页面的可见交互控件无空名称；键盘路径焦点可见。
- 正式页面没有低于 12px 的可见文本。
- Evidence Drawer 支持 Escape 关闭和焦点返回；窄屏 Intervention 使用对话层。
- 状态均同时使用文字/标识和颜色；支持 `prefers-reduced-motion`。
- 详细机器记录：`core-browser-qa.json` 与 `browser-e2e.json`，两者 `passed: true`。

## 真实浏览器 E2E

真实流程再次通过：Snapshot B → 学生提交 → 形成 Snapshot C → 教师仍评价冻结的 Snapshot B → 四项量规逐项确认并填写理由 → 发布 95/100 → 学生打开复盘页。自动检查、AI 建议、教师确认三类信息分开，AI 建议没有正式成绩权限。

## 数据与回归验证

- 非空 PostgreSQL 升级到 `20260921_0005`；原 Attempt/Intervention、业务计数与 807 条 checkpoint 在迁移时保持一致，checkpoint 可恢复。
- TEST 清理：10 个 Gate/E2E Attempt、30 个 Snapshot、10 个 Submission、6 个 Review、24 个 ReviewItem、6 个 FormalGrade 与 49 个 RequirementResult 已清理；原 05A 教学链路保留。
- 清理脚本仅允许 `TEACHING_ENV=TEST`、PostgreSQL、loopback host，并拒绝 production 命名数据库；实际删除还要求 `--confirm DELETE_STAGE05_TEST_DATA`。
- 后端：`uv pip check` 通过；46 个测试全部通过。
- 前端：依赖/版本检查通过；学生端与教师端生产构建通过。
- Ruff：全部通过。
- Impeccable detector：`[]`。

## 剩余风险

- 390×844 按既定产品边界只保证查看、指导、求助和介入，完整代码编辑仍以桌面端为主。
- 浏览器自动化使用本机 Edge/Chromium；未做 Firefox、Safari 实机矩阵。
- Gate/E2E 数据已按要求清理，因此提交/评价/复盘截图是保留的验收证据；再次现场演示需重新运行受保护的测试夹具。
- 投影可读性基于 1440×900 截图缩放审查，尚未覆盖具体教室投影设备的色彩与锐度差异。
