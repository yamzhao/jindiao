## ADDED Requirements

### Requirement: 企业主体必须被唯一锚定
系统 SHALL 在执行专项调查前，使用企业名称、统一社会信用代码、地区、登记状态和天眼查企业标识完成主体消歧，并生成贯穿本次运行的稳定 `subject_id`。

#### Scenario: 唯一匹配企业
- **WHEN** 企业搜索结果中存在与输入标识唯一一致的有效主体
- **THEN** 系统 SHALL 固定该主体并使所有后续 Evidence 使用相同 `subject_id`

#### Scenario: 企业名称存在歧义
- **WHEN** 多个候选主体无法依据请求信息唯一消歧
- **THEN** 系统 SHALL 停止事实调查并返回候选主体及 `entity_ambiguous` 错误，而不是任选一个主体

### Requirement: 天眼查查询必须先发现能力
系统 SHALL 在主体锚定后取得企业 capability manifest，并且只能调用 manifest 中已声明可用的企业业务能力。

#### Scenario: 能力存在
- **WHEN** capability manifest 包含目标调查能力
- **THEN** 系统 SHALL 调用对应天眼查工具并记录能力名称、工具名称和查询状态

#### Scenario: 能力不存在
- **WHEN** capability manifest 不包含目标调查能力
- **THEN** 系统 SHALL 将该项标记为 `capability_absent`，且不得伪造或猜测天眼查工具名

### Requirement: 所有报告事实必须标准化为证据
系统 MUST 将进入 Finding、规则计算或报告的外部事实映射为统一 Evidence，至少包含证据标识、主张、值、主体、来源类型、来源工具或文件、源记录标识、查询时间、数据时点、置信度、Mock 标识和所支持字段。

#### Scenario: 天眼查记录映射
- **WHEN** 天眼查工具返回一条有效业务记录
- **THEN** 系统 SHALL 生成 `source_type=tianyancha` 且 `is_mock=false` 的 Evidence，并保留可追溯的源工具和记录标识

#### Scenario: 派生事实映射
- **WHEN** 一个结论由多条原始证据推导而来
- **THEN** 系统 SHALL 使用 `source_type=derived` 并引用全部支撑 Evidence，不能将派生结论伪装为源记录

### Requirement: 查询结果状态必须保持语义区分
系统 SHALL 区分 `verified_records`、`verified_empty`、`capability_absent`、`source_error` 和 `degraded_mock`，不得将查询错误或能力缺失描述为已核验无风险。

#### Scenario: 有效空结果
- **WHEN** 已声明能力成功执行但返回空记录
- **THEN** 系统 SHALL 记录 `verified_empty` 并禁止使用 Mock 替换该结果

#### Scenario: 数据源失败
- **WHEN** 查询因鉴权、限流、超时或协议错误最终失败
- **THEN** 系统 SHALL 记录 `source_error` 和错误原因，并在覆盖率及报告中披露该缺口

### Requirement: 系统必须输出调查覆盖率
系统 SHALL 基于计划调查项的已核验、空结果、能力缺失、错误和降级状态计算覆盖率，并提供领域级明细。

#### Scenario: 部分领域查询失败
- **WHEN** 至少一个计划调查项为 `source_error`
- **THEN** 系统 SHALL 降低对应领域覆盖率并在最终结果中列出未完成项

### Requirement: 凭据不得进入业务产物
系统 MUST 只从运行时环境读取天眼查授权信息，并在日志、Evidence、错误、报告和 Mock 数据中移除授权头及密钥。

#### Scenario: MCP 调用失败并记录诊断
- **WHEN** 天眼查 MCP 返回包含请求头的诊断信息
- **THEN** 系统 SHALL 在持久化或输出前对授权字段进行脱敏

