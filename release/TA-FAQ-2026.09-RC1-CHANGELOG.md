# TA-FAQ-2026.09-RC1 变更台账

| 原因 | 变更 |
| --- | --- |
| DEMO_BLOCKER | 增加 TEST/DEMO 限定的幂等初始化与 CLI 复位流程。 |
| DEMO_BLOCKER | 增加演示 Preflight，FAIL 时明确禁止演示。 |
| DEMO_BLOCKER | 增加教师/演示管理员健康页数据接口。 |
| BUG | SSE 使用显式 state_version 游标重连，补读断线期间事件。 |
| PILOT_FEEDBACK | 增加试用数据合同、匿名导出、教师记录模板与独立迁移任务夹具。 |
| PILOT_FEEDBACK / DEMO_BLOCKER | 增加 TEST/DEMO 专用登录页与服务端账号校验，学生/教师登录后进入真实工作台。 |
| BUG / PILOT_FEEDBACK | 修正检查版本对当前编辑文件的校验，并将学生端 Snapshot 术语改为可执行的“检查版本”。 |
| BUG / PILOT_FEEDBACK | 修复指导已生成却被学生端隐藏的问题；教练侧栏收敛为检查结论、教练建议和我的发现。 |
| BUG / PILOT_FEEDBACK | 学生端运行结果直接显示检查失败项、运行时异常、文件和行号；完整技术证据保留在抽屉中。 |
| PILOT_FEEDBACK | 运行检查自动保存并固定代码版本；Workspace 增加受限的新建、重命名和删除文件操作。 |
| PILOT_FEEDBACK | 普通练习默认取消基于失败次数的阻塞式教师介入；学生可继续运行、验收并获得最高 L1 分层提示，考核、风险事件及任务显式策略仍保留教师介入。 |
| PILOT_FEEDBACK | 降低政策 FAQ 起步难度：提供关键词筛选脚手架，练习模式允许局部示例；验收证据用中文解释误命中原因并给出下一步。 |
| BUG / PILOT_FEEDBACK | “生成可核验回答”改用真实 answer() 与引用合同检查；明确返回字段、通过标准和填空式教练提示。 |
| SECURITY | 固化演示数据隔离、无 HTTP 重置入口和多租户同 UID 阻断记录。 |

无依赖变化，无核心七页面结构调整，无教学路由、成绩或工具权限合同变化。
