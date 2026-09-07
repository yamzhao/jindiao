# AgentArts 真实 multi 流程联调（2026-09-06）

## 验收口径

本次用户授权继续调试，优先跑通真实 multi，token 超限时可采用短期替代方案。保留真实 Qwen、天眼查、原生 openJiuwen AgentTeams、四个专业调查 Agent、Leader 和独立 Reviewer，不使用 Mock、不跳过审核、不伪造完成事件。覆盖不足导致的 `partial` 与执行失败 `failed` 分开记录。

流程通过必须同时满足：真实采集；全部 15 个固定核查项提交；六个调查角色完成且 Reviewer 有结构化审核；报告 GET 200；SSE 连续且包含报告事件和一致终态；游标重放精确匹配。数据覆盖完整是另一个指标，不能把 `partial` 改写成“全部尽调完成”。

## 预算定位与短期方案

早期云端预算只有 24 次模型请求、120000 输入 / 20000 输出 / 140000 总 token。已发布镜像包含快照 Evidence 值去重，不重复实施同类优化。

先使用仓库真实 multi 测试的有限预算（600 秒，160 次模型请求，1200000 输入 / 300000 输出 / 1500000 总 token，160 次工具调用，并发 6，schema 重试 12，返工 2 轮）在相同热修复 ARM64 镜像内运行。

- 首次诊断在采集后因只读容器未给 `/home/jindiao/.openjiuwen` 提供可写目录失败。这是隔离测试环境问题；为该精确路径添加一次性 tmpfs，未放开整个容器文件系统。
- 第二次实际启动原生 AgentTeams。四个专业调查 scheduled 任务、初审、两项定向返工任务均 completed；二次复核任务 in_progress 时耗尽预算。
- 第一笔超过输入上限的记账：输入 1218273、输出 9799、总量 1228072，调查约 135.5 秒。并发在途调用结束后累计 66 次成功模型调用、输入 1265228、输出 10381、总量 1275609；工具 31、snapshot reads 6、schema 重试 1、返工 1，峰值并发 5。
- 明确是累计输入超过 120 万，而非输出 30 万、总量 150 万或 600 秒耗尽。大量协调轮次会重复携带上下文，不能只按单次请求大小估算整队预算。
- 达到限额后不再启动新模型请求，但团队未立即结束，仍可能等待阶段超时；主动停止本次临时容器并回收其 tmpfs。该失败没有被标为成功。

短期保持业务代码、模型、数据源和审核门禁不变，仅使用更充足但有限的调查预算：2400000 输入 / 300000 输出 / 2700000 总 token，其他上述限制不变。配置样例见 [live-multi.env.example](../deploy/agentarts/live-multi.env.example)。这不是关闭预算，也不是正式生产预算建议；采集账本独立、并发请求可能产生限额后的收尾 usage，不能当作供应商账户硬费用上限。

## 云端发布与实测

- 运行时：`jindiao-demo`，版本 `live-multi-0906`；控制台已确认 `Latest` 指向该版本。
- 镜像未变：`swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:agentarts-20260906-public-web-fix-arm64`，digest 见[上一轮热修复记录](agentarts-live-workflow-debug-2026-09-06.md)。未重发整棵工作树。
- 保存前对比：只修改 8 项预算配置并增加 `JINDIAO_MAX_CONCURRENCY=6`，共 23 项环境变量；其余值与保存前一致，包括两项凭据、真实数据模式、attached 和 memory。
- 未创建新运行时或网关，未修改 IAM/API Key 权限，未开启 detached、SFS、LTS 或公网明文端口。旧版本保留可回滚。
- 测试客户端：临时本地 BFF、HttpOnly 会话、CSRF、服务端 API Key、派生 owner/session；TLS 始终验证证书和主机名。
- BFF 使用已有可配置上限 `max_event_bytes=1048576`。默认值未修改；上一轮已做离线 256 KiB/1 MiB 边界验证，云端仍需以下结果确认。
- 云端 Run：`baa1a6b60e10441b9c80d97601dfa711`。创建 202，0.61 秒；SSE 200，首帧 1.063 秒；约 21.93 秒完成采集与快照冻结。
- 131.23 秒进入 `run.failed`，错误 `agent_execution_failed: AgentTeams review ended with unresolved repair tasks`，不是 token 超限；状态 GET 200 / failed，结果 GET 502。不能把团队退出解释为业务完成。
- 共 17 个连续事件，最大 data 帧 1917 字节，无报告事件；Last-Event-ID=1 重放后续 16 个序号一致。该次未触及 SSE 帧上限，不能据此证明大报告流已通过。
- 继续使用同版本隔离容器和相同 240 万输入预算诊断结构化审核/返工，不打印模型思考或凭据。

## 后续生产化事项

预算耗尽应及时传播为稳定终态；压缩冗余协调上下文，减少无业务增量的调用；评估 v2 报告事件改为结果引用，以适应超过 1 MiB 的报告；数据源缺口与错误逐项治理。当前联调不覆盖跨沙箱恢复、持久化、多副本 BFF 或生产身份管理。

## 相同镜像本地真实 multi 复验

使用与 `live-multi-0906` 相同预算、Qwen、天眼查和镜像，在无宿主机端口的隔离只读容器中复验。108.19 秒返回 `partial` 报告，六个调查角色以及两个共享采集角色均 completed，15 个固定核查全部提交；最终 ReviewSubmission 无 RepairTask。`formal_agent_run=true`、`is_mock=false`、`degraded=false`，15 条 Evidence 中天眼查 14、公开网页 1，Mock 为 0；报告 62630 字符。

- 采集：6 次成功模型请求，总 11093 token，MCP 17 次。
- 调查：35 次请求、34 次成功且带供应商 usage，总 562250 token（输入 555214、输出 7036），工具 24 次，snapshot reads 4，峰值并发 5，未耗尽预算。
- 覆盖 48 项：verified_records 21、capability_absent 20、source_error 7。`partial` 是真实数据覆盖不足，不是执行失败。
- 诊断仍发现 Reviewer 存在把可选证据或合理 inconclusive 误判为问题的质量波动；不能以这次成功宣称返工失败已确定修复或多次运行稳定。未放宽证据门禁或强行清除返工。
- 验证结束自动回收临时容器及 tmpfs；没有写出完整报告、模型思考或凭据。

该本地结果证明当前配置能走完真实原生团队流程；网关、代理与 SSE 完整性另行验收。

## 云端尾部恢复热修复

再次通过网关运行 `30594b00326a434890bb1248b56366dc`，144.11 秒仍失败，但错误变为 `AgentTeams Reviewer did not submit a review`。15 个核查提交已通过校验，原生流结束不代表权威黑板已完成审核。该次也是 17 个连续事件、无报告、结果 GET 502；游标重放通过，整体不通过。

针对业务提交与团队生命周期边界，增加有界恢复：

1. 原生流退出后，仅当全部核查及分配均已提交、审核仍未闭环时，最多启动两次审核尾部恢复。
2. 每次使用新原生团队/session 执行上下文，但保留同一 Run、冻结快照、授权黑板、结果版本、审核历史及同一个 BudgetLedger；600 秒外层期限、模型/工具/token/返工上限不重置。
3. 让真实 Leader 调度真实 Reviewer 或目标专业 Agent。未提交审核、返工未解决或预算耗尽仍失败；不人工清空 RepairTask、不伪造结果。
4. 原生 `team.completed` 仅记为 `team.runtime.ended`；业务 `team.completed` 只由权威黑板校验生成。
5. `team.runtime_ready` 即表明需要清理，不再依赖真实流中未必出现的 `tool_result/build_team` 片段；恢复前先停止、关闭、删除上一支运行期团队，避免残留团队并发写黑板。

TDD 已先观察缺审核、无恢复边界、缺清理的失败测试，再实现修复；新增耗尽不重试、返工保留到专业 Agent 与 Reviewer 新版本提交的回归。相关回归与静态检查记录随最终验证更新。

- 新镜像：`swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:agentarts-20260906-multi-recovery-arm64`。
- image/config ID：`sha256:d29d2b66dc6700e11d09d57d39b1f3ab06c6b6ebaedf76743977f00726e3ffca`。
- SWR manifest digest：`sha256:e8892a7e3cc65ee8bf467989bc0919023860aa6a5fe9741f7a555414690b7e8b`；上传后远端 manifest 确认为 linux/arm64。
- [专用 Dockerfile](../deploy/agentarts/Dockerfile.multi-recovery) 校验基线文件哈希，仅复制两个源文件到源码/安装包，配套白名单构建上下文约 25.5 KB，无凭据或其他未发布工作树内容。
- 镜像内断网测试 13 passed，Docker HEALTHCHECK healthy，pip check 无依赖冲突；临时健康检查容器已删除。
- 云版本 `live-recovery-0906`，2026-09-06 22:24:47 GMT+08:00 确认 Latest 切换；保存前验证全部 23 项环境变量完全未变。前版保留可回滚。
- 新云端 Run：`dd93c40d37b74289bdb8d152d9ea6126`，创建 202 / 0.44 秒，SSE 首帧 1.038 秒，采集/快照约 17.2 秒。最终验收如下。

## 真实网关 multi 验收通过（覆盖为 partial）

链路：临时本地 BFF 登录/Cookie/CSRF → 服务端 API Key → 真实 AgentArts PREFIX_MATCH/Latest → 原生 AgentTeams + Qwen + 天眼查。

- Run：`dd93c40d37b74289bdb8d152d9ea6126`；约 149.36 秒收到 `run.partial`，业务 duration 148400 ms。
- 状态 GET 200：partial / terminal / termination=partial，无执行 error；报告 GET 200，327026 字节。
- `formal_agent_run=true`、`mode=multi`、`model_name=qwen-plus`、`is_mock=false`、`degraded=false`，结果 errors 为空。
- 两个共享采集角色、Leader、四专业 Agent、Reviewer 均 completed。固定目录 15/15 核查项无重复，Reviewer 有正式审核任务；经历 1 轮返工，最后审核无待执行 RepairTask。
- 报告 68149 字符；Evidence 15 条（天眼查 14、公开网页 1），Mock 0。
- SSE 27 个事件，序号从 1 连续到 27，包含 8 个 section.completed，末尾 `report.completed → run.partial`。报告 data 行实测 327418 字节（不含其他 SSE frame 字段），大于默认 262144 字节，小于显式上限 1048576 字节；本次 1 MiB 有界配置完整转发，不再返回 proxy.error。不能用本次大小反推之前其他 Run 的精确帧长度。
- Last-Event-ID=1：HTTP 200，重放后续 26 个序号与原流精确一致。业务完成、终态一致、连续事件和重放断言全部通过，探针退出 0 / verified=true。
- 覆盖仍为 48 项中的 verified_records=21、capability_absent=20、source_error=7；因此正确终态是 **partial**，不能表述为全部数据齐备或完整尽调结论。

| 用量 | 共享采集 | 调查团队 |
| --- | ---: | ---: |
| 模型请求 / 成功且含 usage | 6 / 6 | 67 / 65 |
| 输入 token | 10310 | 1296465 |
| 输出 token | 705 | 10211 |
| 总 token | 11015 | 1306676 |
| MCP 调用 | 17 | 0 |
| 业务工具调用 | 0 | 35 |
| Schema 重试 / 返工轮次 | 0 / 0 | 1 / 1 |

本次累计调查输入约 130 万，超过之前 120 万试验预算，未超过本轮 240 万输入 / 270 万总量限制。资源 GET 的 budget.used/remaining 仍为空，不代表零用量；以上采用结果 execution_cost 的供应商 usage 账本。

公共 SSE 尚未映射原生团队内部逐角色模型事件和恢复次数，因此不能由本次 SSE 判定恢复分支是否实际触发；恢复与不清空返工的行为另有测试覆盖。一轮真实通过证明流程打通，不证明所有模型返回均稳定收敛或证据解释完全正确。

登出 204，临时 BFF 已停止，临时账号/identity key 未持久化，不绕过归属隔离重查历史 Run；未保存报告全文或实际密钥。Docker 检查仅保留原有 healthy 的 `jindiao-arm64-20260906-171833`（127.0.0.1:18081）。没有开放公网明文端口。

### 验证范围与遗留项

相关回归通过；4 个改动的源/测试文件 Ruff、mypy 通过。全仓 `pytest --no-cov -o addopts='' -q` 实测 **638 passed、1 failed、4 skipped**（21.88 秒）。失败为冻结清单校验：`pyproject.toml`、`requirements.txt`、`uv.lock` 与 `release/frozen-manifest-v1.json` 不符，这三个依赖文件不是本轮修改或热修复上传内容。保留冻结历史，不为测试变绿而重写旧清单；后续应单独做完整版本冻结。跳过包括 3 项需显式启用的实时测试和 1 项受沙箱回环 socket 限制的流式测试；真实云验收证据以本节独立探针为准。

未验收：长时稳定性、多次恢复收敛、完整源覆盖、运行中取消、跨沙箱恢复、detached/SFS、多副本与生产登录。前端还未接入；当前完成的是后端安全代理至真实云 multi 的流程联调。

回环 socket 测试已在允许本地网络的环境单独补跑：`tests/integration/test_bff_streaming.py` 1 passed（0.67 秒），验证首帧及时性、并发与断开清理；没有追加模型或天眼查调用。上述全仓原始统计不改写。
