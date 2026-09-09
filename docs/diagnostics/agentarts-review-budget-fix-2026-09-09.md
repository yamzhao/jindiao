# AgentArts 审核返工预算修复 — 2026-09-09

> 后续用户明确允许对齐本地 Demo、跳过 Reviewer 未完成闭环，Latest 已切为 `demo-multi-0909`，并返回真实 partial 报告。见[Demo 策略验收](agentarts-demo-multi-2026-09-09.md)。下文保留本次严格策略的修复及失败证据，不代表最新策略状态。

**结论：返工预算误扣缺陷已修复并发布；本次授权的真实 multi 复验仍失败，原因转为结构化输出校验重试耗尽，Result 500。不能宣称端到端业务已通过。**

## 范围与根因

前版 `release-backend-0909` 的真实 Run `3a0a2f0d213f4cb492c42e439fdeee47` 在约 320 秒因 repair-round budget exhausted 失败，Result 500。离线复现确认：带返工的审核在 scope/version 校验之前扣返工额度，即使审核被拒绝，额度仍消耗；两个无效审核就能耗尽 2 轮额度。并发同一审核也可能双扣。

用户明确授权修复，并对同一企业再进行一次真实 multi。此次仅修改审核计费及提交顺序，不提高预算、不跳过独立 Reviewer、不启用不完整调查兜底、不发送贷款参数。

## 修复

- `SubmissionBlackboard.submit_review()` 先验证 schema、授权范围、版本及幂等，构造回执后，在同一黑板锁内对有效新返工审核扣一次额度并提交。
- 范围/版本错误不扣返工额度；仍计入 schema retry 和工具调用限额。
- 幂等重放不重复扣费；预算拒绝时不写入审核；最终无返工审核不消耗额度。
- 计费开关是内部调用参数，不暴露给模型工具输入。原 `MultiInvestigatorTeam` 仍由自身返工循环计费，默认不在黑板重复扣费。
- `InvestigationTeamState` 的另一个审核工具复用同一实现，删除原有重复的先扣费逻辑。
- 证据、目录、审核约束、次数上限保持不变。

## 验证

- 按 TDD 先新增 13 个用例：修复前 9 failed / 4 passed，失败覆盖 check/evidence/target 越界、非法版本以及并发重复扣费。
- 修复后相关 78 项回归通过。原离线复现连续拒绝 3 次审核，accepted_reviews=0、repair_rounds=0、未耗尽返工额度。
- 两个源文件及新增测试 Ruff、mypy 通过，git diff --check 通过。
- 独立只读代码审查未发现 Critical/Important/Minor 问题，核对了账本锁顺序、并发去重、预算拒绝不提交和旧执行器兼容。
- 显式 test 环境、严格预算下全仓：1207 passed、3 failed、3 skipped（42.05 秒）。失败与修复前相同：旧冻结清单哈希不符、两个旧 MultiInvestigatorTeam 测试低预算触发预派发保护；不把它们掩盖或称为全仓通过。
- 新 ARM64 镜像内断网：90 项审核/预算/AgentTeams/正式管线测试通过。
- 新镜像实际 HTTP Mock 验证：健康检查、非 root、pip check、single/multi 创建 202、Result 200、prototype-v1、连续 SSE、归属 404、旧契约 422、invocations SSE 均通过；不是本节后续真实业务验收。

## 镜像与云端版本

- 镜像：`swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:release-20260909-review-budget-arm64`。
- image/config ID：`sha256:5a21ab727947cab47ca646d43728ae98f58be94ed1edff518059402120d2ffc8`。
- registry manifest digest：`sha256:74093a15d5293efd30816490604f240debacb8370ffc534af9684a1b8844302f`。
- 复用依赖前逐项核验当前锁文件适用于 Linux ARM64 的 229 个包及 Git 提交全部匹配；完整白名单源码快照重新构建，不混入密钥/日志/artifacts。源码与回执再次核对一致，上传前远端标签不存在。
- 回执：`artifacts/deployments/agentarts-20260909-review-budget/receipt.json`。
- 2026-09-09 18:10:33 GMT+08:00 控制台确认 Latest → `release-reviewfix-0909`，使用本轮新镜像。
- 保存前在内存比对，26 项环境变量完全未变：Qwen/天眼查凭据、formal、attached/memory、严格预算、600 秒、350 万总 token、2 轮返工、两种 demo_partial=false 均保持。
- 旧镜像与 `release-backend-0909` / `live-recovery-0906` 版本保留；无权限/认证/网络暴露变更。

## 真实复验

已按用户授权执行一次真实 multi：`ebfcfd8d0a7449bd8ab7311c866b6e1c`。健康 200、创建 202（约 1.99 秒）、首事件约 3.06 秒、snapshot.frozen 序号 33（约 21.81 秒）。

- 218.38 秒收到 `run.failed`，探针 218.91 秒退出 1 / verified=false。
- 错误为 `agent_execution_failed / orchestration schema-retry budget exhausted`，Result GET 500 / 1521 字节，无报告。
- ErrorRecord.details 中调查账本：55 次模型请求、55 次成功且有供应商 usage；输入 1057729、输出 20076、合计 1077805 token；工具 46、snapshot reads 4、schema retries 12、**repair rounds 0**、unknown usage 0、reserved tokens 0、峰值并发 5。
- 调查账本 wall_time_ms=196151、deadline_remaining_ms=383849；因此不是 600 秒时限、350 万总 token 上限或用量不完整所致。
- 真实无效提交不再扣返工额度，与离线回归结论一致。但未获得每次被拒绝的具体结构化内容，不能把 12 次计数全部归因于 Reviewer 的某一个字段；结构化校验失败需独立定位。
- SSE 94 个事件、1～94 连续，最大 data 行 1895 字节；19 条 check.started、20 条 submission.accepted、5 条 execution.step.failed，没有 report.completed。条数不等于唯一核查数。
- 相同 Idempotency-Key 返回同一 Run；Last-Event-ID=1 重放后续 93 个事件逐对象相等；另一 owner 的 Result 查询 404。
- 运行中同一 Session/owner 的 GET 状态也成功返回 200 / running / investigation，没有创建额外 Run。
- 公共状态投影仍只有 checks 长度 1、review round 0/pending 和空 budget.used/remaining，不能用它代替 ErrorRecord.details 的实际账本。投影完整性不在本次返工计费修复内。

仅本轮授权的一次真实复验，没有提高预算、启用部分调查兜底或自动重跑。云端保持修复版 `release-reviewfix-0909`，旧版本保留。后续应先取得安全的校验字段/错误码诊断并修复具体契约不一致，再另行授权真实复验；不建议扩大 schema retry 上限来掩盖问题。

本地仅保留 Run 路由信息与计数验收回执（`artifacts/deployments/gateway-probe-ebfcfd8d0a7449bd8ab7311c866b6e1c/receipt.json`），不保存模型/天眼查/API Key 或报告全文。路由信息不是认证凭据，任何后续读取仍需正确网关认证与归属。
