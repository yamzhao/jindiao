# OpenSpec 实现验证报告

> 本页是 2026-09-03 的历史记录。报告反馈部分已由 2026-09-06 本地 Demo 替代，旧格式计数不得视为真实回放证据；当前结果见 [反馈闭环验收](reporting-feedback-verification.md)。原报告 Skill 包已按原哈希保存在 `release/reference/skills/team/feedback-evolved-reporting/`。

- Change：`design-enterprise-due-diligence-product`
- 验证日期：2026-09-03
- 结论：通过（按用户要求，本轮明确排除任务 14.6）
- 代码环境：macOS arm64、CPython 3.11.4、单机文件系统

## 范围与判定

OpenSpec apply 状态为 98/99；唯一未勾选项是 14.6“容器冷启动和 README 从零复现演练”。该项由用户明确要求暂时忽略，仍保持未完成，未被错误标记为通过。本轮对其余 proposal、design、52 条 requirement、75 个 scenario、98 个已完成任务及实现进行一致性验证。

在上述范围内没有剩余 CRITICAL 或 WARNING。OpenSpec change 尚不应归档；待 14.6 以后完成并重新验证后再归档。

## 本轮缺口修复

| 原问题 | 修复与验证证据 | 结果 |
| --- | --- | --- |
| AgentTeams 是旁路事件壳 | 非 `offline_mock` 默认选择 `OpenJiuwenTeamRuntime`；`TeamAgentSpec` 通过官方 `Runner.run_agent_team_streaming` 执行。完整冻结上下文注入团队，首个可观测协调事件成为专项执行硬门槛；框架错误或空运行阻断结果。新增强制门控集成测试。 | 已修复 |
| DeepSearch 任务落入通用工具 | `deepsearch-agent` 现在创建场景隔离的 `ScenarioDeepSearchProvider`，通过 `DeepSearchEvidenceAgent`/`MockFallbackService` 返回 `deepsearch.local` 与 `mock://` Evidence。 | 已修复 |
| `verified_empty` 被整域 Mock 覆盖 | Tianyancha 成功空结果立即返回唯一 `verified_empty` coverage，不加载该领域 Mock Evidence、Finding 或 section data。 | 已修复 |
| SSE 完成后一次性回放 | `DueDiligenceService.stream` 使用后台执行任务与实时队列；主体、计划、Agent、证据、回退、冲突、返工、章节和技能候选在产生时映射为公开事件。断开会取消后台任务。 | 已修复 |
| RepairTask 只生成不执行 | 按 `max_repair_rounds` 定向重跑目标 Agent，记录 completed 次数；返工任务/证据使用版本化身份，推进时钟下也不会发生幂等冲突；预算耗尽仍保持 unconfirmed。 | 已修复 |
| Skill 自演进未接产品 | 唯一 result 请求新增可选 `skill_feedback`。反馈必须带来源、引用和失败证据引用；候选经过固定集回放后只写入隔离目录，结果和 SSE 披露状态。自动批准配置被拒绝，稳定 Skill 只能由已有人工审批接口激活。 | 已修复 |
| benchmark 缺少正式预检和超时 | 输出目录创建前校验类别、有效空行为、企业键、场景版本、文件哈希和两类 expected；每个运行受冻结超时取消；partial manifest 只能显式用于测试。 | 已修复 |
| 无 Git HEAD 时版本不可用 | 使用 `source-sha256:` 对源码、配置、Skills、manifest 和依赖清单生成确定性代码指纹；冻结结果不再含 `unavailable`。 | 已修复 |
| `report_as_of` 未生效 | Run Context 独立冻结请求报告时点，Reviewer、质量计算和 Decision 使用同一日期。 | 已修复 |
| 唯一 API 无法切换 single/multi | result 端点新增可选 `mode=single|multi` 查询参数，默认 multi；JSON/SSE 均使用并回显实际模式，非法值在创建运行前拒绝。 | 已修复 |
| 天眼查全域失败时演示报告不够完整 | 主体锚定后、请求显式允许降级时，四个报告领域均可由固定完整模板生成 `degraded_mock` Evidence，最终保留八个章节和真实主体字段。 | 已修复 |
| Markdown Mock 提醒重复 | 报告标题后只显示一次醒目 Mock 数据提示；正文不重复通用提醒，Evidence 行仍保留来源与 `mock://` 引用。 | 已修复 |
| 缺少独立对比脚本 | 新增 `scripts/compare_agents.py`，直接复用正式 BenchmarkRunner；Makefile 和复现元数据均指向该入口。 | 已修复 |

## 自动化门禁

| 验证项 | 结果 | 证据摘要 |
| --- | --- | --- |
| OpenSpec strict | 通过 | `openspec validate design-enterprise-due-diligence-product --strict` 退出 0；遥测域名不可达只产生退出后的非功能提示 |
| Ruff | 通过 | 全仓 lint 与 format check 通过，181 files formatted |
| mypy | 通过 | 118 个 `src`/`tests` 源文件无类型错误 |
| pytest + coverage | 通过 | 223 passed；总覆盖率 89.62%，高于 80% 门禁 |
| Result API | 通过 | 仍只有一个 `/api/v1/due-diligence/result` 业务端点；支持 single/multi，JSON/SSE 最终 Result 等价 |
| 渐进 SSE | 通过 | 阻塞调查尚未完成时已收到 `entity.resolved`，完成后以 `report.completed` 终止 |
| 数据源状态 | 通过 | records、empty、capability absent、source error、显式 degraded mock 均有自动测试 |
| Skill 演进 | 通过 | 候选隔离、固定回放、治理拒绝、未审批不激活、人工激活与回滚测试通过 |
| 依赖导入 | 通过 | `openjiuwen=0.1.17`、`openjiuwen-deepsearch=0.2.0`、`jindiao=0.1.0` |
| 冻结文件哈希 | 通过 | `release/frozen-manifest-v1.json` 与全部列出的文件/目录哈希一致 |

测试中的 3 条 warning 来自 openJiuwen 传递依赖中的 Pydantic v2 旧配置和 DashScope 旧 Assistants API，不来自 Jindiao 源码，不改变功能或门禁结果。

## 协作增益复验

正式 benchmark 已重新执行：10 个场景 × 3 次重复 × 2 种模式，共 60 条 paired records。预声明主指标仍证明协作增益：

| 指标 | Single | Multi | Multi - Single |
| --- | ---: | ---: | ---: |
| 质量总分 | 0.656250 | 0.761250 | +0.105000 |
| 成功率 | 0.800000 | 0.800000 | 0 |
| 工具调用数 | 4.000000 | 4.600000 | +0.600000 |
| 冲突检出数 | 0 | 0.300000 | +0.300000 |
| 已请求并执行返工 | 0 | 0.600000 | +0.600000 |

质量差值 95% 配对区间为 `[0.046624, 0.163376]`，不跨 0。multi 的毫秒级时延和工具调用代价已在冻结对比表中披露，不将其包装为性能收益。

本次冻结代码指纹为 `source-sha256:abf68ad42ca92d336736986c4f7733c460ef7bdcd8410b36ffa8a54e1d4e9619`。原始记录、摘要、失败样本和对比表位于 [`benchmarks/reference/`](../benchmarks/reference/)，文件哈希位于 [`release/frozen-manifest-v1.json`](../release/frozen-manifest-v1.json)。

## 明确保留项

- 任务 14.6 保持 `[ ]`，本轮不执行、不判定，也不作为用户指定范围内的 CRITICAL。
- OpenSpec CLI 上报匿名遥测时无法解析 `edge.openspec.dev`；strict 校验本身已成功，不影响 change 有效性。
