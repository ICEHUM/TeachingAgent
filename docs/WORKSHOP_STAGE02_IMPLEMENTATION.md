# 智训工坊 · PYB-01 纵向切片实施记录

日期：2026-09-23
状态：真实业务 API 全链路验收通过；阶段 02 完成。

本阶段只实现已确认的 Python 基础编程首题“两数相加”，保留旧 FAQ 回归场景和现有技术栈。学生登录优先进入新示例任务，看到题面和 `main.py` 起始代码；保存会生成 Snapshot。公开 Run 对输入 `2,3` 与 `0,7` 逐例运行，展示输入、预期、实际与错误。Submit 要求同一 Snapshot 的公开检查已通过，然后运行边界用例；未通过时不建立 Submission，所有用例通过且证据写入业务数据库后才可提交。教师课堂列表显示任务名，可进入学生详情和证据。

公开与边界用例的标准输出由服务端比较，预期答案不会进入学生工作区或执行命令。学生端和 Tutor 只能读取公开检查详情或边界检查的通过状态；正式成绩仍需教师确认。任务权限仍由服务端固定配置控制，不能从任务版本 JSON 扩张可执行工具。

已验证：Ruff、学生和教师 TypeScript 类型检查、32 项相关后端测试；本地 PostgreSQL 初始化了 PYB-01 演示课程，登录与工作台 API 返回新任务；师生浏览器在 1440×900、1366×768、1024×768、390×844 检查，无页面级横向溢出，键盘焦点可见。教师手机端的导航遮挡已修正。

2026-09-23 复核发现 Docker Desktop 4.91.0 实际已安装于用户目录，镜像盘在 `D:\TeachingAgent\.runtime\docker-desktop`；此前只是 CLI 未加入 PATH，且 Desktop 后台被失效的本地 socket 阻断。备份临时 socket 目录并关闭 Docker WSL 后端后，Linux Engine 29.8.0 恢复。项目自带双 OpenHands 工作区检查通过：真实容器命令、工作区隔离、认证与回收均正常。随后在只读 Snapshot 容器中实测 PYB-01：正确程序的公开与隐藏检查均通过；故意漏掉负数边界的程序通过公开检查但未通过隐藏检查，隐藏用例详情没有出现在返回结果中。无需卸载或重装 Docker。

2026-09-23 使用 `backend/scripts/verify_python_basics_live.py --local-stage02b-config` 在本地 DEMO PostgreSQL、运行中的业务 API 与真实 Docker/OpenHands 容器中完成全链路验收。两个专门标记的演示学生样本经文件保存、Snapshot、公开 Run、隐藏 Submit 和教师详情接口：负数边界错误样本的公开检查为 `SATISFIED`、隐藏检查为 `NOT_SATISFIED`，未创建 Submission；正确样本两项检查均为 `SATISFIED`，成功创建 Submission。两个样本的教师端均能查看对应快照和验收状态；学生可见的隐藏检查详情不包含输入及预期答案。此阶段的运行闭环已验收完成。独立 Runner、PYB-02/03 和进一步界面收敛属于后续阶段。
