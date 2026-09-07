## ADDED Requirements

### Requirement: 尽调核查目录必须独立版本化
系统 SHALL 使用 `DueDiligenceCheckCatalog` 固定调查范围。每个核查项 MUST 具有唯一 `check_id`、负责人角色、必需/可选子模块、Prompt 模板、Evidence 要求、时间窗口、缺数策略、严重度策略、报告映射和输出 schema 版本；其子模块引用 MUST 存在于当前 `ReportCatalog`。

#### Scenario: 加载首版核查目录
- **WHEN** 服务启动或运行请求选择一个核查目录版本
- **THEN** 系统 SHALL 校验目录唯一性、Prompt 可用性、8/48 报告映射和 schema，并至少包含工商存续状态与盈利能力下滑核查项

#### Scenario: 核查项引用未知子模块
- **WHEN** `required_submodule_ids` 或 `optional_submodule_ids` 包含当前报告目录不存在的 ID
- **THEN** 系统 SHALL 拒绝加载该核查目录并返回可定位的配置错误

### Requirement: 正式调查必须由模型驱动的 Agent 执行
当运行配置为 formal model-backed 模式时，系统 MUST 通过 openJiuwen Agent runtime 执行版本化 Prompt、LLM 请求、快照读取和结构化提交，不得将固定 Python 判断顺序、预组装 Finding 或确定性事件壳冒充为 Agent 执行。

#### Scenario: 运行正式 single 调查
- **WHEN** 客户端以 `mode=single` 提交请求且模型路由配置完整
- **THEN** 系统 SHALL 创建一个真实调查 Agent，并记录至少一次成功的非 fake LLM 请求、provider usage 和结构化核查提交

#### Scenario: 正式运行缺少模型配置
- **WHEN** single 或 multi 调查缺少必需模型配置或凭证
- **THEN** 系统 SHALL 在调查业务执行前明确失败，不得静默回退到旧确定性策略

### Requirement: 调查 Prompt 必须共享业务核心并保留角色差异
系统 SHALL 从版本控制的 Prompt bundle 构造调查 Agent System Prompt。公共核心 MUST 包含核查目录语义、Evidence 引用、来源状态、报告时点、快照只读边界、缺数规则、结构化提交和禁止模型决定最终分数。single、Leader、专业 Agent 和 Reviewer 只在角色职责层不同。

#### Scenario: 为两种模式加载 Prompt
- **WHEN** 系统为同一快照和核查目录构造 single 与 multi Prompt
- **THEN** 两种模式 SHALL 共享同一 `prompt_core_version` 和 `prompt_core_sha256`，并分别记录角色 Prompt 哈希

#### Scenario: Prompt bundle 不完整
- **WHEN** 公共核心、目标角色层或输出 schema 指令缺失
- **THEN** 系统 SHALL 拒绝启动对应 Agent 并返回可定位错误

### Requirement: 调查 Agent 只能读取冻结快照
single 和 multi 的业务 Agent SHALL 只能通过受控只读接口查询同一个 `EnterpriseContextSnapshot`，不得调用天眼查 MCP、DeepSearch、任意网页搜索或修改快照。读取接口 SHALL 返回规范子模块上下文、来源状态和 Evidence ID，而非未脱敏原始响应。

#### Scenario: Agent 查询核查项所需事实
- **WHEN** Agent 请求其任务包含的标准子模块
- **THEN** 快照接口 SHALL 返回该子模块的冻结事实、coverage 状态、Evidence ID 和冲突/缺口信息，并记录读取审计事件

#### Scenario: Agent 尝试读取任务外或外部数据
- **WHEN** multi 专业 Agent 请求未分配核查项的数据范围或任何外部 Tool
- **THEN** 系统 SHALL 按最小权限策略拒绝请求且不改变任务、预算或快照

### Requirement: single 必须由一个 Agent 完成全部固定核查与自检
single runtime SHALL 仅创建一个调查 Agent 身份。该 Agent SHALL 领取完整核查目录，自主安排核查顺序，读取快照，逐项提交结果，并在同一会话中完成结构化自检。

#### Scenario: single 在预算内完成
- **WHEN** single Agent 完成全部启用核查项和自检
- **THEN** 每个核查项 SHALL 有一个有效终态提交，且调查阶段 `agent_count` SHALL 为 1

#### Scenario: single 遗漏核查项
- **WHEN** Agent 结束、超时或耗尽预算时仍有启用核查项未提交
- **THEN** 系统 SHALL 将其标记为缺口并产生 partial/failed 结果，不得在 Agent 外用业务逻辑静默补齐

### Requirement: multi 必须在 AgentTeams 中完成固定核查协作
multi runtime SHALL 让 Leader、专业调查 Agent 和 Reviewer 使用 agent-Core 任务/消息机制完成分配、调查、复核和有界返工。Leader SHALL 按目录分配全部启用核查项；团队生命周期 MUST 持续到任务完成、失败、取消或预算耗尽。

#### Scenario: 团队建立成功
- **WHEN** Leader 完成预定义 AgentTeams roster 创建
- **THEN** runtime MUST NOT 因建队成功而停止，并 SHALL 继续执行核查任务直到全部达到终态

#### Scenario: Reviewer 发现可修复的 Evidence 缺口
- **WHEN** Reviewer 提交带目标 Agent、check ID 和缺口说明的 RepairTask
- **THEN** Leader SHALL 在共享返工与资源预算内定向派回原专业 Agent，并等待版本化重提交

### Requirement: 每个 Agent 必须提交风险项与事实证据的结构化结果
权威业务交付 SHALL 使用 `AgentInvestigationResult`。每个参与 Agent MUST 有稳定身份、角色、任务、终止状态和事实 Evidence 引用；调查 Agent 还 MUST 为每个核查项提交 `risk`、`no_risk` 或 `inconclusive` 状态。自由文本最终回答 MUST NOT 被解析为正式业务结果。

#### Scenario: Agent 发现盈利能力下滑
- **WHEN** `profitability-decline` 的必需财务 Evidence 满足目录规则且显示持续下滑
- **THEN** Agent SHALL 提交带 `check_id`、严重度、结论、置信度和 Evidence ID 的 RiskItem，并在 Agent 结果中保留相应 FactEvidenceRef

#### Scenario: 必需 Evidence 不足
- **WHEN** 核查项的必需子模块为 `source_error`、`not_requested` 或事实不足以达到目录定义的 no-risk 条件
- **THEN** Agent SHALL 提交 `inconclusive` 和 `missing_evidence`，不得提交 `no_risk`

#### Scenario: 提交引用不存在的 Evidence
- **WHEN** Agent 提交的结论引用当前快照不存在的 Evidence ID
- **THEN** 提交工具 SHALL 拒绝写入并返回结构化校验错误，只能在剩余 schema 重试预算内更正

### Requirement: Reviewer 必须检查证据充分性而不能改写事实
模型 Reviewer SHALL 检查未覆盖核查项、证据引用、互相矛盾的结论、重复风险和 `inconclusive` 合理性，并可产生 ReviewIssue 与 RepairTask。Reviewer MUST NOT 修改冻结 Evidence、报告目录、核查目录或正式 RiskRule。

#### Scenario: 两个专业 Agent 提交冲突结论
- **WHEN** Reviewer 发现同一事实被解释为互相不兼容的风险状态
- **THEN** Reviewer SHALL 记录双方 check/result/Evidence ID，提交定向 ReviewIssue，并在预算允许时请求返工

### Requirement: 确定性业务门禁必须统一执行
两种调查模式结束后 MUST 使用同一 Evidence 引用校验、确定性 Reviewer 底线检查、RiskRuleEngine 和 ResultAssembler。Agent 输出的分数、分档、准入建议或动态目录修改 MUST NOT 直接成为正式 Decision 或报告结构。

#### Scenario: Agent 提交最终风险分
- **WHEN** 任一 Agent 尝试提交最终分数或准入决定
- **THEN** 提交层 SHALL 拒绝或忽略该字段，正式 Decision SHALL 仅由已接受核查结果和版本化规则产生

### Requirement: 最终结果必须同时包含 Agent 维度和 8/48 报告结构
`DueDiligenceResult` SHALL 包含 `agent_results` 和 `report_structure`。`agent_results` SHALL 为每个 Agent 提供风险项与事实证据引用；`report_structure` SHALL 提供目录版本、`module_count=8`、`submodule_count=48` 及稳定模块/子模块列表。现有 findings、evidence、sections 和 decision 字段 SHALL 保持兼容。

#### Scenario: 组装 multi 最终结果
- **WHEN** multi 调查和确定性后处理完成
- **THEN** Result SHALL 按 Agent 保留各自 RiskItem/FactEvidenceRef，并按核查到报告的映射将接受结果投影到统一 8/48 章节

#### Scenario: 支持 Agent 没有风险项
- **WHEN** Leader、Context Agent 或 DeepSearch Agent 没有产生风险判断
- **THEN** 其 `risk_items` SHALL 为空数组，但事实引用、任务摘要、终止状态和成本 SHALL 仍可审计

### Requirement: offline runtime 必须与正式 Agent 评测分离
deterministic harness 或可脚本化 fake model SHALL 仅用于契约、安全、错误处理和回归测试，其结果 MUST 显式标记 `formal_agent_run=false`。

#### Scenario: fake model 完成完整调查
- **WHEN** 测试通过 fake model 执行 single 或 multi
- **THEN** 系统 SHALL 保留测试结果，但该结果 MUST NOT 被聚合为真实 Agent 协作增益证据
