## Why

比赛 Demo 需要证明“用户反馈 → 真实前后对比 → 手动应用 → 下一次报告生效”，而不是仅生成候选或预置分数。用户于 2026-09-06 同意裁剪完整注册表、双角色鉴权和发布 API，保留最小可验证闭环。

## What Changes

- 仅演进 feedback-evolved-reporting 的 gap_placement：appendix_only → section_and_appendix；保留附录、事实、证据、风险判断和 Mock 提示，不改 System Prompt 或调查 Agent。
- 保留 Run 首次受理时的版本/策略/哈希绑定，以及同一安全视图上的真实回放和八个独立固定案例。
- 使用本地 JSON 文件保存当前策略、候选和评测；演示者通过本地 CLI 明确应用或恢复基线，不建设 SQLite 多表注册表、完整审计事务或多写者发布。
- 仅新增两个 v2 接口：提交反馈、查询对比。默认关闭，显式启用 Demo；仅允许本地单进程、隔离环境，不增加双角色认证，不弱化既有 owner/session 检查。
- 安全快照纳入同次 artifact manifest，JSON/SSE/文件/RunRepository 的实际版本和回放可用性保持一致。
- 旧 skill_feedback 保留并 deprecated，明确 rejected/use_feedback_api，不再走格式计数伪回放，也不猜测历史 anonymous 报告归属。

## Capabilities

### New Capabilities

- user-feedback-reporting-loop: 隔离比赛 Demo 的用户反馈、白名单候选、真实回放、手动应用和新 Run 生效。

### Modified Capabilities

无。依赖 add-agentarts-run-api-and-workflow-observability 的 Run 生命周期；保留公平 single/multi 的冻结事实和确定性裁决边界。此前四管理 API、SQLite、双角色治理要求由本次用户批准的 Demo 范围替代，并非声称已实现。

## Impact

涉及 contracts、reporting、RunCoordinator/RunContext/service/result assembler、artifact、两个 API、本地 CLI、测试和 docs/api/README.md。无新增部署服务、模型调用或外部依赖。共享部署、公共用户认证、生产级恢复与发布治理留到后续变更。
