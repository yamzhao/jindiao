# 本地真实 single 联调：调查阶段超时

## 结论

2026-09-08 00:35:59–00:36:46（Asia/Shanghai），在用户明确授权后，通过本地 `18088` 联调代理发起一次“华为技术有限公司”的真实 single Run。创建、数字字符串兼容、SSE、模型调用及资料采集均有运行证据，但调查 Agent 达到 30 秒超时后失败，未生成报告，不能视为完整真实业务验收通过。

未重启容器、修改预算、调用 ECS 或另建 Run 重试。验收后 `/ping` 仍返回 Healthy。

## 请求与边界

- Run ID：`ba4fbb0421a44086ba7544f9b331e12c`
- 入口：`http://127.0.0.1:18088/api/v2/due-diligence/runs`
- 实际后端：`jindiao-local-jindiao-1`；配置 `formal + tianyancha + qwen-plus`，Mock 降级关闭。
- 只传客户名称；省略 `uscc` 和 `scenario_id`，未使用 Mock 信用代码。
- 测试申报字段：`product=流动资金贷款`、`amount="12"`（万元）、`term="12"`（月）；它们仅用于协议联调，不表示真实授信申请。
- `mode=single`、`execution_profile=attached`。
- 固定幂等键：`local-real-huawei-20260908-uv3Wsg-single-1`。本次只发送一次创建请求，后续仅查询和重放事件。
- 沿用运行配置：每 Agent 30 秒；64 次模型请求、60 次工具调用；输入 300000、输出 100000、总计 400000 token。

本次通过代理直接调用 HTTP API，而非再次点击浏览器创建，避免重复收费任务。

## 实测结果

| 检查 | 结果 |
| --- | --- |
| 创建 | HTTP 202；`"12"` 数字字符串未触发原 422 |
| 未完成结果 | 初次 GET result 返回 202 |
| SSE | HTTP 200，47 个事件，sequence 1–47 连续 |
| 资料采集 | 约 16.05 秒完成；事件记录 25 次 MCP 调用、193 条证据 |
| 冻结快照 | `snapshot:3a0ca0a2cbc49394bfd31a8d`，36 个子模块 |
| 单智能体调查 | 约 16.21 秒开始；30.028 秒后 `agent.failed / TimeoutError` |
| Run 终态 | 约 46.24 秒 `run.failed`，`result_available=false` |
| 最终结果接口 | HTTP 500，`internal_error / Internal application error` |
| 断点重放 | Last-Event-ID=24，HTTP 200，内容与原事件序列的后缀完全一致 |
| 服务健康 | 验收结束后 `/ping` HTTP 200，Healthy |

关键时间（UTC）：

- `00:36:15.944465 +08:00` / `16:36:15.944465Z`：single-investigator 开始。
- `00:36:45.972802 +08:00` / `16:36:45.972802Z`：同一 Agent 报 `TimeoutError`。

## 原因与可观测性问题

`src/jindiao/application/formal_pipeline.py` 将 `context.policy.request_timeout_seconds` 传给 Agent 运行时；`src/jindiao/orchestration/agent_runtime.py` 使用 `asyncio.timeout(request.timeout_seconds)` 包裹整个 Agent 的流式执行。此次 30 秒是调查 Agent 整段执行的限制，不是整条 Run 的总时间，也不只是单个 HTTP 请求的读取超时。起止时间与配置完全吻合。

`src/jindiao/application/errors.py` 未专门映射 `TimeoutError`，最终落入通用 `internal_error`，所以公共结果没有明确告诉前端“调查超时”。本轮仅诊断，未修改该逻辑。

失败文件中的 `metrics.json` 将 token/tool 计数记录为 0，但公共 `model.request.completed` 事件有明确用量；这些 0 不能当作实际未发生调用。已完成的 7 次 qwen-plus 请求合计记录输入 26816、输出 667、总计 **27483 token**。超时时在途调用是否计费、是否有未回传用量，不能由这份事件记录确定；这不是最终账单总量。供应商成本字段为 0 也不代表免费。

## 证据位置

独立本地数据卷 `jindiao-local_local-artifacts` 内：

- `/app/artifacts/ba4fbb0421a44086ba7544f9b331e12c/trace.jsonl`
- 同目录 `error.json`、`metrics.json`、`manifest.json`
- `/app/artifacts/run-state/events/ba4fbb0421a44086ba7544f9b331e12c.jsonl`

本记录只保留状态、计数、时序和路径，不包含凭据、原始提示词或完整企业资料。

## 后续建议（尚未执行）

先将本地 Agent 执行超时调整为例如 300 秒，保持已授权的 400000 token 预算不变，经用户确认后再用新幂等键发起一次测试。增加超时并不保证通过：完整调查仍可能触及当前 token 预算，需依据下一次实际事件判断，不应预先无界放大预算。

此外应将超时映射为明确错误，并在失败路径保留已发生用量。此前已定位的前端 `runResult.report_markdown` 空值错误仍是独立问题；本次未产生报告，无法进行真实报告页面展示验收。
