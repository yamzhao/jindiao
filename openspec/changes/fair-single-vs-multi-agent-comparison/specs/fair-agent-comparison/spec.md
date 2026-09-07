## ADDED Requirements

### Requirement: 成对比较必须只执行一次共享上下文采集
`PairedComparisonRunner` SHALL 为每个预注册样本先执行一次 Enterprise Context Agent、策略批准的 DeepSearch 基础补充/缺口补证和 Context Freezer，再将同一个不可变 `EnterpriseContextSnapshot` 传给 single 与 multi 调查。系统 MUST NOT 为两臂分别调用天眼查、年报 Tool 或其他 DeepSearch 能力。

#### Scenario: 启动有效成对运行
- **WHEN** 样本主体、报告时点和采集配置已确定
- **THEN** Runner SHALL 生成一个共享 `snapshot_id`/`snapshot_sha256`，并让两臂引用该同一快照

#### Scenario: 一侧请求刷新上下文
- **WHEN** single 或 multi 调查过程中请求新增外部事实或刷新快照
- **THEN** 当前 pair SHALL 停止成为有效正式比较；如确需刷新，系统 SHALL 生成新快照并重新开始完整 pair

#### Scenario: 年报基础补充启用
- **WHEN** pair 的 acquisition policy 启用年报社保基础补充
- **THEN** `deepsearch-agent` SHALL 在分叉前调用专属年报 Tool 恰好一次，其 Evidence 与成本 SHALL 由两臂共享

### Requirement: mode 必须只表达调查拓扑
在本变更的 API、Result 和 benchmark 中，`mode=single|multi` SHALL 仅表示 investigation 阶段使用一个 Agent 还是 AgentTeams。共享 Enterprise Context Agent 和 DeepSearch Agent MUST NOT 计入两臂的调查 Agent 数或被描述为 single 模式内部的额外调查 Agent。

#### Scenario: single 使用了共享采集 Agent
- **WHEN** 一个 paired case 先由 Context Agent 采集、再由 single Agent 调查
- **THEN** 结果 SHALL 分别记录 acquisition agents 与 `investigation.agent_count=1`，不得据此把 single 标成 multi

### Requirement: 成对对比必须在调查前验证等价指纹
Runner SHALL 为 pair 构造并保存 `ComparisonFingerprint`，至少包含代码/schema 版本、快照 ID/哈希、报告时点、模型 provider/name、采样参数、随机种子（若可用）、公共 Prompt 核心哈希、CheckCatalog/ReportCatalog 哈希、分析预算、规则和评分器版本。除 mode、协作拓扑和已记录角色 Prompt 外，关键字段 MUST 一致。

#### Scenario: 指纹预检通过
- **WHEN** 两臂所有必需公平性字段一致
- **THEN** Runner SHALL 分配同一 `comparison_id`，保留两臂指纹并允许进入调查

#### Scenario: 快照或核查目录不一致
- **WHEN** 两臂的 snapshot、公共 Prompt、核查目录、模型或分析预算任一关键字段不同
- **THEN** Runner SHALL 拒绝生成正式 pair，并列出全部不一致字段

### Requirement: 两臂必须共享数值相同的总分析预算
single 与 multi SHALL 分别获得数值相同的 investigation 总预算，包括 LLM 请求、输入/输出/总 Token、墙钟时间、最大并发、schema 重试和返工轮次。multi 的 Leader、专业 Agent、Reviewer 和所有协调消息 MUST 共用唯一账本，不得按成员重置额度。

#### Scenario: multi 创建多个核查任务
- **WHEN** Leader、专业 Agent 和 Reviewer 发起模型请求或返工
- **THEN** 全部调用 SHALL 计入 multi 的同一 investigation 预算，协调成本不得排除

#### Scenario: 分析预算耗尽
- **WHEN** 任一臂达到 LLM、Token 或截止时间上限
- **THEN** runtime SHALL 拒绝新工作并取消剩余任务，保留已提交结果和实际资源消耗，且该样本 SHALL 作为 partial/failed 留在聚合中

### Requirement: 共享采集成本与两臂调查成本必须分开计量
系统 SHALL 将 Enterprise Context Agent、DeepSearch Agent、年报 public-Web Tool 和底层 Tool/MCP 消耗记录为 `shared_acquisition_cost`；single/multi 的模型、Token、返工和时延记录为各自 `investigation_cost`。共享成本 MUST NOT 被复制到两臂或用于掩盖 multi 的协调成本。

#### Scenario: Context Agent 调用十个 MCP capability
- **WHEN** 共享采集阶段完成十次底层 MCP 调用
- **THEN** pair SHALL 记录十次共享 acquisition 调用及其状态和时延，而两臂的 Tool/MCP 调用数 SHALL 保持为零

#### Scenario: 单独生产请求而非 pair
- **WHEN** 客户端只执行一种 mode
- **THEN** Result SHALL 分别报告该请求的 acquisition 与 investigation 成本，但 MUST NOT 自动生成协作增益结论

### Requirement: 正式对比必须证明两臂都实际使用模型
formal paired comparison MUST 对 single 和 multi 都记录成功的非 fake LLM 请求、provider usage、非零 Token 和有效结构化提交，并 MUST 拒绝用 deterministic harness、固定 Python 顺序或事件壳伪造 Agent 执行。

#### Scenario: 一侧使用 fake model
- **WHEN** 任一臂 `formal_agent_run=false`、没有实际模型 usage 或没有有效核查提交
- **THEN** Runner SHALL 保留调试结果但不得生成正式协作增益判定

### Requirement: 对比轨迹必须可审计但不得暴露私有思维链
两臂 SHALL 使用同一公开轨迹 schema，记录阶段、Agent/任务/check ID、Prompt/目录版本与哈希、模型状态和 usage、受限判断摘要、快照 Evidence ID、提交验证、复核、返工、预算和终止原因。系统 MUST 从 Agent chunk、SSE、JSONL、Result 和 benchmark 产物递归删除 Prompt 正文、私有 reasoning、chain-of-thought、密钥和未脱敏原始响应。

#### Scenario: 模型 chunk 包含 reasoning_content
- **WHEN** provider 在任意嵌套位置返回 `reasoning_content` 或等价私有字段
- **THEN** 所有公开产物 SHALL 不包含该字段或其文本内容

#### Scenario: 审计风险判断来源
- **WHEN** 操作者查看某 RiskItem 的形成依据
- **THEN** 系统 SHALL 提供 Agent、check ID、受限 `decision_summary`、Evidence ID、快照哈希和 Prompt/目录版本，而不提供或推断私有思维链

### Requirement: 对比必须同时报告质量、覆盖、时延和成本
每个 pair SHALL 使用预先版本化的评分器报告风险识别质量、固定核查覆盖、Evidence 充分性、schema/任务成功率、首条有效提交和端到端时延、LLM/Token 成本、冲突检出与返工，并保留每次运行的原始计数和失败样本。

#### Scenario: multi 质量更高但成本更高
- **WHEN** multi 的预定义质量指标优于 single，但 Token 或时延更高
- **THEN** 报告 SHALL 同时呈现质量收益、资源代价和共享采集成本，不得隐去不利指标

#### Scenario: 某次运行失败
- **WHEN** 任一臂超时、schema 失败或未完成核查目录
- **THEN** 该样本 SHALL 保留在成功率、覆盖率和资源聚合中，不得事后删除

### Requirement: 协作增益声明必须基于完整预注册试验
系统 MUST 仅在完成 benchmark manifest 预先声明的场景、重复次数、主指标、权重和判定阈值后生成正式结论，不得根据结果事后更改口径、排除失败样本或扩大核查范围。

#### Scenario: multi 未达到预注册改善阈值
- **WHEN** 完整有效对比结束且 multi 未满足预注册增益条件
- **THEN** 正式报告 SHALL 明确记录“未证明协作增益”，并保留全部运行和成本结果

#### Scenario: live 数据无法冻结为同一快照
- **WHEN** 两臂无法被证明使用同一内容哈希的上下文
- **THEN** 输出 SHALL 标记为诊断性 live smoke，而不是正式公平比较证据

### Requirement: 两种模式必须保持相同 API 与确定性结果口径
single 和 multi SHALL 继续使用同一 `POST /api/v1/due-diligence/result`、核心 `DueDiligenceResult` schema、RiskRuleEngine 和 ResultAssembler。新增 snapshot、Agent 结果、报告目录和 comparison metadata SHALL 以向后兼容方式暴露。

#### Scenario: 同一快照切换调查模式
- **WHEN** Runner 分别以 single 和 multi 调查同一快照
- **THEN** 两个结果 SHALL 满足同一核心 schema、包含相同 8/48 报告结构、使用同一规则版本，并正确标记各自调查拓扑与公平性指纹
