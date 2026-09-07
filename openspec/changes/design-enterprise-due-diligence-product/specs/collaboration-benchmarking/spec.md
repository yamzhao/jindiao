## ADDED Requirements

### Requirement: 单双 Agent 对照必须共享同一业务内核
single-agent 与 multi-agent 模式 MUST 使用相同输入场景、场景快照、模型、采样参数、工具适配器、Evidence/Finding schema、规则引擎、报告组装器和总资源预算；差异只能是编排与协作策略。

#### Scenario: 运行成对基准
- **WHEN** benchmark runner 对同一场景执行 single 和 multi 模式
- **THEN** 两次运行 SHALL 记录相同的数据哈希、模型配置、规则版本、Skill 基准版本和预算

### Requirement: 基准集必须覆盖协作价值场景
冻结基准集 SHALL 包含正常企业、主体歧义、司法高风险、经营异常、证据冲突、有效空结果、能力缺失、数据源错误和报告缺口等场景，并为每个场景提供独立预期答案。

#### Scenario: 校验 benchmark manifest
- **WHEN** 启动完整基准评测
- **THEN** runner SHALL 验证必需场景类别、场景版本和 expected 数据均存在，否则拒绝生成正式对比结论

### Requirement: 系统必须量化质量、成功率和耗时
benchmark SHALL 至少计算端到端耗时、首条有效证据耗时、schema 成功率、质量总分、工具调用数、token、冲突数和返工数，并保留逐场景结果。

#### Scenario: 某模式运行超时
- **WHEN** 一次运行未在统一截止时间内生成有效 Result
- **THEN** benchmark SHALL 将其计为失败并保留已发生的耗时和资源消耗，不得从聚合结果中删除

### Requirement: 质量评分必须透明且可重算
质量总分 SHALL 按覆盖率 30%、证据支持率 25%、风险质量 20%、冲突检出率 15% 和报告结构 10% 聚合；各子指标定义和原始计数必须随结果保存。

#### Scenario: 重算质量分
- **WHEN** 使用导出的逐场景计数重新执行评分器
- **THEN** 重算结果 SHALL 与正式输出一致，并能定位每个扣分项

### Requirement: 多智能体增益结论必须基于完整对照结果
正式报告 SHALL 同时展示 single 和 multi 在全部冻结场景上的结果与差值，并且只有当 multi 在耗时、质量或成功率至少一个预先声明的主指标上优于 single 时，才可声明协作增益。

#### Scenario: multi 质量更高但耗时更长
- **WHEN** multi 的预先声明主质量指标优于 single，但端到端耗时更高
- **THEN** 报告 SHALL 同时声明质量收益和时延代价，不得隐去不利指标

#### Scenario: 没有主指标提升
- **WHEN** multi 在全部预先声明主指标上均未优于 single
- **THEN** 报告 SHALL 判定本次实验未证明协作增益，而不得更换场景或临时改权重后宣称成功

### Requirement: 基准运行必须可复现
benchmark SHALL 记录代码版本、运行命令、随机种子、模型与采样参数、硬件摘要、并发数、数据哈希、规则版本、Skill 版本和时间戳，并导出 JSONL 原始记录及 Markdown 汇总。

#### Scenario: 第三方复跑固定 Mock 基准
- **WHEN** 第三方按 README 一键命令使用相同版本和本地 Mock 数据运行
- **THEN** 系统 SHALL 生成相同 schema 的逐场景和聚合产物，并可解释允许的模型波动

#### Scenario: 使用独立对比脚本
- **WHEN** 操作者运行仓库提供的 single/multi 对比脚本
- **THEN** 脚本 SHALL 复用正式 BenchmarkRunner 和冻结 manifest 生成 Markdown 报告，不得实现第二套指标或评分逻辑

### Requirement: 预期答案不得泄漏给被测 Agent
expected Findings 和 Decision SHALL 只对评分器可见，运行时 Agent、工具和检索索引不得读取这些文件。

#### Scenario: Agent 尝试访问 expected 路径
- **WHEN** 被测运行请求或工具尝试读取场景 `expected/` 目录
- **THEN** ScenarioRepository SHALL 拒绝访问并记录评测完整性错误
