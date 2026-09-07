## 比赛 Demo 实施任务（2026-09-06 重定范围）

旧版 32 项任务由用户批准的 Demo 范围替代，生产治理并非“已完成”，而是移至 design.md 的 Deferred。已实现的契约、映射和 golden 保留。

- [x] D1 对齐 proposal/design/spec/tasks：两 API、本地命令、JSON 状态，明确隔离部署与非目标
- [x] D2 保留已实现的 ReportPolicy/请求契约、固定 GapAnnotationBuilder、显式渲染和 baseline golden
- [x] D3 冻结八个独立回归视图与显式 oracle/hash；实现实际输出检查器和9样本回放，补变异拒绝测试
- [x] D4 实现安全 replay 契约/引用校验/重现，纳入 artifact manifest，快照失败隔离
- [x] D5 实现轻量本地 JSON 候选/状态，apply/reset/show 命令，保留文件完整性与过期检查
- [x] D6 接入 Run 首次受理绑定、独立 service 与 paired 指纹，验证幂等/accepted/在途版本不变
- [x] D7 接入两 v2 API、Demo 默认关闭及本地边界、owner/session、引用/幂等/容量校验；不开放发布路由
- [x] D8 停止旧 skill_feedback 伪回放，v1/v2/invocations deprecated 并明确拒绝，不阻断主报告
- [x] D9 验收完整闭环与失败/篡改/源 Run 隔离、JSON/SSE/artifact/仓库一致
- [x] D10 同步 API、技术栈、产品、Skill说明及演示文档，提供可重复独立目录 Demo
- [x] D11 运行相关/全量离线测试、Ruff、mypy、OpenSpec 校验，记录实测与未完成项

## 前一批记录

此前完成纯契约/渲染层，162 项相关测试通过；没有 Run 绑定、真实回放、注册表或反馈路由。此记录不替代本次集成验收。

## 本次集成核验（2026-09-06）

D3–D11 已逐项核对并补齐。全量离线测试 613 passed / 4 skipped，覆盖率 87.11%；其中回环 SSE 在开放本机回环权限后单独补跑 1 passed，剩余 3 项为未开启的凭据型 live 测试。Ruff、format、mypy（224 个源文件）和本变更 OpenSpec strict 均通过。

独立目录 Demo 已实跑并恢复 baseline；产物位于 `artifacts/reporting-feedback-verification-20260906/`。完整发现、修改、命令、版本证据与未实现边界见 [验收记录](../../../docs/reporting-feedback-verification.md)。原 Skill 冻结副本与 benchmark 记录保留；本变更未归档。
