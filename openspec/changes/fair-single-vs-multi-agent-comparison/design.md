## Context

当前 `DueDiligenceService` 在 `mode=single` 时按固定领域顺序调用 Python `InvestigationToolset`；`mode=multi` 会创建 AgentTeams roster，但成员并未通过 Prompt 执行业务核查，后续调查、复核和返工仍由应用层编排。现有 `TianyanchaHybridToolset` 同时承担数据访问、来源归一化和 Finding 生成，DeepSearch 也嵌在调查路径中。因此 single/multi 不仅没有比较真实的 Agent 能力，还可能观察到不同的实时数据和不同的工具失败状态。

现有代码已经具备主体解析、天眼查 MCP 路由、DeepSearch provider、Evidence/Finding 契约、Reviewer、风险规则、报告组装、唯一 Result API 和 benchmark 框架。本变更保留这些资产，但重新定义边界：外部信息获取是两种调查模式之前的共享阶段；被测 Agent 只在冻结事实之上执行固定核查；正式风险分和报告由确定性组件统一产生。

报告目录和核查目录不是同一概念。报告目录回答“最终报告要呈现什么”，固定为 8 大模块/48 个标准子模块；核查目录回答“Agent 要判断什么”，例如工商存续状态是否正常、盈利能力是否持续下滑。一个核查项可以读取多个子模块，一个子模块也可以支持多个核查项。

## Goals / Non-Goals

**Goals:**

- 用一个企业上下文 Agent 统一管理天眼查 MCP 调用、主体锚定、能力发现、数据归一化和 48 子模块覆盖状态。
- 用独立 DeepSearch Agent 执行策略显式批准的基础补充和缺口/冲突补证，并保持一手来源与补充来源的可区分性。
- 生成不可变且可哈希的 `EnterpriseContextSnapshot`，使同一样本的 single/multi 两臂读取完全相同的事实。
- 用版本化固定核查目录驱动 Prompt 调查，使核查范围、证据要求和缺数语义不随模型自由发挥。
- 让 single 的一个 Agent 和 multi 的 AgentTeams 真正执行相同核查项，且只以调查拓扑作为主要实验变量。
- 让最终结果同时提供按 Agent 聚合的“风险项 + 事实证据”和稳定的 8/48 报告基本信息。
- 保留统一的确定性校验、规则评分、报告组装、资源预算和隐私边界。

**Non-Goals:**

- 不允许调查 Agent 直接访问天眼查、DeepSearch、任意网页或其他原始外部 Tool/MCP。
- 不让模型决定正式风险分、准入结论、报告目录或动态增删核查项。
- 不把 `verified_empty` 自动解释为无风险，也不把来源不可用解释为已核查无异常。
- 不记录、返回或尝试重建模型私有 chain-of-thought；只保留结构化判断摘要和 Evidence 引用。
- 不新增对外 Agent/Tool HTTP 端点，不在本变更中引入分布式队列或外部数据库。
- 不要求生产请求每次同时运行 single 和 multi；公平比较由显式 paired benchmark 负责。

## Architecture

```text
Request / Benchmark Case
          |
          v
Shared Evidence Acquisition (run once per paired case)
  Enterprise Context Agent -> Tianyancha MCP Gateway
               |                         
               +-> Supplement Policy -> DeepSearch Agent
                    |- baseline_enrichment: annual-report Skill/Tool
                    `- evidence_gap: gaps/conflicts
                                           |
                                           v
                              deterministic Context Freezer
                                           |
                                           v
                         EnterpriseContextSnapshot (immutable)
                              /                         \
                             v                           v
             Single Investigator Runtime     Multi Investigator Runtime
                 1 Agent + self-review       Leader + specialists + reviewer
                             \                           /
                              v                         v
                  structured AgentInvestigationResult(s)
                                           |
                                           v
       Evidence validation -> deterministic review -> RiskRuleEngine
                                           |
                                           v
                  ResultAssembler -> DueDiligenceResult
                   agent_results + report_structure(8/48)
```

## Decisions

### 1. 将共享采集与被测调查拓扑彻底分离

一个业务请求的正式执行分为 `acquisition`、`investigation` 和 `adjudication` 三阶段。企业上下文 Agent 与 DeepSearch Agent 属于 acquisition；single/multi 属于 investigation；Evidence 校验、规则计算和报告组装属于 adjudication。

调查 Agent 不获得原始外部工具权限，只获得：

- 冻结 `EnterpriseContextSnapshot` 的只读查询能力；
- 当前 `DueDiligenceCheckCatalog` 的核查任务；
- 结构化提交核查结果和 Reviewer 意见的能力。

这样避免把“哪个模式碰巧取到了什么数据”混入 Agent 拓扑比较。替代方案是让 single 和 multi 各自调用同一工具网关；即使 schema 和预算相同，实时来源、调用顺序和失败时点仍可能不同，不能视为严格公平，故不采用。

### 2. 企业上下文 Agent 是天眼查 MCP 的唯一业务调用者

`EnterpriseContextAgent` 使用版本化采集 Prompt 和受控 `TianyanchaMcpGateway`，负责：

1. 解析并锁定企业主体；
2. 发现当前可用 MCP capability；
3. 依据报告目录为 48 个标准子模块规划调用；
4. 将响应转换为规范 Evidence、子模块数据和来源状态；
5. 生成 coverage manifest 与补证请求，但不产生风险判断。

Gateway 而非 Prompt 承担身份授权、主体约束、capability 白名单、并发、重试、分页、预算、字段清洗和幂等写入。外部响应一律视为不可信数据，不能改变 Agent 角色或工具权限。

采集完成不等于每个子模块都有记录；每个子模块必须有明确状态：`available`、`verified_empty`、`capability_absent`、`source_error`、`not_requested`。formal 快照中原则上不允许 `not_requested`；若预算或硬故障造成未请求，必须作为显式 coverage 缺口保留。

### 3. DeepSearch Agent 只执行显式补充任务，不重写权威来源

复用 `agentize-bounded-deepsearch` 已有的 `deepsearch-agent`、成员级 Skill/Tool 白名单和 Provider 硬约束，但将它从 multi 调查 roster 迁移到共享 acquisition runtime。它不继承天眼查 MCP，不向其他 Agent 传播自己的 Skill/Tool，也不计入 single/multi 的调查 Agent 数。

`DeepSearchAgent` 仅接收 Context Agent 或确定性 `SupplementPolicy` 产生的 `SupplementTask`。任务分为：

- `baseline_enrichment`：报告目录或来源策略预先声明的基础补充；首个任务是通过专属 `tianyancha-annual-report-social-security` Skill/Tool 获取年报社保事实；
- `evidence_gap`：`capability_absent`、重试耗尽的 `source_error`、一手来源冲突或固定核查所需事实缺失时的有界补证。

所有任务必须指明目标标准子模块、允许 Tool/来源、主体、报告截止时间和预算。年报 Tool 访问的是受限天眼查 public-Web Provider，不属于天眼查 MCP；因此不违反“Context Agent 是天眼查 MCP 唯一调用者”的边界。

年报 Tool 当前只解析社保人数和缴费披露，是 `annual_reports` 内的局部事实组，不能单独把整个年报子模块标为完整，也不能支持盈利能力等需要财务报表的结论。`verified_empty` 仅表示在限定年份未获得公开社保披露，不表示企业无风险。

DeepSearch 输出独立 Evidence，必须包含 URL/文档标识、发布者、抓取时间、适用时点、内容哈希和可信度标签。它不得把天眼查 MCP 的 `verified_empty` 改为“有记录”，不得覆盖或删除原始来源状态；冲突通过 `conflicts_with` 关系表达，由后续核查 Agent 判断。

formal 路径中由真实模型 `deepsearch-agent` 调用成员专属 Tool。现有 `DeepSearchEvidenceAgent` 应用 facade 只作为 deterministic harness 和迁移兼容入口；同一 Run 只能选择一个执行面，避免 Agent Tool 与 facade 重复调用 Provider。

### 4. Context Freezer 生成唯一、不可变的事实输入

确定性 `ContextFreezer` 校验主体、报告时点、目录版本、Evidence ID 唯一性、来源谱系、子模块覆盖状态和补证关系，随后生成 `EnterpriseContextSnapshot`。核心字段包括：

- `snapshot_id`、`schema_version`、`snapshot_sha256`；
- `subject`、`report_as_of`、`created_at`；
- `report_catalog_version`、`source_manifest_version`；
- 48 个 `SubmoduleContext` 及各自来源状态；
- 规范 Evidence Store 与 provenance；
- acquisition agents、工具调用和实际成本摘要；
- unresolved gaps/conflicts。

快照落定后不得追加或修改 Evidence。需要新数据时必须生成新快照和新哈希。paired benchmark 的两臂只能引用同一个 `snapshot_id`。

### 5. ReportCatalog 固定 8 大模块和 48 个标准子模块

`ReportCatalog` 使用独立版本和稳定排序。模块分布固定为：

| 模块 | 子模块数 | 说明 |
| --- | ---: | --- |
| `report-summary` | 0 | 基于其他模块确定性汇总 |
| `risk-summary` | 0 | 基于正式风险项确定性汇总 |
| `company-profile` | 6 | 企业基本信息 |
| `judicial-risk` | 9 | 司法风险 |
| `operational-risk` | 12 | 经营合规风险 |
| `operations-analysis` | 13 | 经营与财务分析 |
| `related-parties` | 4 | 关联方与控制关系 |
| `peer-analysis` | 4 | 同业与区域比较 |
| **合计** | **48** | 汇总模块不重复计数 |

目录以现有 `tianyancha-capability-routes.json` 的标准 `submodule_id` 为迁移基线。Provider 派生字段必须归入相应标准子模块；例如工商年报社保数据属于 `operations-analysis/annual_reports` 内部的 `social_security` 事实组，不能创建 `annual_report_social_security` 作为第 49 个子模块。该事实组可以被多个核查项引用，但不得重复计数为报告子模块。

### 6. DueDiligenceCheckCatalog 独立于报告目录

每个核查定义至少包含：

- `check_id`、`title`、`description`、`owner_role`；
- `required_submodule_ids`、`optional_submodule_ids`；
- `prompt_template_id`、`evidence_requirements`、`time_window`；
- `missing_data_policy`、`severity_policy`、`output_schema_version`；
- `report_section_ids`、`enabled`、稳定顺序和目录版本。

首版至少覆盖工商存续状态、注册信息异常变化、控制权集中/不明、重大司法执行、失信/限高、经营异常、行政/环保/海关处罚、股权冻结/质押、盈利能力下滑、偿债压力、现金流压力、收入异常、关联方风险和同业偏离等固定核查项。

核查项与 48 子模块为多对多关系。例如 `profitability-decline` 可同时读取 `financial_summary`、`income_statement` 和 `annual_reports`；`registration-status-normal` 主要读取 `registration`，并可用 `registration_changes` 解释异常状态。目录加载时必须拒绝未知子模块、重复 `check_id`、缺失 Prompt 或非法缺数策略。

### 7. single 与 multi 共享核查语义，只改变调查拓扑

两种模式共享：冻结快照、核查目录、公共 Prompt 核心、模型和采样配置、结构化输出 schema、总分析预算、确定性后处理与评分器。

single 创建一个真实 openJiuwen Agent。它领取完整核查目录，自主安排核查顺序，读取快照，逐项提交结果，并在同一会话中执行结构化自检。系统不得在 Agent 结束后以 Python 业务逻辑悄悄补齐遗漏核查。

multi 创建预定义 AgentTeams：

- Leader：只做任务分配、进度管理和定向返工，不直接生成正式风险项；
- Corporate Agent：工商存续、股权治理和主体信息；
- Judicial & Compliance Agent：司法、执行、处罚与经营异常；
- Financial & Operations Agent：盈利、偿债、现金流和经营趋势；
- Related & Peer Agent：关联方、同业与区域比较；
- Reviewer：检查证据充分性、矛盾、重复风险和缺口，产生 ReviewIssue/RepairTask。

`deepsearch-agent` 不属于该调查团队；它已经在共享采集阶段完成任务。调查团队只读取冻结快照，因此 single 和 multi 获得完全相同的年报补充事实。

角色可以在配置中合并，但核查目录、证据要求和总预算不能因此改变。Leader、Reviewer 和成员通信的模型/Token/时延全部计入 multi 的分析成本。

### 8. 核查输出以 AgentInvestigationResult 为权威提交

每个参与 Agent 都产生一个 `AgentInvestigationResult`；即使 Leader 或 Context Agent 没有风险项，也应以空数组和对应事实/运行摘要体现职责。调查类 Agent 的每个核查结果至少包含：

- `check_id`、`status`：`risk`、`no_risk` 或 `inconclusive`；
- 受限长度的 `decision_summary`；
- `risk_items`；
- `fact_evidence_refs`；
- `missing_evidence`、`conflicts`、`confidence`；
- `prompt_version`、`submission_version` 和任务归属。

每个 `RiskItem` 至少包含 `risk_id`、`check_id`、标题、风险类别、严重度、结论、状态、Evidence ID 和置信度。`FactEvidenceRef` 是对顶层规范 Evidence 的紧凑引用，不复制未脱敏原文。

任何正式结论引用的 Evidence ID 必须存在于冻结快照。必需证据缺失、来源失败或无法解决的冲突必须产生 `inconclusive`；只有达到核查目录声明的证据要求时才能输出 `no_risk`。

### 9. 确定性后处理是统一裁判器

Agent runtime 结束后，两种模式都通过同一流水线：

1. schema、主体、快照和 Evidence 引用完整性校验；
2. 确定性 Reviewer 底线检查和重复项归并；
3. 版本化 `RiskRuleEngine` 计算正式分数与 Decision；
4. `ResultAssembler` 生成模块、汇总和 Markdown。

模型建议的最终分、准入决定或目录修改一律忽略或拒绝。模型 Reviewer 能发现语义矛盾和提出返工，但不能绕过确定性门禁。

### 10. 最终结果同时表达 Agent 贡献和报告基本信息

`DueDiligenceResult` 保留现有 `findings`、`evidence`、`sections`、`decision` 等字段，并新增：

- `agent_results`：按稳定顺序列出 acquisition、single 或 multi 运行中每个 Agent 的角色、核查项、风险项和事实证据引用；
- `report_structure`：`catalog_version`、`module_count=8`、`submodule_count=48` 和各模块/子模块的稳定目录；
- `context_snapshot` 摘要：快照 ID/哈希、报告时点、coverage、缺口和共享采集成本；
- `comparison_metadata`：formal 标记、Prompt/核查/规则/评分器版本及两臂公平性信息。

Agent 结果与报告章节不是一一对应关系。Assembler 依据核查项映射把多个 Agent 的结果投影到 8/48 报告结构，同时保留 Agent 维度供审计。

### 11. 公平比较以冻结快照为共同前置条件

`PairedComparisonRunner` 对每个样本先执行一次共享 acquisition，冻结后再分叉 single/multi 两臂。比较指纹至少包含：

- 代码和 schema 版本；
- `snapshot_id`、`snapshot_sha256`、`report_as_of`；
- 模型 provider/name、采样参数和可用时随机种子；
- 公共 Prompt 核心哈希、核查目录和报告目录哈希；
- 两臂分析预算、规则版本和评分器版本。

允许不同的只有 `mode`、协作拓扑和已记录的角色 Prompt。共享采集成本单列为 `shared_acquisition_cost`，不复制进任一臂；两臂只比较 `investigation_cost`，其中 multi 的协调、Reviewer 和返工成本全部计入。单独执行的生产请求仍报告自己的采集和调查成本，但不自动形成 paired 结论。

### 12. 正式结论必须来自真实模型用量与完整预注册试验

formal paired comparison 要求两臂都有成功的非 fake LLM 请求、provider usage、非零 Token 和结构化提交。相同的总分析预算包括 LLM 请求、输入/输出 Token、墙钟时间、最大并发、schema 重试和返工。调查 Agent 没有外部 Tool 权限，因此底层 MCP/DeepSearch 调用预算属于共享 acquisition，不属于两臂分析预算。

Benchmark manifest 必须预先固定场景、重复次数、主指标、权重和阈值。质量、核查覆盖、Evidence 充分性、成功率、时延、Token 成本、冲突检出和返工均需报告。失败或超时样本不得从聚合中删除；若主指标未达到阈值，结论必须是“未证明协作增益”。

### 13. 公开轨迹可审计但不包含私有推理

公开事件包含阶段、Agent/任务/核查标识、Prompt 与目录版本/哈希、模型请求状态和 usage、受限 `decision_summary`、快照查询、提交校验、Evidence ID、ReviewIssue、预算快照和终止原因。

所有 Agent chunk、SSE、JSONL、Result 与 benchmark 产物都递归移除 `prompt`、`system_prompt`、`reasoning`、`reasoning_content`、`chain_of_thought`、密钥和未脱敏原始响应。可审计性通过结构化判断摘要、事实引用和版本信息实现，而非保存私有思维链。

## Invariants

- 一个 formal paired comparison 只能引用一个冻结快照。
- 一个报告目录版本必须恰好包含 8 个模块和 48 个唯一标准子模块。
- 一个核查目录只能引用当前报告目录中存在的子模块。
- 调查 Agent 的外部 Tool/MCP 权限集合必须为空。
- `deepsearch-agent` 的年报 Skill/Tool 只在共享 acquisition runtime 可见，调查团队成员不得继承。
- 年报社保 Evidence 只能形成 `annual_reports` 的局部覆盖，不能单独形成完整年报 coverage。
- 每个 `risk` 或 `no_risk` 结果必须满足目录定义的 Evidence 要求；否则只能是 `inconclusive`。
- 每个正式 RiskItem 的 Evidence ID 必须存在于冻结快照并能追溯到来源。
- single/multi 的正式 Decision 必须由同一规则版本确定性产生。

## Risks / Trade-offs

- [共享采集会掩盖 single/multi 在工具选择上的差异] → 本变更有意只比较调查与协作能力；工具获取能力作为 acquisition 的独立质量指标评测，不混入拓扑实验。
- [Context Agent 一次覆盖 48 子模块可能消耗较大] → 使用目录驱动的批量规划、gateway 缓存/分页和 coverage policy；成本单列并可独立优化。
- [DeepSearch 材料的可信度低于权威 MCP] → 保留来源等级和冲突关系，不覆盖一手来源；核查目录可禁止低可信来源单独支持 `no_risk`。
- [固定核查项可能限制模型发现新风险] → Agent 可提交 `emergent_observation` 供人工或后续目录版本评审，但本次正式分数只使用预注册核查项，避免不同模式自行扩大范围。
- [multi 在相同总预算下可能因协调开销降低覆盖] → 这是需要如实测量的协作代价，不给 multi 隐形附加额度。
- [新增 Result 字段可能影响客户端] → 以兼容新增字段实施，旧字段继续由统一 Assembler 生成，并用 API/SSE 契约测试保证一致。

## Migration Plan

1. 先建立 ReportCatalog、CheckCatalog、Snapshot、AgentResult 和成本分层契约及校验测试。
2. 将现有天眼查/Provider 调用重构到 Enterprise Context Agent + Gateway，确保只生成事实和 coverage，不生成风险结论。
3. 接入 DeepSearch GapRequest 与 ContextFreezer，完成 8/48 冻结快照和来源谱系测试。
4. 实现只读 Snapshot 工具、结构化提交工具和共享 Prompt bundle。
5. 切换 single 为一个真实调查 Agent，验证完整固定核查、自检和缺失不回填。
6. 切换 multi 为真实 AgentTeams，验证任务分配、复核、返工和团队资源清理。
7. 接入确定性裁决和新 Result 字段，保持唯一 Result API 兼容。
8. 重构 paired benchmark 为“一次采集、两臂调查”，完成 frozen/fake 回归和 live smoke。
9. 新路径通过回归后，删除旧伪 Agent 业务路径或隔离为明确的 deterministic test harness。

回滚可以恢复旧运行路径，但该路径必须显式标记 `formal_agent_run=false`，且不得生成公平比较或协作增益结论。

## Open Questions

- 当前锁定的 agent-Core 版本中，单 Agent 应使用哪一类 invoke/streaming API 才能稳定采集 usage、取消和结构化工具事件？实施前以最小探针确认。
- AgentTeams 原生任务与消息能否直接承载版本化 `CheckAssignment`/`AgentInvestigationResult`？若不能，使用应用黑板和提交工具作为权威交付面。
- 首版核查目录的完整业务清单、阈值和严重度矩阵需要业务方确认；架构先保证其版本化、可校验且 single/multi 完全共用。
- `emergent_observation` 是否进入人工审核队列或仅记录在审计轨迹，留待后续 change 决定。
