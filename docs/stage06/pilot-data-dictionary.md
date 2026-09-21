# Stage 06 课堂试用数据合同

本合同只定义真实试用后可以采集的事实，不包含推断出的学习成效，也不把 DEMO 数据当作课堂结果。

| 字段 | 来源 | 含义 | 匿名导出 |
| --- | --- | --- | --- |
| learner_key | User.id | 由导出盐进行 HMAC-SHA256 假名化的学生标识 | 是 |
| attempt_key | Attempt.id | 假名化任务尝试标识 | 是 |
| task_version | TaskVersion | 固定 `FAQ-001-v1` | 是 |
| started_at / ended_at | Attempt / TeachingEvent | 开始与实际结束时间；未结束留空 | 是 |
| requirement_satisfied / total | RequirementResult | 当前 Snapshot 下服务端验收聚合事实 | 是 |
| guidance_levels / count | TeachingEvent | 实际发生的提示等级与次数 | 是 |
| teacher_interventions | Intervention | 教师介入次数 | 是 |
| snapshot_count | Snapshot | 学生代码版本变化次数 | 是 |
| transfer_result | TRANSFER_TASK RequirementResult | 独立变式任务是否完成；未开展留空 | 是 |
| teacher_score | FormalGrade | 教师发布后的正式成绩；未发布留空 | 是 |
| student_feedback | 试用后结构化问卷 | 学生自愿反馈；不采集敏感个人信息 | 脱敏后 |

导出不包含姓名、邮箱、原始用户 ID、代码全文、模型密钥、宿主路径或完整日志。`PILOT_EXPORT_SALT` 至少 16 个字符，并由试用负责人保管；同一批次使用同一盐以便纵向关联，不同学校应使用不同盐。
