## Why

当前实现把数据获取、事实归一化、风险判断和报告组装混在 single/multi 策略中：`single` 主要由固定 Python 顺序执行，`multi` 虽创建 AgentTeams，但业务调查仍由应用代码完成。两种模式既没有真正比较一个调查 Agent 与调查 Agent 团队，也会因为各自访问实时 Tool/MCP 而引入数据时点、调用选择和可用性差异。

本变更将尽调拆成“共享上下文采集—Prompt 驱动核查—确定性裁决与报告”三层。天眼查 MCP 统一由企业上下文 Agent 使用；DeepSearch Agent 只执行策略显式批准的基础补充或缺口补证，其中复用 `agentize-bounded-deepsearch` 已有的成员级年报 Skill/Tool。采集结果冻结后，single 和 multi 使用同一份证据快照、同一套固定核查项和同一总分析预算。这样才能把被测变量收敛到调查拓扑，并让最终风险、事实证据和 8 大模块/48 子模块报告结构可审计、可复现。

## What Changes

- 新增共享 `Enterprise Context Agent`：统一完成主体解析、天眼查 MCP 能力发现、48 个标准子模块的数据获取、来源状态记录和事实归一化，不直接作风险结论。
- 将 `agentize-bounded-deepsearch` 中已注册的 `deepsearch-agent` 迁移为共享采集阶段的受限补充 Agent：只接受显式 `SupplementTask`，支持目录要求的 `baseline_enrichment`（首个能力为年报社保基础补充）以及 `evidence_gap`（缺口/冲突补证），保存来源、时间和内容哈希，不得覆盖天眼查 MCP 的 `verified_empty` 或原始来源状态。
- 保留年报 Skill/Tool 只对 `deepsearch-agent` 可见、其他 Agent 不继承的最小权限设计；formal 路径由该模型 Agent 真正调用 Tool，现有应用 facade 仅保留为 deterministic harness/兼容适配器。
- 新增确定性 `Context Freezer`：把两类采集结果合并为不可变、可哈希的 `EnterpriseContextSnapshot`，作为 single/multi 调查的唯一事实输入。
- 新增版本化 `ReportCatalog`，固定报告为 8 大模块、48 个标准子模块；`report-summary` 与 `risk-summary` 为汇总模块，不额外计入子模块；工商年报社保信息作为局部事实归入既有 `annual_reports`，不得动态生成第 49 个子模块，也不得据此宣称完整年报已覆盖。
- 新增独立、版本化 `DueDiligenceCheckCatalog`，以核查项而非报告目录驱动调查。核查项与报告子模块采用多对多映射，并固定负责人角色、证据要求、时间窗口、缺数策略、Prompt 模板和输出 schema 版本。
- 将 `mode=single|multi` 的语义限定为调查拓扑：single 为一个真实模型 Agent 执行全部固定核查项并自检；multi 为 Leader、专业调查 Agent 和 Reviewer 通过 AgentTeams 分工、复核和有界返工。
- 禁止调查 Agent 直接调用天眼查 MCP 或 DeepSearch；它们只能读取冻结快照、按 Prompt 执行核查并提交结构化结果。证据不足必须输出 `inconclusive`，不得等同于 `no_risk`。
- 保留并强化确定性 Evidence 校验、Reviewer 底线检查、RiskRuleEngine 和 ResultAssembler；模型不得直接决定最终风险分、准入结论或修改报告目录。
- 扩展 `DueDiligenceResult`：加入 `agent_results`，每个 Agent 均返回其风险项与事实证据引用；加入 `report_structure`，明确目录版本、8/48 计数和各模块结构。
- 重构公平 benchmark：共享采集阶段每个样本只执行一次，其成本单列；single/multi 两臂共享冻结快照、模型、采样参数、Prompt 核心、核查目录和总分析预算，multi 的协调、复核与通信消耗全部计入 multi。
- `deepsearch-agent` 不进入 multi 调查 roster，也不计入 single/multi 的调查 Agent 数；其模型、Skill/Tool 和年报 Provider 消耗统一计入共享采集成本。
- 扩展公开轨迹和用量统计，但继续禁止持久化或返回 Prompt 正文、私有思维链、密钥和未脱敏原始响应。
- 明确 deterministic/fake runtime 仅用于契约测试，不得作为“真实 Agent 协作增益”的正式证据。

## Capabilities

### New Capabilities

- `enterprise-context-acquisition`: 企业上下文 Agent、DeepSearch Agent、8/48 报告目录、来源状态和冻结证据快照的边界与契约。
- `prompt-driven-agent-execution`: 固定核查目录、single/multi Prompt 驱动调查、结构化 Agent 结果、证据引用和确定性后处理。
- `fair-agent-comparison`: 共享采集、成对调查、等价指纹、分析预算、实际成本计量和协作增益声明规则。

### Modified Capabilities

无。当前项目尚无发布到 `openspec/specs/` 的同名 capability；本变更以新增 capability 约束并替换现有伪 Agent 执行语义。

## Impact

- 影响 `src/jindiao/tianyancha/`、`src/jindiao/deepsearch/` 和场景数据适配层：从“每个调查策略自行取数”重构为共享的企业上下文采集流水线。
- 复用 `agentize-bounded-deepsearch` 已完成的年报 Provider、Skill、Tool、成员级白名单和来源状态契约，但改变其最终编排位置，并以本 change 的共享采集语义作为目标架构。
- 影响 `src/jindiao/orchestration/`：single/multi 改为只消费冻结快照和固定核查目录的真实 Prompt 驱动运行时。
- 影响 `src/jindiao/contracts/`、`src/jindiao/reporting/` 和 `config/`：新增快照、报告目录、核查目录、Agent 风险结果和证据引用契约。
- 影响 budget、SSE/JSONL trace 和 benchmark：区分共享采集成本与两臂分析成本，并增加可比性指纹与正式结论门禁。
- 现有唯一 `POST /api/v1/due-diligence/result` 端点保持不变；新字段以兼容方式加入，但 formal 运行缺少模型配置时必须明确失败，不能静默回退到确定性流程。
