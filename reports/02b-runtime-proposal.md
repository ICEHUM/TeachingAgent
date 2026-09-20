# Stage 02B PostgreSQL 运行时提案

记录日期：2026-09-20

本文件只记录 Docker 只读审计和 PostgreSQL 测试镜像提案。尚未拉取镜像、创建容器、修改业务代码或执行数据库迁移。

## Docker 环境澄清

按要求在 `D:\TeachingAgent` 执行：

| 检查 | 直接命令结果 | 结论 |
|---|---|---|
| `where docker` | 未找到命令 | 当前 Codex 进程不能按命令名解析 Docker CLI |
| `docker version` | `docker` 未识别 | 同上，并不代表 Docker Desktop 未安装 |
| `docker context ls` | `docker` 未识别 | 同上 |

进一步核对发现：

- Docker CLI 实际位于 `C:\Users\ICEHENG\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe`。
- Docker Desktop 的命名管道（包括 `dockerDesktopLinuxEngine`）存在。
- 当前 Codex 沙箱拒绝直接执行用户目录中的 `docker.exe`；在获得宿主执行权限后，使用绝对路径完成了只读核验。
- Client：Docker 29.8.0，Windows/amd64。
- Server：Docker Desktop 4.91.0，Engine 29.8.0，Linux/amd64。
- Context：`desktop-linux` 指向 `npipe:////./pipe/dockerDesktopLinuxEngine`，并为当前上下文。

这与阶段00实测记录一致。阶段02A“没有可用 Docker CLI”的表述应收窄为：当时的 Codex 沙箱不能按命令名调用宿主 Docker CLI；宿主 Docker Linux Engine 本身可用。

## 固定 PostgreSQL 镜像建议

建议镜像：

```text
docker.io/library/postgres:17.11-alpine3.24
OCI index digest: sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995
linux/amd64 manifest digest: sha256:aa90e97ee862e558111d34cfb8b2c4bec768c2b039fb791341686928560263b3
```

执行时使用完整引用：

```text
docker.io/library/postgres:17.11-alpine3.24@sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995
```

选择依据：

1. PostgreSQL 17 仍受官方支持到 2029-11-08。
2. 17.11 是 2026-09-20 可用的 PostgreSQL 17 当前补丁版本。
3. `alpine3.24` 固定基础发行版，避免 `alpine` 标签漂移。
4. OCI index 摘要固定发布物；另记录宿主实际使用的 `linux/amd64` 清单摘要，便于拉取后双重核验。
5. 官方镜像清单对应源码 revision `2603e26e245e558218728ee14e0a42dcb020dc7f`，镜像创建时间为 2026-09-17。

## 获批后的实例边界

- 容器名称：`teachingagent-postgres-02b`
- 仅绑定：`127.0.0.1:<随机空闲端口>:5432`
- 使用独立 Docker volume，不复用开发或生产数据库数据。
- 使用测试专用初始化管理员凭据；凭据只通过进程环境或临时受限文件传入，不提交 Git。
- 所有 Docker 调用显式连接 `npipe:////./pipe/dockerDesktopLinuxEngine`。
- 拉取后验证 `RepoDigests`、容器内 `postgres --version` 和 `SELECT version()`，不接受摘要或版本不一致。

## 审批门

等待确认上述固定版本、摘要和实例边界。确认前不执行 `docker pull`、`docker run`、Alembic online migration、checkpoint setup 或 Stage 02B 业务代码修改。
