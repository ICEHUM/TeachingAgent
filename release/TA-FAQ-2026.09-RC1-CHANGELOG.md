# TA-FAQ-2026.09-RC1 变更台账

| 原因 | 变更 |
| --- | --- |
| DEMO_BLOCKER | 增加 TEST/DEMO 限定的幂等初始化与 CLI 复位流程。 |
| DEMO_BLOCKER | 增加演示 Preflight，FAIL 时明确禁止演示。 |
| DEMO_BLOCKER | 增加教师/演示管理员健康页数据接口。 |
| BUG | SSE 使用显式 state_version 游标重连，补读断线期间事件。 |
| PILOT_FEEDBACK | 增加试用数据合同、匿名导出、教师记录模板与独立迁移任务夹具。 |
| SECURITY | 固化演示数据隔离、无 HTTP 重置入口和多租户同 UID 阻断记录。 |

无依赖变化，无核心七页面结构调整，无教学路由、成绩或工具权限合同变化。
