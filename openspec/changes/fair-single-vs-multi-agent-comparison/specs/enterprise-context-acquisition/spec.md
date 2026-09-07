## ADDED Requirements

### Requirement: 报告目录必须固定为 8 大模块和 48 个标准子模块
系统 SHALL 使用版本化 `ReportCatalog` 定义最终报告结构。目录 MUST 恰好包含 8 个唯一模块和 48 个唯一标准子模块；`report-summary` 与 `risk-summary` SHALL 作为汇总模块且不重复计算子模块。

#### Scenario: 加载有效报告目录
- **WHEN** 系统加载当前生产 `ReportCatalog`
- **THEN** 目录 SHALL 报告 `module_count=8`、`submodule_count=48`，并通过模块 ID、子模块 ID、顺序和映射唯一性校验

#### Scenario: Provider 产生工商年报社保数据
- **WHEN** 年报 Provider 返回社保人数或参保信息
- **THEN** 系统 SHALL 将该信息归入标准子模块 `annual_reports`，不得创建 `annual_report_social_security` 或其他第 49 个子模块

### Requirement: 企业上下文 Agent 必须统一获取天眼查企业上下文
正式采集 SHALL 由一个模型驱动的 `EnterpriseContextAgent` 依据版本化采集 Prompt 完成主体解析、capability 发现和 48 子模块覆盖规划。只有该 Agent 绑定的受控 Gateway SHALL 获得天眼查 MCP 业务调用权限，且 Agent MUST NOT 直接提交风险判断、风险分或准入决定。

#### Scenario: 为企业执行正式上下文采集
- **WHEN** 主体已解析且天眼查模型与 MCP 路由配置完整
- **THEN** 系统 SHALL 创建一个 Enterprise Context Agent，按报告目录获取并归一化企业事实，同时记录 Agent、Prompt、MCP capability、调用状态和实际 usage

#### Scenario: 调查 Agent 请求天眼查工具
- **WHEN** single 或 multi 调查阶段的任一 Agent 请求原始天眼查 MCP capability
- **THEN** 系统 SHALL 拒绝调用并记录脱敏权限事件，冻结快照和既有 Evidence MUST 保持不变

### Requirement: MCP Gateway 必须执行确定性权限与数据边界
`TianyanchaMcpGateway` SHALL 绑定 Run、Agent、主体、报告时点、capability manifest 和采集预算，并负责授权、参数校验、重试、分页、并发、字段清洗、幂等和 Evidence 写入。MCP 响应 MUST 被视为不可信数据，不能修改 Prompt、角色、权限或业务规则。

#### Scenario: Context Agent 调用已授权 capability
- **WHEN** Agent 为当前主体请求 manifest 声明的 capability
- **THEN** Gateway SHALL 调用对应 MCP，把结果标准化为带主体、时点、来源和内容哈希的 Evidence，并返回 Evidence ID 与来源状态

#### Scenario: MCP 响应包含伪造指令
- **WHEN** MCP 文本要求忽略系统指令、访问凭证或调用未授权工具
- **THEN** 系统 SHALL 仅将文本作为不可信事实数据处理，权限和执行策略 MUST 不变

### Requirement: 每个标准子模块必须具有明确来源状态
采集结果 SHALL 对全部 48 个标准子模块记录且仅记录一个覆盖状态：`available`、`verified_empty`、`capability_absent`、`source_error` 或 `not_requested`。状态 MUST 保留来源、调用和报告时点语义，不得用 Mock 或推断事实覆盖真实空结果和错误。

#### Scenario: 天眼查确认某类记录为空
- **WHEN** 对应 capability 成功执行并确认没有记录
- **THEN** 子模块 SHALL 标记 `verified_empty`，保留调用证据，且后续 DeepSearch MUST NOT 将该状态改写为 `available`

#### Scenario: 正式采集未执行必需子模块
- **WHEN** 因预算或运行终止导致某标准子模块未调用
- **THEN** 子模块 SHALL 标记 `not_requested` 并进入 unresolved gaps，系统不得把它解释为无异常

### Requirement: DeepSearch Agent 必须只处理显式补充任务
`DeepSearchAgent` SHALL 仅接收由 Context Agent 或确定性 `SupplementPolicy` 产生的 `SupplementTask`。任务类型 MUST 为目录要求的 `baseline_enrichment` 或缺口/冲突驱动的 `evidence_gap`，并指明目标子模块、原因、允许 Tool/来源、查询边界和预算。DeepSearch Evidence MUST 与权威 MCP Evidence 分层保存，且不得删除、覆盖或伪造原始来源状态。

#### Scenario: 年报社保作为基础补充
- **WHEN** 当前报告目录启用 `annual_reports` 且年报社保 Provider 已配置
- **THEN** SupplementPolicy SHALL 生成一个目标为 `annual_reports` 的 `baseline_enrichment` 任务，由 `deepsearch-agent` 通过其专属 Skill/Tool 执行一次，并在冻结前保存结果

#### Scenario: capability 缺失且允许公开信息补证
- **WHEN** 子模块状态为 `capability_absent` 且 policy 允许 DeepSearch
- **THEN** 系统 SHALL 创建有界 `evidence_gap` SupplementTask，DeepSearch Agent 仅围绕该缺口检索并保存 URL/文档标识、发布者、抓取时间、适用时点、内容哈希和可信度标签

#### Scenario: 补充来源与 MCP 事实冲突
- **WHEN** DeepSearch Evidence 与已有 MCP Evidence 不一致
- **THEN** 系统 SHALL 保留双方 Evidence 并记录 `conflicts_with`，不得自动选择一方或改写原始响应

### Requirement: 年报 Skill 和 Tool 必须保持 Agent 级最小权限
系统 SHALL 复用 `agentize-bounded-deepsearch` 的成员专属年报 Skill/Tool，且只向共享采集阶段的精确 `deepsearch-agent` 暴露。Enterprise Context Agent、single、multi 的 Leader/专业 Agent/Reviewer SHALL NOT 继承或直接调用该能力。

#### Scenario: 构造共享采集运行时
- **WHEN** 年报社保基础补充启用
- **THEN** `deepsearch-agent` SHALL 获得 `tianyancha-annual-report-social-security` Skill 和对应 Tool，其他 Agent 的 Skill/Tool 列表 SHALL 不包含二者

#### Scenario: 构造 multi 调查团队
- **WHEN** 共享快照已冻结并启动 multi investigation
- **THEN** 调查 roster SHALL 不包含 acquisition `deepsearch-agent`，所有成员只能读取同一冻结快照

### Requirement: 年报社保只能形成标准年报子模块的局部覆盖
年报社保 Tool 的 Evidence SHALL 归入 `operations-analysis/annual_reports` 的 `social_security` 事实组，不得创建新的报告子模块。该 Tool 只支持社保人数和缴费披露；除非其他来源满足目录定义的完整字段集合，否则 `annual_reports` coverage MUST NOT 标记为完整。

#### Scenario: 年报 Tool 返回社保记录
- **WHEN** Tool 返回 `verified_records` 且只包含社保披露
- **THEN** 系统 SHALL 保存对应 Evidence 并将 `annual_reports` 标记为局部覆盖，不得宣称已获得完整财务年报

#### Scenario: 年报 Tool 返回 verified empty
- **WHEN** Tool 在限定年份没有找到可用社保披露
- **THEN** 系统 SHALL 保留已检查年份和 `verified_empty` 事实组状态，但不得将整个 `annual_reports` 或任何风险核查标记为无异常

### Requirement: Context Freezer 必须生成不可变且可验证的快照
确定性 `ContextFreezer` SHALL 在调查开始前校验主体、报告时点、目录版本、Evidence ID、来源谱系、覆盖状态和 SupplementTask 关系，并生成内容寻址的 `EnterpriseContextSnapshot`。快照至少包含 `snapshot_id`、`snapshot_sha256`、subject、`report_as_of`、48 个子模块上下文、Evidence Store、coverage、未解决缺口/冲突和共享采集成本。

#### Scenario: 成功冻结企业上下文
- **WHEN** 采集和允许的补证结束且结构校验通过
- **THEN** 系统 SHALL 以稳定序列化生成快照哈希并将快照设为只读，后续调查只能引用该快照中的 Evidence

#### Scenario: 冻结后需要新增数据
- **WHEN** 后续流程发现必须补充新的外部 Evidence
- **THEN** 系统 SHALL 生成新的快照版本和新哈希，不得原地修改已用于调查或比较的快照

### Requirement: 共享采集阶段必须输出可审计的 Agent 贡献与成本
Context Agent 和 DeepSearch Agent SHALL 产生结构化 `AgentInvestigationResult` 或等价 acquisition contribution，其中风险项允许为空，但 MUST 包含阶段、角色、处理的子模块/SupplementTask、事实 Evidence 引用、Prompt 版本、终止状态和实际 LLM/Tool/MCP/Token/时延成本。

#### Scenario: DeepSearch 没有发现补充事实
- **WHEN** DeepSearch Agent 完成 GapRequest 但没有可采纳 Evidence
- **THEN** 其 Agent 结果 SHALL 保留空事实引用、查询范围、终止状态和实际成本，而不得伪造风险项或删除该执行记录
