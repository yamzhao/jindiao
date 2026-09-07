## ADDED Requirements

### Requirement: 尽调范围必须覆盖标准报告业务章节
系统 SHALL 支持报告摘要、风险摘要、企业基本信息、司法风险、经营风险、经营情况、关联信息和同类企业分析，并为每个章节输出状态、覆盖率、Findings 和 Evidence 引用。

#### Scenario: 完整场景生成报告
- **WHEN** 一个测试企业为所有报告领域提供可用数据
- **THEN** 系统 SHALL 生成全部章节且每个章节均具有非空状态和覆盖信息

#### Scenario: 某章节无可用能力
- **WHEN** 一个章节的全部来源能力均缺失且无允许使用的 Mock 数据
- **THEN** 系统 SHALL 保留该章节并标记数据缺口，而不是删除章节或表述为无风险

### Requirement: 风险必须区分准入类和关注类
系统 SHALL 将 accepted Findings 分类为准入类风险、关注类风险或非风险事实，并保留分类依据和 Evidence 引用。

#### Scenario: 命中硬性准入规则
- **WHEN** accepted Finding 满足版本化准入规则的全部条件
- **THEN** 系统 SHALL 将其列入准入类风险并输出规则标识、处置建议和证据

#### Scenario: 命中累计关注规则
- **WHEN** accepted Finding 只满足关注类规则
- **THEN** 系统 SHALL 将其列入关注类风险并参与累计评分，但不得擅自转成硬拒绝

### Requirement: 风险分必须由版本化确定性规则计算
系统 MUST 使用 Python 规则引擎对 accepted Findings 计算分值，输出 `rule_version`、规则触发明细、单项分值和总分；模型生成内容不得直接修改计算结果。

#### Scenario: 规则计算成功
- **WHEN** Reviewer 提供一组 accepted Findings
- **THEN** 总风险分 SHALL 等于全部触发规则分值之和，且每项分值均可追溯到 Finding 和 Evidence

#### Scenario: 无证据 Finding 尝试计分
- **WHEN** 一个 Finding 没有满足规则所需的 Evidence
- **THEN** 规则引擎 SHALL 拒绝该 Finding 触发规则并记录证据不足原因

### Requirement: 初始准入分档必须与标准报告一致
系统 SHALL 使用 `[0,20)` 为通过、`[20,80)` 为人工复核、`[80,+∞)` 为拒绝的初始分档，并在结果中返回阈值版本。

#### Scenario: 分数处于边界
- **WHEN** 风险分分别为 19、20、79 和 80
- **THEN** 系统 SHALL 依次返回通过、人工复核、人工复核和拒绝

### Requirement: 数据缺口必须影响置信度而非伪装成低风险
系统 SHALL 基于调查覆盖率、Evidence 完整性、未解决冲突和数据时效性计算决策置信度，并列出待人工确认项。

#### Scenario: 关键司法来源不可用
- **WHEN** 司法领域存在未恢复的 `source_error`
- **THEN** 系统 SHALL 降低置信度、列出缺口并避免将空白解释为无司法风险

### Requirement: 决策必须可解释和可复核
最终 Decision SHALL 包含分档、总分、主要风险、规则明细、证据链接、覆盖率、置信度、数据时点和规则版本。

#### Scenario: 审核人员复核拒绝结论
- **WHEN** 最终分档为拒绝
- **THEN** 审核人员 SHALL 能从决策依次定位到触发规则、Finding 和原始 Evidence

### Requirement: 规则变更必须受代码与回归治理
系统 MUST 禁止运行时 Agent 或 Skill 自演进修改风险规则、阈值和分档；规则版本变更必须通过显式配置、代码评审和边界测试。

#### Scenario: 演进候选建议改变拒绝阈值
- **WHEN** Skill 演进输出包含阈值或硬规则修改
- **THEN** 系统 SHALL 拒绝该候选自动激活并记录治理违规原因

