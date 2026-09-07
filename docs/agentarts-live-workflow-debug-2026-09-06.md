# AgentArts 真实业务流程调试（2026-09-06）

## 范围与状态

用户在控制台保存业务凭据后，已回读 `Latest → live-smoke-0906`，真实 Qwen 与天眼查配置生效。测试通过临时回环 BFF 发起，使用服务端 API Key、HttpOnly 登录会话与 CSRF；不携带 `scenario_id`，不允许降级 Mock。没有开放 ECS 公网端口，没有开启 detached、LTS 或 SFS。

原始真实 single 失败；同镜像本地复现定位到公开网页证据的 Markdown 渲染缺陷，已修复、测试并上传独立热修复镜像。云端已保存 `live-webfix-0906`，`Latest` 指向该版本。保存前逐项比对 22 项环境变量，确认与用户已保存的配置一致，未修改密钥或预算。

热修复后，云端真实 single 已产出一份 `partial` 报告，结果 GET 200，真实模型与天眼查数据已通过 BFF 链路返回，未使用 Mock。但报告 SSE 帧仍存在中断问题，另一轮真实 single 触发 token 预算保护；不能将这轮判为端到端完整通过。

> 后续进展：在用户授权有界增大预算后，真实 multi 已于 `live-recovery-0906` 通过报告 GET、SSE 和重放，见[真实 multi 验收](agentarts-real-multi-2026-09-06.md)。下文保留当时失败和待验证假设，不应当作当前状态。

## 首次云端 single

- Run：`d3885a9729094823a69348ca9efcde98`。
- 创建 202，查询 200，进入 investigation。
- SSE 200，首 data 帧约 0.046 秒；22 条事件连续，包含 9 次 `model.request.completed`，末事件 `run.failed`。
- BFF 结果接口 502；终态失败，不是报告完成。
- `Last-Event-ID: 1` 重放 21 条事件，序号精确匹配。
- 登出 204，临时代理已停止。未保存测试账号、API Key 或报告全文。

## 根因复现

使用与云端相同的基线镜像 `sha256:c1a82a2612049afa2436cbb0170bdada0b38ecd4a8813f7b4527e9d122f91719`，注入相同的真实模式与预算。容器只读，仅日志、临时文件和 artifacts 使用一次性 tmpfs，不发布端口。

首个诊断容器因缺少可写 `/app/logs` 在 SDK 初始化时停止，没有执行业务调用；补充 tmpfs 后，约 110.67 秒复现：

```text
DueDiligenceService._run_impl
  → ResultAssembler.assemble
  → MarkdownReportRenderer.render
  → _evidence_line
  → KeyError: SourceType.PUBLIC_WEB
```

模型调查执行完成后，公开网页/年报补充证据进入报告，但 `_SOURCE_LABELS` 只有天眼查、Mock、派生来源，遗漏契约已支持的 `public_web`。此为已复现的确定性缺陷；云端没有启用原始日志，未声称取得首次云 Run 的相同完整堆栈。

修复只增加 `SourceType.PUBLIC_WEB: "公开网页"`，不删除网页证据，不将其伪装为 Mock，也不修改证据门禁或风险规则。

## TDD 与镜像证据

- 新增四种合法 `SourceType` 的真实渲染参数化测试，覆盖章节引用与证据附录。
- 修复前：public_web 用例出现预期 KeyError，其余三种通过。
- 修复后：76 项相关报告/流程/反馈回归通过；Ruff、mypy（两个相关文件）通过。
- 热修复镜像内断网执行同一来源测试：4 passed。
- 临时断网容器：Docker HEALTHCHECK healthy；`pip check` 退出 0；无宿主机端口发布，验证后已自动删除。
- 最终追加 BFF 大帧边界测试后，相关报告、流程、反馈及 BFF 安全/集成/e2e 共 124 passed（32 项依赖/弃用警告）；三个相关 Python 文件 Ruff、mypy 均通过。不是全仓测试或真实云流程全部通过。

热修复不是整个当前工作树的重发。使用 [专用 Dockerfile](../deploy/agentarts/Dockerfile.public-web-hotfix)，校验原渲染文件 SHA-256 后，只修改基线镜像内源码与已安装包中的同一来源映射，避免混入尚未发布的报告策略改动。

```text
swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:agentarts-20260906-public-web-fix-arm64
```

- 本地 image/config ID：`sha256:f900227a61433f360b35b658049f7f191dbfee989d33205b13a0593b920e3658`。
- SWR push manifest digest：`sha256:df4b721fccfd1b6bc52045a345f3f0741648d6c3351ebbf3f897b4c4f70eee99`。
- 上传后重新读取远端 manifest，确认 `linux/arm64` 且 config ID 与本地一致。
- 原基线标签与 `live-smoke-0906`/`v1` 保留，没有覆盖。

## 真实复验遇到的预算边界

热修复镜像本地真实 single 在约 113.5 秒触发 `AgentExecutionError: orchestration token budget exhausted`，未完成报告。该次不是新的 Markdown 异常，也不是完整成功。测试保留 120000 输入、20000 输出、140000 合计 token 上限；该异常汇总未区分具体哪个 token 维度耗尽，不作猜测。

未自动增加预算或关闭预算保护。应用当前根据供应商返回 usage 记账，这不是供应商账户的预付费硬限额。

## 热修复后的云端复验

- 版本：`live-webfix-0906`，访问方式 `Latest`。
- Run：`2265b806cb3e4095b2e59bb5680d2e62`。
- 创建 202，约 0.48 秒；SSE 200，首帧约 1.034 秒。
- 约 22.58 秒完成 acquisition 并冻结 snapshot。
- 约 106.47 秒收到调查 Agent 完成事件；结果 `duration_ms=105834`。
- 状态 GET 200：`status=partial`、`stage=terminal`、`termination.reason=partial`、无执行错误。
- 结果 GET 200：`mode=single`、`is_mock=false`、`degraded=false`、`formal_agent_run=true`、模型 `qwen-plus`。
- 报告 59695 字符，15 条证据（14 条天眼查、1 条公开网页），Mock 证据 0；结果 errors 为空。
- 两个共享采集 Agent 与 `single-investigator` 均 completed。这不意味着数据源覆盖完整。

| 用量 | 共享采集 | 调查 |
| --- | ---: | ---: |
| 成功模型请求 | 6 | 3 |
| 输入 token | 10326 | 74542 |
| 输出 token | 763 | 3515 |
| 总 token | 11089 | 78057 |
| MCP 调用 | 17 | 0 |
| Tool 调用 | 0 | 17 |

覆盖状态共 48 项：`verified_records=21`、`verified_empty=0`、`capability_absent=20`、`source_error=7`、`degraded_mock=0`。这些是覆盖项计数，不是证据条数；7 项源错误的具体原因尚未逐项验证，不推断为认证失败，也不能表述为“尽调全部完成”。

### SSE 中断：已知事实与待验证假设

该 Run 的 BFF SSE 收到 30 条有序业务事件后出现代理错误数据帧；未收到 `report.completed` 与 `run.partial`。HTTP 200 不能代表流完整。旧诊断输出的 replay valid 仅比较了两份序列（含相同的空序号），**不能作为完整重放验收证据**。

代码中 `RunEventPublisher.complete()` 按顺序发布 `run.phase.completed`、携带完整结果的 `report.completed`、`run.partial/completed`。当前 BFF 单 SSE 帧默认最大 262144 字节，超限、无效编码、密钥回显、超时或上游错误均可能生成同一种 `proxy.error`。本次中断位置与大报告帧超限吻合，但没有取得该 Run 的原始帧长度，因此超限仍是待确认假设，不能仅凭报告字符数定论。

离线使用合成中文报告、跨网络块传输验证现有配置：

- 约 600 KB 报告帧在默认 256 KiB 限制下返回 `proxy.error`，不会伪造 `run.partial`。
- 显式 `JINDIAO_BFF_MAX_EVENT_BYTES=1048576` 时，同帧与后续 `run.partial` 完整转发。
- 大于 1 MiB 的帧仍被拒绝，上游关闭；独立结果 GET 在三个用例中均可正常读取。
- 三项边界测试通过。这仅证明本地有界配置行为，不等于该云 Run 已修复。生产默认值、真实配置文件未修改。

收到 `proxy.error` 后，应停止当前流并查询 Run：若状态已是终态，用结果 GET 获取报告；若仍运行，可有限重连。不应反复重建 Run、无限重放同一超限帧或伪造终态序号。长期方案应评估 v2 报告事件仅包含结果引用，同时单独保留 v1 兼容语义，需做协议变更设计。

### 第二轮云端复验：预算耗尽

- Run：`e4eb30c438d84fa98103ae7c245001f5`，仍使用相同版本与预算。
- 创建 202，约 0.43 秒；SSE 200，首帧约 1.033 秒。
- 约 117.77 秒收到 `run.failed`；23 条事件序号连续，10 次 `model.request.completed`。
- 状态 GET 200：`agent_execution_failed`，`orchestration token budget exhausted`；结果 GET 502。
- `Last-Event-ID: 1` 重放 22 条，序号匹配。该次失败终态较小，没有产出报告帧，因此未能验证前一轮的大帧假设。
- 资源返回的预算 used/remaining 为空，未获得精确耗尽维度；不能据此声称实际 usage 为 0。
- 两次云端探针均登出 204、临时 BFF 已停止。未保留临时身份密钥，不通过绕过 Run 归属限制访问历史 Run。
- 最终容器/进程检查仅保留原有 `jindiao-arm64-20260906-171833`（healthy，`127.0.0.1:18081`）；没有遗留本次 probe 进程，未更改原容器。

已停止追加真实调用，未提高 token 上限。下一步先完善预算/重试诊断与输入裁剪，再在原预算内复验；若需提高费用上限，应先明确新预算。

本地诊断脚本保存在被忽略的 `artifacts/agentarts-live-debug-20260906/`，只读取专用 dotenv 并在内存/标准输入中使用凭据，不包含实际密钥值。再次执行脚本会产生真实模型/数据调用，不能作为离线测试使用。

## 安全与尚未验收

用户消息自动附带的 IDE 选区曾暴露业务凭据，已提醒轮换；没有复述或写入文档、源码、镜像。轮换需要同步本地与云端，并由用户完成。

真实 multi、运行中取消、长期后台运行、跨沙箱恢复均未验收；single 稳定性与 SSE 完整性未验收前，不扩展到 multi。BFF 仍是临时本地测试账号，不是生产 SSO/多副本服务。上述 `partial` 报告不是完整尽调结论。
