## ADDED Requirements

### Requirement: 镜像必须兼容 AgentArts HTTP 入栈协议
AgentArts 交付镜像 MUST 监听 `0.0.0.0:8080`、使用 ARM64 架构并提供 `POST /invocations`。`/invocations` SHALL 接收 JSON，并根据 `Accept` 返回 JSON 或 SSE；本地开发端口可通过配置覆盖但不能改变 AgentArts 镜像默认值。

#### Scenario: AgentArts 标准 JSON 调用
- **WHEN** 网关向 `/invocations` 发送合法 JSON 请求并要求 `application/json`
- **THEN** 服务 SHALL 调用 RunCoordinator 并返回 JSON 结果或明确的 accepted/run resource

#### Scenario: AgentArts 标准 SSE 调用
- **WHEN** 网关向 `/invocations` 发送合法请求并要求 `text/event-stream`
- **THEN** 服务 SHALL 返回符合公开事件契约的 SSE，并在终态发送完成或失败事件

### Requirement: PREFIX_MATCH 必须路由所有版本化业务路径
部署配置 MUST 使用 PREFIX_MATCH，使 `/invocations` 和 `/invocations/{custom_path}` 同时可用。自定义 path SHALL 覆盖 v2 Run API 的 GET、POST 和 SSE 路由，应用内部不得重复实现 `/runtimes/{runtime_name}` 前缀。

#### Scenario: 网关调用 Run 创建和查询
- **WHEN** 网关分别调用 `.../invocations/api/v2/due-diligence/runs` 的 POST 和 `.../runs/{run_id}` 的 GET
- **THEN** 两次调用 SHALL 路由到同一个运行时和同一 RunRepository，不得因为 HTTP 方法不同而被拒绝

### Requirement: `/ping` 必须反映初始化和后台运行状态
服务 MUST 提供 `GET /ping`，初始化时返回 `Initing`，无后台 Run 且可接受请求时返回 `Healthy`，存在 detached 后台 Run 时返回 `HealthyBusy`；健康检查不得触发模型、MCP 或尽调执行。

#### Scenario: detached Run 执行期间健康检查
- **WHEN** 至少一个 detached Run 正在执行
- **THEN** `/ping` SHALL 返回 HTTP 200 和 `{"status":"HealthyBusy"}`

### Requirement: 会话和身份头必须参与路由与授权
服务 MUST 支持并记录 `X-Hw-Agentarts-Session-Id` 作为 AgentArts 会话关联，使用 `X-Hw-Agentgateway-User-Id` 或网关认证主体校验 Run 归属。跨 endpoint 访问同一 Run 时，客户端必须保持一致的 session/endpoint 版本策略。

#### Scenario: 同一会话订阅 Run 事件
- **WHEN** 创建、查询和订阅请求使用同一个合法 session ID
- **THEN** 服务 SHALL 关联到同一 Run，并只返回该主体可见的数据

### Requirement: detached 能力必须通过部署前探针门禁
系统 MUST 将 detached profile 作为显式配置，只有在 AgentArts 后台任务、HealthyBusy、共享存储、跨 endpoint session、SSE 重连和取消传播探针全部通过后才能启用。探针失败时 SHALL 使用 attached profile 或拒绝创建 detached Run。

#### Scenario: 平台不支持后台任务恢复
- **WHEN** detached 生命周期探针发现沙箱回收会丢失后台任务且没有恢复机制
- **THEN** 服务 SHALL 禁止 detached，比赛演示继续使用 attached SSE，不得返回虚假的 202 可恢复承诺

### Requirement: 部署观测和敏感配置必须安全
AgentArts 可观测日志 SHALL 记录 request/run/phase/status/latency/usage 等安全字段，不得记录 API key、Authorization、Prompt、reasoning 或原始响应。模型、MCP 和网关凭据 MUST 通过运行时环境或平台认证注入。

#### Scenario: 开启 LTS 日志
- **WHEN** AgentArts 运行时启用日志上报
- **THEN** 日志中 SHALL 能按 run_id 关联阶段耗时和终态，但搜索密钥和私有推理内容不得命中
