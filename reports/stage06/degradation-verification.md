# Stage 06 服务降级验证

| 故障 | 验证方式 | 结果 |
| --- | --- | --- |
| DeepSeek 不可用 | MockTransport 主动拒绝连接，调用真实 `DeepSeekTeachingLLM` | PASS：返回 L0 安全模板，`fallback_reason=api_unavailable`，不出现代码答案 |
| OpenHands 不可用 | 将运行时置空，仍通过真实 API 保存代码、创建 Snapshot，再运行检查 | PASS：保存与 Snapshot 为 200；运行检查为 503 `openhands_unavailable`；`student_failure_count` 保持 0 |
| PostgreSQL 不可用 | 应用连接到本机未监听端口并调用 Submission API | PASS：响应为 5xx，不返回 200/201，不伪装提交成功 |
| SSE 断线 | 先写入 `state_version=1` 的业务事件，再以 `since_state_version=0` 重连 | PASS：补读帧包含 `id: 1` 和 `teaching_event`；前端保存最后游标并重连 |

Stage 06 专项降级测试：6 passed。完整后端套件另见最终报告。模型和 OpenHands 故障不改变正式成绩、教学阶段或学生失败计数。
