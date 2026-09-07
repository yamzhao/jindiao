## ADDED Requirements

### Requirement: 系统只能暴露一个尽调业务端点
系统 SHALL 仅通过 `POST /api/v1/due-diligence/result` 接收企业尽调任务；内部 Agent、工具、规则引擎和 DeepSearch 不得暴露独立业务 HTTP 端点。

#### Scenario: 客户端提交合法请求
- **WHEN** 客户端向 result 端点提交满足 schema 的企业标识和运行参数
- **THEN** 系统 SHALL 启动一次尽调运行并分配 `request_id` 和 `run_id`

### Requirement: 同一端点必须支持 JSON 与 SSE 内容协商
系统 SHALL 根据 `Accept` 头，以 `application/json` 返回完成结果，或以 `text/event-stream` 返回进度事件及最终结果。

#### Scenario: 请求 JSON
- **WHEN** 客户端发送 `Accept: application/json`
- **THEN** 系统 SHALL 在运行结束后返回满足 Result schema 的单个 JSON 文档

#### Scenario: 请求 SSE
- **WHEN** 客户端发送 `Accept: text/event-stream`
- **THEN** 系统 SHALL 在运行过程中发送有序事件，并以携带完整 Result 的终止事件结束

#### Scenario: 请求不支持的媒体类型
- **WHEN** `Accept` 不包含 JSON 或 SSE
- **THEN** 系统 SHALL 返回明确的内容协商错误，不得创建运行

### Requirement: 同一端点必须支持编排模式切换
系统 SHALL 通过唯一 result 端点的可选 `mode=single|multi` 查询参数选择编排策略，默认使用 `multi`，并在最终 Result `meta.mode` 中回显实际模式。

#### Scenario: 客户端选择单智能体
- **WHEN** 客户端向 result 端点提交 `mode=single`
- **THEN** JSON 或 SSE 运行 SHALL 使用 single-agent 策略，且不得注册第二个业务端点

#### Scenario: 客户端提交非法模式
- **WHEN** `mode` 不是 `single` 或 `multi`
- **THEN** API SHALL 在创建运行前返回请求校验错误

### Requirement: 最终结果必须提供完整前端视图模型
Result MUST 包含 `meta`、`subject`、`decision`、`risk_summary`、`coverage`、`sections`、`findings`、`evidence`、`agent_trace`、`collaboration`、`evaluation`、`skill_evolution`、`report_markdown` 和 `errors`。

#### Scenario: 正常完成尽调
- **WHEN** 尽调运行成功完成
- **THEN** 所有必需顶层字段 SHALL 存在，且引用的 Finding、Evidence、Agent 和规则标识必须可解析

#### Scenario: 带缺口完成尽调
- **WHEN** 非关键数据源失败但系统仍可输出不完整报告
- **THEN** Result SHALL 保留全部顶层字段，并在 coverage、errors 和 report_markdown 中一致披露缺口

### Requirement: SSE 事件必须稳定、有序且可关联
每个 SSE 事件 SHALL 包含事件类型、`request_id`、`run_id`、递增 `sequence`、时间戳和符合事件类型的 payload。

#### Scenario: 多 Agent 并行产生事件
- **WHEN** 多个 Agent 几乎同时上报进度
- **THEN** API 层 SHALL 为客户端输出单调递增且不重复的 sequence

### Requirement: SSE 必须覆盖核心产品状态
系统 SHALL 支持 `run.accepted`、`entity.resolved`、`plan.created`、`agent.started`、`evidence.collected`、`source.fallback`、`conflict.detected`、`repair.requested`、`section.completed`、`report.completed`、`skill_evolution.proposed` 和 `run.failed` 事件。

#### Scenario: 发现证据冲突并返工
- **WHEN** Reviewer 创建冲突并由 Leader 发出 RepairTask
- **THEN** 流 SHALL 依次包含可关联的 `conflict.detected` 和 `repair.requested` 事件

### Requirement: JSON 与 SSE 的最终结果必须等价
同一冻结输入、场景、模型配置和运行标识下，JSON 模式的 Result 与 SSE `report.completed` 中的 Result SHALL 满足同一 schema 和业务语义。

#### Scenario: 比较两种响应模式
- **WHEN** 契约测试分别执行 JSON 和 SSE 并忽略时间戳等运行差异字段
- **THEN** 两种最终结果的主体、决策、风险、Evidence 引用、章节和报告内容 SHALL 等价

### Requirement: Markdown 报告必须来源于结构化结果
系统 MUST 使用与前端相同的 ReportViewModel 渲染 Markdown，包含结论、风险摘要、全部业务章节、证据与来源说明、数据缺口和免责声明。

#### Scenario: 结构化风险发生变化
- **WHEN** accepted Findings 或规则结果改变后重新组装 Result
- **THEN** `risk_summary`、相关 section 和 `report_markdown` SHALL 同步反映变化，不得维护独立口径

#### Scenario: 报告包含 Mock 或降级数据
- **WHEN** ReportViewModel 包含 Mock Evidence 或 `degraded_mock` 状态
- **THEN** Markdown SHALL 仅在报告顶部显示一次醒目的 Mock 数据提示，正文不得重复渲染同类提醒，同时 Evidence 来源字段仍须可追溯

### Requirement: 错误与取消必须具有明确语义
系统 SHALL 区分请求、主体、数据源、Agent、审查、规则和报告错误；SSE 客户端断开时 SHALL 取消仍在运行的下游任务。

#### Scenario: SSE 客户端主动断开
- **WHEN** 服务检测到事件流连接关闭
- **THEN** 系统 SHALL 取消未完成 Agent 和工具任务，并记录运行取消状态

#### Scenario: 主体无法唯一确定
- **WHEN** 企业主体消歧失败
- **THEN** 系统 SHALL 返回失败结果或 `run.failed`，不得生成貌似完整的尽调报告
