# Result 接口真实联调报告（2026-09-03—2026-09-04）

## 结论

本轮真实联调通过：单智能体与多智能体均可通过唯一 Result 接口运行，`multi + JSON` 阻塞问题已修复。

- 天眼查 MCP 实网连通，完成主体解析、能力发现、真实记录查询和空结果语义验证。
- 阿里云百炼 OpenAI 兼容端点与 `qwen-plus` 可用，模型连接参数已进入 AgentTeams。
- `single + JSON`、`single + SSE` 与 `multi + JSON` 均返回成功结果。
- `multi + JSON` 完成 AgentTeams 建队、5 个业务 Agent 并行调查、独立复核、八章报告装配和运行时清理；修复后连续两次真实调用均通过。
- AgentTeams 临时团队在建队检查点后被停止、从运行池移除并删除，不再依赖模型循环执行生命周期清理。

## 脱敏配置

| 项目 | 生效值 |
| --- | --- |
| 模型 provider | `OpenAI`（OpenAI 兼容端点） |
| 模型 | `qwen-plus` |
| Base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| temperature | `0.3` |
| 单次外部调用超时 | `30s` |
| 数据源 | `tianyancha` |
| 降级策略 | 请求显式使用 `allow_degraded_mock=true` |
| 密钥 | 仅保存于本机 Git 忽略的 `.env`，本文档不记录 |

用户提供的 `LLM_MODEL_PROVIDE` / `LLM_*` 名称已映射为项目实际读取的 `MODEL_*` 与 `JINDIAO_REQUEST_TIMEOUT_SECONDS`。

## 实测结果

| 用例 | 结果 | 关键数据 |
| --- | --- | --- |
| 天眼查 MCP 授权与协议 | PASS | 主体来源 `tianyancha`，发现 84 个 capability |
| 天眼查领域路由 | PASS | governance 17，judicial 15，operations 23，peers 4 |
| 天眼查真实记录 | PASS | 基本信息和年报为 `verified_records` |
| 天眼查空结果 | PASS | 不存在主体返回 0 候选，状态为 `verified_empty` |
| 千问模型直连 | PASS | `qwen-plus` 返回有效响应 |
| Result `single + JSON` | PASS | HTTP 200，1.606s，38,677 bytes |
| Result `single + SSE` | PASS | HTTP 200，1.569s，20 个事件，序号严格递增 |
| Result `multi + JSON`（修复后 A） | PASS | HTTP 200，5.504s，40,055 bytes |
| Result `multi + JSON`（修复后 B） | PASS | HTTP 200，4.513s，40,041 bytes |
| AgentTeams 清理 | PASS | stop 后从 pool 移除，随后 team deleted |
| 完整自动化测试 | PASS | 226 passed，pytest exit 0，覆盖率 89.35% |
| 静态检查 | PASS | Ruff 通过；mypy 检查 72 个源码文件无问题 |

## Multi Result 摘要

- Run ID：`496a2995811e477c91f46e8e7a8dea3c`、`ab9c73cb27b34e55ae2f53f3e4fcdae7`。
- 主体：华为技术有限公司；主体来源：`tianyancha`。
- 模式：`multi`；两次均为 HTTP 200；运行耗时分别为 5.504s 与 4.513s。
- Result 顶层 14 个固定字段齐全，包含完整 `report_markdown`。
- 报告章节：8；Finding：7；Evidence：7；Errors：0。
- Evidence 来源：天眼查 3 条、Mock 4 条。
- Evidence 状态：`verified_records=3`、`capability_absent=3`、`degraded_mock=1`。
- 业务协作：5 个 Agent、5 个任务、4 个并行任务；Agent trace 5 条。
- Markdown 顶部 `Mock 数据提示` 仅出现 1 次。
- 结果状态为 `partial`，原因是部分标准报告字段来自能力缺失补充或显式降级，不代表接口执行失败。

## 根因与修复

### 第一层：运行输入与模型配置缺失

初始实现没有向 TeamAgent 传递非空 `query`，也没有把 provider、Base URL、API Key、temperature 和 timeout 装配进 `TeamAgentSpec.model_router`。这会使 AgentTeams 只进入等待状态，或者无法建立模型客户端。

修复后，运行时获得非空、带主体与任务分工的协调 query；模型连接参数只在内存中传递，不进入公共 Result、报告或 trace。

### 第二层：模型驱动清理形成无限循环

首次修复后，模型能够调用 `build_team` 和 `clean_team`，但预置成员仍为 `UNSTARTED`，openJiuwen 拒绝清理。尝试先广播激活成员又暴露了 in-process 并发状态竞态及共享 teammate 工具释放问题，导致模型重复调用工具直至超时。

最终修复把业务协调与资源生命周期分离：

1. AgentTeams leader 真实调用模型并执行 `build_team`。
2. 适配层在建队前启动官方 `TeamMonitor` 订阅，以 `team_created` 生命周期事件作为确定性建队完成信号，roster 快照仅作已完成时的快速路径。
3. 适配层调用官方 `stop_agent_team`，终止后续模型扩展和成员误启动。
4. 流关闭后调用官方 `delete_agent_team(force=True)`，删除临时团队与 session 状态。
5. 整个运行仍受 30 秒 deadline 保护；异常时取消 runtime task 并返回结构化错误。

一次稳定性复测暴露了单纯轮询 roster 的时序窗口：建队已成功，但轮询未在下一轮模型任务扩展前观测到完整成员。改为事件订阅后，连续两次实网调用均在 6 秒内完成。该方案保留了真实 AgentTeams 建队与模型调用，同时避免把确定性的资源清理交给概率性模型决策。

## 回归验证

新增或加强的回归覆盖包括：

- TeamAgentSpec 完整模型路由配置。
- AgentTeams 必须收到包含主体和任务分工的非空 query。
- 只有 roster 完整建立后才能停止并强制删除临时团队。
- 非终止 runtime 必须在 deadline 到期后被取消并返回 `AgentExecutionError`。
- 私有 reasoning/prompt 字段不得进入公共 runtime event。

## 本地证据

- Multi JSON Result A：`artifacts/496a2995811e477c91f46e8e7a8dea3c/result.json`
- Multi Markdown A：`artifacts/496a2995811e477c91f46e8e7a8dea3c/report.md`
- Multi trace A：`artifacts/496a2995811e477c91f46e8e7a8dea3c/trace.jsonl`
- Multi metrics A：`artifacts/496a2995811e477c91f46e8e7a8dea3c/metrics.json`
- Multi JSON Result B：`artifacts/ab9c73cb27b34e55ae2f53f3e4fcdae7/result.json`
- Multi Markdown B：`artifacts/ab9c73cb27b34e55ae2f53f3e4fcdae7/report.md`
- 历史轮询时序失败：`artifacts/93c2f57d3ffc452cb32175e6b624fc6e/error.json`
- 历史第一阶段失败：`artifacts/9e3d0253b1b241f5906ee1c3e4db3761/error.json`
- 历史第二阶段失败：`artifacts/796670afe23d4a8791671d65e3ac5126/error.json`

上述运行目录位于 Git 忽略的 `artifacts/` 下。
