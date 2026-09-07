## ADDED Requirements

### Requirement: 项目必须提供三个可复用技能包
项目 SHALL 提供 `tyc-evidence-acquisition`、`evidence-backed-due-diligence` 和 `feedback-evolved-reporting` 三个独立 Skill，并可被当前应用之外的兼容 Agent 或 AgentTeams 挂载。

#### Scenario: 检查技能目录
- **WHEN** 发布包被构建
- **THEN** 三个 Skill SHALL 分别具有独立目录、入口 `SKILL.md` 和版本信息

### Requirement: 每个 Skill 必须提供完整复用说明
每个 Skill MUST 说明用途、适用边界、前置条件、输入、输出、安装或挂载方式、最小示例、评测集、版本和变更记录。

#### Scenario: 外部开发者复用技能
- **WHEN** 开发者只阅读该 Skill 目录的公开说明
- **THEN** 开发者 SHALL 能完成挂载并运行最小示例，而无需依赖 Jindiao 私有路径或密钥

### Requirement: 证据采集 Skill 必须封装来源语义
`tyc-evidence-acquisition` SHALL 提供主体锚定、capability routing、Evidence 映射以及记录、空结果、能力缺失、源错误和降级状态判断，不得在 Skill 内硬编码真实凭据。

#### Scenario: 在其他 Agent 中查询企业能力
- **WHEN** 外部 Agent 以企业标识和 MCP provider 挂载该 Skill
- **THEN** Skill SHALL 返回标准化 Evidence 和来源状态，而不是暴露未经约束的工具响应

### Requirement: 团队尽调 Skill 必须封装协作协议
`evidence-backed-due-diligence` SHALL 定义 InvestigationPlan、Finding、Evidence、ReviewIssue、RepairTask 和 ReviewDecision 的协作方式及验证规则。

#### Scenario: 新增一个专项 Agent
- **WHEN** 团队挂载一个符合 Skill 输出 schema 的新调查 Agent
- **THEN** Reviewer 和 ResultAssembler SHALL 能消费其产物而无需修改其他 Agent prompt

### Requirement: 报告 Skill 必须支持基于反馈生成候选版本
`feedback-evolved-reporting` SHALL 采集运行轨迹、Reviewer 评语、人工反馈和评测分数，并使用 TeamSkillEvolutionRail 生成带来源说明的改进候选。

#### Scenario: 用户反馈报告风险解释不清晰
- **WHEN** 反馈被归因到报告表达规则且拥有对应失败样例
- **THEN** 系统 SHALL 生成新的候选 Skill 版本和变更摘要，保持稳定版本不变

### Requirement: 自演进必须经过中断审查和固定集回放
候选 Skill MUST 经 EvolutionInterruptRail 产生审批中断、EvolutionReviewRuntime 二次审查和固定回放集回归后，才可等待人工激活。

#### Scenario: 候选通过静态审查但回归退化
- **WHEN** 候选在证据支持率、风险召回率、事实准确性或 schema 成功率任一保护指标低于稳定版本
- **THEN** 系统 SHALL 拒绝激活并保留稳定版本

#### Scenario: 候选通过全部门禁
- **WHEN** 候选通过审查、回放和策略门槛
- **THEN** 系统 SHALL 标记为 awaiting-approval，只有人工批准后才可成为 active 版本

### Requirement: 自演进必须保护不可变治理边界
系统 MUST 阻止候选修改风险规则、评分阈值、数据源优先级、Mock 标识、证据门槛、密钥处理和人工审批要求。

#### Scenario: 候选尝试隐藏 Mock 标识
- **WHEN** 候选内容要求报告不显示 Mock 来源
- **THEN** 审查流程 SHALL 将其标为治理违规并拒绝进入回放和激活阶段

### Requirement: 技能版本必须可审计和回滚
系统 SHALL 为稳定版和候选版记录版本、父版本、内容哈希、反馈来源、评测结果、审批人、状态和变更日志，并支持切回任一已批准稳定版本。

#### Scenario: 新激活版本在线回放失败
- **WHEN** 操作者选择回滚到前一稳定版本
- **THEN** 后续运行 SHALL 使用旧版本且历史运行继续保留其原始 Skill 版本引用

