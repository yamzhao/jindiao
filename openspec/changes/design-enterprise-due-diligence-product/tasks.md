## 1. 工程基础与配置

- [x] 1.1 按 design 分层补齐 `src/jindiao/` 包目录和公共导出，并添加模块边界测试
- [x] 1.2 在根目录 `requirements.txt` 与 `pyproject.toml` 中对齐 FastAPI、Pydantic、SSE、agent-Core、DeepSearch、测试和质量工具的公开包依赖，并生成 `uv.lock`
- [x] 1.3 实现集中式 Settings，支持模型、天眼查 MCP、超时、并发、预算和 Mock 根目录的环境变量配置
- [x] 1.4 定义应用错误分类、错误码与异常到 API/运行结果的映射
- [x] 1.5 配置 Ruff、mypy、pytest 和覆盖率门禁，使 `make check` 可一次执行

## 2. 核心领域契约

- [x] 2.1 实现企业输入、候选主体、ResolvedSubject 与 EntityAmbiguousError 模型
- [x] 2.2 实现 SourceStatus、Evidence、EvidenceReference 和覆盖率模型及跨字段校验
- [x] 2.3 实现 InvestigationPlan、InvestigationTask、Finding、ReviewIssue、RepairTask 和 ReviewDecision 模型
- [x] 2.4 实现 RuleHit、Decision、RiskSummary、ReportSection 和 ReportViewModel 模型
- [x] 2.5 实现 result 请求、完整 Result、错误、AgentTrace、Collaboration、Evaluation 和 SkillEvolution 模型
- [x] 2.6 实现 SSE 事件联合类型、递增序列封装与事件 payload 校验
- [x] 2.7 为标识引用、无证据不得 accepted、Mock 来源一致性和 Result 引用完整性编写契约测试

## 3. 固定 Mock 场景仓库

- [x] 3.1 定义并文档化 `mock_data/scenarios/<scenario_id>/manifest.json` schema 和版本规则
- [x] 3.2 实现 ScenarioRepository 的企业键匹配、文件白名单、哈希校验和不可变快照加载
- [x] 3.3 实现 Run Context 对 `scenario_snapshot_id`、内容、规则版本和 Skill 版本的冻结
- [x] 3.4 禁止运行时访问 `expected/`，为评分器提供独立只读 expected loader
- [x] 3.5 实现场景间隔离测试、同企业多 Agent 一致性测试和运行期间文件变更测试
- [x] 3.6 按标准报告构造首批正常、司法高风险、经营异常和证据冲突企业场景及预期结果

## 4. 天眼查 MCP 证据适配

- [x] 4.1 实现可注入 transport 的异步天眼查 MCP client、超时、有限重试和错误分类
- [x] 4.2 实现企业搜索与基于名称、信用代码、地区及状态的主体唯一锚定
- [x] 4.3 实现 capability manifest 获取、缓存和基于配置的领域到工具路由
- [x] 4.4 实现工商治理、司法合规、经营情况和关联同业返回值的 Evidence normalizer
- [x] 4.5 实现 `verified_records`、`verified_empty`、`capability_absent`、`source_error` 和 `degraded_mock` 状态机
- [x] 4.6 实现 Evidence 去重但保留多来源链的合并策略
- [x] 4.7 添加成功记录、有效空结果、能力缺失、超时、限流、鉴权失败和派生事实的契约测试
- [x] 4.8 添加授权头、API key 和 MCP 诊断内容的日志及错误脱敏测试

## 5. DeepSearch 与 Mock 回退

- [x] 5.1 在 `src/jindiao/deepsearch/` 实现不泄漏 SDK 类型的统一检索接口
- [x] 5.2 实现只索引当前场景 `corpus/` 的本地 DeepSearch provider 和生命周期管理
- [x] 5.3 实现包含场景、版本、文件和片段的稳定 `mock://` 引用生成器
- [x] 5.4 实现仅在 capability 缺失时自动 Mock、仅显式演示降级时允许源错误 Mock 的回退服务
- [x] 5.5 添加有效空结果禁止回退、未开启降级禁止回退、Mock 显著标记和跨场景检索隔离测试

## 6. 风险规则、覆盖率与报告

- [x] 6.1 定义版本化准入类和关注类规则配置及受支持的 Finding 条件
- [x] 6.2 实现无证据拒绝计分、规则命中明细和总分一致性校验的确定性规则引擎
- [x] 6.3 实现 `[0,20)` 通过、`[20,80)` 人工复核和 `[80,+∞)` 拒绝分档及边界测试
- [x] 6.4 实现按领域的调查覆盖率、数据时效、未解决冲突和决策置信度计算
- [x] 6.5 实现包含八类标准报告内容和前端十类信息视图的 ReportViewModel assembler
- [x] 6.6 实现从 ReportViewModel 单一来源渲染的 Markdown 报告模板
- [x] 6.7 添加准入/关注分类、数据缺口、规则追溯、章节完整性及结构化结果与 Markdown 一致性测试

## 7. 单智能体基线

- [x] 7.1 定义 single 与 multi 共用的 OrchestrationStrategy 接口和运行预算模型
- [x] 7.2 实现一个顺序执行主体、调查、自检和 Finding 输出的 single-agent 基线
- [x] 7.3 确保 single-agent 复用相同 ScenarioRepository、工具、规则引擎和 ResultAssembler
- [x] 7.4 添加 single-agent 在正常、空结果、能力缺失和错误场景的端到端测试

## 8. AgentTeams 多智能体协作

- [x] 8.1 使用 TeamAgentSpec 配置 Leader、Governance、Judicial & Compliance、Operations & Peer、DeepSearch 和 Reviewer
- [x] 8.2 实现 Leader 的 capability-aware InvestigationPlan、任务 DAG、预算和跳过原因生成
- [x] 8.3 实现三个专项调查 Agent 的领域边界、工具白名单和结构化 Finding/Evidence 输出
- [x] 8.4 实现 DeepSearch Evidence Agent 仅消费允许回退的检索任务
- [x] 8.5 实现运行期 Evidence Store 的幂等合并、来源链和 Agent 产物校验
- [x] 8.6 实现 Reviewer 的主体、时间、金额、状态、重复、证据充分性和跨 Agent 冲突检查
- [x] 8.7 实现冲突组、定向 RepairTask、最大返工次数和预算耗尽后的未确认语义
- [x] 8.8 使用 `Runner.run_agent_team_streaming` 执行 AgentTeams，并将成员事件聚合到应用事件流
- [x] 8.9 添加并行调度、无证据 Finding 拒绝、冲突阻断、定向返工和有界收敛集成测试
- [x] 8.10 验证规则引擎和 ResultAssembler 只在 Reviewer 最终状态后运行

## 9. 唯一 Result API

- [x] 9.1 实现唯一 `POST /api/v1/due-diligence/result` 路由和请求校验，不注册内部业务端点
- [x] 9.2 实现 `application/json` 内容协商和完整 Result 返回
- [x] 9.3 实现 `text/event-stream` 内容协商、稳定事件类型、sequence 排序和 `report.completed` 终止事件
- [x] 9.4 实现 agent-Core/DeepSearch 内部事件到公开 SSE schema 的 EventMapper
- [x] 9.5 实现主体失败、数据源缺口、运行失败和可恢复不完整报告的 HTTP/SSE 错误语义
- [x] 9.6 实现 SSE 客户端断开后的 Agent 和工具任务取消
- [x] 9.7 添加仅一个业务端点、JSON schema、SSE 顺序、JSON/SSE 最终语义等价和取消行为测试

## 10. 三个可复用 Skills 与自演进

- [x] 10.1 创建 `tyc-evidence-acquisition` Skill，包含 SKILL.md、输入输出 schema、最小示例、评测集、版本和 changelog
- [x] 10.2 创建 `evidence-backed-due-diligence` Team Skill，固化计划、Evidence/Finding、Reviewer 和 RepairTask 协作协议
- [x] 10.3 创建 `feedback-evolved-reporting` Team Skill，固化报告反馈归因和改进候选格式
- [x] 10.4 为三个 Skill 实现仓库无关的挂载配置和可在示例 Agent/AgentTeams 中运行的复用测试
- [x] 10.5 接入 TeamSkillEvolutionRail 生成只写入候选目录的技能修订建议
- [x] 10.6 接入 EvolutionInterruptRail 和 EvolutionReviewRuntime，实现中断审批与二次审查
- [x] 10.7 实现风险规则、阈值、来源优先级、Mock 标识、证据门槛和密钥治理的不可演进检查
- [x] 10.8 实现候选/稳定版本元数据、内容哈希、反馈来源、评测记录、人工激活和回滚
- [x] 10.9 添加候选退化拒绝、治理违规拒绝、未审批不激活、通过后激活和回滚测试

## 11. 观测与运行产物

- [x] 11.1 实现 request/run/agent/task/tool/evidence 关联的结构化 JSONL 观测
- [x] 11.2 记录端到端及阶段耗时、首条有效证据、调用数、token、冲突和返工指标
- [x] 11.3 实现字段白名单脱敏和不记录私有推理文本的安全日志策略
- [x] 11.4 将运行结果、脱敏轨迹和报告写入 `artifacts/`，并添加默认 Git 忽略规则
- [x] 11.5 添加并发事件关联、错误观测和敏感信息不落盘测试

## 12. 单智能体与多智能体基准

- [x] 12.1 定义 benchmark manifest，冻结场景类别、模型参数、种子、预算、重复次数和主指标
- [x] 12.2 将基准集扩充到 10–20 个企业场景，覆盖主体歧义、风险、冲突、空结果、能力缺失、源错误和报告缺口
- [x] 12.3 实现对同一冻结输入成对运行 single/multi 的 benchmark CLI，并校验配置公平性
- [x] 12.4 实现覆盖率、证据支持率、风险质量、冲突检出率和报告结构五项质量指标
- [x] 12.5 实现质量权重 30/25/20/15/10、成功率、耗时和资源指标的聚合及可重算明细
- [x] 12.6 导出逐场景 JSONL、聚合 JSON、Markdown 对比表和失败样本清单
- [x] 12.7 实现 expected 数据运行时隔离测试、结果重算测试和无主指标提升时禁止宣称协作增益的检查
- [x] 12.8 在冻结环境执行重复实验，记录 multi 相对 single 的提升、置信区间和时延/成本代价

## 13. 开源复现与部署

- [x] 13.1 完成 `.env.example`，仅包含安全占位符并说明天眼查及模型凭据注入方式
- [x] 13.2 完成 Makefile 和 scripts，使安装、开发、测试、Mock 演示、benchmark 和报告生成均有一键命令
- [x] 13.3 完成单服务 Dockerfile、健康检查和不依赖数据库的本地容器运行配置
- [x] 13.4 按社区工程规范完善 README、架构图、快速开始、唯一 API 示例、Skills 复用和实验方法
- [x] 13.5 将评审通过的产品设计同步为 `docs/product-design.md`，并与 technical-stack 和 OpenSpec artifacts 建立链接
- [x] 13.6 完成 CONTRIBUTING、许可证、代码规范、测试说明、目录说明和安全披露说明
- [x] 13.7 验证删除本地 agent-Core 与 DeepSearch 源码目录后仍可通过 `requirements.txt` 或 `pyproject.toml` 安装，并重新锁定依赖

## 14. 最终验证与参赛交付

- [x] 14.1 运行 OpenSpec 严格校验并修复全部 proposal/design/spec/tasks 结构问题
- [x] 14.2 运行 Ruff、mypy、单元测试、集成测试和 API 端到端测试并保存摘要
- [x] 14.3 在无天眼查凭据的纯 Mock 单机环境完成 JSON 与 SSE 全流程演示
- [x] 14.4 在授权环境完成天眼查主体、能力、记录、空结果、缺失能力和错误降级联调
- [x] 14.5 运行完整 single/multi benchmark，确认至少一个预声明主指标证明协作增益并如实披露代价
- [ ] 14.6 执行容器冷启动和 README 从零复现演练，确认仓库不含密钥、私有绝对路径或未声明服务依赖
- [x] 14.7 冻结数据、规则、Skills、模型配置和评测结果版本，生成最终演示检查清单

## 15. 演示完整性与模式切换增量

- [x] 15.1 为唯一 Result API 增加 `mode=single|multi` 查询参数，JSON/SSE 均回显实际运行模式且保持只有一个业务端点
- [x] 15.2 验证实时来源全部失败且显式允许降级时，固定完整 Mock 模板可生成八个章节并保留真实主体及 Mock 来源语义
- [x] 15.3 在 Markdown 顶部只显示一次醒目 Mock/降级提示，移除正文重复提醒但保留 Evidence 来源可追溯性
- [x] 15.4 增加复用正式 BenchmarkRunner 的独立 single/multi 对比脚本，并同步 README、API、评测与产品文档
