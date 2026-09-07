## Context

状态：Accepted，2026-09-06 经用户同意改为比赛 Demo。替代此前生产化的 SQLite 注册表、双角色鉴权、四管理接口方案；不改变已经实现的 ReportPolicy、GapAnnotationBuilder 和基线 golden。实现状态见 tasks.md。

目标：生成旧报告 → 提交用户反馈 → 真实 before/after + 独立回归 → 演示者本地应用 → 新 Run 消费候选 → 恢复基线。
不改 System Prompt、风险阈值、事实、证据或数据源，不训练模型，不让任意自然语言生成可执行规则。

## Decisions

### ADR-001：保留受限策略和真实评测

Status: Accepted

候选唯一允许变更为 gap_placement=section_and_appendix，基线 appendix_only；在相关章节结论后重复原附录已披露缺口，附录不删。版本基线 1.1.0；候选使用唯一 1.1.N。用户 kind 驱动允许的变更，text 仅作反馈背景，不写入 Skill 指令。

固定 GapAnnotationBuilder 保留 source_error、capability_absent 和 Mock 补足语义；verified_empty 不是缺口。目录 capability 优先，domain 兜底；未知项留附录。completeness/gap_reasons 未在原附录披露的内容不凭空增加。

真实回放使用一个源案例和八个预先固定的独立安全视图：正常无缺口、单缺口、多缺口、能力缺失、已核验空、Mock/降级、风险与人工复核、无法映射全局缺口。固定集保存显式 oracle 和文件哈希，不从用户案例生成固定集，不以案例数量当得分。

输出检查器独立解析实际 Markdown 的章节和缺口块，核对 gap_id、原文本和章节位置。移除合法新增块后须等于基线，完整安全视图前后哈希不变，引用合法，Mock 提示保留。拒绝伪造标记/章节。零分母 rate=null/applicable=false。用户每个目标章节及至少一个独立样本严格改善，全样本保护检查通过且不退化才能 awaiting_approval。失败/无收益不能应用。

取舍：只证明展示位置改善，不证明风险召回、模型智能或协作提升；八个样本不是全面质量保证。

### ADR-002：受理绑定与安全快照

Status: Accepted

RunCoordinator.create 首次 accepted 保存 ReportingPolicyBinding（版本、revision、内容、哈希）；幂等返回旧 Run 必须保留旧绑定。执行传入 RunContext 和共享渲染器，不再次读取 active；accepted 排队、在途和历史 Run 不换版。独立 service 在入口冻结；paired runner 对两臂使用同一已冻结 binding，指纹记录 policy/renderer/mapping。

ResultAssembler 用私有回调交接 ReportViewModel，不用全局 last_view。安全视图经 redact_json 和 schema/引用校验后，必须能逐字重现 redact_text(report_markdown)。快照保存 run_id、binding、view/hash、report/hash、实现指纹，模型仅用于溯源。

RunArtifactStore.complete 同次提交 report-replay.json、最终 result/report/metrics 和 manifest，再由 RunCoordinator 保存结果并发 report.completed/终态。快照专属失败令 report_replay_available=false 并给出原因；主结果保存失败仍按 Run 失败，不伪造成功。旧报告无有效快照返回 409，不猜造或回填。

### ADR-003：轻量文件与本地应用，暂不建完整注册表

Status: Accepted

使用 artifact_root/reporting-demo/ 下的 state.json 与 candidates/<evolution_id>.json。state 保存 active binding 和本地应用收据；候选 envelope 保存反馈、源快照、完整评测及内容哈希。采用同目录临时文件与原子替换；命令使用短文件锁拒绝同时写入，不承诺共享文件系统或多服务写者。源 Run 归属和结果只查现有 RunRepository。

提交反馈不改 active。本地 CLI 必须显式指定 artifact root 和 Demo 开关：
- apply <evolution_id> --reason ...：只允许已通过候选，校验内容/实现/suite 指纹与当前基线；重新验证真实评测，确认仍通过才原子替换 state。
- reset --reason ...：切回 bootstrap，保留旧候选及报告，递增 revision。
- show：查看当前绑定。
不对外提供 activate/rollback HTTP 路由，不支持任意历史版本上传或自由策略编辑。旧格式计数候选不导入。本地文件控制权是演示信任边界，不宣称防恶意本机管理员篡改。

取舍：无 SQLite、多表事务、身份审计、后台恢复任务或分布式 CAS；文件损坏明确报错，不能静默给出假版本。演示建议专用临时目录，单服务、单操作人。

### ADR-004：两个显式启用的 Demo 接口

Status: Accepted

仅新增：
- POST /api/v2/due-diligence/runs/{run_id}/feedback
- GET /api/v2/skill-evolutions/{evolution_id}

JINDIAO_REPORTING_DEMO_ENABLED 默认 false。仅 memory/local 存储及 development/test 环境允许启用；绑定回环地址启动，反馈接口拒绝非回环对端和浏览器跨源请求，不信任转发头。代理/云部署不是本期支持配置；本地限制不是用户认证，公网展示应另行加可信网关和基本认证。保留现有 Run owner/session 校验，错配统一 404。不得用 Demo 模式宣称真实用户鉴权。

POST 先验证源 Run completed/partial + result_available + 有效快照，验证章节和 Evidence 属于报告；text 1–2000、目标 1–8、Evidence 最多 32。要求 Idempotency-Key，使用 owner/session/run/key 构造稳定操作 ID；同请求重复返回原资源，不同内容 409。首建 201，已完成重复 200；Demo 同步评测，不设计持久化 evaluating/202 恢复收据。

GET 默认不返回全文；include=reports 返回 before/after/diff，case_id 只选择固定样本 ID，不能读取路径。反馈与对比继承源 Run owner/session；演进不改变源 Run/result/已结束 SSE。

合法但无改善返回 rejected/no_change、no_applicable_gap 或 unmapped_gap。无效字段/引用 422，未知/归属不符 404，未终态/无快照/基线过期 409，超限 413，同时评测 429，运行错误 500，Demo 未启用/不支持存储 503。单快照 4 MiB，总输入和输出各 32 MiB；串行本地评测每案例边界检查总 10 秒预算，无后台自动发布。硬中断进程执行器及复杂任务恢复推迟，不宣称有硬实时 SLA。

旧 v1/v2/invocations 的 skill_feedback 统一 deprecated，返回 rejected/use_feedback_api，不适配旧自由文本或 artifact URI，不阻断主报告。这是显式兼容退化，不继续生成伪分数。

## Validation and Rollout

先测试再实现，保留 golden 和独立 oracle。验收覆盖真实9样本、输出变异、Run 幂等绑定、应用前后/恢复、manifest一致、快照失败、内容篡改、源 Run 隔离、API本地边界和归属。用临时目录运行演示，输出对比和新 Run 版本证据，不改现有工作区 active。更新接口、技术栈、产品与演示文档，注明功能边界和实测结果。

## Deferred

SQLite 完整注册表、双角色/SSO、多租户、公开发布 API、共享存储演进、强恢复/完整审计、多候选并行、任意 Prompt/Skill 自由演进。此前请求契约可留作兼容类型，不代表路由已开放。
