## Why

当前尽调服务已经能够执行共享证据采集、冻结快照、single/multi 调查、确定性裁决并输出完整结果，但 API 仍把一次运行绑定在单个 `POST /api/v1/due-diligence/result` 请求和 SSE 连接上。前端无法可靠查询运行状态、断线续订事件、单独获取结果或发起取消；同时 AgentArts 的标准 `/invocations`、PREFIX_MATCH 路由、8080/ARM64 部署约束尚未形成明确的服务契约。

现在需要把“运行过程展示”从一次性响应提升为可查询的 Run 资源，同时保留现有结果接口兼容性，并为 AgentArts 的 attached 与 detached 两种执行模式建立可验证的边界。

## What Changes

- 新增版本化 Run 资源 API：创建 Run、查询状态、订阅/重放事件、获取最终结果和取消运行。
- 新增 AgentArts 协议适配端点 `POST /invocations` 与 `GET /ping`，并支持 PREFIX_MATCH 下的自定义业务路径。
- 建立统一 Run 生命周期状态机：accepted、running、completed、partial、failed、cancelled，以及 acquisition、snapshot、investigation、adjudication、reporting 阶段状态。
- 将当前 acquisition、snapshot、Agent、Check、Submission、Review、Repair、Budget 和 Report 事件统一为带版本、阶段、执行者和任务关联的公开事件契约。
- 将事件序列分配、事件持久化、SSE 订阅和 Run 状态投影收敛到同一个发布管道，支持 `Last-Event-ID` 断线重连和事件重放。
- 改造 formal pipeline，使 Agent Runtime 的安全事件在执行过程中实时发布，而不是仅在 Agent 返回后批量回放。
- 引入可替换的 RunRepository/EventStore：本地 Mock 使用内存或本地产物，AgentArts 使用会话存储或 SFS Turbo；不把容器本地磁盘作为线上持久化保证。
- 增加取消传播、partial result、幂等创建、运行归属和重启/实例回收时的失败语义。
- 保留 `POST /api/v1/due-diligence/result` 作为兼容适配器，继续支持 JSON、SSE 和 `mode=single|multi`。
- **BREAKING（部署配置）**：AgentArts 交付镜像切换为 `0.0.0.0:8080`、ARM64 和 `/ping` 健康检查；本地开发可通过配置保留其他端口。
- 不新增 Agent、Tool、MCP 或 DeepSearch 的独立业务接口；`deepsearch-agent` 继续属于共享 acquisition，不计入 investigation roster。

## Capabilities

### New Capabilities

- `run-resource-api`: Run 创建、查询、结果、取消、幂等和兼容接口语义。
- `workflow-observability`: 版本化公开事件、实时发布、事件存储、SSE 重放和进度投影。
- `run-persistence-and-replay`: Run/事件/结果持久化抽象、故障恢复、数据保留和隐私边界。
- `agentarts-runtime-deployment`: `/invocations`、`/ping`、PREFIX_MATCH、会话标识、健康状态和容器部署契约。

### Modified Capabilities

无。仓库当前没有已发布的主规格；既有 OpenSpec 变更中的结果、single/multi、DeepSearch 和反馈能力作为兼容约束，在本变更的设计与规格中进行集成说明。

## Impact

- 影响 `src/jindiao/api`、`application/service.py`、`formal_pipeline.py`、Agent Runtime、AgentTeams Runtime、事件契约、SSE、观测产物和 Dockerfile。
- 新增 RunRepository、EventStore、RunProjection、取消令牌和 AgentArts 存储/部署适配器及其测试。
- 扩展 `DueDiligenceResult` 周边的运行态读模型，但不改变既有风险规则、证据门禁、8/48 报告结构和 single/multi 公平比较口径。
- 新增 AgentArts 集成 POC、attached 竞赛配置和可选 detached 产品配置；detached 默认只有在异步生命周期及持久化验证通过后启用。
- 与 `add-user-feedback-reporting-loop` 共享 Run 查询、结果产物和权限/归属模型，避免重复建立运行元数据系统。
