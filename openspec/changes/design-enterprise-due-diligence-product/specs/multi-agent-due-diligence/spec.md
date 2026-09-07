## ADDED Requirements

### Requirement: 多智能体必须由 AgentTeams 统一编排
系统 SHALL 使用 openJiuwen agent-Core AgentTeams 运行协调者、专项调查 Agent、DeepSearch 检索 Agent 和独立 Evidence Reviewer，并由同一个 Run Context 管理主体、快照、预算、截止时间和版本信息。

#### Scenario: 启动多智能体运行
- **WHEN** 应用以 multi-agent 模式执行尽调
- **THEN** 系统 SHALL 创建一个 AgentTeams 运行并为所有成员注入相同 Run Context

### Requirement: 协调者必须产生显式调查计划
Leader SHALL 根据报告范围、企业 capability manifest 和运行预算生成结构化 InvestigationPlan，包含任务标识、领域、输入、预期产物、依赖、负责人和截止条件。

#### Scenario: 计划覆盖标准报告领域
- **WHEN** 企业主体和能力清单已确定
- **THEN** Leader SHALL 为治理关联、司法合规、经营同业和必要的补充检索生成任务或明确的跳过原因

### Requirement: 无依赖的专项调查必须支持并行执行
系统 SHALL 并行调度相互独立的领域任务，并在共享 Evidence Store 中以幂等标识合并结果。

#### Scenario: 三个领域任务互不依赖
- **WHEN** 治理、司法和经营调查均已就绪且并发预算允许
- **THEN** 系统 SHALL 并行启动任务并独立记录每项任务的开始、结束、状态和耗时

### Requirement: Agent 只能提交结构化调查产物
专项 Agent MUST 输出符合 schema 的 Finding 和 Evidence 引用，不得直接覆盖最终风险分、准入决策或其他 Agent 的证据。

#### Scenario: Agent 提交无证据风险
- **WHEN** 专项 Agent 输出风险 Finding 但未提供任何 Evidence 引用
- **THEN** 系统 SHALL 拒绝该 Finding 进入规则计算并将其送交 Reviewer 处理

### Requirement: Reviewer 必须独立检查证据与冲突
Evidence Reviewer SHALL 检查主体一致性、来源、时间、金额、状态、重复记录、结论支持关系和跨 Agent 冲突，并生成可定位的 ReviewIssue。

#### Scenario: 两个 Agent 对同一事实结论冲突
- **WHEN** Findings 对同一主体、同一字段和重叠时点给出不兼容值
- **THEN** Reviewer SHALL 创建冲突组，引用双方 Evidence，并阻止相关结论直接进入最终报告

#### Scenario: Evidence 充分且无冲突
- **WHEN** Finding 满足证据门槛且不存在未解决冲突
- **THEN** Reviewer SHALL 将其标记为 accepted，允许规则引擎和报告组装器消费

### Requirement: 返工必须定向且有界
Leader SHALL 根据 ReviewIssue 生成只包含待修复字段、缺失证据和目标 Agent 的 RepairTask，并受最大返工次数、工具调用数和截止时间限制。

#### Scenario: 缺少关键日期
- **WHEN** Reviewer 发现风险事实缺少决定规则适用性的日期
- **THEN** Leader SHALL 只向负责该领域的 Agent 发送日期补证任务，而不是重跑全部调查

#### Scenario: 返工预算耗尽
- **WHEN** ReviewIssue 在达到返工上限后仍未解决
- **THEN** 系统 SHALL 保留该问题并将对应结论标为未确认，不得无限循环或假定问题已解决

### Requirement: 确定性组件必须在团队审查后运行
风险规则和最终结果组装 MUST 由普通 Python 组件在 Reviewer 完成后执行，Agent 不得绕过这两个组件生成正式结果。

#### Scenario: 团队调查完成
- **WHEN** 所有任务已完成、失败或达到预算且 Reviewer 已给出最终状态
- **THEN** 系统 SHALL 以 accepted Findings 和显式覆盖缺口调用规则引擎及 ResultAssembler

### Requirement: 团队协作过程必须可观察
系统 SHALL 输出 Agent 任务、状态、耗时、工具调用、证据数量、冲突、返工和失败摘要，且不得暴露内部思维链。

#### Scenario: 前端查看协作详情
- **WHEN** multi-agent 运行结束
- **THEN** 最终结果 SHALL 包含可展示的 `agent_trace` 和 `collaboration` 摘要，而不包含私有推理文本

