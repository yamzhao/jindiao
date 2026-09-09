# AgentArts multi 对齐本地 Demo 策略 — 2026-09-09

**结论：按用户明确授权的 Demo 策略，本次真实 multi 的 Run/Events/Result、幂等、重放和归属隔离通过；约 278 秒返回 partial 报告。仅提交 18/19 项核查，完整审核未闭环，不能称为完整尽调或 Reviewer 审核通过。**

## 用户授权与边界

用户要求继续调试，并明确允许 Reviewer 与本地 Demo 策略一致、可以跳过。该授权改变的是业务审核策略，不是身份认证、证据校验、模型预算或源数据可信度。

本地 `deploy/local/compose.live.yaml` 显式开启 `JINDIAO_MULTI_DEMO_PARTIAL_ENABLED=true`。已发布镜像已有相同能力，因此本轮不修改业务代码、不重建或重传镜像，只更新现有运行时的一个环境变量。

策略含义：全部专业核查提交且尚无审核时可提前结束调查、跳过 Reviewer；也有最多 180 秒且不超过调查分配时间一半的 Demo 截止机制。提前结束只使用真实已接受的核查，缺项与审核未完成必须披露，终态为 partial，不伪造审核通过。若用量不完整，禁止追加报告模型，只进行确定性报告组装；用量完整的 multi 仍沿用原报告流程。

## 配置与验证

- 版本：`demo-multi-0909`；2026-09-09 18:25:28 GMT+08:00 控制台确认 Latest 指向该版本。
- 镜像不变：`swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:release-20260909-review-budget-arm64`。
- image/config ID：`sha256:5a21ab727947cab47ca646d43728ae98f58be94ed1edff518059402120d2ffc8`。
- registry digest：`sha256:74093a15d5293efd30816490604f240debacb8370ffc534af9684a1b8844302f`，沿用上一轮已核验镜像。
- 保存前在内存逐项比对 26 项变量，仅 `JINDIAO_MULTI_DEMO_PARTIAL_ENABLED` false → true；其余 25 项及键集合完全未变。
- `JINDIAO_SINGLE_DEMO_PARTIAL_ENABLED=false`、严格 token 预算、600 秒、350 万总 token、2 轮返工、Qwen/天眼查路由及凭据、attached/memory 均不变。
- 未改 API Key 权限、委托、前缀匹配、存储、日志或网络。旧严格版本 `release-reviewfix-0909` 保留可回切。
- 已发布 ARM64 镜像内断网跑 Demo 专项回归：7 passed / 23 deselected（4.58 秒），覆盖真实提交保留、不伪造审核、超时部分结果、用量不完整时不新增报告模型及严格路径拒绝。

## 真实联调

使用同一企业公开名称“同盾科技（上海）有限公司”，不发送贷款参数、不传 Mock 场景。通过现有网关认证、正确归属/Session 和严格 TLS 进行真实 multi。

- Run：`dfd068894d2e4d07bf88fb113cd17fb5`。
- `/ping` 200，Run 创建 202（约 0.80 秒）。
- 验收除 Run/Result/SSE/重放/归属外，额外要求报告 Markdown 和 summary 均明确包含“审核未完成”，且为 partial；不能把跳过 Reviewer 当成审核成功。
- 276.57 秒收到 `report.completed → run.partial`；约 278.29 秒全部验收结束，探针退出 0 / verified=true。
- 状态 GET 200 / partial / result_available=true / 无执行错误；Result GET 200，219589 字节，`prototype-v1`、`mode=multi`、`is_mock=false`。
- 报告 50169 字符、8 个模块、165 条 Evidence：天眼查 162、公开网页 1、derived 2，Mock 为 0。
- 摘要和 Markdown 都明确包含“审核未完成”“需人工复核”“调查提前收尾”，披露断言通过。partial 不是仅因数据覆盖不足，还包含未完成的调查/审核。
- SSE 118 条、sequence 1～118 连续，最大 data 行 219983 字节；包含 8 个 section.completed 和 7 个 execution.step.completed，末尾报告与 Run 终态一致。这是直接网关验收，不是本轮重新验证了浏览器/BFF 链路。
- Last-Event-ID=1 重放后续 117 个事件逐对象一致；相同幂等键返回同一 Run；另一 owner 读取结果返回 404。

### 真实性与剩余工作

只读复查同一 Run 的事件，21 条 submission.accepted 对应 **18 个不同核查项**，固定目录总数 19，缺少 `equity-encumbrance`。不要把提交事件数当作独立核查数。

曾产生一次 review.submitted（review_version=1、issue_count=3、repair_count=0）；本次不是 Reviewer 从未运行，而是在缺项/审核未完整闭环时按 Demo 策略允许提前出报告。报告已明确披露，未伪造缺项结果。

8 个模块不等于 8 个完整分析：本次仅 business_plan.analysis 非空（198 字符）；business_analysis 状态 unavailable，其余带 status 的模块为 partial，存在各自 missing_fields；risk_points 不定义同样的 status/analysis 字段。报告仍是部分可用的结构化事实与风险输出，不是完整业务建议。

**已知状态投影问题未在本轮修复**：GET 状态中的 `review.status=completed` / round=0，与报告“审核未完成”不一致；checks 长度仍为 1，budget.used/remaining 仍为空。前端不得凭这些字段认定 Reviewer 通过或用量为零；应保留 Run 的 partial 提示，并显示报告中的缺项/审核披露。7 个步骤完成事件表示流程已结束，不表示所有业务核查都通过。

未修改核心业务代码、未增加预算、未重写历史失败 Run。本轮只有一次新的真实 multi，后续补查仅 GET 同一 Run 的结果/事件，不重复调用模型。
