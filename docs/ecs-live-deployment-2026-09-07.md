# ECS 真实模型与天眼查部署记录（2026-09-07）

## 交付状态

已在 `1.95.121.114` 完成完整应用镜像上传构建、真实业务预检、主服务切换和重启读取验证。运行模型为 `qwen-plus`，数据源为天眼查，正式 Agent 运行模式为 `formal`，Mock 降级关闭。

| 项目 | 实际值 |
| --- | --- |
| 发布目录 | `/opt/jindiao/releases/ecs-20260907-093227` |
| 镜像 | `jindiao:ecs-20260907-093227`，`linux/amd64` |
| 镜像 ID | `sha256:e2acc87a486d56277281fbd3e3eae6edef794b1d6bf1f446991d197f94318475` |
| 容器 | `jindiao-ecs`，非 root UID 999，`running / healthy` |
| 监听 | 服务器 `127.0.0.1:8080` |
| 持久化卷 | `jindiao-ecs-artifacts-20260907-093227-live` → `/app/artifacts` |
| 运行配置 | `/opt/jindiao/runtime/ecs-20260907-093227.env`，root 所有，权限 0600 |
| 执行与存储 | `attached + local`，单 worker |
| 自动启动 | 容器 `unless-stopped`；`jindiao-docker.service` 为 enabled / active |
| 旧容器 | `jindiao-ecs-before-20260907-093227`，已停止，restart=no |
| 原数据卷 | `jindiao-ecs-artifacts`，完整保留 |

独立 Docker daemon 使用 `/run/jindiao-docker.sock`，CLI 位于 `/opt/jindiao/tools/docker/docker`。服务器未开放公网应用端口。

本次使用 09:32 固定源码快照，归档 SHA-256 为 `a0a68e518e88968ce2c67c4d1d74d695bf2efe7530af1505c6d2eb6e92e639ab`。镜像中 296 个应用与资源文件、137 个安装包文件已逐文件核对哈希；安装时生成的 egg-info 元数据不作为源码哈希对象。镜像不含真实 dotenv 文件。

部署期间 10:01 出现另一批 Mock/Demo 企业名称和配套脚本、测试、文档修改。这些后续修改未混入已验证镜像，也未被覆盖；收尾核对确认应用源码、依赖文件、真实数据路由和规则仍与发布快照一致。完整差异清单保存在本次 `manifest.json`。

## 真实运行配置

- `MODEL_PROVIDER=OpenAI`、`MODEL_NAME=qwen-plus`；模型地址、模型密钥和天眼查授权从本机 `.env` 定向提取，经 SSH 单独传输，未进入源码包。
- `JINDIAO_AGENT_RUNTIME_MODE=formal`、`JINDIAO_DATA_SOURCE_MODE=tianyancha`、`JINDIAO_ALLOW_DEGRADED_MOCK=false`。
- 采用 `deploy/agentarts/live-multi.env.example` 的有界配置：600 秒、160 次模型请求、160 次工具调用、并发 6、240 万输入 / 30 万输出 / 270 万总 token、2 轮返工、12 次 schema 重试。该预算不是供应商账户的费用上限。
- `JINDIAO_REPORTING_DEMO_ENABLED=false`、detached 关闭。BFF 与 AgentArts 云端版本不在本次 ECS 部署范围内。
- 本地临时运行配置已删除，服务器配置目录权限 0700、文件权限 0600。未在本文或验证摘要中记录密钥。

## 真实业务验证

以华为技术有限公司发起一笔无 `scenario_id`、禁止降级的真实 multi 尽调，Run ID 为 `45cb5ef00221428d920d0aad64b0818e`。

| 检查 | 结果 |
| --- | --- |
| 实际模型与数据 | `qwen-plus`；天眼查 29 条、公开网页 1 条证据；Mock 0 |
| 正式运行标记 | `formal_agent_run=true`、`is_mock=false`、`degraded=false` |
| 结果 | HTTP 200，`partial`，无执行 error；约 133 秒完成预检 |
| 核查与角色 | 15 项核查、8 个采集/调查角色完成；8 个报告章节 |
| 报告 | Markdown 141881 字符；未将报告全文写入本地部署摘要 |
| SSE | 27 个连续事件，包含 `report.completed` 和 `run.partial` |
| 重放与隔离 | Last-Event-ID 重放一致，其他 owner 返回 404 |
| 切换后读取 | 相同 Run 的状态、结果、事件哈希完全一致 |
| 重启后读取 | 同上；无新增模型或天眼查调用 |

`partial` 表示来源覆盖不完整，不能当作所有数据齐备的完整尽调结论。此次验证证明部署与真实调用链路可用，不代表长期运行稳定性或运行中任务跨重启恢复。

共享采集使用 6 次模型请求、12046 个 token；调查团队使用 42 次模型请求、793894 个 token。模型账本的成功且含 usage 请求分别为 6 和 40。

## 数据与回滚保留

原卷的 62 条历史 Run、493 个文件复制到新卷，逐文件校验历史内容未变；本次真实验收 Run 的 8 个文件及其幂等记录一并迁入。原卷和旧容器均未覆盖。旧历史报告仍保持原来的 Mock 标记，新请求使用真实配置。

首次切换在幂等索引合并路径检查失败时自动恢复了旧容器，原卷未修改。修正索引路径为 `run-state/runs/idempotency.json` 后，先在临时内存卷验证合并，再成功切换。失败记录保留为 `switch-attempt-1.log`。

预检容器、预检卷和首次失败的临时目标卷已清理。旧容器是历史 Mock 配置，只用于恢复旧环境；如需在回滚时继续保持真实模式，必须先验证旧镜像与真实配置的兼容性。

## 测试记录与遗留项

- 本地快照 Ruff 和格式检查通过，mypy 检查 225 个源文件通过；测试覆盖率 87.13%。
- 本地原始全量结果：638 passed、1 failed、4 skipped；回环 SSE 补测 1 passed。
- Linux 镜像全量结果：637 passed、3 failed、3 skipped。两项失败来自测试资源根路径配置，修正测试配置后定向复验 2 passed。此前只读日志目录导致的收集错误也已通过临时可写测试目录解决。
- 剩余已知失败是旧 `release/frozen-manifest-v1.json` 对三个当前依赖文件的哈希校验；保留历史冻结记录，未修改测试或旧清单来掩盖差异。因此不能声称全量测试无失败。
- 镜像 `pip check` 通过。实际业务验证独立使用真实供应商，未将离线测试当作真实调用证明。

## 访问和运维

本机建立 SSH 隧道：

```bash
ssh -N -L 18080:127.0.0.1:8080 root@1.95.121.114
```

随后访问 `http://127.0.0.1:18080/ping` 或 `http://127.0.0.1:18080/docs`。API 使用方式见 [API 文档](api/README.md)。身份头属于可信部署边界内的归属上下文，不是独立认证入口。

服务器检查与重启：

```bash
export PATH=/opt/jindiao/tools/docker:$PATH
export DOCKER_HOST=unix:///run/jindiao-docker.sock
docker ps --filter name=jindiao-ecs
curl --fail http://127.0.0.1:8080/ping
# attached 模式下，先确认没有运行中任务再重启。
docker restart jindiao-ecs
```

服务器证据位于 `/opt/jindiao/test-results/ecs-20260907-093227`；本地副本和逐文件发布清单位于 `artifacts/ecs-20260907-093227/`。
