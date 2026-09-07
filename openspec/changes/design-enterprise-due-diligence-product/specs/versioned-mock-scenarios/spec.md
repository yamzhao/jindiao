## ADDED Requirements

### Requirement: 每个测试企业必须拥有独立的固定场景
系统 SHALL 以 `scenario_id` 和企业键组织 Mock 数据，每个测试企业的场景与其他企业隔离，且相同版本的内容在不同 Agent 和重复运行之间保持不变。

#### Scenario: 两个 Agent 查询同一企业字段
- **WHEN** 两个 Agent 在同一运行中读取相同企业、场景版本和字段
- **THEN** 它们 SHALL 获得相同值、相同数据时点和相同源记录标识

#### Scenario: 查询不同企业
- **WHEN** 运行分别选择两个不同企业场景
- **THEN** ScenarioRepository SHALL 只返回各自目录内的数据，禁止跨企业混读

### Requirement: Mock 场景必须具有可校验清单
每个场景 MUST 提供 manifest，至少包含场景标识、企业键、版本、数据时点、文件清单和内容哈希。

#### Scenario: 场景加载成功
- **WHEN** manifest 与全部场景文件存在且哈希匹配
- **THEN** 系统 SHALL 生成不可变 `scenario_snapshot_id` 并将其固定到 Run Context

#### Scenario: 文件被修改但版本未更新
- **WHEN** 任一文件内容与 manifest 哈希不匹配
- **THEN** 系统 SHALL 终止场景加载并返回完整性错误，不得静默继续

### Requirement: 所有 Agent 必须共享同一个只读快照
系统 SHALL 在运行开始时一次解析场景版本，并通过统一只读 ScenarioRepository 向所有 Agent 和工具提供数据。

#### Scenario: 运行期间磁盘文件发生变化
- **WHEN** Run Context 已固定快照后底层场景文件被修改
- **THEN** 当前运行 SHALL 继续使用已固定内容或安全失败，不得让不同 Agent 看到不同版本

### Requirement: Mock 回退必须服从来源状态机
系统 SHALL 仅在天眼查能力明确缺失时使用 Mock；只有请求显式开启演示降级时，才可在最终 `source_error` 后使用 Mock。

#### Scenario: 天眼查返回有效空记录
- **WHEN** 目标 capability 存在且查询成功返回空
- **THEN** 系统 MUST 保留 `verified_empty`，不得读取对应 Mock 数据

#### Scenario: capability 明确缺失
- **WHEN** capability manifest 不包含目标能力且当前场景提供对应数据
- **THEN** 系统 SHALL 返回标记为 Mock 的 Evidence 并保留 `capability_absent` 回退原因

#### Scenario: 演示降级未开启且数据源失败
- **WHEN** 天眼查查询最终失败且请求未允许降级
- **THEN** 系统 SHALL 报告 `source_error`，不得自动返回 Mock 记录

#### Scenario: 实时来源全部失败且显式允许演示降级
- **WHEN** 主体已唯一锚定、所有报告领域的天眼查查询均为 `source_error` 且请求允许降级
- **THEN** 系统 SHALL 从固定完整补充模板生成八个报告章节，保留真实主体字段，并将全部补充 Evidence 标记为 `degraded_mock`

### Requirement: Mock Evidence 必须显著标记和可引用
所有来自结构化 Mock 或 DeepSearch 本地语料的 Evidence MUST 设置 `source_type=mock`、`is_mock=true`，并提供稳定的 `mock://` 引用。

#### Scenario: DeepSearch 返回本地材料片段
- **WHEN** DeepSearch 从当前场景语料命中一个片段
- **THEN** Evidence SHALL 包含场景、版本、文件和片段定位信息，且最终报告必须披露其 Mock 属性

### Requirement: DeepSearch 检索必须限制在当前快照
DeepSearch 本地 provider SHALL 只索引当前 `scenario_snapshot_id` 的语料，任何检索结果必须通过场景隔离校验。

#### Scenario: 查询词同时出现在两个企业语料中
- **WHEN** 当前运行只选择其中一个企业场景
- **THEN** DeepSearch SHALL 只返回当前企业快照中的匹配片段

### Requirement: Mock 预期结果必须支持自动评测
场景 SHALL 可包含与输入数据分离的 expected Findings 和 Decision，benchmark 可以读取它们计算指标，但运行中的 Agent 不得访问预期答案。

#### Scenario: 执行 benchmark
- **WHEN** benchmark runner 加载场景
- **THEN** 应用运行 SHALL 只能访问业务数据和语料，评分器 SHALL 在运行结束后独立读取 expected 数据
