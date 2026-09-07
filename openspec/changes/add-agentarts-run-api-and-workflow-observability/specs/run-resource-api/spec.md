## ADDED Requirements

### Requirement: 系统必须提供版本化 Run 资源创建接口
系统 MUST 提供 `POST /api/v2/due-diligence/runs`。请求 SHALL 支持现有企业、报告时点、语言、场景、降级开关和反馈字段，并以 `mode=single|multi` 选择调查拓扑。成功创建 SHALL 返回 `request_id`、`run_id`、session 标识、状态和 self/events/result/cancel 链接。

#### Scenario: 创建多 Agent Run
- **WHEN** 客户端提交合法企业输入并指定 `mode=multi`
- **THEN** 系统 SHALL 创建状态为 `accepted` 的 Run，返回唯一 `request_id` 和 `run_id`，且不得在响应中泄漏 Prompt 或原始外部响应

#### Scenario: 非法 mode 在创建前被拒绝
- **WHEN** 客户端提交不属于 `single|multi` 的 mode
- **THEN** 系统 MUST 返回 422，且不得分配 Run ID、启动 Agent 或写入运行事件

### Requirement: Run 创建必须支持幂等和归属校验
系统 MUST 接受 `Idempotency-Key`，并以调用主体、幂等键和规范化请求哈希判定重复。相同三元组 SHALL 返回同一个 Run；同一主体复用 key 但请求不同 SHALL 返回 409。Run 只能由创建主体或受信管理员读取、订阅、获取结果和取消。

#### Scenario: 重复提交同一请求
- **WHEN** 同一主体使用相同 `Idempotency-Key` 和相同请求再次创建 Run
- **THEN** 系统 SHALL 返回原 Run 的资源表示，不得启动第二次 acquisition

#### Scenario: 幂等键冲突
- **WHEN** 同一主体使用已存在的 `Idempotency-Key` 提交不同企业或不同 mode
- **THEN** 系统 MUST 返回 409，并保持原 Run 不变

### Requirement: Run 状态查询必须返回前端可消费的进度投影
系统 MUST 提供 `GET /api/v2/due-diligence/runs/{run_id}`，返回 Run 状态、当前阶段、最新事件序号、阶段进度、snapshot 摘要、Agent/Check/Review/Budget 投影、终止原因和 `result_available`。查询不得重新执行模型或外部数据源。

#### Scenario: 查询 multi 调查进度
- **WHEN** Run 正处于 investigation 阶段且部分 Check 已提交
- **THEN** 响应 SHALL 包含 48 子模块采集进度、15 项 Check 的完成计数、Agent 状态、Reviewer round 和最新 sequence

#### Scenario: 查询未知 Run
- **WHEN** `run_id` 不存在或不属于当前主体
- **THEN** 系统 SHALL 返回不可区分的 404，不披露其他主体的 Run 是否存在

### Requirement: 结果接口必须区分执行中、可恢复结果和失败
系统 MUST 提供 `GET /api/v2/due-diligence/runs/{run_id}/result`。运行中且无结果时返回 202；完成或 partial 时返回满足现有 `DueDiligenceResult` 的安全 JSON；失败且无结果时返回可解析的错误记录。结果 SHALL 保留 agent_results、context_snapshot、execution_cost、comparison_metadata 和 report_markdown。

#### Scenario: 获取完成结果
- **WHEN** Run 状态为 `completed`
- **THEN** 系统 SHALL 返回 200 和完整 `DueDiligenceResult`，其报告结构、Evidence 引用和规则结果可校验

#### Scenario: 获取 partial 结果
- **WHEN** Run 因来源缺口、预算或取消已生成可用的部分报告
- **THEN** 系统 SHALL 返回 200，状态为 `partial`，并同时披露 coverage、errors 和 termination reason

#### Scenario: Run 仍在执行
- **WHEN** Run 尚未完成且没有可用结果
- **THEN** 系统 SHALL 返回 202 和当前状态链接，不重复创建执行任务

### Requirement: 取消接口必须传播到所有下游运行时
系统 MUST 提供 `POST /api/v2/due-diligence/runs/{run_id}/cancel`。取消 SHALL 幂等，向 coordinator、Agent Runtime、AgentTeams、MCP、DeepSearch 和工具传播取消令牌，并最终产生 `run.cancelled` 或明确的失败状态。

#### Scenario: 取消运行中的 multi Run
- **WHEN** Run 正在执行且主体调用 cancel
- **THEN** 系统 SHALL 停止未完成 Agent/Tool，保留已提交 Check，返回取消后的终止原因和完成/未完成任务集合

#### Scenario: 重复取消已终止 Run
- **WHEN** 客户端再次取消 `completed`、`failed` 或 `cancelled` Run
- **THEN** 系统 SHALL 返回当前终态，不重复执行取消副作用

### Requirement: 旧 result 接口必须作为兼容适配器保留
系统 SHALL 保留 `POST /api/v1/due-diligence/result` 的现有 JSON/SSE 和 `mode` query 语义。该路由 MUST 调用同一个 RunCoordinator，不得复制业务编排逻辑。`POST /invocations` 的业务输入也 SHALL 能适配到同一 coordinator。

#### Scenario: v1 JSON 兼容调用
- **WHEN** 客户端向旧 result 路由发送 `Accept: application/json`
- **THEN** 系统 SHALL 返回与 v2 result 等价的 `DueDiligenceResult`

#### Scenario: v1 SSE 兼容调用
- **WHEN** 客户端向旧 result 路由发送 `Accept: text/event-stream`
- **THEN** 系统 SHALL 订阅同一 Run 事件流，并以携带完整结果的 `report.completed` 结束

### Requirement: 请求、认证和运行期错误必须分层表达
系统 MUST 在创建前区分 400/401/403/406/409/422/429 等请求错误；创建成功后的来源、Agent、预算、取消和报告错误 MUST 写入 Run 的 error record 与公开终态事件。错误消息不得包含密钥、Prompt、reasoning 或原始响应。

#### Scenario: 运行期 Agent 失败
- **WHEN** Run 创建成功后某个 Agent 执行失败且无法生成 partial result
- **THEN** Run SHALL 进入 `failed`，结果查询返回错误记录，事件流包含 `run.failed`

#### Scenario: 未授权读取
- **WHEN** 其他主体查询已存在的 Run
- **THEN** 系统 SHALL 返回 404 或统一的授权错误，不返回企业名称、阶段或证据摘要
