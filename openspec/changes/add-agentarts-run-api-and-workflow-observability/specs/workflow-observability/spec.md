## ADDED Requirements

### Requirement: 公开事件必须使用版本化、可关联的 envelope
每个公开事件 MUST 包含 `schema_version`、`event_id`、`event_type`、`request_id`、`run_id`、Run 内单调递增的 `sequence`、`occurred_at`、`stage`、`actor`、可选 task/check 关联和经校验的 payload。`event_id` SHALL 在同一 Run 内唯一且可由 Run ID 与 sequence 稳定重建。

#### Scenario: 多 Agent 并发产生事件
- **WHEN** Leader、多个 specialist 和 Reviewer 几乎同时发布事件
- **THEN** 客户端 SHALL 收到不重复、严格递增 sequence 的事件，并能通过 actor/task/check 字段定位来源

### Requirement: 事件类型必须覆盖完整工作流生命周期
事件契约 MUST 支持 `run.started`、`run.phase.started`、`run.phase.completed`、`run.completed`、`run.partial`、`run.cancel_requested`、`run.cancelled`、Agent 完成/失败/取消，以及现有 acquisition、snapshot、model、check、submission、review、budget、report 和 skill evolution 事件。终止事件 SHALL 只出现一次。

#### Scenario: multi 发生 Reviewer 返工
- **WHEN** Reviewer 发现冲突并创建 RepairTask，specialist 完成修订后再次提交
- **THEN** 事件流 SHALL 保留 review issue、repair request、resubmission、review round 和最终 run 终止事件的关联

### Requirement: 事件必须在运行过程中实时发布
Agent Runtime、AgentTeams Runtime、EnterpriseContextAgent、SingleInvestigatorAgent 和 AgentTeamsInvestigatorTeam MUST 支持事件 sink/callback，将安全事件在产生后立即交给 EventPublisher。系统 MUST NOT 仅在 Agent 返回后批量回放全部事件作为实时进度。

#### Scenario: acquisition 仍在执行
- **WHEN** EnterpriseContextAgent 尚未完成 48 个子模块采集
- **THEN** SSE 订阅者 SHALL 先收到 acquisition/agent/model/tool/check 相关进度，而不是等待整个 acquisition 返回

### Requirement: 事件发布必须驱动统一状态投影
EventPublisher 在持久化事件后 MUST 应用 `RunProjection`，更新阶段状态、Agent、Check、Review、Budget、snapshot、coverage、section 和结果可用性。`GET /runs/{run_id}` 只能读取该投影，不得由路由层自行推断不同口径。

#### Scenario: Check 提交被接受
- **WHEN** `submission.accepted` 事件落库
- **THEN** 对应 Agent 和 Check 的 completed 计数、最近 submission_version 和 overall progress SHALL 原子更新

### Requirement: SSE 必须支持历史重放和断线续订
事件接口 MUST 支持 `Last-Event-ID` 和等价 `after` 游标；连接建立时先补发历史事件，再订阅新事件。SSE SHALL 提供 keepalive 和 retry 提示，且终止事件必须在发送前落库。attached 断开可按 profile 取消，detached 断开不得隐式取消 Run。

#### Scenario: 前端断线后重连
- **WHEN** 客户端最后收到 sequence=86 后以 `Last-Event-ID: 86` 重连
- **THEN** 服务 SHALL 从 sequence=87 开始补发，不重复 86，也不跳过已持久化事件

### Requirement: 公开事件必须递归脱敏并限制 payload
事件、投影、SSE、JSONL 和结果序列化 MUST 移除 Prompt 正文、`reasoning`、`reasoning_content`、`chain_of_thought`、密钥、未脱敏 MCP/网页响应和任意用户提供的秘密。模型事件最多暴露状态、usage、版本、受限 decision_summary 和 Evidence ID。

#### Scenario: 模型 chunk 包含私有推理
- **WHEN** runtime chunk 在任意嵌套层包含 `reasoning_content` 和 Authorization
- **THEN** 所有公开事件、事件日志和结果中均不得出现这些字段或其内容
