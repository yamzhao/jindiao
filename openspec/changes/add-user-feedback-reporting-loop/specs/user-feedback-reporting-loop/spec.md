## ADDED Requirements

### Requirement: Demo 必须限定为显式开启的本地能力

系统 MUST 默认关闭反馈 Demo；仅 development/test 且 memory/local 可启用。接口 MUST 拒绝非回环对端和浏览器跨源调用，保留既有 Run owner/session 检查，不把身份头当认证，不支持公开部署或双角色治理。

#### Scenario: Demo 未开启或越界调用

- **WHEN** Demo 关闭、存储/环境不支持或调用不在本地边界
- **THEN** 系统 MUST 拒绝反馈管理，不改变现有 Run 主链路，不读取反馈报告

#### Scenario: owner 或 session 不匹配

- **WHEN** 调用者上下文不属于源 Run
- **THEN** 系统 MUST 返回 404，候选及完整对比也执行相同检查

### Requirement: 反馈必须绑定可回放的终态源报告

系统 SHALL 提供 POST /api/v2/due-diligence/runs/{run_id}/feedback 与 GET /api/v2/skill-evolutions/{evolution_id}；MUST 不提供 HTTP activate/rollback。POST MUST 验证 completed/partial、result_available、有效快照、章节/Evidence 归属和白名单字段，要求 Idempotency-Key。

#### Scenario: partial 报告提交有效反馈

- **WHEN** partial 源 Run 结果及安全快照有效且目标章节存在
- **THEN** 系统 SHALL 创建独立候选并真实评测，首次201、同键同请求200，同键不同请求409

#### Scenario: 报告尚未就绪或没有快照

- **WHEN** 源 Run 为 accepted/running/failed/cancelled 或快照无效
- **THEN** 系统 MUST 返回409，不从残留文件或不完整 result 猜造输入

#### Scenario: 查询实际前后文本

- **WHEN** GET 选择 include=reports 或合法 case_id
- **THEN** 系统 SHALL 返回相应真实 before/after/diff 和逐样本指标，不将 ID 解释为文件路径

### Requirement: 演进只能调整已有缺口位置

系统 MUST 仅允许 gap_disclosure_placement 驱动 appendix_only 到 section_and_appendix。用户文字仅作背景，不进入 Prompt/Skill 指令或代码。缺口映射固定，MUST 保留附录、事实、证据、风险分、来源和 Mock 提示。

#### Scenario: 未核验缺口与已核验空

- **WHEN** source_error 或无 Mock 补足的 capability_absent 有可靠章节映射
- **THEN** 系统 SHALL 允许追加原披露；verified_empty、纯 completeness 元数据和无法映射项不得虚构改善

### Requirement: Run 必须在首次受理时固定实际策略

系统 MUST 在 RunCoordinator.create 首次受理时持久化策略版本/内容/哈希，执行只消费绑定；独立 service 入口冻结一次，paired 两臂共享绑定并记录指纹。

#### Scenario: 应用发生在 accepted 之后

- **WHEN** 旧 Run 已 accepted 但尚未执行，随后应用新策略并重试原幂等键
- **THEN** 系统 MUST 返回原 Run/原绑定，旧任务保持旧行为，仅新受理任务使用新版

### Requirement: 快照与最终结果必须一致持久化

系统 MUST 对安全视图重校验引用并逐字复现安全报告；replay、result、report 与 metrics 纳入同次 manifest，RunRepository 保存后才发完成事件，JSON/SSE/文件/仓库元数据一致。

#### Scenario: 快照专属保存失败

- **WHEN** 脱敏、重现、体积或快照写入失败
- **THEN** 主报告 SHALL 继续交付并明确不可回放，不保存未脱敏副本

#### Scenario: 主结果保存失败

- **WHEN** 主产物或 RunRepository 保存失败
- **THEN** 系统 MUST 按 Run 失败处理，不发送 report.completed 或返回缓存的伪成功结果

### Requirement: 评测必须实际运行源案例和八个独立案例

系统 MUST 冻结 suite 与实现指纹，实际渲染同输入的新旧策略，保存全文、diff、哈希和独立检查器结果，不调用模型或外部数据源。独立 suite MUST 包含显式预期映射。

#### Scenario: 用户目标和独立样本共同改善

- **WHEN** 用户每个目标章节和至少一个独立案例指标严格改善、全样本不退化且内容/引用/Mock/结构保护检查通过
- **THEN** 候选 SHALL 为 awaiting_approval，但 MUST 不自动应用

#### Scenario: 输出伪造或不适用

- **WHEN** 输出没变、删除证据、改分、藏 Mock、伪造缺口块、只改善源样本或没有适用缺口
- **THEN** 检查器 MUST 拒绝候选；零分母 rate=null，不以配置变化或用例数判成功

### Requirement: 本地文件状态与明确命令必须闭合生效链路

系统 SHALL 用轻量 JSON 保存不可变候选/评测和活动策略。本地 apply MUST 验证候选完整性、当前父版本和评测指纹，重验通过后原子替换 active；reset MUST 恢复基线且保留历史。无需完整生产注册表，但不得读损坏策略后静默降级。

#### Scenario: 应用候选并恢复基线

- **WHEN** 演示者明确执行本地 apply，之后执行 reset
- **THEN** 后续新 Run SHALL 先使用候选再恢复基线，旧 Run/评测/报告不变

#### Scenario: 过期或损坏候选

- **WHEN** 基线/revision 变化、评测/实现/suite 指纹改变或文件损坏
- **THEN** apply MUST 拒绝且 active 不变，旧格式计数候选不可导入

### Requirement: 反馈失败与旧入口必须隔离

系统 MUST 不因反馈改变源 Run 终态或重开 SSE；设置输入/输出上限与串行执行。旧 skill_feedback SHALL 在 v1/v2/invocations 保留并 deprecated，但统一 rejected/use_feedback_api，不再伪回放。

#### Scenario: 旧反馈请求或新评测失败

- **WHEN** 旧请求携带 skill_feedback 或新候选评测发生失败
- **THEN** 主尽调/源报告 SHALL 保持原生命周期，明确拒绝原因，不产生自动发布或额外取数

### Requirement: 演示必须如实表述能力

文档和 Demo MUST 展示真实对比、新 Run 生效证据和恢复基线，明确只优化报告展示，不宣称模型、风险召回或协作增益，也不声称本地边界等于生产鉴权。

#### Scenario: 演示验收

- **WHEN** 执行闭环演示
- **THEN** 系统 SHALL 在独立目录输出 before/after/diff、逐样本指标、实际应用收据和新旧 Run 策略证据
