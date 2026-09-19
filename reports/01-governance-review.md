# 阶段01前置治理比较记录

比较时间：2026-09-20。

## 比较结论

项目根目录在初始基线提交时没有 `AGENTS.md`、`PRODUCT.md`、`DESIGN.md`。唯一检出的其他 `AGENTS.md` 位于 `.cache/uv` 的第三方依赖中，未读取为项目规则、未修改、未覆盖。

执行包三份治理文件与当前项目方向一致：高职 AI 应用开发实训、FAQ 首任务、LangGraph 教学控制、OpenHands 受限执行、教师确认正式成绩、设计技能不进入教学 Agent、禁止自动启用 hooks。

## 审核后调整

| 文件 | 执行包内容 | 落位调整 | 原因 |
| --- | --- | --- | --- |
| AGENTS.md | 引用执行包内 `docs/01` 和当前 steps | 改为当前仓库存在的 `docs/functional-design-v2.md`、`reports/00-baseline.md` 和当前阶段指令 | 避免根项目出现失效路径 |
| AGENTS.md | 禁止覆盖锁文件和 hooks | 增加新增或升级依赖先报告版本、传递依赖和锁文件差异并等待确认 | 固化负责人本阶段的依赖门槛 |
| PRODUCT.md | 将 v2 验证描述为本次未独立复测 | 改为按阶段00报告区分已复测和历史证据 | 阶段00已经复测依赖和 OpenHands 工作区 |
| PRODUCT.md | 引用执行包内 `docs/01` | 改为当前仓库的 v2 设计和阶段00报告 | 避免失效路径 |
| DESIGN.md | 完整视觉门槛 | 保留语义，统一为当前仓库可独立阅读的表述 | 本阶段不开发 UI，不改变设计决策 |

## Hooks和第三方文件

没有创建 `.git/hooks` 自定义脚本、`.codex/hooks` 或 `.agents/hooks`。项目技能目录中的 Impeccable 参考文档可能提及 hooks，但没有启用任何 hooks。第三方缓存文件保持不变。
