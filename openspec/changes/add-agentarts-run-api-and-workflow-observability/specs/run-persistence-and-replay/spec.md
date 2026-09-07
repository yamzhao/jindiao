## ADDED Requirements

### Requirement: 执行层必须依赖可替换的 RunRepository 和 EventStore
系统 MUST 通过接口提供 Run 的创建、读取、状态更新、结果保存、错误保存，以及事件 append、`read_after`、订阅和最新序号查询。业务编排 MUST 不直接操作具体文件路径、SQLite 或云存储 API。

#### Scenario: 离线 Mock 运行
- **WHEN** 测试使用 deterministic harness 执行 Run
- **THEN** 系统 SHALL 可注入内存 repository/event store，且 API 语义与 AgentArts profile 一致

### Requirement: AgentArts profile 不得依赖容器本地磁盘持久化
在 AgentArts profile 中，Run metadata、事件、result、report 和可重连所需的索引 MUST 写入已验证的会话存储或共享 SFS Turbo。若共享存储不可用，系统 MUST 禁止开启 detached，不得静默回退到容器本地目录。

#### Scenario: 沙箱被回收后查询 Run
- **WHEN** Run 已写入共享存储且原沙箱被销毁，后续请求被路由到另一实例
- **THEN** 新实例 SHALL 能读取 Run 状态、事件和结果，或明确返回存储不可用错误

### Requirement: 事件和结果写入必须原子且可校验
EventStore MUST 为每个 Run 分配单调且不重复的 sequence；终态状态、终止事件和结果文件 MUST 按“先写安全产物、校验 manifest/hash、再提交可见状态”的顺序发布。部分写入不得成为可查询的 completed 结果。

#### Scenario: 结果写入中进程失败
- **WHEN** result 写入或 manifest 校验在提交前失败
- **THEN** Run SHALL 保持非 completed 状态并记录可诊断错误，客户端不得读到半个结果

### Requirement: 重启和实例回收必须有明确恢复语义
服务启动时 MUST 扫描或查询遗留 `running` Run；若没有可靠的外部执行恢复能力，遗留 Run SHALL 标记为 `failed` 或 `interrupted`，不得自动伪造继续执行。已落库事件和安全结果必须保持可读。

#### Scenario: detached Run 在重启时仍为 running
- **WHEN** 服务重启但没有平台级任务恢复凭据
- **THEN** Run SHALL 被标记为可识别的中断失败，保留已有进度和事件，且不启动重复 Agent

### Requirement: 事件保留和隐私边界必须可配置
系统 MUST 配置单 Run 事件上限、保留时间、结果大小和订阅队列上限；超限策略 SHALL 优先保留终态、Check、Review、Budget 和错误事件。过期事件只能在仍能提供快照/最小状态的前提下清理，敏感内容不得因保留而绕过既有脱敏规则。

#### Scenario: 订阅者消费速度低于事件产生速度
- **WHEN** SSE 客户端无法及时读取高频 telemetry
- **THEN** 系统 SHALL 限制队列增长，可丢弃可重建的非关键 telemetry，但不得丢失终态和业务提交事件
