# ECS 晚间重新部署与真实联调（2026-09-07）

## 发布范围与当前状态

已将最新工作区版本 `ecs-20260907-230420` 切换到主服务。真实 multi、切换及重启后的新旧报告读取均已验证，当前容器 `jindiao-ecs` 为 running / healthy。

按用户要求，正式发布重新打包 23:04 的完整工作区，包括未提交修改、最新扁平请求接口和本轮修复。逐文件清单见 `artifacts/ecs-20260907-230420/manifest.json`。后续每次重新部署都应重新读取当时最新工作区，不能直接复用旧源码快照；打包后和切换前应检查代码是否继续变化。

| 项目 | 实际值 |
| --- | --- |
| 服务器 | `1.95.121.114` |
| 镜像 | `jindiao:ecs-20260907-230420`，linux/amd64 |
| 镜像 ID | `sha256:81299e1d558bcc0552524676a1a6798837ed47bf43b43b0f754a763f96082d08` |
| 源码包 SHA-256 | `f6592932a8b1c480ef94f48230da5664334e552218a4ac4ae9395b4f7a41aeb1` |
| 发布目录 | `/opt/jindiao/releases/ecs-20260907-230420` |
| 运行配置 | `/opt/jindiao/runtime/ecs-20260907-230420.env` |
| 服务监听 | `127.0.0.1:8080`，单 worker |
| 主容器持久化卷 | `jindiao-ecs-artifacts-20260907-230420-live` |
| 旧容器 / 原卷 | `jindiao-ecs-before-20260907-230420` / `jindiao-ecs-artifacts-20260907-093227-live`，完整保留 |
| 配置 | `qwen-plus`、`formal`、`tianyancha`、Mock 降级关闭、`attached + local` |

真实凭据沿用已验证的服务器配置，以权限 0600 独立保存，未写入源码包。新版 19 项核查的实测调查输入约 219 万 token，早间 240 万输入上限不足以预留报告请求。最终配置调整为 320 万输入、30 万输出、350 万总 token；仍限制 160 次模型请求、160 次工具调用、并发 6、600 秒及 2 轮返工。未启用 Mock。

## 联调发现与修复

1. 同一批天眼查结果含重复记录，导致证据 provenance 唯一性校验失败。采集网关现在同时处理批内和跨页去重，保留首次来源及内容不同的记录。
2. 专家提交被校验拒绝后，团队可能结束但留下核查缺项。恢复流程只补交缺失核查，沿用原快照、分配和预算，并要求补齐后重新审核；恢复次数仍有上限。
3. 所有任务完成、成员空闲时，底层事件流可能继续等待，导致审核收尾停滞。运行时连续两次检查任务终态与成员执行状态后，释放事件流供已有业务恢复逻辑继续。事件流由单个异步任务持续读取和清理，以保留底层会话上下文。
4. 无证据章节的模型文字触发校验后，原逻辑会丢弃全报告分析。现在丢弃该章节的无依据文字并保留原缺资料状态，其他章节仍严格验证引用和数值。未知证据仍会被拒绝。

各修复均先建立失败用例，再运行相关回归。早期预检失败没有切入主服务，也没有修改历史报告。

## API 契约变化

本轮候选新报告使用 `schema_version=prototype-v1`，顶层固定为：

```text
schema_version, meta, subject, summary, report, risk_findings, evidence, report_markdown
```

新增七步执行计划和进度事件。内部核查结果、Agent 轨迹及模型成本保存在服务器私有产物，不再作为新版公共报告顶层字段。旧报告按原契约读取，不能用新报告字段强行解析历史数据。

真实请求不传 `scenario_id`，示例：

```bash
ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 root@1.95.121.114
```

另一个终端检查健康状态：

```bash
curl --fail http://127.0.0.1:18080/ping
```

创建 Run 会调用真实供应商并产生模型用量。v2 和 `/invocations` 使用 `customerName / uscc` 扁平请求，旧 `enterprise` 入参返回 422；v1 保留原嵌套请求。示例：

```bash
curl --fail http://127.0.0.1:18080/api/v2/due-diligence/runs \
  -H 'Content-Type: application/json' \
  -H 'X-Hw-Agentgateway-User-Id: local-user' \
  -H 'X-Hw-Agentarts-Session-Id: local-session-001' \
  -H 'Idempotency-Key: local-check-001' \
  -d '{"customerName":"华为技术有限公司","mode":"multi","execution_profile":"attached"}'
```

用相同身份与 Session 查询返回的 Run ID：`GET /api/v2/due-diligence/runs/{run_id}`、`GET .../{run_id}/events`、`GET .../{run_id}/result`。SSH 隧道是访问边界；身份头本身不提供公网认证。

## 最终验收结果

真实验收 Run：`e2a147a7c55344b2868e5f48297b092d`，华为技术有限公司，multi，约 326 秒产出报告。

| 检查 | 结果 |
| --- | --- |
| 实际来源 | 天眼查 189 条、公开网页 1 条、派生证据 2 条，Mock 0 |
| 模型与核查 | qwen-plus；19 项核查、6 个调查/审核角色完成 |
| 报告 | HTTP 200，`prototype-v1`，`partial`；Markdown 63997 字符，6 条风险，模拟案例 0 |
| 执行事件 | 116 个连续 SSE 事件，七步执行完成，report.completed 与 GET result 一致 |
| 接口协议 | 最新 customerName 扁平请求通过，旧 enterprise 入参 422；幂等与 owner/session 隔离通过 |
| 切换后 | 新报告和历史真实报告的状态、结果、事件哈希保持一致 |
| 重启后 | 同上；验证只重放已有 Run，无新增模型调用 |
| 历史数据 | 654 个原文件内容保留，加入本轮 10 个文件；最终 85 条 Run、66 份报告兼容性通过 |
| 本地访问 | 已重建独立后台 SSH 隧道，127.0.0.1:18080/ping 返回 Healthy |
| 测试 | 本地 781 passed / 1 failed / 4 skipped；Linux 容器 782 passed / 1 failed / 3 skipped；Ruff、格式、mypy 和 pip check 通过 |

报告生成模型实际调用 1 次，128966 token；调查账本 53 次请求、1311657 token；共享采集 6 次请求、20486 token。总计 1461109 token。报告无 `generation_failed`，但仍有资料缺失和能力缺口；多个章节的 analysis 为空，已核验的结构化事实、风险及缺口均保留。`partial` 不代表资料完整或所有叙述字段齐备。

## 切换与证据

切换脚本要求真实 multi 协议验证通过、存在天眼查证据、无 Mock 证据且无 `generation_failed`。确认无进行中 Run 后，复制原生产卷与本轮预检卷到新卷，逐文件校验历史数据，并合并 `run-state/runs/idempotency.json`。原容器和原卷保留，启动失败时自动恢复。本次切换和重启均成功；预检容器已停止，早期失败及成功候选的独立卷保留供诊断。

最终发布证据：本地 `artifacts/ecs-20260907-230420/`、服务器 `/opt/jindiao/test-results/ecs-20260907-230420/`。前期诊断证据保留在对应的 `ecs-20260907-203805` 目录。摘要只包含状态、计数、校验值，不包含模型密钥或企业报告全文。

仍有旧 `release/frozen-manifest-v1.json` 与当前依赖文件哈希不一致的历史测试失败；未修改冻结清单或弱化该测试。本次 ECS 验证不等于 AgentArts 网关验证，attached 运行中任务也不承诺跨重启恢复。


服务器查看运行状态：

```bash
export PATH=/opt/jindiao/tools/docker:$PATH
export DOCKER_HOST=unix:///run/jindiao-docker.sock
docker ps --filter name=jindiao-ecs
curl --fail http://127.0.0.1:8080/ping
```

回滚到本次切换前版本时，先确认没有进行中的 Run，停止并重命名当前容器，再将 `jindiao-ecs-before-20260907-230420` 改回 `jindiao-ecs`，恢复 `unless-stopped` 并启动。旧容器继续使用原卷，不要覆盖新卷；切换后创建的数据需另行保留和迁移。

收尾时应用源码仍与发布包逐文件一致。23:30 出现的本地 `compose.yaml` 参数化改动未用于此 ECS（ECS 使用显式 docker run、独立 env-file 和命名卷）；部署记录在验收后更新。原归档与后续差异均在最终清单中保留。
