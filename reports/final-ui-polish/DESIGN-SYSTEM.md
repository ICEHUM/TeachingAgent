# Final UI Design System

本轮保留学生三栏工作台、教师课堂台、Evidence Drawer、Intervention、提交/评价/复盘的既有信息架构，只统一视觉语法与交互反馈。

## Foundations

| 类别 | 最终规则 |
|---|---|
| 字体 | UI 使用 `Segoe UI Variable Text / Microsoft YaHei UI`；代码和 Snapshot 使用 `Cascadia Code / Consolas` |
| 字号 | 正文 14px；区块标题 15–16px；元信息下限 12px；正式成绩 24px |
| 行高 | 正文 1.6；长指导 1.62–1.68 |
| 间距 | 4 / 8 / 12 / 16 / 24 / 32 / 48px |
| 圆角 | 控件 6px；面板 10px；对话层 14px |
| 控件高度 | 桌面 36px；窄屏触控 44px |
| 边框 | 默认 `#e0e4ea`，强调 `#c3cad5`；只用于分组和可操作边界 |
| 阴影 | 控件仅 1px 轻阴影；浮层使用 18px/48px 阴影；普通内容不悬浮 |
| 焦点 | 2px `#175cd3` 实线外框，2px 偏移 |
| 动效 | 140ms 颜色/边框/阴影过渡；`prefers-reduced-motion` 下缩短到 0.01ms |

## Semantic states

- 信息：淡蓝背景与边框，同时带文字标签。
- 成功：淡绿背景、绿色文字，用于“已满足/已确认/已完成”。
- 等待：淡黄背景、棕色文字，用于“待处理/待评价/等待教师”。
- 危险：淡红背景、红色文字，用于“未满足/恢复失败”。
- 状态不只依赖颜色：Badge、图标、标题和说明文字共同表达。

## Component contracts

- **Button**：主操作为实心蓝；次操作为白底边框；轻操作透明。学生顶部顺序固定为运行检查、创建 Snapshot、保存。
- **Badge**：统一最小高度、圆角、边框和语义色；不使用彩虹色。
- **Tabs**：下划线表示当前项；计数使用紧凑中性标识。
- **Input / Textarea**：统一 36px 控件节奏、边框、placeholder 和 focus；教师理由保持人工确认语义。
- **Drawer / Dialog**：结论优先、证据摘要其次、技术追溯最后；原始结构化输出折叠。
- **Snapshot**：使用等宽字与浅蓝中性底，明确冻结版本。
- **Requirement status**：名称、状态、Snapshot、Evaluator、Evidence 和时间保持可追溯。
- **Score / Teacher review**：未确认显示“待评价”，不使用 0 分占位；自动检查、AI 建议、教师确认分轨呈现。

## Taste detail review

采用了克制的中性色、单一蓝色强调、6–10px 小圆角、轻阴影、紧凑标签和 140ms 微交互。拒绝了渐变、玻璃拟态、发光、插画、巨型 KPI、过多胶囊和落地页式留白；未改变复杂工作台的信息架构。
