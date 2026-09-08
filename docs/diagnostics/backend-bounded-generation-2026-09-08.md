# 后端有界生成修复与验证

## 结论

已修复上一轮真实 Run `c99a8745ffc941049f9fc0cbdbef05f1` 暴露的重复上下文、预算后置检查和失败用量漏记问题，并通过 `bin/start.sh` 更新本地后端。没有提高配置预算、启用 Mock 降级、改写历史报告或修改 ECS。

本轮没有创建新的收费尽调 Run。最新代码的真实模型生成仍需另一次明确授权验证；离线通过与既有报告读取通过不能等同于真实生成稳定通过。

## 主要改动

1. **仅压缩模型视图，保留事实。** 单核查器使用 `snapshot-context-v2`：重复事实进入 fact_sets，记录列表使用列名/行表，证据元数据共用。缺失单元格与显式 null、0、false 可区分；源数据中的 `$ref` 等普通键不会被误解为压缩标记。原始快照、审计和公共报告契约不变。
2. **精确引用。** `record_evidence_ids` 仅连接与记录精确匹配且已授权的证据；模型仍须遵守每个 check 的 scope。短 ID 只替换生成的引用字段，不改写原始事实/元数据文本。批量工具 Schema 前置引用限制与可紧凑表达的缺口规则，运行时仍原子校验全部核查，不伪造缺失决策。
3. **请求前预留预算。** 输入按序列化 UTF-8 字节及协议余量做保守估算，原子预留输入/有界输出，包含并发在途和未知用量占用。未知请求结构拒绝派发；输出默认有限上限 10000 并按剩余额度夹紧。保留全局输入 300000、输出 100000、总计 400000 的配置。
4. **覆盖真实模型调用。** 核查、报告、AgentTeams 统一使用 BudgetedModel；采集与补充检索共享 BudgetedExecutionRuntime，即使 SDK 不发 usage 事件，也能记录实际请求次数。真实供应商的隐式 HTTP 重试关闭，避免一次预留里隐藏额外请求。
5. **跨阶段不重置额度。** 核查扣除已有采集成本，报告扣除采集加核查成本；未知/不完整 usage 不允许进入下一阶段重新获得额度。调用及每次流式等待受共享剩余 deadline 限制，超时/取消先结算已观察用量，未报告部分保留未知状态。
6. **失败成本真实落盘。** 普通供应商异常和框架吞掉的预算异常仍携带安全 execution_cost；service 指标按采集＋核查＋报告合计，不重复累加事件。新增 `provider_usage_complete`，无法准确计量时明确 false，不把缺失数据当零费用；没有账本时事件 usage 只作为下界。

## 验证证据

- TDD：新增压缩、Schema、预算预检、并发占用、取消/超时、失败计量、跨阶段未知用量、原始字符串碰撞、采集无 usage 事件等反例，先观察失败再实现。
- 大数据量离线样本：420 条证据、4 个重复子模块，从 **804241 bytes 降为 109985 bytes，减少 86.32%**；测试侧逆变换逐字段等于压缩前视图。此为构造样本，不是上一轮真实上下文的测量结果。
- 实际 ReAct＋离线模型：一次读快照、完整批量提交、证据还原和结束流程通过；AgentTeams 的分配/审核/修正脚本路径通过。
- 独立审查复验：采集全无 usage 时实际调用/账本请求均为 3、unknown=3；部分缺 usage 时仍记录 3 次、unknown=2、保留已观察 16 tokens。均不冻结快照、不进入核查，HTTP 显式禁止。
- 最终完整离线测试：**1096 passed / 1 failed / 4 skipped**。唯一失败为原有 `test_frozen_release_manifest_matches_versioned_components`：pyproject.toml 与 HEAD 相同，旧冻结清单不同；未改写清单。三个收费 live 测试关闭，一个回环 SSE 测试在获准后单独复跑通过。
- 本轮相关 Ruff/格式检查、17 个主要源码及关联测试文件的 mypy 通过。仅有既有第三方弃用警告。

一条新增负向报告测试曾继承本地模型配置，连接在 DNS 阶段失败。随后显式清空模型 key/base URL，并为该测试文件加入禁止构造外部 Model 的自动保护；没有创建新尽调 Run，修正后的测试通过。后续独立复验显式禁止 HTTP。

## 本地运行验证

更新前确认没有活跃 Run。实际运行包位于 `/usr/local/lib/python3.11/site-packages/jindiao`，13 个修复模块与镜像中的构建源码逐字节一致，包括新 accounted_runtime 和 compact_context，不仅检查 `/app/src`。

关键安装哈希：

- compact_context.py：`a15e0c2db81726c8b87971d455441878c3528baa03c67d14ebf0af5ee4aa31be`
- snapshot_access.py：`2ec3d08189765ee9b85a0a783c62e451dd96ba7237094bdfe7a784569041344d`
- blackboard.py：`4a5a7e358bfc9a69d4e84161832dbfa622e51a41294c7a1cab03d3843d7be81e`
- base.py：`0ed67dc547dce830f4ec8808d25c807a5154a4901be5d909778f2cc5a6a3353b`
- budgeted_model.py：`fcbd3b9d416f680235f2d322ae15c053ce261b5864f8aadaaed7d9eb13171eb9`
- accounted_runtime.py：`555faf5b8016f0a56483f54072d3bda2cc06b6ac19cf0c32ee4a16a59409dbb6`
- formal_pipeline.py：`c69578300366b29eacfe1799772d950808c9db13f144f3492a5114b79066a19a`
- service.py：`414f3df299932660652133e5357169e14bae5759d4fc6974c0349d3b98818d51`

实际配置仍为 formal+tianyancha、qwen-plus、300 秒、300000/100000/400000 tokens、allow_degraded_mock=false。

已有真实成功 Run `dabbc8e06a4b4b9cb408058cf5b7ca00` 在更新后，8080/18088 交替 24 次并发结果读取全部 200 且一致；76 条 SSE、报告完成事件与 GET 结果一致，Last-Event-ID 精确重放。规范化结果哈希仍为 `ac75eaac935d40f9d1ec9863414bad984274a8f1b828a2f0b2d58f7677ff7f9e`。

旧失败 Run `c99a8745ffc941049f9fc0cbdbef05f1` 仍是 failed / HTTP 500 / 53 条事件，保留原 token-budget 错误，没有伪造新报告或改写旧失败指标。

## 下一步

用上述新版本执行一次明确授权的真实生成，再检查新报告是否无 generation_failed、工商/财务数据及缺口是否准确、预算计量是否完整。当前只能声明代码与离线、本地读取验证完成，不能承诺外部模型和数据源的所有请求都成功。

后续授权实测已执行一次：`1017bce4937c4b6a9476d3885ed587c1`。采集 517 条证据后，下一次核查请求在预算预检处被拒，未生成新报告；7 次已完成模型调用合计 55090 tokens，计量完整一致。正常业务生成验收仍未通过。参见 `local-real-bounded-generation-9-2026-09-08.md`；具体输入估算与冻结输入尚未留存，下一步需先补可回放诊断，不再直接追加收费重跑。
