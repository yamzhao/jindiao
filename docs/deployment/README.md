# 单机部署

- 状态：Implemented v2 Run API（AgentArts 默认 attached）
- 架构：单进程、单容器、无数据库

本地安装：

```bash
cp .env.example .env
make install
make dev
```

容器安装：

```bash
docker compose up --build
```

Compose 只创建 `jindiao` 一个服务，并将 `./artifacts` 挂载到容器。镜像以非 root 用户运行，AgentArts 交付默认监听 `0.0.0.0:8080`，健康检查访问 `/ping`/8080。运行时不需要 PostgreSQL、MySQL、Redis、MQ 或独立 DeepSearch 服务。

本地开发仍可通过 `uvicorn ... --port 8000` 使用旧端口；容器和 AgentArts 配置固定使用 8080。线上不要把容器本地磁盘当作 detached 持久化介质，需先配置并验证 session/SFS adapter。

运行 profile 与存储 backend 必须成对配置：

| 配置 | 适用场景 | 存储 | 断线/重启语义 |
| --- | --- | --- | --- |
| `JINDIAO_EXECUTION_PROFILE=attached`（默认） | 比赛、Mock、首次 AgentArts 联调 | `memory` 或 `local` | 请求取消或进程退出后不承诺恢复 |
| `JINDIAO_EXECUTION_PROFILE=detached` | 仅在 POC 通过后 | `session` 或 `sfs` | Run 可跨 endpoint 查询；沙箱回收仍需平台恢复能力 |

`detached` 还必须设置 `JINDIAO_DETACHED_PROBE_PASSED=true`；Settings 会拒绝缺少后台任务或共享存储能力的组合。任何探针失败都应回退 attached 或拒绝创建，不得把 `asyncio.create_task()` 或本地文件描述为可靠 detached。

Mock 演示无需凭据且默认 `JINDIAO_DATA_SOURCE_MODE=mock`。实时数据模式在 `.env` 中设置 `JINDIAO_DATA_SOURCE_MODE=tianyancha`，并配置 `TIANYANCHA_MCP_URL` 与 `TIANYANCHA_MCP_AUTHORIZATION`；模型配置为 `MODEL_NAME`、`MODEL_BASE_URL` 和 `MODEL_API_KEY`。不要把 `.env` 或运行 artifacts 提交到仓库。

授权写入本机 `.env` 后，可执行聚合输出的安全联调；该命令验证主体、capability、基础画像、内部年报和空结果，不保存原始 MCP 响应：

```bash
make verify-tianyancha
```

验证从零安装时执行 `make verify-install`，它会导入公开安装的 `openjiuwen`、`openjiuwen-deepsearch` 与本项目，并拒绝本地绝对路径、`file://` 或相邻源码依赖。DeepSearch 固定到公开 commit，因此镜像构建阶段需要访问 GitCode。

AgentArts 是可选发布适配：Gateway 通过 PREFIX_MATCH 将 `/invocations` 及 v2 Run 资源的 GET/POST/SSE 路径映射到同一个运行时；v1 `result` 作为兼容接口保留。Memory 只保存反馈、运行摘要与 Skill 版本元数据，Sandbox 可运行规则和 benchmark。它不改变本地单体结构，也不上传企业原始证据。

回滚时关闭 v2/detached 开关并继续使用 v1 facade；已写入的安全事件和结果产物可保留。发布门禁至少执行：

```bash
python scripts/agentarts_prefix_probe.py http://127.0.0.1:8080
python scripts/agentarts_prefix_probe.py https://<gateway-origin> \
  --custom-prefix /runtimes/<runtime-name>/invocations \
  --env-file .env.agentarts
```

网关探针要求 HTTPS，并从进程环境或显式指定的 dotenv 文件读取 `AGENTARTS_AUTHORIZATION`。API Key 认证时该变量使用 `Bearer <API-Key>`；不要把实际密钥写在命令行、源码或日志中。探针不会自动加载项目 `.env`，直连本地时不会发送网关凭据；每次使用独立 Session/幂等键，并检查 SSE 的首尾事件和连续序号。

2026-09-06 已在用户指定 ECS 完成 AMD64 镜像构建、非 root 冷启动、健康检查、HTTP/SSE 集成测试和已完成 Run 的重启读取验证，详见 [ECS 集成测试报告](../ecs-integration-test-2026-09-06.md)。同日完成 ARM64 buildx 构建、容器回归、健康检查和 SWR 上传，详见 [ARM64 镜像交付报告](../agentarts-arm64-delivery-2026-09-06.md)。真正经过 AgentArts 网关的联调仍待运行时创建，不能用 ECS 或本地容器结果替代。

独立安装包使用 `JINDIAO_PROJECT_ROOT` 定位外置 config/skills；Dockerfile 设置为 `/app`，源码开发默认从源码树定位。不要移除镜像中的 config、mock_data、skills 目录。受限网络构建可通过 `DEBIAN_MIRROR`、`PYPI_INDEX_URL` 参数选择下载源，不改变冻结依赖版本。

更多演示验收项见 [最终演示清单](../demo-checklist.md)。
