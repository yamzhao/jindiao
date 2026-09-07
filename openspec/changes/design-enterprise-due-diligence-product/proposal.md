## Why

企业信用与风控尽调需要同时处理工商治理、司法合规、经营情况、关联关系和同业对比等异构信息，单一智能体容易出现遗漏、证据混淆、冲突未发现以及结论不可复核的问题。参赛作品需要以可复现实验证明多智能体的协作增益，因此应先建立一套证据驱动、数据来源可追踪、规则判定确定且能在单机 Mock 环境稳定复现的产品契约。

## What Changes

- 建立面向企业信用与风控尽调的完整产品流程，覆盖主体确认、计划拆解、并行调查、证据审查、冲突修复、风险判定和 Markdown 报告生成。
- 以 openJiuwen agent-Core 的 AgentTeams 作为唯一多智能体编排核心，以 DeepSearch 作为受统一调度和观测的检索型能力，不沿用既有低代码 Workflow 作为实现基础。
- 天眼查 MCP 作为企业公开信息的首选来源，查询流程遵循“企业搜索与主体锚定—能力发现—业务查询”；所有事实进入统一 Evidence 契约后才能支撑报告结论。
- 明确数据降级语义：能力存在且返回空数据表示“已核验无记录”，不得以 Mock 补齐；仅当能力缺失，或演示环境明确处于降级模式时，才允许使用带显著来源标记的本地 Mock 数据。
- 采用“每个测试企业一份固定数据集”的 Mock 策略：同一企业场景具有不可变版本和内容哈希，所有 Agent 在一次及重复评测中读取相同快照，不使用因 Agent 或运行次数变化的数据。
- 将报告中的准入类风险与关注类风险分离，由确定性规则引擎计算风险分和准入建议；模型负责提取、归纳和解释，不得自由改写硬规则、阈值或最终分数。
- 对外只提供 `POST /api/v1/due-diligence/result`，同时支持 JSON 与 SSE，返回前端所需的完整结构化数据、协作过程、评测指标和 `report_markdown`。
- 沉淀三个可复用 Skills：天眼查证据采集、证据化尽调分析、基于反馈的报告改进；自演进必须经过审查、固定集回放和人工批准，禁止静默修改稳定 Skill。
- 建立单智能体与多智能体的公平对比基准，统一模型、参数、数据、结果契约和资源预算，量化质量、成功率和耗时中的至少一个维度，并保留可复跑的原始结果。
- 限定为单机高代码应用：业务数据使用本地 Mock 文件，不引入外部数据库、消息队列或分布式组件；代码、配置示例、一键部署和评测命令必须可公开复现。

## Capabilities

### New Capabilities

- `enterprise-identity-and-evidence`: 企业主体消歧、天眼查 MCP 能力发现与查询、统一证据模型、来源追踪及查询覆盖率管理。
- `multi-agent-due-diligence`: 协调者、专项调查 Agent、DeepSearch 检索 Agent、证据 Reviewer 与报告组装之间的任务拆解、并行协作、冲突发现和定向返工。
- `risk-assessment-and-decision`: 覆盖标准报告章节的风险发现、准入类/关注类风险分类、确定性评分、决策分档及证据充分性校验。
- `versioned-mock-scenarios`: 按测试企业隔离的固定 Mock 数据集、版本和哈希校验、统一快照读取，以及能力缺失和演示降级时的可审计回退规则。
- `due-diligence-result-contract`: 唯一 result 接口的请求、JSON/SSE 事件、完整前端视图模型、错误语义和 Markdown 报告输出。
- `reusable-skills-and-evolution`: 三个可复用技能包的输入输出、挂载方式、最小示例、版本治理，以及基于反馈但受审查、回放和审批约束的自演进流程。
- `collaboration-benchmarking`: 单智能体与多智能体在相同数据、模型、参数及预算下的对照运行、指标计算、轨迹观测和可复现结果导出。

### Modified Capabilities

无。当前项目尚无已发布的 OpenSpec capabilities。

## Impact

- 影响 `src/jindiao/` 中的 API、AgentTeams 编排、天眼查适配、DeepSearch 适配、证据审查、规则引擎、报告组装和观测模块。
- 新增 `mock_data/` 中按企业组织的版本化场景契约，以及 `benchmarks/` 中单双 Agent 共用的基准数据和评分规则。
- 新增顶层 `skills/` 下三个可复用技能包及其受控演进、版本和回归验证约定。
- 固化唯一公开业务端点及其 Pydantic/SSE 契约；内部 Agent、工具和 DeepSearch 不单独暴露 HTTP 接口。
- 通过 `requirements.txt` 和 `pyproject.toml` 安装 agent-Core 的公开发行包及 DeepSearch 的公开固定 commit；本地源码仓库只作为 API 与实现参考，并通过 uv 锁定可复现版本。
- 天眼查授权信息仅通过运行时环境变量注入，不进入源码、OpenSpec、Mock 数据、日志或开源仓库。
