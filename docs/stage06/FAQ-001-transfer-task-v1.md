# FAQ-001 独立迁移任务 v1

场景改为“图书馆设备借用 FAQ”，资料与原实训室问答不同，问题采用同义表达，学生不能直接复用原答案。

- 输入资料：`fixtures/faq-001-transfer-v1/library_equipment.json`
- 目标问题：“借用录音笔需要满足什么条件？”资料原句为“音频采集设备须由完成安全培训的学生预约”。
- 未知问题：“图书馆能否代订高铁票？”必须保持无结果。
- 必须输出来源 URL；不得沿用 FAQ-001 原资料或答案文本。
- 服务端策略：`max_help_level=L0`，只允许 DIAGNOSTIC / EVALUATION，不允许代码补丁。
- 记录字段：`attempt_id`、`snapshot_id`、`operation_id`、`help_level`、`independent_completed`、`evidence_refs`、`evaluated_at`。
- `independent_completed` 只有在变式资料命中、未知问题拒答、来源验证全部满足且没有超过 L0 帮助时为 `true`；未开展为 `null`，不得写成成功。

本文件是合同和测试夹具，不是课堂成效记录。
