---
target: Stage 05A teacher classroom
total_score: 25
max_score: 40
na_heuristics: ""
p0_count: 1
p1_count: 3
target_identity: "file:D:\\TeachingAgent\\frontend\\teacher"
timestamp: 2026-09-20T09-51-48Z
slug: frontend-teacher
---
# Stage 05A Impeccable 发布前 Critique

Method: dual-agent (A: `/root/impeccable_design` · B: `/root/impeccable_detector`)

目标：`frontend/student`、`frontend/teacher` 及其真实运行页面。
审查模式：Operate；基准 1440×900，复核 1366×768、1280×800。

## 设计特异性

学生端具有高产品特异性：学习阶段、Workspace、Snapshot、Requirement、Evidence、学生观察和分级指导形成了完整的教学闭环。教师端同样具有高特异性：待介入队列、失败次数、帮助级别、Requirement、Snapshot 差异、教学过程与恢复操作均直接服务课堂干预。

界面没有采用营销页、通用聊天框、AI 渐变或卡片堆叠。三栏学生工作台与列表/侧栏教师工作台在三个目标桌面尺寸下保持稳定。

## Design Health Score

### 学生端：27/40（修正前，Acceptable）

| # | Heuristic | Score | Key issue |
|---|---|---:|---|
| 1 | Visibility of system status | 3 | 保存、运行、连接、旧 Snapshot 状态完整；保存失败恢复动作不足 |
| 2 | Match system / real world | 3 | 教学语言自然，Snapshot/Evaluator 等术语缺少就地解释 |
| 3 | User control and freedom | 2 | 有切换确认和 Esc；缺少运行取消与冲突合并 |
| 4 | Consistency and standards | 3 | 结构一致，少量中英术语混排 |
| 5 | Error prevention | 3 | Snapshot 顺序、hash 与服务端校验扎实 |
| 6 | Recognition rather than recall | 3 | 阶段、验收、下一步持续可见；禁用原因仍可加强 |
| 7 | Flexibility and efficiency | 2 | 支持 Monaco 与 Ctrl+S，其他高频动作无快捷键 |
| 8 | Aesthetic and minimalist design | 3 | 高密度但克制，少量元数据字号偏小 |
| 9 | Error recovery | 2 | 运行错误具体，保存冲突恢复不足 |
| 10 | Help and documentation | 3 | 证据和下一步上下文化，缺系统术语帮助 |

### 教师端：25/40（修正前，Acceptable）

| # | Heuristic | Score | Key issue |
|---|---|---:|---|
| 1 | Visibility of system status | 2 | “实时更新”与实际刷新机制不一致 |
| 2 | Match system / real world | 3 | 课堂分组自然，内部术语仍偏多 |
| 3 | User control and freedom | 2 | 可切换分组/轨迹，空分组与详情上下文曾脱节 |
| 4 | Consistency and standards | 3 | 列表、详情、状态一致 |
| 5 | Error prevention | 3 | 权限、版本校验、安全重试清楚 |
| 6 | Recognition rather than recall | 3 | 学生状态、证据、历史同屏 |
| 7 | Flexibility and efficiency | 1 | 无搜索/排序/快捷导航，自动刷新缺失 |
| 8 | Aesthetic and minimalist design | 3 | 双栏高效，详情纵向较长 |
| 9 | Error recovery | 3 | `RESUME_FAILED` 可重试且操作不丢失 |
| 10 | Help and documentation | 2 | 有操作说明，缺术语帮助 |

## 认知负荷与情绪路径

- 学生端为中等负荷。任务、代码、验收和教练同时可见，但每个单一决策点不超过四个操作；下一步行动持续显示，减少工作记忆负担。
- 教师端为中等负荷。详情侧栏信息较长，智能体轨迹已按需折叠；Requirement、观察、指导和教师操作仍保持一条可解释证据链。
- 学生的正向峰值是“运行检查→看到绑定 Snapshot 的验收证据→阶段推进”；低谷是保存冲突与等待教师。
- 教师的正向峰值是“看到待介入原因→核对 Evidence→授权/恢复→状态变为已恢复”；错误恢复文案保留教师操作，可信度较好。

## 优点

1. Snapshot 与 RequirementResult 严格绑定，旧证据可查但不会伪装成当前验收。
2. “环境异常不计入学习失败”“恢复失败，可安全重试”等文案明确区分学生问题和系统问题。
3. 1440×900、1366×768、1280×800 均保持稳定列关系，没有大面积装饰、渐变或卡片化。

## 优先问题与处理结果

| Priority | Finding | Resolution |
|---|---|---|
| P0 | 页面原先只读取 `user/attempt`，评审入口若使用 `user_id/attempt_id` 会进入缺上下文页 | 已兼容两套参数，并在真实浏览器以别名参数进入完整学生/教师工作台 |
| P1 | 教师端显示“实时更新”，实际只有 online/offline 监听 | 已改为 5 秒自动刷新、显示最近更新时间，并保留断网/重连状态 |
| P1 | 切换到空分组后旧学生详情仍保留 | 已在切换分组时同步清空/选择详情；自动刷新 5 秒后复测仍保持空列表与空详情一致 |
| P1 | 保存失败只依赖短时 toast | 已保留持久“保存失败，代码仍在编辑器中”状态，并提供“重试保存”动作 |
| P2 | 小字号元数据、tablist 方向键、术语解释仍可加强 | 保留为后续可用性改进，不影响 Stage 05A 主闭环 |

正常“等待运行”的感叹号也已改为中性圆点，避免把尚未运行表达成错误。

## Detector 与浏览器证据

- `frontend/student`：detector 返回 1 条 `side-tab` warning，位置为 Evidence Drawer footer 的 `border-left: 2px solid var(--border-strong)`。这是中性说明边界，不是粗彩色卡片侧边，判定为高概率误报。检测器在存在该 finding 时仍返回 exit 0，与参考文档中的 exit 2 约定不一致。
- `frontend/teacher`：0 findings，exit 0。
- 两个目标各只运行一次 detector；没有重跑。
- 正确参数页面已通过独立浏览器 DOM 与 1440×900 截图确认。Impeccable overlay 未注入：当前浏览器 evaluate 为只读，子任务也无法启用 IAB visibility；使用全新隔离浏览器页面的 DOM/截图作为 fallback。

## Persona red flags

- 熟练用户：除 Ctrl+S 外的快捷路径较少，教师端暂无搜索和排序。
- 首次使用者：Snapshot、Requirement、Evaluator 需要按需解释；禁用项原因仍可更显式。
- 无障碍用户：焦点环、文本状态、reduced motion 已覆盖；少量 9–11px 元数据与 tablist 方向键模式仍需后续增强。

## Run Notes

- Target slugs：`frontend-student`、`frontend-teacher`。
- Ignore list：`.impeccable/critique/ignore.md` 不存在。
- Assessment independence：A 在读取 detector 结果前完成；B 独立运行 detector/浏览器证据。
- CLI detector：student 1 warning（判定误报），teacher 0。
- Browser visibility：子任务不可见；主流程使用本机真实浏览器完成 E2E，并另以隔离浏览器截图补充 critique 证据。
- Overlay injection：未执行，受只读 evaluate 限制。
- Live server：未为 critique 另启服务，因此无需清理。
- Temp cleanup：B 创建的临时 profile、DOM、stderr、截图已全部清理。

Questions skipped: 0 unresolved critique decisions; Stage 05A 范围内的 P0/P1 已修正，P2 已记录为后续可用性改进。
