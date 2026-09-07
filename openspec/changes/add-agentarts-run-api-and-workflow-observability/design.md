## Context

Jindiao 当前已经形成了稳定的业务执行链：共享 acquisition（EnterpriseContextAgent 和受限 DeepSearch）生成不可变 `EnterpriseContextSnapshot`，single 或 AgentTeams 执行固定 CheckCatalog，随后由 Evidence 门禁、RiskRuleEngine 和 ResultAssembler 生成 `DueDiligenceResult`。`mode=single|multi` 只改变 investigation 拓扑，不能改变共享事实、核查目录或确定性裁决。

现有 API 仍只有 `POST /api/v1/due-diligence/result`。JSON 请求会等待完整结果，SSE 运行则在连接生命周期内创建 producer；formal pipeline 的一部分 Agent 事件在 Agent 返回后才批量发布。运行 ID、事件序列和最终产物没有独立的可查询资源语义。当前 `RunArtifactStore` 写入本地磁盘，也不能直接作为 AgentArts 线上状态存储。

AgentArts 的 PREFIX_MATCH 可以把标准 `/invocations` 和自定义路径路由到同一个运行时，因此 API 可以拆成 Run 创建、状态、事件、结果和取消等多个接口。平台同时要求 HTTP 服务监听 `0.0.0.0:8080`、使用 ARM64 镜像并提供 `/invocations`；`/invocations` 支持 JSON/SSE，`/ping` 用于健康检查。平台运行时具有弹性伸缩能力，本地磁盘不应被视为跨沙箱持久化介质。

## Goals / Non-Goals

**Goals:**

- 建立稳定的 Run 资源模型，使前端可以创建运行、观察阶段和 Agent/Check 进度、重连事件、获取结果并取消运行。
- 在不泄漏 Prompt、reasoning、密钥和原始外部响应的前提下，将 acquisition、snapshot、investigation、review、repair、budget 和 reporting 过程转换为可审计公开事件。
- 让事件实时从 Agent Runtime 进入统一发布管道，并由同一个序列分配器服务 SSE、持久化轨迹和状态投影。
- 保留 `POST /api/v1/due-diligence/result` 的 JSON/SSE 兼容行为，并增加 AgentArts `/invocations` 协议适配。
- 通过 `RunRepository`、`EventStore` 和 `RunProjection` 解耦执行、存储、SSE 和前端读模型；本地 Mock 与 AgentArts SFS/会话存储使用不同实现。
- 为 attached（请求绑定）和 detached（请求解耦）执行模式定义明确的取消、重启、持久化和能力门禁。
- 保持现有 8/48 报告结构、Evidence 引用、RiskRuleEngine、single/multi 公平比较和 DeepSearch acquisition 边界不变。

**Non-Goals:**

- 不把每个 Agent、Tool、MCP 或 DeepSearch 暴露为独立业务 HTTP 接口。
- 不在本变更中修改风险规则、核查目录、Prompt 内容、Agent 角色或公平比较指标。
- 不把前端可见事件设计成模型思维链转储；`agent.output`、reasoning 和原始模型 chunk 仍然是内部数据。
- 不默认承诺跨 AgentArts 沙箱的 detached 任务恢复；必须先通过平台异步生命周期和持久化 POC。
- 不在业务代码中直接绑定 SQLite、本地路径或某一个云存储 API；持久化通过接口和配置选择。
- 不在本变更中实现前端页面、反馈策略审批 UI 或通用任务队列。

## Decisions

### 1. 使用 Run 资源而不是继续扩展单次 result 响应

新增版本化路径：

```text
POST /api/v2/due-diligence/runs
GET  /api/v2/due-diligence/runs/{run_id}
GET  /api/v2/due-diligence/runs/{run_id}/events
GET  /api/v2/due-diligence/runs/{run_id}/result
POST /api/v2/due-diligence/runs/{run_id}/cancel
```

创建请求沿用现有 `DueDiligenceRequest` 字段，并把 `mode` 放入请求体；响应返回 `request_id`、`run_id`、AgentArts session、状态、时间和 HATEOAS links。所有后续接口只接收不透明的安全 `run_id`，不接受文件路径或任意 artifact URI。

备选方案是继续让前端只解析 `POST result` 的 SSE。该方案无法支持页面刷新后的状态恢复、独立结果查询和取消，因此只保留为 v1 兼容路径。

### 2. 以一个协调器统一 JSON、SSE、Run API 和 AgentArts 适配

引入 `RunCoordinator` 作为唯一执行入口：

```text
Protocol adapters
  ├── /invocations
  ├── /api/v1/due-diligence/result
  └── /api/v2/due-diligence/runs/*
             ↓
       RunCoordinator
             ↓
  FormalDueDiligencePipeline / deterministic harness
             ↓
  ResultAssembler + RunEventPublisher
```

路由层不能各自调用 `DueDiligenceService.run()`、`stream()` 并重新拼接状态，否则会再次出现 ID、序列、错误和取消语义不一致。旧 service 方法改为 coordinator 的兼容 facade。

### 3. attached 与 detached 作为显式执行配置

| 配置 | 适用场景 | 连接断开 | 持久化要求 |
| --- | --- | --- | --- |
| `attached` | 比赛、离线 Mock、AgentArts 首次联调 | 可取消下游运行 | 内存/本地产物即可，但不承诺重连恢复 |
| `detached` | 产品化前端、多次查询、页面刷新 | Run 继续执行 | 必须使用已验证的共享存储和任务生命周期 |

`POST /api/v2/due-diligence/runs` 只在 coordinator 能提供对应 profile 时接受。AgentArts 首版默认使用 attached SSE；detached 必须通过 POC 开关显式启用。不能因为 `asyncio.create_task()` 创建成功就宣称 detached 可靠。

### 4. 公开事件采用稳定 envelope，业务 payload 保持白名单

新事件 envelope 包含：

```text
schema_version
event_id
event_type
request_id
run_id
sequence
occurred_at
stage
actor { kind, id, role }
task { task_id, check_id } (optional)
payload
```

`event_id` 由 `run_id + sequence` 生成，`sequence` 由 Run 级持久化分配器产生。payload 继续保留现有按事件类型的根键约束，例如 `check.completed` 使用 `payload.check`，但只允许安全字段：状态、数量、版本、Evidence ID、受限 `decision_summary`、错误码和终止原因。

增加 `run.started`、`run.phase.started`、`run.phase.completed`、`run.completed`、`run.partial`、`run.cancel_requested`、`run.cancelled` 以及 Agent 完成/失败/取消事件；现有 acquisition、snapshot、check、submission、review、budget、report 事件保持兼容。旧 SSE 客户端只读取原有事件名，新客户端使用 `schema_version` 和 `stage`。

### 5. 所有内部事件经过一个实时发布管道

发布管道职责如下：

```text
AgentExecutionEvent / TeamRuntimeEvent
              ↓
       CanonicalEventMapper
              ↓
       RunEventPublisher
        ├── allocate sequence
        ├── redact and validate
        ├── EventStore.append
        ├── RunProjection.apply
        ├── subscriber queues
        └── JSONL/OpenTelemetry trace
```

`FormalDueDiligencePipeline`、`EnterpriseContextAgent`、`SingleInvestigatorAgent` 和 `AgentTeamsInvestigatorTeam` 需要把事件 sink 传递到实际的 runtime stream；不能等 Agent 返回后再从 tuple 批量回放。模型 reasoning 和 raw chunk 在 mapper 层丢弃，只有模型请求状态和 usage 摘要可见。

### 6. RunProjection 作为前端读模型

`GET /runs/{run_id}` 不重新计算结果，而是读取由事件折叠形成的 projection。最小字段包括：

- Run 状态、当前阶段、开始/结束时间和最新事件序号；
- acquisition 的主体解析、48 子模块覆盖和 Evidence 数量；
- snapshot ID/SHA-256、缺口和冲突数量；
- investigation 的 Agent 列表、15 项 Check 的 assigned/started/completed 状态；
- multi 的 review round、RepairTask 和 Reviewer 状态；
- budget 使用量、剩余量、峰值并发和终止原因；
- reporting 的 8 个 section 完成数和 `result_available`。

投影只暴露结构化状态，不暴露快照原文、MCP 响应或私有 Agent 输出。最终事实仍以 `DueDiligenceResult` 为准。

### 7. EventStore 支持重放，而不是让 SSE 直接消费临时队列

事件接口读取 `Last-Event-ID` 或 `after`，先从 EventStore 补发历史事件，再订阅新事件。连接断开不自动改变 Run 状态；attached 模式由 adapter 的取消策略决定，detached 模式必须继续执行。SSE 使用 keepalive、`retry` 和有限订阅队列；终止事件必须持久化后再发送。

事件序列必须全局按 Run 单调递增。EventStore append 失败时不能向客户端发送看似成功的序列；无法持久化的运行进入可识别的 failed 状态。

### 8. 持久化使用可替换实现并区分本地与 AgentArts

抽象接口：

```python
RunRepository.create / get / update_status / save_result / save_error
EventStore.append / read_after / subscribe / last_sequence
```

本地测试使用 `InMemoryRunStore`，deterministic harness 可使用现有 artifact 目录。AgentArts 使用会话存储保存单 Session 中间状态，并优先使用挂载的 SFS Turbo 保存跨实例可读的 Run metadata、事件、result 和 report。文件写入采用安全 segment、临时文件替换、manifest/hash 和 redaction；不把本地容器目录作为线上保证。

如果未来需要强一致多主写入，再增加数据库 adapter；本变更不要求引入数据库服务。

### 9. 取消、错误和部分结果采用显式语义

取消令牌从 HTTP 层传到 coordinator、formal pipeline、Agent runtime、AgentTeams Runner、MCP 和 DeepSearch。运行可以在已有完整证据和章节足够时生成 `partial` result，否则生成 `failed`。所有终态都必须发出对应的 run 事件，并写入终止原因。

客户端的 HTTP 错误与运行期错误分开：请求校验/认证在创建前返回 4xx；创建成功后发生的 Agent、来源、预算或报告错误写入 Run 状态和事件，查询接口返回可解析的 error record。

### 10. AgentArts 只作为协议和生命周期边界

服务必须提供：

```text
POST /invocations       JSON/SSE 标准协议
GET  /ping              Initing/Healthy/HealthyBusy
PREFIX_MATCH custom paths for /api/v2/...
```

外部网关路径由 AgentArts 拼接为 `/runtimes/{runtime}/invocations/{custom_path}`，应用内部不实现 `/runtimes` 前缀。所有调用使用稳定的 `X-Hw-Agentarts-Session-Id`；Run 归属可使用网关传入的用户标识。Dockerfile 改为 8080/ARM64 交付，端口、SSE、会话存储和 detached 生命周期通过独立 POC 验证。

## Risks / Trade-offs

- [AgentArts 沙箱回收导致后台 Run 丢失] → detached 默认关闭；使用会话存储/SFS，并在 POC 通过后才启用。重启时遗留 running Run 标记为 interrupted/failed，不伪造自动恢复。
- [多实例同时追加事件造成序列冲突] → 单一 EventStore sequence allocator；文件实现限定单 writer，未来多主场景使用带 CAS 的存储 adapter。
- [事件实时性改善后暴露更多敏感数据] → canonical mapper 统一递归脱敏和字段白名单；不允许原始 chunk 直出。
- [SSE 订阅者过多导致执行阻塞] → 有界队列、事件持久化和历史重放；非关键 telemetry 可丢弃，终态/check/review 事件不可丢弃。
- [v1 客户端依赖旧字段和一次性返回] → 保留 v1 adapter，新增字段采用向后兼容方式；v2 读模型与 `DueDiligenceResult` 分离。
- [formal pipeline 事件接口改造引入 Agent 行为变化] → 先为 runtime event sink 增加契约测试，再逐步切换 acquisition、single、multi；业务结果和公平比较测试作为回归门禁。
- [SFS/NFS 上原子替换或锁行为与本地不同] → 只把 SFS adapter 用于安全产物和追加日志，启动时执行读写/锁探针；失败时拒绝 detached，而不是回退到本地盘。
- [Run 查询暴露企业敏感信息] → 只返回摘要和引用；使用 AgentArts 入站认证、用户归属校验和安全 run_id，完整结果继续经过既有脱敏序列化。

## Migration Plan

1. 新增 Run、阶段、进度、事件 envelope、取消令牌和 repository/store Protocol；为现有 `DueDiligenceResult` 增加兼容适配，不改变业务结果。
2. 实现内存 RunRepository/EventStore/Projection，将当前 v1 JSON/SSE 接到 `RunCoordinator`，先保证离线 Mock 和旧端到端测试不回归。
3. 改造 EventMapper 和 formal runtime sink，逐事件实时发布 acquisition、snapshot、single、multi、review、section 和终态事件；删除 SSE/trace 双序列分配。
4. 增加 v2 Run 路由、Last-Event-ID 重放、结果查询、取消和幂等创建；完成状态、错误、partial result 和并发订阅测试。
5. 实现本地产物 adapter 与 AgentArts storage adapter；完成脱敏、manifest/hash、重启和遗留运行处理。
6. 调整 Dockerfile、`/ping`、端口和 AgentArts 标准 `/invocations` 适配；使用 PREFIX_MATCH 验证 GET/POST/SSE/custom path。
7. 以 attached 配置进行比赛演示和回滚验证；只有 AgentArts 异步生命周期、HealthyBusy、SFS/会话存储、多实例重连 POC 通过后，才开放 detached 配置。
8. 更新 `docs/api/README.md`、`technical-stack.md`、部署说明和反馈闭环说明。`add-user-feedback-reporting-loop` 复用 RunRepository、结果链接和归属模型，不重复建立运行元数据表。

回滚策略：关闭 v2/detached 开关并恢复 v1 adapter；保留已写入的安全事件和结果产物。任何 EventStore 或 AgentArts storage 初始化失败时，服务只启用 attached/local profile，不静默宣称线上可恢复。

## Open Questions

- AgentArts 会话存储是否能满足同一 Run 在不同自定义 endpoint 和不同沙箱实例间的读取一致性，仍需实测确认。
- SFS Turbo 对追加 JSONL、文件锁和临时文件替换的实际语义，需要在目标 VPC 中做最小探针。
- `/invocations` 的 SSE 代理是否完整保留 `Last-Event-ID`、keepalive 和断线重连头，需要网关联调确认。
- detached 任务被 sandbox 回收时，平台是否提供可恢复执行或至少可靠的终止回调；若没有，产品 profile 需要外部任务执行器，这不在本变更首期范围内。
- 事件保留时长、单 Run 事件上限和前端是否需要按阶段过滤，待实际页面消费方式确认。
