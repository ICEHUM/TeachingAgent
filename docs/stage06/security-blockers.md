# Stage 06 安全边界与阻断项

## 正式多租户阻断项

OpenHands Agent Server 与学生进程当前仍使用同一容器 UID。该项在“正式开放多租户任意代码课堂”前必须解决；Stage 06 仅允许固定 FAQ、固定工具目录与小规模受控试用。

## Stage 06 继续执行的限制

- Snapshot 只读挂载，Attempt 工作区隔离。
- Docker socket、DeepSeek 主密钥和系统密钥不进入学生容器。
- internal Docker network；CPU 1、内存 1 GiB、PID 128、启动/执行超时和输出上限继续生效。
- 工具能力只开放 FAQ-001 所需 DIAGNOSTIC / EVALUATION。
- 业务数据库是事实权威，checkpoint 只用于恢复。
- DEMO 初始化与重置拒绝生产、非本机数据库；重置只通过 CLI。
