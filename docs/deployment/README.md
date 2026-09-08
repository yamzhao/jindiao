# 部署与交付

- 状态：部署工具已实现；工具测试、历史部署验收与新版本线上验收分别记录。
- 更新：2026-09-08。
- 关键词：本地、Docker、ECS、AgentArts、维护窗口、回滚。

本地入口、ECS 部署和 AgentArts 镜像交付共用根目录 Dockerfile，不改变单进程、单容器应用结构；不需要数据库、Redis、MQ 或独立 DeepSearch 服务。ECS 明确拆为本地打包与服务器部署，登录和传输由操作者完成，不再提供跨机器一键发布。脚本可从任意工作目录调用；显式相对路径按调用目录解析。

| 入口 | 本轮范围 | 默认行为 |
| --- | --- | --- |
| `bin/start.sh` / `restart.sh` / `stop.sh` | 本地 shell 启动、重启、停止 | 默认真实调用；`--mock` 显式使用 Mock；均可预览 |
| `bin/ecs-package`（本机） | 生成源码/服务端脚本部署包及校验文件 | 离线打包；`--dry-run` 只预览，不执行 SSH/Docker |
| 包内 `deploy.sh`（ECS） | 校验已上传包、构建、预检、迁移、切换及失败恢复 | 仅预览；部署要求 `--apply --maintenance-confirmed` |
| 包内 `start.sh` / `restart.sh` / `stop.sh`（ECS） | 操作已部署的现有容器 | 不构建、不换卷；重启/停止需维护确认 |
| `bin/agentarts` | ARM64 构建、无凭据健康检查、SWR 推送、远端镜像校验 | 仅预览；执行要求 `--apply`；不发布云端运行时 |

## 1. 本地 shell 启动、重启与停止

启停本身只需要 Bash、Docker Engine、支持 `up --wait` 的 Compose，不再通过 Python 编排。应用镜像中的 Python 仍为 3.11；本地打包/AgentArts 工具另需宿主机 Python 3.11。本地 Docker context 必须是 Unix socket，不允许误操作 SSH/TCP 远端。

```bash
./bin/start.sh --dry-run
./bin/start.sh
./bin/restart.sh
./bin/stop.sh
./bin/local status
./bin/local logs
```

启动默认读取项目 `.env`，也可用 `--env-file /absolute/path/runtime.env` 选择已有配置。shell 不 source dotenv、不打印密钥，也不会创建或覆盖 `.env` / `.env.local`。Compose 的真实模式覆盖层固定 `formal + tianyancha + attached + local`，关闭 Mock 降级、detached 和报告反馈 Demo，保留文件中的模型路由、凭据和预算。

启动前要求非空 `MODEL_PROVIDER`、`MODEL_NAME`、`MODEL_BASE_URL`、`MODEL_API_KEY` 和 `TIANYANCHA_MCP_AUTHORIZATION`；缺配置时直接失败，不回退 Mock。启动和健康检查不会主动发起尽调，但后续业务请求会调用真实供应商并计费，应用预算不等于供应商账户费用上限。真实请求省略 `scenario_id`，不要用虚构 Mock 信用代码。

仅在明确需要离线测试时：

```bash
./bin/start.sh --mock
```

此时使用 `deploy/local/mock.env.example` 和显式 Mock 覆盖层，清空容器中的模型/天眼查凭据；不会改动真实配置文件。

- 服务仅监听 `127.0.0.1:8080`，可通过 `start.sh --port 18080` 改端口。
- 数据沿用 `jindiao-local_local-artifacts` named volume，历史 Mock 报告保留原标记，不改写成真实结果。
- `start.sh` 使用 Compose 构建/应用配置并等待健康，可用于从旧 Mock 容器切换为真实模式。
- `restart.sh` 只重启现有容器并等待健康，**不构建镜像、不重新读取 .env、不切换运行模式**；配置修改后应执行 `start.sh`。
- `stop.sh` 只停止精确匹配项目/服务标签的后端容器，不删除容器、卷或网络；已经停止/不存在时可重复执行。
- 启停通过项目内锁防止脚本并发；异常强杀遗留 `artifacts/.local-service.lock` 时先确认没有操作正在运行，再处理该空锁目录。
- 本地启停会中断未完成任务，执行前停止新请求并等待 Run 结束；不能承诺 attached 任务跨重启续跑。ECS 另有维护确认和空闲检查。
- `--dry-run` 不访问 Docker、不验证实际业务凭据，也不写文件；真实启动失败会返回非零。日志可能含敏感数据，分享前先脱敏。

旧 `bin/local` 保留为 shell 分发入口（`up/start/restart/stop/status/logs`），`make local-start/local-restart/local-stop` 对应三个脚本。源码开发仍使用 `make install && make dev`（8000，热重载）；离线样例/底层 Settings 默认不变，本节真实模式由启动覆盖层显式选择。

需要复用已部署前端时，见[线上前端 + 本地后端联调代理](local-workbench-debug.md)。代理独立运行，不在启停脚本的控制范围内，不会修改 ECS。

直接 `docker compose up --build` 不自动选择上述真实覆盖层；它仍按原 `.env` 和 `./artifacts` bind mount 运行。使用统一入口可避免配置语义不一致。

## 2. ECS 与 AgentArts

- [ECS 本地打包与服务器部署](ecs.md)：手动传输、Python 3.6 兼容、JSON 配置、维护窗口与回滚边界。
- [AgentArts 镜像交付与后续部署](agentarts.md)：镜像命令、SWR 前置配置，以及仍需完成的运行时、身份、凭据、网关、版本发布和验收。
- [浏览器 BFF](bff.md)：独立安全代理；本轮入口均不部署 BFF、前端或公网 HTTPS 网关。

## 3. 共同约束

### 构建与源码

依赖由 `pyproject.toml` 和 `uv.lock` 固定，DeepSearch 安装需要访问 GitCode；不使用相邻 `agent-core` / `deepsearch` 源码目录。Dockerfile 保留 `DEBIAN_MIRROR`、`PYPI_INDEX_URL` 构建参数，受限网络可手动使用；当前入口未封装自定义下载源。

ECS 本地打包和 AgentArts 镜像交付每次都从当前工作区重新生成运行时白名单快照，包含未提交/未跟踪的业务代码。源码只打包 Dockerfile、依赖文件、README、`src/config/mock_data/skills`；ECS 外层包另含服务端脚本、非敏感配置样例和校验清单。排除 dotenv、隐藏文件、日志、产物及证书/私钥文件，拒绝运行时树内符号链接；不清理或修改原工作区。白名单不等于扫描源码中的硬编码密钥，发布者仍需检查内容。

快照逐文件计算 SHA-256。ECS 本地打包完成前检查源码是否继续变化；打包完成后的编辑不会进入已生成的包，需要重新打包。手动上传后先校验外层压缩包，服务器脚本再校验包内资产与源码摘要；它不访问本地工作区。AgentArts 在推送前再次检查源码变化。两者只交付各自已冻结的版本。

### 凭据、费用与验收

不把完整开发 `.env` 上传到云端。ECS 使用显式指定的现有服务器配置，AgentArts 只使用既有 Docker registry 登录；业务密钥与网关 API Key 的注入另行处理。预览不写文件、不连接服务器、不构建、不推送。

自动验收是进程健康、配置、依赖与数据/镜像校验，**不会运行收费的真实模型或天眼查 smoke**。健康成功不代表业务全部通过。真实验收需明确预算、执行 single/multi、检查报告终态、SSE 连续性和重放；`partial` 可表示覆盖不足，不能改写为完整源覆盖。

`scripts/agentarts_prefix_probe.py`、`remote_integration_probe.py`、`remote_persistence_probe.py` 当前发送 Mock `scenario_id`，只能用于对应 Mock 配置，不能直接用于 `formal + tianyancha` 正式验收。真实请求参见 [API 文档](../api/README.md)，省略 `scenario_id`，每次新请求使用新的幂等键。

### 执行与存储

| 配置 | 适用范围 | 断线/重启语义 |
| --- | --- | --- |
| `attached + memory` | AgentArts 首次联调、Mock | 不承诺沙箱回收或进程重启恢复 |
| `attached + local` | 本机、当前单机 ECS | 可保留已完成结果；运行中任务不承诺跨重启续跑 |
| `detached + session/sfs` | 仅独立平台 POC 通过后 | 必须验证共享存储、后台任务与沙箱生命周期 |

`detached` 还要求 `JINDIAO_DETACHED_PROBE_PASSED=true`。本轮入口不启用 detached，不把后台协程、本地卷或一轮网关成功描述为跨实例可靠恢复。

## 4. 历史证据与本轮验证

历史记录保留原时点，不替代最新源码或新脚本的线上验收：

- [ECS 早间部署](../ecs-live-deployment-2026-09-07.md)；[晚间部署与新旧结果读取](../ecs-redeployment-2026-09-07-evening.md)。配置示例引用晚间记录路径，执行前必须确认文件仍正确，尤其模型预算。
- [ARM64 首次交付](../agentarts-arm64-delivery-2026-09-06.md) → [网关联调](../agentarts-gateway-debug-2026-09-06.md) → [真实 multi 验收](../agentarts-real-multi-2026-09-06.md)。AgentArts 已有历史真实链路证据，不再标注为“运行时尚未创建”。

本轮验证入口：

```bash
.venv/bin/pytest --no-cov -o addopts='' tests/unit/test_deployment_entrypoints.py tests/unit/test_ecs_deployment.py tests/unit/test_ecs_package.py -q
.venv/bin/ruff check scripts/deploy.py scripts/deployment tests/unit/test_deployment_entrypoints.py tests/unit/test_ecs_deployment.py tests/unit/test_ecs_package.py
.venv/bin/mypy scripts/deploy.py scripts/deployment tests/unit/test_deployment_entrypoints.py tests/unit/test_ecs_deployment.py tests/unit/test_ecs_package.py
```

自动化测试不执行真实 ECS 切换或 SWR 推送；首次实机发布仍需独立验收。完整项目历史上存在 `release/frozen-manifest-v1.json` 与当前依赖文件的哈希漂移，不能通过跳过断言或改写历史清单冒充全量通过。

后续已通过密码登录完成只读核对，确认原有服务健康且宿主机为 Python 3.6.8。拆分后的控制器已在该解释器内完成内存加载、`--help` 和只读 Docker 查询；未上传部署包、构建镜像或切换服务。完整部署验收仍须在维护窗口独立进行，不能以兼容检查代替。

2026-09-08 拆分后验证：相关部署测试 **51 passed**；实际工作区源码打包、外层 SHA-256 校验、解包及包内脚本预览通过；Ruff、mypy（10 个文件）和四个 Bash 入口语法通过。完整离线回归 **891 passed、1 failed、4 skipped**，失败仍为未修改的 `pyproject.toml` 与旧冻结清单哈希不符。未启动或重启任何业务服务。

### 后续：shell 启停与本地真实模式

按追加需求，启停已改为 shell，并用 `bin/start.sh` 实际切换本地后端。新容器 `jindiao-local-jindiao-1`（`2a7125960a58`）healthy，配置为 `formal + tianyancha + qwen-plus + attached + local`，Mock 降级关闭。项目 `.env` 和原模型预算未改写；两个历史 Run 的 7 个持久化文件前后摘要相同：`1be6280453214d10075a6346f229ddd12d93fad327c47a412766467e4b86c422`。数据卷仍为 `jindiao-local_local-artifacts`。

`127.0.0.1:8080/ping` 和联调代理 `127.0.0.1:18088/ping` 均返回 Healthy。这里只验证了模式/健康和历史数据，未发起收费尽调，不等于真实供应商授权与完整业务验收。ECS 的 start/restart/stop 未实机执行；包内脚本校验与预览通过。

最新相关测试 **73 passed**，Ruff、mypy 和 shell 语法检查通过；完整离线回归 **913 passed、1 failed、4 skipped**，仍只有上述既有冻结清单哈希失败。测试命令增加 `tests/unit/test_service_shell_scripts.py`。
