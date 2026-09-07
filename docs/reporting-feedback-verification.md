# 用户反馈报告闭环验收记录

- 变更：`add-user-feedback-reporting-loop`（spec-driven，本地比赛 Demo）
- 日期：2026-09-06
- 环境：macOS arm64，CPython 3.11.4，现有 `.venv`，离线虚构数据
- 结论：本次 Demo 范围 D1–D11 已实现并验证；未归档。生产治理仍为 Deferred。

## 实测门禁

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 全量测试与覆盖率 | `.venv/bin/python -m pytest` | **613 passed，4 skipped，87.11%**，高于 80% 门槛，退出 0 |
| 补跑真实回环 SSE | `.venv/bin/python -m pytest --no-cov tests/integration/test_bff_streaming.py` | 开放本机回环权限后 **1 passed**，退出 0；补齐上行四项 skip 中的回环测试 |
| Ruff | `.venv/bin/ruff check .` | 全仓通过 |
| 格式 | `.venv/bin/ruff format --check .` | 347 个文件符合格式 |
| 类型 | `.venv/bin/mypy src tests scripts/run_reporting_feedback_demo.py` | 224 个源文件无错误 |
| OpenSpec | `OPENSPEC_TELEMETRY=0 openspec validate add-user-feedback-reporting-loop --strict` | valid，退出 0 |
| 独立目录 Demo | `.venv/bin/python scripts/run_reporting_feedback_demo.py --output artifacts/reporting-feedback-verification-20260906` | verified=true，九例实测，apply/new Run/reset 全链路通过 |

全量测试的另三项 skip 是需要凭据及显式开关的 live single、multi、paired 测试，本轮未执行。回环测试在额外权限下实际跑通；本轮共验证 614 个不同离线测试。告警为依赖弃用提示及读取已 deprecated 的旧 skill_feedback 字段，没有放宽覆盖率或跳过新增回归。

## 本轮发现与修复

| 问题 | 处理与证据 |
| --- | --- |
| 零分母未披露适用性，重点 Finding 引用漏检 | 新增 applicable；校验 decision.major_risk_finding_ids 与 risk_summary.top_finding_ids；快照读取也验证报告逐字重现 |
| 输入/输出超限被降为普通 500，运行异常无候选收据 | 独立 limit/deadline 异常与稳定原因码；413/500 保存可查询 failed 候选，源 Run 与 active 不变 |
| 无法映射缺口缺少明确原因 | 返回 rejected/unmapped_gap，正常无缺口仍为 no_applicable_gap |
| 损坏 JSON envelope 抛非预期错误，候选 binding 未与实际回放策略核对 | 明确 ValueError；apply 校验策略、确定性版本、revision、评测哈希/全文及实现指纹，重跑通过才应用 |
| 紧凑快照小于上限但实际落盘超过上限 | 写后校验实际字节数和可重载内容，失败移除快照并标记不可回放，主产物 manifest 仍有效 |
| RunRepository 写失败仍能返回缓存结果 | 先持久化再更新缓存；结果查询要求可读终态 |
| 最终 Run 状态晚于完成事件保存 | 预构造最终投影，先保存再发布 reporting phase/report.completed/终态事件；失败不占用成功序列，不宣布成功 |
| 旧 Python coordinator 残留格式计数伪回放 | 删除计数评测实现，明确 rejected/use_feedback_api；v1/v2/invocations 契约 deprecated，拒绝反馈但完成主报告 |
| 文档仍描述 SQLite、双角色与四个接口 | API、技术栈、产品、README、演示清单及 Skill 说明更新为两 API + 本地 CLI；保留原冻结 Skill 副本和哈希 |
| 静态检查失败与 Demo 依赖工作目录 | 修复测试类型、异步子进程、未使用导入及格式；默认 app 的规则路径基于 project_root，独立脚本从新目录初始化 |

新增变异和失败测试覆盖：改分/改证据/隐藏 Mock、伪造标记/章节/文本、重复块、仅源样本改善、重点引用无效、哈希匹配但报告不可重现、候选 binding/评测/指纹篡改、快照缺失/篡改/写失败、主产物与仓库保存失败、在途 service 与 accepted 幂等冻结、本地边界/归属/引用/锁/超限/截止时间、三个旧入口、完整 CLI 闭环及跨工作目录执行。原 baseline golden 与固定 suite 的 oracle 没有为迁就实现而改写。

## 实际 Demo 证据

产物目录：`artifacts/reporting-feedback-verification-20260906/`（本地生成，不应作为真实企业数据提交）。

| 阶段 | 实际策略版本 |
| --- | --- |
| 源报告 | 1.1.0 |
| 已反馈、尚未应用的新 Run | 1.1.0 |
| apply 后新 Run | 1.1.72435741775793330110106693357 |
| reset 后新 Run | 1.1.0（revision=2） |

源 Run：`dee0edacbe7d4b2cab01e3f75d66da6b`。候选：`evo-04c96aacba3b6a3962c950da00c9231c918881c9ea0d70f5f219da0f2263eeec`。

`summary.json` 记录 verified=true、case_count=9、source_unchanged=true。`evaluation.json` 保存源案例与八个独立案例的真实全文、diff、分子/分母与保护检查；`apply-receipt.json` 和 `reset-receipt.json` 保存实际命令结果。四阶段 Run/Result、每 Run 的 manifest 与安全 replay 可互相核对。下一次新运行的随机 ID、候选版本、时间和耗时会不同。

## 对应任务与边界

D3 对应固定 suite、输出检查器及变异测试；D4 对应快照与最终产物；D5 对应 JSON store/CLI；D6 对应 RunContext/RunCoordinator/service/paired 绑定；D7 对应两个显式启用的本地 API；D8 对应旧入口拒绝；D9 对应完整闭环及失败隔离；D10 对应文档与独立脚本；D11 对应上方实际命令。

只证明已有披露的位置改善，不证明风险召回、模型能力或协作增益。local 单进程文件写入有序，但没有跨存储事务、事件 outbox 或进程崩溃后的发布恢复保证；共享部署、生产认证、完整审计、多写者发布和硬实时中断均未实现。运行中的文件/实现或 suite 变化会使旧候选失效；重新创建源 Run 和候选后再评测，不回填旧快照。

原 Frozen v1 报告 Skill 包已逐字保存在 `release/reference/skills/team/feedback-evolved-reporting/`。旧 manifest 仅改为指向这份原副本，原 tree hash 和 benchmark 记录均未改变。当前包保留 ABI 1.0.0，报告策略的运行版本另从 1.1.0 起算。完整操作见 [反馈 Demo](reporting-feedback-demo.md) 和 [API](api/README.md)。
