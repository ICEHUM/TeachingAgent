# Stage 06 小规模试用操作手册

## 课前

1. 仅在固定 FAQ-001-v1、固定工具目录的小规模受控环境使用。
2. 设置 `TEACHING_ENV=TEST`（或隔离的 DEMO）、业务库与 checkpoint 连接；不要把密钥写入仓库。
3. 运行 `python backend/scripts/stage06_demo.py init --local-stage02b-config`。
4. 启动 API、学生前端和教师前端。
5. 运行 `python backend/scripts/stage06_preflight.py --local-stage02b-config --output reports/stage06/preflight.json`。只在 `demo_ready=true` 时开始。

## 课堂中

教师首先查看“需要关注”，记录实际介入与异常。DeepSeek 降级时学生仍可编辑、保存、创建 Snapshot 和运行自动检查；OpenHands 不可用时停止新的运行检查，已有证据仍可查看且不累计学生失败；PostgreSQL 不可用时停止新的提交和评价。SSE 断线后客户端携带最后游标重连并补读事件。

## 课后

填写教师记录模板。设置独立导出盐后执行：

`python backend/scripts/export_pilot_data.py --course-code <课程代码> --output <文件.csv>`

演示复位只使用命令行：

`python backend/scripts/stage06_demo.py reset --local-stage02b-config --confirm RESET_DEMO_RC06`

脚本只处理 `DEMO-FAQ-001-RC06` 命名空间，并拒绝非 TEST/DEMO、非本机或 production 命名数据库。没有公开 HTTP 重置接口。
