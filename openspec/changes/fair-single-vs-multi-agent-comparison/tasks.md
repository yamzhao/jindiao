## 1. 目录与核心数据契约

- [x] 1.1 先添加失败测试，约束 `ReportCatalog` 必须稳定包含 8 个模块、48 个唯一标准子模块及既定模块分布
- [x] 1.2 实现版本化 `ReportCatalog`、加载器和启动校验，并将年报社保信息归入 `annual_reports`，禁止动态第 49 子模块
- [x] 1.3 先添加失败测试，约束 `DueDiligenceCheckCatalog` 的唯一 check ID、必需字段、Prompt 引用、缺数策略及对子模块的合法引用
- [x] 1.4 实现首版固定核查目录，至少覆盖工商存续、治理、司法执行、经营合规、盈利能力、偿债/现金流、关联方和同业偏离
- [x] 1.5 先添加失败测试，再定义带 `baseline_enrichment|evidence_gap` 原因的 `SupplementTask`、来源状态、`SubmoduleContext`、provenance 和不可变 `EnterpriseContextSnapshot` 契约
- [x] 1.6 先添加失败测试，再定义 `CheckResult`、`RiskItem`、`FactEvidenceRef`、`AgentInvestigationResult`、`ReportStructure` 和引用完整性校验
- [x] 1.7 定义 acquisition/investigation 分层成本、运行终止原因和 `ComparisonFingerprint` 契约，加入稳定序列化与哈希测试

## 2. Agent Runtime 探针与版本化 Prompt

- [x] 2.1 用最小可执行探针确认当前 agent-Core `ReActAgent` 的 invoke/streaming、ToolCard 注册、provider usage、取消和资源清理接口，并把结论固化为测试或实现注释
- [x] 2.2 定义 Context Agent、DeepSearch Agent、single、Leader、专业调查 Agent 和 Reviewer 共用的 `AgentExecutionRuntime` 与公开事件契约
- [x] 2.3 新增采集 Prompt bundle，包含 8/48 coverage、Evidence-only 输出、来源状态、外部内容不可信和禁止风险判断
- [x] 2.4 新增调查 Prompt 公共核心及 single/Leader/专业 Agent/Reviewer 角色层，包含固定核查、只读快照、Evidence 引用、缺数语义和禁止最终评分
- [x] 2.5 实现 Prompt bundle 加载、必需片段校验、运行数据结构化注入、版本和 SHA-256 计算
- [x] 2.6 添加 Prompt 回归测试，校验 single/multi 公共核心一致、角色差异可追踪、外部数据不能进入 System 指令层且 Prompt 正文不进入公开产物
- [x] 2.7 扩展 Settings/RunPolicy，区分 formal 与 deterministic harness，并在正式模式缺少模型路由时明确失败

## 3. Enterprise Context Agent 与天眼查 Gateway

- [x] 3.1 先添加采集契约测试，证明一个样本只由 Context Agent 执行主体解析、capability 发现和 48 子模块 coverage，且不产生 RiskItem
- [x] 3.2 将现有天眼查数据获取与 Finding 生成拆分，建立只产生来源观测、Coverage 和 Evidence 的 `TianyanchaMcpGateway`
- [x] 3.3 实现 Run/Agent/主体/报告时点/capability/预算绑定，拒绝未授权 Agent、主体不匹配和未声明 capability
- [x] 3.4 实现分页、重试、并发、幂等、字段清洗和 Evidence 标准化，保留 MCP 工具名、参数哈希、时间、内容哈希和来源状态
- [x] 3.5 实现 `EnterpriseContextAgent` builder 与运行循环，由版本化 Prompt 规划 48 子模块采集并提交 coverage/Evidence
- [x] 3.6 添加 `available`、`verified_empty`、`capability_absent`、`source_error`、`not_requested` 和 Prompt Injection 响应的单元/集成测试
- [x] 3.7 验证所有天眼查业务 ToolCard 只授予 Context Agent，single/multi 调查 Agent 请求时确定性拒绝

## 4. DeepSearch 补证与 Context Freezer

- [x] 4.1 先添加失败测试，约束只有显式 `SupplementTask` 可触发 DeepSearch，支持目录化年报 `baseline_enrichment` 与有界 `evidence_gap`，且 `verified_empty` 不得被覆盖
- [x] 4.2 实现确定性 SupplementPolicy：在配置启用时生成一次年报社保基础补充任务，并为允许的 capability 缺失、来源错误、事实冲突和必需事实缺口生成 gap 任务
- [x] 4.3 复用 `agentize-bounded-deepsearch` 的专属 Skill/Tool 和 Provider 硬约束，实现 formal `DeepSearchAgent` 调用、来源元数据、内容哈希与独立 Evidence 写入；facade 仅保留为 deterministic harness
- [x] 4.4 添加年报 Skill/Tool 仅 deepsearch 可见、调查 roster 排除 deepsearch、年报局部 coverage、无结果、低可信来源、冲突、越界、预算耗尽和 Prompt Injection 测试
- [x] 4.5 先添加失败测试，再实现 `ContextFreezer` 对主体、时点、目录、Evidence ID、来源谱系、coverage 和 SupplementTask/gap/conflict 的校验
- [x] 4.6 实现快照稳定序列化、内容哈希和只读存储；任何补证更新必须产生新 snapshot ID/哈希
- [x] 4.7 添加 8/48 完整快照、partial 快照、哈希稳定性、冻结后修改拒绝和跨主体 Evidence 拒绝测试

## 5. 只读快照访问、提交黑板与安全轨迹

- [x] 5.1 先添加失败测试，再实现按任务最小权限读取 `EnterpriseContextSnapshot` 的只读 ToolCard，不暴露未脱敏原始响应
- [x] 5.2 实现 Run 级提交黑板和 `submit_check_result`，校验 schema、snapshot、subject、check/task 所有权、Evidence ID、版本和幂等性
- [x] 5.3 实现 `submit_review`，支持结构化 ReviewIssue、冲突标记和定向 RepairTask，禁止提交最终风险分或目录修改
- [x] 5.4 实现 `risk|no_risk|inconclusive` Evidence 门禁：必需证据缺失、来源失败或未解冲突不得通过为 `no_risk`
- [x] 5.5 扩展 `BudgetLedger` 原子记录 LLM 请求、输入/输出 Token、schema 重试、返工、并发、截止时间和快照读取
- [x] 5.6 增加 `acquisition.*`、`snapshot.*`、`model.*`、`check.*`、`submission.*`、`review.*` 和预算事件，并映射到兼容 SSE/JSONL 轨迹
- [x] 5.7 统一递归脱敏并添加测试，证明 Prompt 正文、reasoning/chain-of-thought、密钥和原始 MCP/网页响应不进入公开事件、Result 或 benchmark

## 6. 真实 Single Investigator Runtime

- [x] 6.1 先用可脚本化 fake model 添加端到端失败测试，要求一个 Agent 读取冻结快照、完成全部固定核查、提交结构化结果并自检
- [x] 6.2 实现 single `ReActAgent` builder，注入模型、single Prompt、完整 CheckCatalog、只读快照工具、提交工具和最大迭代限制
- [x] 6.3 实现 single invoke/streaming 循环、统一事件转换、provider usage、预算取消和 Agent 资源清理
- [x] 6.4 禁止 single 在核查缺失时由 Python 或旧 `investigate(domain)` 路径静默回填，并以 partial/failed 明确收敛
- [x] 6.5 添加工商存续正常、盈利能力下滑、必需财务 Evidence 缺失、冲突 Evidence、schema 修正和预算耗尽测试
- [x] 6.6 增加需凭证才运行的 live single smoke，校验真实 LLM usage、固定核查提交、只读快照边界和无私有思维链输出

## 7. 真实 Multi Investigator Runtime

- [x] 7.1 先用 fake model 添加 AgentTeams 端到端失败测试，要求建队后实际完成固定核查分配、并行调查、Reviewer 复核和有界返工
- [x] 7.2 重构 investigation Team spec，为 Leader、Corporate、Judicial & Compliance、Financial & Operations、Related & Peer 和 Reviewer 注入版本化角色 Prompt 与最小权限，并确保 roster 不包含共享 acquisition `deepsearch-agent`
- [x] 7.3 实现 Leader 按 CheckCatalog 生成 `CheckAssignment`、覆盖全部启用核查项并通过 AgentTeams 分配
- [x] 7.4 实现专业 Agent 只读快照、提交 `AgentInvestigationResult` 和版本化重提交的业务闭环
- [x] 7.5 实现 Reviewer 读取提交黑板、产生 ReviewIssue/RepairTask，Leader 定向返工且所有成本计入 multi 唯一账本
- [x] 7.6 删除 `build_team` 成功即 stop 的终止条件，改为等待全部核查任务达到终态，并保证成功、异常、取消时清理团队资源
- [x] 7.7 添加核查遗漏、重复分配、跨任务读取、Agent 冲突、Reviewer 返工、全局预算耗尽和生命周期测试
- [x] 7.8 增加需凭证才运行的 live multi smoke，证明团队在建队后产生真实 LLM、快照读取、提交和复核事件

## 8. 确定性裁决、Result 与 API 集成

- [x] 8.1 先添加 Result 契约测试，要求 `agent_results`、`report_structure(8/48)`、context snapshot 摘要和分层成本存在且稳定序列化
- [x] 8.2 扩展 `DueDiligenceResult`，以兼容方式加入 Agent/Check/Risk/Fact、ReportStructure、SnapshotSummary 和 comparison metadata
- [x] 8.3 更新确定性 Evidence 校验与 Reviewer 底线检查，拒绝未知 Evidence、跨快照引用、非法 no-risk、重复风险和未终态核查
- [x] 8.4 更新 RiskRuleEngine，使正式分数仅基于已接受的固定核查结果与版本化规则，不接受模型提交的最终分数
- [x] 8.5 更新 ResultAssembler，将多个 Agent 结果按多对多映射投影到 8/48 章节，并保留 Agent 维度与现有核心字段
- [x] 8.6 将现有动态 `annual_report_social_security` 内容迁入 `operations-analysis/annual_reports/social_security`，按局部字段计算 coverage，并添加不会出现第 49 子模块或误报完整年报的回归测试
- [x] 8.7 将 `DueDiligenceService` 切换为 acquisition → freeze → selected investigation mode → adjudication 流水线
- [x] 8.8 保持唯一 Result JSON/SSE 端点与最终结果等价，扩展事件顺序、关联、断连取消和脱敏契约测试

## 9. 公平成对 Benchmark

- [x] 9.1 先添加失败测试，证明每个 pair 仅执行一次 acquisition（含最多一次年报基础补充）且两臂收到相同 snapshot ID/哈希
- [x] 9.2 实现 `PairedComparisonRunner` 的一次采集、冻结分叉、两臂执行和结果关联
- [x] 9.3 实现 ComparisonFingerprint 预检与差异报告，只允许 mode、拓扑和已记录角色 Prompt 不同
- [x] 9.4 实现数值相同的两臂 investigation 总预算，确保 multi Leader/成员/Reviewer/通信/返工共用唯一账本
- [x] 9.5 从实际 ledger 聚合 `shared_acquisition_cost` 与两臂 `investigation_cost`，不复制共享 MCP/DeepSearch 成本
- [x] 9.6 实现 formal Agent 证据门禁：任一臂 fake/offline、无 provider usage、Token 为零或无有效核查提交时禁止正式结论
- [x] 9.7 扩展 benchmark manifest，预注册场景、重复次数、主指标、权重和阈值，并保留超时/schema/partial 失败样本
- [x] 9.8 更新 JSONL/Markdown 产物，同时呈现质量、核查覆盖、Evidence 充分性、成功率、时延、Token、共享采集成本、冲突和返工
- [x] 9.9 添加快照不一致、目录/Prompt/模型/预算不等价、共享成本重复、multi 隐形额度和不完整试验的拒绝测试

## 10. 验证、迁移与文档收尾

- [x] 10.1 运行目录/契约、Context Agent、DeepSearch、Freezer、single、multi、安全、Result、API 和 benchmark 定向测试并修复回归
- [x] 10.2 运行完整 lint、type-check 和测试集，保存命令、通过数与已声明的 live 跳过原因
  - 2026-09-05 最终复验：`.venv/bin/ruff check .` 通过；`.venv/bin/ruff format --check .` 通过（286 files）；`.venv/bin/mypy src tests` 通过（187 source files）；`.venv/bin/pytest` 通过（434 passed、3 skipped、coverage 87.59%）。跳过项均为需凭证的 live smoke：single 需 `JINDIAO_RUN_LIVE_SINGLE=1`，multi 需 `JINDIAO_RUN_LIVE_MULTI=1`，paired 需 `JINDIAO_RUN_LIVE_PAIRED=1`；paired 已按 10.3 单独执行并通过。
- [x] 10.3 完成至少一组同快照、同模型、同核查目录和同分析预算的 live single/multi smoke；仅在指纹等价时标记 formal
  - 2026-09-05：`JINDIAO_RUN_LIVE_PAIRED=1 JINDIAO_LOG_LEVEL=WARNING .venv/bin/pytest -q --no-cov -s tests/integration/test_live_paired_comparison.py` 通过（1 passed）。pair `pair:e3db8bc1c6d9693629f6d5c2` 共享 snapshot `snapshot:b713bc490afe3b8954da2108` / SHA-256 `b713bc490afe3b8954da21087574c4d94a51681ccb66b28dfaf60f44d1646613`；两臂均使用 OpenAI-compatible `qwen-plus`、同 15 项 CheckCatalog 与同一 investigation budget（LLM 160 次、输入 1,200,000 Token、输出 300,000 Token、总计 1,500,000 Token）。single 实测 19 次 LLM / 1,124,467 Token，multi 实测 43 次 LLM / 828,676 Token，`formal_eligibility.eligible=true`。仅记录脱敏指纹与 usage，未保存 Prompt 正文、私有推理或未脱敏外部响应。
- [x] 10.4 更新 README、API、系统架构、Agent 工作流和 benchmark 文档，解释三层架构、8/48、固定核查、Agent 结果和不暴露思维链边界
- [x] 10.5 新路径通过回归和 live smoke 后，删除旧伪 Agent 业务路径或隔离为 `formal_agent_run=false` 的 deterministic harness
- [x] 10.6 运行 `openspec verify`，确认 proposal/design/specs/tasks 与最终实现一致后再进入归档流程
  - 2026-09-05：当前 OpenSpec CLI 1.2.0 不提供 `verify` 子命令；已运行官方等价结构校验 `openspec validate fair-single-vs-multi-agent-comparison --strict`（exit 0，change valid），并按 completeness/correctness/coherence 三维逐项核对 proposal、design、3 份 delta spec、72 项任务、实现与场景测试，未发现 critical、warning 或 suggestion。
