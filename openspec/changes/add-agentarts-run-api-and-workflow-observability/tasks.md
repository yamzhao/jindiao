## 1. 契约与配置基础

- [x] 1.1 定义 RunResource、RunCreateRequest、RunProgress、AgentView、CheckView、ReviewView 和 RunLink Pydantic 契约。
- [x] 1.2 扩展 RunStatus/RunTermination，覆盖 accepted、running、completed、partial、failed、cancelled 和阶段状态。
- [x] 1.3 定义 attached/detached execution profile、幂等键、session 和主体归属配置，并为无效配置增加启动校验。
- [x] 1.4 定义版本化 RunEvent envelope、actor/stage/task 关联和公开 payload 白名单，保持旧 RunEvent 可反序列化。
- [x] 1.5 增加运行态契约测试：字段边界、状态迁移、ID 安全、幂等冲突和隐私字段拒绝。

## 2. Run 存储与事件基础设施

- [x] 2.1 定义 RunRepository、EventStore、RunEventPublisher、RunProjection 和 CancellationToken Protocol。
- [x] 2.2 实现线程/协程安全的 InMemoryRunRepository、InMemoryEventStore 和订阅队列，用于单元测试及 deterministic harness。
- [x] 2.3 实现 Run 级 sequence allocator，保证 append 的 sequence 单调、唯一、可恢复，移除 SSE 与 trace 双序列来源。
- [x] 2.4 实现 RunProjection 的阶段、acquisition、snapshot、Agent、Check、Review、Budget、section 和结果可用性折叠逻辑。
- [x] 2.5 实现事件/结果原子写入、manifest/hash 校验、幂等操作收据和错误状态记录。
- [x] 2.6 为并发发布、重复 append、订阅者慢消费、终态唯一性和 partial/failed 恢复增加存储测试。

## 3. 实时事件管道

- [x] 3.1 重构 EventMapper，将 AgentExecutionEvent、TeamRuntimeEvent 和团队终态映射为公开事件，并补齐 agent/team/run lifecycle 事件。
- [x] 3.2 在 RunEventPublisher 中统一执行脱敏、schema 校验、序列分配、持久化、projection 更新和 subscriber fan-out。
- [x] 3.3 为 EnterpriseContextAgent、DeepSearchAgent、SingleInvestigatorAgent 和 AgentTeamsInvestigatorTeam 增加实时 event sink。
- [x] 3.4 改造 FormalDueDiligencePipeline，边执行边发布 acquisition、snapshot、investigation、review 和 repair 事件，不再只回放 tuple。
- [x] 3.5 将模型事件限制为状态、usage、版本、受限 decision_summary 和 Evidence ID，丢弃 reasoning/raw chunk。
- [x] 3.6 增加实时事件测试，验证首个 acquisition/Agent 事件在阶段结束前可见，且公开产物不含 Prompt、密钥和原始响应。

## 4. RunCoordinator 与生命周期

- [x] 4.1 实现 RunCoordinator.create、execute、get、get_result、cancel 和 subscribe，统一调用 formal/legacy 执行路径。
- [x] 4.2 将现有 DueDiligenceService.run/stream 改为 coordinator facade，保持 JSON/SSE 结果语义不变。
- [x] 4.3 实现 accepted→running→terminal 状态迁移、阶段计时、活动任务注册、幂等创建和终态事件。
- [x] 4.4 实现 cancellation token 向 Agent Runtime、AgentTeams、MCP、DeepSearch 和 Toolset 的传播及资源清理。
- [x] 4.5 实现来源/Agent/预算/报告失败和可恢复 partial result 的统一错误映射与 termination reason。
- [x] 4.6 增加 coordinator 的超时、重复请求、取消竞态、运行期失败和结果可用性测试。

## 5. HTTP API 与 SSE

- [x] 5.1 新增 `POST /api/v2/due-diligence/runs`，返回 202 RunResource、links、session 和幂等收据。
- [x] 5.2 新增 `GET /api/v2/due-diligence/runs/{run_id}`，只读取 RunProjection 并执行主体归属校验。
- [x] 5.3 新增 `GET /api/v2/due-diligence/runs/{run_id}/events`，支持 Last-Event-ID/after、历史补发、keepalive、retry 和终态关闭。
- [x] 5.4 新增 `GET /api/v2/due-diligence/runs/{run_id}/result`，区分 202、completed、partial 和失败错误响应。
- [x] 5.5 新增 `POST /api/v2/due-diligence/runs/{run_id}/cancel`，实现幂等取消和终止详情返回。
- [x] 5.6 保留 `/api/v1/due-diligence/result` 和 mode query 兼容适配，并让 `/invocations` 复用同一 coordinator。
- [x] 5.7 增加 API 契约测试：JSON/SSE、幂等、权限、404/409/422/429、断线重连、取消和 JSON/SSE 结果等价。

## 6. 持久化与 AgentArts 存储适配

- [x] 6.1 将现有 RunArtifactStore 重构为 LocalRunStore adapter，保留安全 JSONL/result/report 产物和测试兼容性。
- [x] 6.2 实现 AgentArts session/SFS adapter，验证 metadata、事件、result、report 的共享可读性、原子替换和 hash manifest。
- [x] 6.3 增加存储不可用、写入中断、manifest 损坏、重启遗留 running Run 和跨实例读取测试。
- [x] 6.4 实现事件/产物保留、大小上限、慢订阅者背压和关键事件不可丢弃策略。
- [x] 6.5 增加隐私回归，确认本地、SFS、SSE、projection 和 result 序列化都遵守统一脱敏规则。

## 7. AgentArts 协议与容器交付

- [x] 7.1 增加 `POST /invocations` 标准 JSON/SSE adapter，兼容直接请求体和平台输入 envelope。
- [x] 7.2 增加 `GET /ping`，实现 Initing、Healthy、HealthyBusy 和非健康状态，且不触发业务执行。
- [x] 7.3 将 Dockerfile、健康检查和启动命令切换为 0.0.0.0:8080，完成 ARM64 buildx 构建验证。
- [x] 7.4 编写 PREFIX_MATCH 多路径联调脚本，覆盖 Run create/query/events/result/cancel 的 GET/POST/SSE 调用。
- [x] 7.5 验证 X-Hw-Agentarts-Session-Id、X-Hw-Agentgateway-User-Id、认证、endpoint 版本和 Run 归属行为。
- [ ] 7.6 完成 AgentArts POC：HealthyBusy、后台任务、SFS/会话存储、断线重连、取消和 sandbox 回收语义。

## 8. attached/detached 发布门禁与文档

- [x] 8.1 默认启用 attached 竞赛配置，验证 v1/v2 SSE 在无外部数据库和无天眼查凭据的 Mock 环境可运行。
- [x] 8.2 只有全部 AgentArts 异步生命周期和持久化 POC 通过后才开放 detached 配置，失败时明确拒绝或降级 attached。
- [x] 8.3 增加运行 profile、存储 backend、事件 schema 和 API 版本的迁移/回滚配置说明。
- [x] 8.4 更新 docs/api/README.md、technical-stack.md、product-design.md、部署说明和前端事件消费示例。
- [x] 8.5 说明 `deepsearch-agent` 属于 acquisition、mode 只切换 investigation，且 Run 观测不改变公平比较口径。
- [x] 8.6 与 add-user-feedback-reporting-loop 对齐 RunRepository、结果链接、归属和策略版本冻结，避免重复元数据系统。

## 9. 质量门禁与交付验证

- [x] 9.1 运行 contract/unit/integration/e2e 测试，覆盖正常、缺口、冲突、预算耗尽、失败、取消和重启场景。
- [x] 9.2 运行 Ruff、mypy、OpenSpec 严格校验和 Docker 镜像启动/健康检查验证。
- [ ] 9.3 在 AgentArts 目标环境执行多接口 JSON/SSE/GET 联调并保存脱敏结果和事件样例。
- [x] 9.4 验证旧 result 客户端无感兼容、v2 Run 前端可重连、最终结果与既有 DueDiligenceResult 等价。
- [x] 9.5 记录 detached 未通过时的限制，不把 in-process background task 或本地文件误称为可靠产品能力。
