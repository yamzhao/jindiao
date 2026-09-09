# Jindiao 尽调 API

本文档对应 `jindiao/src/jindiao/api/app.py` 中实际注册的 HTTP 接口。接口返回 JSON 时使用 `application/json`；需要实时进度时使用 `text/event-stream`（SSE）。所有时间为 ISO 8601 UTC 字符串，日期为 `YYYY-MM-DD`。

> 当前版本没有注册 `/api/agent/components/export`。第 8 节记录已注册、默认关闭的两个本地反馈 Demo 接口；应用和恢复仅通过本地 CLI 执行。

> **连接真实 ECS 时**：创建请求请填写真实企业名称，并省略 `scenario_id`（也不要传空字符串）。文中带 `scenario_id` 的示例仅用于 Mock/Demo 环境。当前 ECS 的 `formal + tianyancha` 模式不接受场景覆盖；旧版本将此参数冲突显示为 `run.failed / internal_error`。修正参数后须用新的幂等键创建新 Run；重复订阅已失败 Run 的 `/events` 只会重放失败事件。健康检查接口是 `/ping`，不是 `/events`。

## 1. 接口总览

| 方法 | 路径 | 用途 | 成功状态 |
| --- | --- | --- | --- |
| POST | `/api/v2/due-diligence/runs` | 创建异步 Run | `202 Accepted` |
| GET | `/api/v2/due-diligence/runs/{run_id}` | 查询 Run 状态与进度 | `200 OK` |
| GET | `/api/v2/due-diligence/runs/{run_id}/events` | 订阅/重放 Run 事件（SSE） | `200 OK` |
| GET | `/api/v2/due-diligence/runs/{run_id}/result` | 获取 Run 结果 | `200` 或 `202` |
| POST | `/api/v2/due-diligence/runs/{run_id}/cancel` | 请求取消 Run | `200 OK` |
| POST | `/api/v1/due-diligence/result` | v1 兼容接口，同步返回结果或 SSE | `200 OK` |
| POST | `/invocations` | AgentArts 适配入口，等价于创建 v2 Run | `202 Accepted` |
| GET | `/ping` | 健康检查 | `200 OK` |
| POST | `/api/v2/due-diligence/runs/{run_id}/feedback` | 提交反馈并同步评测（需显式启用） | `201` / 重复 `200` |
| GET | `/api/v2/skill-evolutions/{evolution_id}` | 本地 Demo 查询真实对比 | `200 OK` |

推荐新客户端使用 v2 Run 生命周期；v1 仅用于兼容已有客户端。

浏览器接入请使用独立的 [BFF 安全代理](../deployment/bff.md)：提供登录会话、CSRF、服务端 API Key 注入以及上述五个 v2 Run 接口的受限代理。它不是本文件所述尽调 app 的内置登录模块，需要单独启动。2026-09-06 真实 Qwen + 天眼查 + AgentArts multi 已通过本地 BFF 完成 15 项核查、六角色审核及报告 GET/SSE/断点重放，使用 `live-recovery-0906`、有界 multi 预算和 1 MiB SSE 上限，详见[真实 multi 验收记录](../agentarts-real-multi-2026-09-06.md)。结果为无 Mock 的 `partial`（数据覆盖不足），不代表完整尽调结论或生产稳定性验收；前端尚未接入。

## 2. 通用请求模型

### 本地模型与 token 预算开关

模型由服务端 `MODEL_NAME` 决定，不由创建请求体指定。本地联调现在使用 `deepseek-v4-flash-0731`，沿用原 `MODEL_PROVIDER`、`MODEL_BASE_URL` 和密钥，未修改 ECS。

默认 `JINDIAO_ENFORCE_TOKEN_BUDGET=true`：输入最多 300000、输出最多 100000、合计最多 400000 tokens；下一次请求按 UTF-8 字节及协议余量保守估算，计入已用、并发预留和未知占用后判断能否派发。实际用量超过数值限额也会停止。估算值不等于供应商实际 token 数。

按当前本地联调要求设置 `JINDIAO_ENFORCE_TOKEN_BUDGET=false` 后，只观察和记录 token 用量，不再以这些数值限额拒绝请求或中断结果生成，费用可能超过旧的 400000-token 范围。单次有界输出（默认最多 10000）、300 秒超时、模型/工具调用次数、证据校验与计量完整性检查仍有效，供应商自己的上下文限制也仍有效。

开关属于服务端配置，不能放在 HTTP 请求 JSON 中。修改 `.env` 后执行 `bin/start.sh` 重新加载；`bin/restart.sh` 仅重启现有容器，不重新构建或加载新配置。

### 2.1 v2 Run / invocations 扁平请求体

`POST /api/v2/due-diligence/runs` 和 `/invocations` 使用 `RunCreateRequest`，字段全部放在一级，不接受 `enterprise`、`business_context`、`region` 或 `company_name` 等旧入参。前端按原型表单字段直接提交，金额以**万元**计。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `customerName` | string | 是 | — | 客户名称，去除首尾空格后必须非空；仅按此名称搜索并确认公司主体 |
| `uscc` | string/null | 否 | `null` | 兼容表单提交，去除首尾空格并转大写；不参与主体搜索、匹配或幂等哈希 |
| `product` | string/null | 否 | `null` | 业务品种，如“流动资金贷款”，不是“新增授信”等发生类型 |
| `amount` | number/string/null | 否 | `null` | 拟申请金额，人民币万元；有限正数，兼容十进制数字字符串（如 `"2"`、`"1.0001"`、`"2e3"`）；不接受布尔值、NaN 或 Infinity |
| `term` | integer/string/null | 否 | `null` | 期限，月；正整数，兼容整数字符串（如 `"12"`），不接受布尔值、小数或指数形式字符串 |
| `manager` | string/null | 否 | `null` | 主办客户经理，用于业务展示，不决定 Run 权限 |
| `branch` | string/null | 否 | `null` | 所属支行，本期作为报告上报机构 |
| `mode` | `single\|multi` | 否 | `multi` | 选择单/多 Agent 执行方式 |
| `report_as_of` | string (`YYYY-MM-DD`)/null | 否 | 运行时日期 | 尽调报告基准日期 |
| `execution_profile` | `attached\|detached` | 否 | `attached` | detached 只有部署探针通过后可用 |
| `session_id` | string/null | 否 | `null` | 运行所属会话，也可由请求头提供；浏览器 BFF 自行管理路由会话 |
| `scenario_id` | string/null | 否 | `null` | 仅测试/演示时指定 Mock 场景；真实天眼查请求省略 |

`customerName` 缺失、为 `null` 或去除首尾空格后为空时返回 `422`，即使提供了 `uscc` 也不启动尽调。同时提交两者时，仅按 `customerName` 确认公司主体，提交的 `uscc` 与查询结果不一致不会阻止匹配；报告中的统一社会信用代码使用已确认主体的资料，不使用表单值覆盖。名称无精确匹配或无法消歧时仍返回主体解析错误。

表单中的可选空白业务文本归一为 `null`；金额和期限推荐使用 JSON 数值，兼容普通文本输入框产生的数字字符串，转换前去除首尾空格。金额可带小数或十进制指数；期限只能是整数字面量（`"12.0"`、`"1.5"`、`"1e2"` 均拒绝）。两者都不接受千分位逗号、单位后缀、空字符串、纯空白、布尔值或非正数；未填写时请省略或传 `null`。内部及转发值归一为数值，非法输入仍返回 `422`，不会启动尽调。

例如 `{"customerName":"华为技术有限公司","amount":"2","term":"12","mode":"single"}` 与金额 `2`、期限 `12` 的数值请求等价；金额仍为 **2 万元**，前端无需先乘以 10000。

业务文本去除首尾空格；语言固定为 `zh-CN`，不再接受 `language`、`allow_degraded_mock`、`skill_feedback`。未知字段返回 `422`，不同时兼容新旧同义入参。单家客户对应一次 Run，不新增批量请求结构。

内部统一将 `amount × 10000` 转为人民币元，BFF 原样转发万元值，不执行换算。报告输出仍采用原字段名：`product → report.business_plan.business_product`、`manager → customer_manager`、`branch → reporting_org`、`amount → application_amount`（元）、`term → application_term_months`（月）。申报字段保留调用方输入并标记 `user_input` 来源，不由 LLM 覆盖；企业身份仍由外部资料核验。

转换后的执行字段参与幂等哈希，兼容字段 `uscc` 除外：可选字段省略与显式 `null` 等价，金额 5000、5000.0 与 `"5000.0"` 等价，期限 12 与 `"12"` 等价；相同 `Idempotency-Key` 仅修改 `uscc` 会复用原 Run，修改客户名称、金额、品种、客户经理、支行等有效内容返回 `409`。创建响应、查询、SSE、取消和最终结果路径不变。

### 2.2 v1 兼容请求体

只有 `/api/v1/due-diligence/result` 继续使用以下 `DueDiligenceRequest` 嵌套结构。内部执行模型也保留该结构，v2 在入口完成转换。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `enterprise` | object | 是 | — | 企业标识，至少填写 `company_name` 或 `unified_social_credit_code` |
| `enterprise.company_name` | string/null | 否 | `null` | 企业名称；首尾空格会被去除 |
| `enterprise.unified_social_credit_code` | string/null | 否 | `null` | 统一社会信用代码；服务端转为大写 |
| `enterprise.region` | string/null | 否 | `null` | 地区，用于消歧 |
| `report_as_of` | string (`YYYY-MM-DD`)/null | 否 | 运行时日期 | Reviewer、规则和最终 Decision 的统一报告时点 |
| `language` | string | 否 | `zh-CN` | 报告语言 |
| `scenario_id` | string/null | 否 | `null` | 冻结 Mock 场景；生产天眼查模式应省略 |
| `allow_degraded_mock` | boolean | 否 | `false` | 仅影响显式 Mock/演示路径的降级行为 |
| `business_context` | object | 否 | `{}` | 调用方已有的申报方案及流水、授信、本行记录摘要；省略与空对象等价，并纳入幂等哈希 |
| `business_context.reporting_org` / `reporting_date` | string/date/null | 否 | `null` | 上报机构和日期 |
| `business_context.business_product` / `customer_manager` | string/null | 否 | `null` | 业务品种与主办客户经理 |
| `business_context.application_type` / `application_amount` / `application_term_months` / `fund_use` | string/number/integer/string | 否 | `null` | 申报类型、金额（人民币元）、期限（月）和用途 |
| `business_context.suggested_*` | number/string/integer/null | 否 | `null` | 已有人工建议金额、利率、授信期限和贷款期限，服务端不得覆盖 |
| `business_context.guarantee_methods` / `repayment_methods` | string[] | 否 | `[]` | 保证方式和还款方式枚举，详见 OpenAPI |
| `business_context.bank_flow` | object/null | 否 | `null` | 调用方流水摘要；不是逐笔流水直连凭据 |
| `business_context.credit_info` | object/null | 否 | `null` | 调用方授信摘要；金额单位为人民币元 |
| `business_context.internal_record` | object/null | 否 | `null` | 调用方本行关系摘要 |
| `skill_feedback` | object/null | 否 | `null` | 已 deprecated；有值时统一 `rejected/use_feedback_api`，不生成候选且不阻断主报告，见第 8 节 |
| `skill_feedback.source` | string | 是（对象存在时） | — | 反馈来源 |
| `skill_feedback.reference` | string | 是 | — | 可审计引用，如 `artifact://...` |
| `skill_feedback.text` | string | 是 | — | 反馈正文 |
| `skill_feedback.evidence_refs` | string[] | 是 | — | 至少一个证据/报告引用 |

### 2.3 通用请求头

| Header | 适用接口 | 说明 |
| --- | --- | --- |
| `Accept: application/json` | v1、v2、`/invocations` | JSON 响应；v1 会等待完整结果，v2 创建仍立即返回 `202` |
| `Accept: text/event-stream` | v1、v2 events、`/invocations` | SSE 流 |
| `Idempotency-Key` | v2 创建 | 与主体和规范化请求哈希共同去重；相同键不同请求返回 `409` |
| `X-Hw-Agentgateway-User-Id` | v2 查询/事件/结果/取消 | Run 所有者；缺省为 `anonymous`。其他主体看不到该 Run，统一返回 `404` |
| `X-Hw-Agentarts-Session-Id` | v2 全部 Run 操作 | 当 Run 绑定会话时必须匹配；也可使用请求体 `session_id` |
| `Last-Event-ID` | v2 events | 从指定 sequence 之后重放；也可使用查询参数 `after` |

身份头只携带 Run 归属上下文，不是独立认证凭据。当前应用直接读取这些头，部署时应由可信网关注入并阻止客户端伪造；不要将 owner/session 匹配描述成已完成用户身份认证。第 8 节反馈 Demo 只支持本地单操作人环境，保留 owner/session 检查，不增加认证或角色系统；BFF 未代理这两个 Demo 路由。

## 3. v2 Run API

路径参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `run_id` | string | 是 | 创建 Run 返回的 Run 标识 |

`GET .../events` 的查询参数：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `after` | integer | 否 | `0` | 只返回 sequence 大于该值的事件；不能为负数（负数会按 0 处理） |

### 3.1 创建 Run

```http
POST /api/v2/due-diligence/runs
Content-Type: application/json
Accept: application/json
Idempotency-Key: live-request-001
X-Hw-Agentgateway-User-Id: user-001
```

真实数据请求示例（省略 `scenario_id`）：

```json
{
  "customerName": "乐视网信息技术（北京）股份有限公司",
  "uscc": null,
  "product": "流动资金贷款",
  "amount": 5000,
  "term": 12,
  "manager": "王某某",
  "branch": "城东支行",
  "report_as_of": "2026-08-31",
  "mode": "multi",
  "execution_profile": "attached"
}
```

返回 `202`（`Location` 响应头指向 `links.self.href`）。返回体是 `RunResource`：

```json
{
  "request_id": "req-01JINDIAO",
  "run_id": "run-01JINDIAO",
  "owner_id": "user-001",
  "mode": "multi",
  "status": "accepted",
  "stage": "accepted",
  "profile": "attached",
  "session_id": null,
  "created_at": "2026-09-06T05:40:08.264834Z",
  "started_at": null,
  "completed_at": null,
  "latest_sequence": 0,
  "progress": {"completed": 0, "total": 0, "percentage": 0.0},
  "acquisition": {"completed": 0, "total": 0, "percentage": 0.0},
  "investigation": {"completed": 0, "total": 0, "percentage": 0.0},
  "reporting": {"completed": 0, "total": 0, "percentage": 0.0},
  "snapshot": null,
  "agents": [],
  "checks": [],
  "review": {"round": 0, "status": "pending", "issue_count": 0, "repair_count": 0},
  "budget": {"used": {}, "remaining": {}, "peak_concurrency": 0},
  "termination": null,
  "error": null,
  "result_available": false,
  "links": {
    "self": {"href": "/api/v2/due-diligence/runs/run-01JINDIAO", "method": "GET"},
    "events": {"href": "/api/v2/due-diligence/runs/run-01JINDIAO/events", "method": "GET"},
    "result": {"href": "/api/v2/due-diligence/runs/run-01JINDIAO/result", "method": "GET"},
    "cancel": {"href": "/api/v2/due-diligence/runs/run-01JINDIAO/cancel", "method": "POST"}
  },
  "metadata": {"request_hash": "sha256-of-normalized-request"}
}
```

`run_id` 只包含字母、数字、`.`、`_`、`:`、`-`，最长 128 个字符。重复提交相同 `Idempotency-Key` 和请求会返回同一 `RunResource`；同键但请求不同返回 `409`。

`RunResource` 字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request_id` / `run_id` | string | 请求与运行的稳定标识 |
| `owner_id` | string | 创建者主体（来自 `X-Hw-Agentgateway-User-Id`） |
| `mode` | `single\|multi` | investigation 拓扑 |
| `status` | `accepted\|running\|completed\|partial\|failed\|cancelled` | Run 生命周期状态 |
| `stage` | `accepted\|acquisition\|snapshot\|investigation\|adjudication\|reporting\|terminal` | 当前阶段 |
| `profile` | `attached\|detached` | 执行配置 |
| `created_at` / `started_at` / `completed_at` | datetime/null | 生命周期时间戳 |
| `latest_sequence` | integer | 已发布事件的最大 sequence |
| `progress`、`acquisition`、`investigation`、`reporting` | object | `{completed,total,percentage}` 进度 |
| `snapshot` | object/null | 快照摘要（`snapshot_id`、SHA-256、coverage、缺口/冲突、采集成本） |
| `agents` / `checks` | array | Agent、Check 的公开状态摘要 |
| `review` / `budget` | object | 复核轮次/问题/返工与预算用量 |
| `termination` | object/null | 终止原因、完成/未完成任务和详情 |
| `error` | object/null | `ErrorRecord` |
| `result_available` | boolean | 是否可以从 result 链接获取完整结果 |
| `links` | object | `self`、`events`、`result`、`cancel` 超媒体链接 |
| `metadata` | object | 当前包含规范化请求哈希，不含敏感内容 |

### 3.2 查询 Run 状态

```http
GET /api/v2/due-diligence/runs/{run_id}
Accept: application/json
X-Hw-Agentgateway-User-Id: user-001
```

返回体仍为 `RunResource`。运行完成后的关键字段示例：

```json
{
  "run_id": "run-01JINDIAO",
  "status": "completed",
  "stage": "terminal",
  "started_at": "2026-09-06T05:40:08.265456Z",
  "completed_at": "2026-09-06T05:40:08.279129Z",
  "latest_sequence": 21,
  "result_available": true,
  "termination": {"reason": "completed", "completed_task_ids": [], "incomplete_task_ids": [], "detail": null},
  "error": null,
  "links": {"result": {"href": "/api/v2/due-diligence/runs/run-01JINDIAO/result", "method": "GET"}}
}
```

状态值：`accepted`、`running`、`completed`、`partial`、`failed`、`cancelled`。`partial` 表示存在数据缺口但仍生成了结果，不等同于无风险。

### 3.3 订阅 Run 事件（SSE）

```http
GET /api/v2/due-diligence/runs/{run_id}/events?after=0
Accept: text/event-stream
X-Hw-Agentgateway-User-Id: user-001
```

也可以把 `after` 换成 `Last-Event-ID: 12`。服务端先重放持久化事件，再推送新事件；事件按 `sequence` 递增，终止于 `run.completed`、`run.partial`、`run.failed` 或 `run.cancelled`。

单条 SSE 的线格式：

```text
id: 1
event: run.accepted
retry: 3000
data: {"event_type":"run.accepted","schema_version":1,"event_id":"run-01JINDIAO:1","request_id":"req-01JINDIAO","run_id":"run-01JINDIAO","sequence":1,"occurred_at":"2026-09-06T05:40:08.264834Z","stage":"accepted","actor":{"kind":"system","id":"jindiao","role":"coordinator"},"task_id":null,"check_id":null,"payload":{"status":"accepted"}}
```

事件对象字段：`event_type`、`schema_version`、`event_id`、`request_id`、`run_id`、`sequence`、`occurred_at`、`stage`、`actor`、`task_id`、`check_id`、`payload`。常见事件包括 `run.started`、`run.phase.started/completed`、`entity.resolved`、`agent.started/completed`、`evidence.collected`、`section.completed`、`report.completed`、`run.partial`、`run.failed`、`run.cancelled`。

#### AI 尽调执行过程

前端使用同一个 v2 SSE 流中的 `execution.*` 渲染执行过程。调用顺序为：创建 Run → 订阅 `links.events.href` → 按 `step_id` 更新步骤卡片 → 收到 Run 终态后关闭流，成功或部分完成时读取 `links.result.href`。不需要新增接口，也不需要解析 Agent 输出文本。

| 事件 | payload | 前端处理 |
| --- | --- | --- |
| `execution.plan.created` | `{ "plan": ExecutionPlan }` | 初始化七个待执行步骤；每次 Run 开始执行时仅一条 |
| `execution.step.started` | `{ "step": ExecutionStepSnapshot }` | 整体替换该步骤，显示运行状态和进展 |
| `execution.step.progress` | 同上 | 整体替换该步骤；由真实里程碑触发，可能没有此事件 |
| `execution.step.completed` | 同上 | 显示业务结论和可展开的事实、证据、缺口 |
| `execution.step.failed` | 同上 | 显示该环节执行失败；不代表企业有风险 |

`ExecutionPlan` 包含 `plan_version="due-diligence-execution-v1"`、`mode="single|multi"` 和 `steps`。每个步骤定义包含 `step_id`、`order`、`title`、`objective`。固定顺序如下；顺序用于展示，实际运行可以并行。

| order | step_id | title |
| --- | --- | --- |
| 1 | `company-verification` | 企业主体与工商信息 |
| 2 | `ownership-and-relations` | 股权与关联关系 |
| 3 | `business-and-supply-chain` | 经营情况与上下游 |
| 4 | `finance-cashflow-solvency` | 财务、流水与偿债能力 |
| 5 | `external-risk-screening` | 司法、行政、税务与舆情 |
| 6 | `cross-risk-review` | 风险交叉审核 |
| 7 | `structured-report-generation` | 结构化尽调报告生成 |

`ExecutionStepSnapshot` 字段：

| 字段 | 含义与限制 |
| --- | --- |
| `step_id / order / title / objective` | 对应计划中的稳定定义 |
| `state` | `pending / running / completed / failed / cancelled` |
| `progress_message` | 运行中的简短进展，最多 160 字；完成后为空 |
| `progress_percent` | 当前运行时没有可靠分母，返回 `null`；完成时为 `100`。不要自行模拟百分比 |
| `outcome` | 仅 completed 有值：`normal` 无需单列关注、`attention` 需关注、`inconclusive` 待核实 |
| `conclusion` | 完成或失败后的简短结论，最多 240 字；运行中为空 |
| `key_facts` | 最多 3 条 `{text, evidence_ids}`，每条文本最多 240 字，引用最多 8 项 |
| `source_tags` | 最多 8 条 `{label, evidence_id, source_type}`；标签最多 80 字 |
| `gaps` | 最多 3 条可读资料缺口，每条最多 240 字；完整缺失说明见最终报告 |
| `executor_ids` | 实际观察到的 Agent ID，最多 8 个；系统执行或未观察到执行者时为空 |
| `duration_ms` | 从首次观察到该步骤运行到完成/失败的毫秒数；没有开始记录时为 `null` |

默认卡片展示 `title + state + (progress_message 或 conclusion)`，完成时配合 `outcome` 显示“无需单列关注 / 需关注 / 待核实”。展开后展示目标、关键事实、证据和缺口。`source_type` 为 `tianyancha / public_web / user_input / derived / mock`；Mock 来源应在界面明确标识。点击证据通过 ID 关联最终结果的 `evidence`；结果生成前不展示尚未校验的事实。

步骤完成表示已完成对现有资料的处理。模块不可用、字段缺失或核验未定时，结论为 `inconclusive`；若已有确定风险，优先返回 `attention`，同时保留缺口。它们都不等于运行失败。报告生成步骤的 `normal` 仅说明结构化报告已生成，不代表授信建议。`key_facts` 仅引用对应事实自身的证据，引用 ID 必须同时存在于本步骤 `source_tags` 和最终结果中。

以下为完成事件示例（示意数据）：

```text
id: 42
event: execution.step.completed
data: {"event_type":"execution.step.completed","schema_version":1,"event_id":"run-01JINDIAO:42","request_id":"req-01JINDIAO","run_id":"run-01JINDIAO","sequence":42,"occurred_at":"2026-09-07T05:50:00Z","stage":"reporting","actor":{"kind":"system","id":"jindiao","role":"coordinator"},"task_id":null,"check_id":null,"payload":{"step":{"step_id":"external-risk-screening","order":5,"title":"司法、行政、税务与舆情","objective":"扫描司法执行、行政处罚、税务、信用及公开舆情风险。","state":"completed","outcome":"attention","progress_message":null,"progress_percent":100,"conclusion":"发现一项执行事项，税务资料仍待核实。","key_facts":[{"text":"存在一项尚未解除的执行事项","evidence_ids":["ev-001"]}],"source_tags":[{"label":"天眼查·被执行明细","evidence_id":"ev-001","source_type":"tianyancha"}],"gaps":["未取得可核验税务资料"],"executor_ids":["judicial-compliance-agent"],"duration_ms":12500}}}
```

同一快照在 started/progress 时 `state="running"`，`outcome=null`、`conclusion=null`，通过 `progress_message` 展示“正在依据已采集资料执行核查”等信息。最终七个完成快照由校验后的 `ProductResult` 统一生成，并在 `report.completed` 和 Run 终态之前发布，避免在审核前把初步发现当成定论。某些路径没有可靠的开始信号，此时允许直接收到 completed，`duration_ms=null`。旧版结果只提供保守的完成摘要，不补造证据或执行者。

Run 状态接口的总体 `progress` 按这七个步骤的最新快照去重统计：收到计划后 `total=7`，只计 `state=completed` 的步骤，不把失败或取消计为成功。执行完成可为 `7/7`、100%，同时因资料缺口保持 `status=partial`；100% 不代表无风险或资料齐全。重启后从已有 SSE 恢复此派生计数，不修改历史报告、不重跑模型；无执行计划的老记录不补造七步进度。

前端 reducer 示例（每个 Run 独立保存状态）：

```javascript
let lastSequence = 0;
let steps = new Map();
function reduce(event) {
  if (event.sequence <= lastSequence) return; // 重放/重连去重
  lastSequence = event.sequence;
  if (event.event_type === "execution.plan.created") {
    for (const definition of event.payload.plan.steps) {
      if (!steps.has(definition.step_id)) {
        steps.set(definition.step_id, {
          ...definition, state: "pending", outcome: null, conclusion: null,
          progress_message: null, progress_percent: null, key_facts: [],
          source_tags: [], gaps: [], executor_ids: [], duration_ms: null
        });
      }
    }
  } else if (event.event_type.startsWith("execution.step.")) {
    steps.set(event.payload.step.step_id, event.payload.step); // 整体覆盖
  } else if (["run.cancelled", "run.failed"].includes(event.event_type)) {
    // 取消没有独立的 step.cancelled 事件；进程中断也可能只有 run.failed。
    for (const [id, step] of steps) {
      if (step.state === "running") {
        steps.set(id, {...step,
          state: event.event_type === "run.cancelled" ? "cancelled" : "failed",
          progress_message: null, outcome: null,
          conclusion: event.event_type === "run.cancelled" ? "本次运行已取消" : "本次运行已中断"
        });
      }
    }
  }
}
// 渲染：Array.from(steps.values()).sort((a, b) => a.order - b.order)
```

使用 `EventSource` 时为上述五种执行事件和四种 Run 终态分别注册 `addEventListener`，解析 `message.data` 后调用 reducer；终态必须 `close()`，防止完成后自动重连。业务报告仍从 result 接口读取。

刷新页面、步骤状态丢失时，从 `after=0` 重放；保留了步骤状态时才使用对应的 `Last-Event-ID`/`after` 续传。这里的游标是数字 `sequence`（如 `42`），不是字符串 `event_id`。GET Run 的状态快照不新增步骤数组。所有事件共享一个递增序号，队列拥塞时完成/失败快照优先入队，出现序号缺口会从存储补齐后继续发送；刷新重建与实时显示使用相同快照。

单 Agent 和多 Agent 使用同一计划，不把七个业务步骤伪装成七个 Agent。真实 AgentTeams 的读取、提交和审核里程碑来自授权快照读取及权威业务黑板；无法可靠映射的技术事件不用于步骤进展。不增加专门生成过程文案的 LLM 调用，不返回 `agent.output`、token delta、提示词或内部思维链。v1 兼容流保持原事件枚举，不包含 `execution.*`。BFF 原样转发这些事件，并继续应用鉴权、重放游标和 SSE 字节上限。

### 3.4 获取 Run 结果

```http
GET /api/v2/due-diligence/runs/{run_id}/result
Accept: application/json
X-Hw-Agentgateway-User-Id: user-001
```

- 结果已生成：`200`，新 Run 返回 `schema_version=prototype-v1` 的固定产品结果。
- 仍在运行：`202`，返回 `{"status":"running|accepted", "run_id":"...", "links": {...}}`。
- 运行失败：`500`，返回 `{"error": <ErrorRecord>}`。

完整结果顶层字段固定如下：

```text
schema_version, meta, subject, summary, report, risk_findings, evidence, report_markdown
```

这是 breaking 字段变更。新运行不再公开旧 `decision`、`coverage`、`sections`、`findings`、`agent_results`、成本、协作和评测字段；这些内容按版本保存在内部运行产物中。升级前没有 `schema_version` 的历史 Run 仍按原格式只读返回，不重新生成或补字段。

固定字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 固定为 `prototype-v1` |
| `meta` | object | request/run 标识、状态、模式、生成时间、报告日期、报告版本和事实 Mock 标记 |
| `subject` | object | `subject_id`、企业名称和统一社会信用代码 |
| `summary` | object | `risk_count`、`ai_suggestion`（`proceed\|manual_review\|stop`）及原因 |
| `report` | object | 业务申报方案和 §1–§7 的八个固定键 |
| `risk_findings` | array | 由已审核风险事实和核查结论归纳的唯一风险卡片列表 |
| `evidence` | array | 本结果实际引用的精简证据及派生来源闭包 |
| `report_markdown` | string | 面向用户的 Markdown 报告 |

`report` 恰好包含 `business_plan`、`company_profile`、`ownership`、`business_analysis`、`financial_analysis`、`bank_flow_analysis`、`external_verification`、`risk_points`。不返回 §8、§9 或“补调与资料”。

除 `risk_points` 外，每个模块直接返回固定业务字段，并统一包含 `status`、`analysis`、`evidence_ids`、`missing_fields`。`missing_fields` 元素为 `{field,reason,message}`。未知数值为 `null`，未知集合为 `[]`；它们不等于 0 或“已核验无记录”。已成功核验为空的集合保持 `[]` 且不为该字段生成缺口；能力不存在、来源失败、分页截断或期间不足分别使用 `capability_absent`、`source_error`、`pagination_truncated`、`missing_period`。金额统一为人民币元，期限为月，比例使用 0–100（例如 `38` 表示 38%）。

业务申报方案按以下规则展示，前端不需要增加输入字段：

| 内容 | 输入/结果映射与展示规则 |
| --- | --- |
| 企业名称、信用代码、行业 | 使用已确认主体的查询资料；表单 `uscc` 不覆盖查询结果 |
| 业务品种、申请金额、期限 | `product → business_product`、`amount × 10000 → application_amount`、`term → application_term_months` |
| 客户经理、所属支行 | `manager → customer_manager`、`branch → reporting_org`；页头和模块内的上报机构统一读取 `report.business_plan.reporting_org` |
| 其他事实 | 未提供的申请类型、资金用途、用途详细说明、上报日期、还款来源、统一授信情况、调查地点保持 `null`，前端显示 `-`；不从报告基准日推测上报日期 |
| 建议项 | 后端生成建议额度、利率、授信期限、贷款期限、担保方式和还款方式；前端按“建议”展示 |

建议采用 `small-short-v1` 规则：自动生成的额度不超过 **10,000 元（1 万元）**，同时不超过已提供的申请金额；两类期限均为 **1～3 个月**，不超过申请期限，贷款期限不超过授信期限。模型结合已有事实、审核结论和风险给出候选条件与理由，服务端校验边界和证据引用。无模型、模型生成失败或遗漏建议项时，规则补全候选条件；有风险或需人工复核时规则默认 1 个月，其余情况默认不超过 3 个月。规则默认建议核验法定代表人保证、按月等额本金还款，不代表已有担保承诺或已验证偿付能力。

本期未配置具体利率表，`suggested_interest_rate` 返回“建议按本行小额短期产品标准定价，最终以审批为准”，不生成虚构利率。审核结论为拒绝、申请金额为零（v1）或模型建议暂不新增授信时，建议额度为 `0`，利率返回“暂不建议新增授信，利率与期限不适用”，两类期限为 `null`，担保和还款方式为 `[]`，原因在 `analysis` 说明。这些不适用项不再标记为缺失资料。人工复核结论不会因小额短期建议而升级为自动通过；v1 已有人工建议值仍保留。

`business_plan.analysis` 给出综合理由和执行前提，`generated_fields` 列出后端自动填入的建议字段；新增 `suggestion_source` 为 `model`（AI 建议）、`rules`（规则保守建议）或 `model_with_rules`（AI 建议及规则补全），没有自动建议时可为 `null`。模型失败时仍保留 `generation_failed` 缺口标记，规则补全不伪装成模型成功。建议生成后会移除对应的 `not_provided` 缺口，其他缺失事实继续保留。已有 Run 的持久化结果不会自动重新生成。

`report.risk_points.finding_ids` 与 `risk_findings[].id` 同序，`summary.risk_count` 等于卡片数量。每张卡片固定返回 `risk_fact`、真实证据标签、核查项标签和可选的一句模拟案例。模拟案例以“模拟案例：”开头并设置 `historical_case_is_mock=true`，不会进入证据或全局 `meta.is_mock`。

裁剪示例：

```json
{
  "schema_version": "prototype-v1",
  "meta": {"request_id": "req-01JINDIAO", "run_id": "run-01JINDIAO", "status": "partial", "mode": "multi", "generated_at": "2026-09-07T06:00:00Z", "report_as_of": "2026-08-31", "report_version": "v1", "is_mock": false},
  "subject": {"subject_id": "tyc:example", "company_name": "示例企业有限公司", "unified_social_credit_code": "91110000EXAMPLE001"},
  "summary": {"risk_count": 1, "ai_suggestion": "manual_review", "ai_suggestion_reason": "存在已审核风险且部分关键资料不足"},
  "report": {
    "business_plan": {"status": "partial", "application_amount": 50000000, "application_term_months": 12, "suggested_amount": 10000, "suggested_interest_rate": "建议按本行小额短期产品标准定价，最终以审批为准", "suggested_credit_term_months": 1, "suggested_loan_term_months": 1, "guarantee_methods": ["legal_representative"], "repayment_methods": ["equal_principal"], "suggestion_source": "rules", "generated_fields": ["suggested_amount", "suggested_interest_rate", "suggested_credit_term_months", "suggested_loan_term_months", "guarantee_methods", "repayment_methods"], "missing_fields": [{"field": "fund_use", "reason": "not_provided", "message": "未提供调用方资料"}]},
    "company_profile": {"status": "complete", "company_name": "示例企业有限公司", "missing_fields": []},
    "ownership": {"status": "partial"}, "business_analysis": {"status": "partial"},
    "financial_analysis": {"status": "partial"}, "bank_flow_analysis": {"status": "unavailable"},
    "external_verification": {"status": "partial"},
    "risk_points": {"finding_ids": ["risk-001"]}
  },
  "risk_findings": [{"id": "risk-001", "title": "存在待关注事项", "source_kind": "mixed", "check_items": [{"id": "material-execution-risk", "label": "重大执行风险"}], "risk_fact": "存在一项已审核的执行风险事实。", "evidence_tags": [{"evidence_id": "ev-001", "label": "天眼查·被执行明细"}], "historical_case": "模拟案例：某企业因执行事项导致经营账户受限。", "historical_case_is_mock": true}],
  "evidence": [{"id": "ev-001", "source_type": "tianyancha", "source_label": "天眼查·被执行明细", "source_tool": "get_execution_info", "summary": "查询取得一项执行记录", "source_ref": "mcp://get_execution_info/record-1", "data_as_of": "2026-08-31", "queried_at": "2026-09-07T05:50:00Z", "supports_fields": [], "derived_from": [], "is_mock": false}],
  "report_markdown": "# 示例企业有限公司 尽调报告\n..."
}
```

完整机器可读样例见 [`samples/product-result-full-input.json`](samples/product-result-full-input.json) 和 [`samples/product-result-missing-input.json`](samples/product-result-missing-input.json)。完整输入样例通过 v1/内部模型提供流水等扩展材料，不能把其中的嵌套输入直接用于新版 v2 表单；两者的结果仍遵循同一 `ProductResult` 契约。在仓库根目录运行 `.venv/bin/python scripts/generate_product_result_samples.py` 可重新生成；字段定义以 OpenAPI 的 `ProductResult` 及其引用 Schema 为准。

### 3.5 取消 Run

```http
POST /api/v2/due-diligence/runs/{run_id}/cancel
Accept: application/json
X-Hw-Agentgateway-User-Id: user-001
```

请求体为空。返回当前 `RunResource`；运行中的 Run 会产生 `run.cancel_requested`，随后通常变为 `cancelled` 并产生 `run.cancelled`。已终止的 Run 重复取消不会改变状态。

返回示例（字段与普通状态查询相同，此处展示取消相关字段）：

```json
{
  "run_id": "run-01JINDIAO",
  "status": "cancelled",
  "stage": "terminal",
  "result_available": false,
  "termination": {"reason": "cancelled", "completed_task_ids": [], "incomplete_task_ids": ["operations"], "detail": null},
  "error": null
}
```

## 4. v1 兼容接口

### 4.1 `POST /api/v1/due-diligence/result`

请求体使用第 2 节的 `DueDiligenceRequest`，`mode` 通过查询参数传入：

```http
POST /api/v1/due-diligence/result?mode=single
Content-Type: application/json
Accept: application/json
```

请求示例：

```json
{"enterprise": {"company_name": "乐视网信息技术（北京）股份有限公司"}, "report_as_of": "2026-08-31", "language": "zh-CN", "scenario_id": "normal-enterprise"}
```

`mode` 可选值为 `single`、`multi`，默认 `multi`。JSON 请求会等待并返回第 3.4 节的 `prototype-v1` 结果（状态 `200`）；设置 `Accept: text/event-stream` 时返回 SSE，最后一条 `report.completed` 的 `payload.result` 与 JSON 结果契约等价。非法 `mode` 或不支持的 `Accept` 会在创建运行前返回 `422` 或 `406`。

JSON 返回字段路径裁剪示意（实际响应会包含第 3.4 节定义的全部固定子字段）：

```json
{"schema_version":"prototype-v1","meta":{"status":"partial","mode":"single"},"subject":{"subject_id":"mock:normal-enterprise","company_name":"乐视网信息技术（北京）股份有限公司"},"summary":{"risk_count":0,"ai_suggestion":"manual_review"},"report":{"business_plan":{},"company_profile":{},"ownership":{},"business_analysis":{},"financial_analysis":{},"bank_flow_analysis":{},"external_verification":{},"risk_points":{"finding_ids":[]}},"risk_findings":[],"evidence":[],"report_markdown":"# 乐视网信息技术（北京）股份有限公司 尽调报告"}
```

## 5. AgentArts 适配接口

### 5.1 `POST /invocations`

请求体直接使用 v2 `RunCreateRequest`，也接受 AgentArts 常见包装：`{"input": <RunCreateRequest>}`。返回 `202` 和 `RunResource`；`Accept: text/event-stream` 时返回该 Run 的 SSE 事件流。

```http
POST /invocations
Content-Type: application/json
Accept: application/json
```

```json
{"customerName": "乐视网信息技术（北京）股份有限公司", "scenario_id": "normal-enterprise", "mode": "multi"}
```

返回示例：

```json
{"request_id": "req-01JINDIAO", "run_id": "run-01JINDIAO", "mode": "multi", "status": "accepted", "stage": "accepted", "result_available": false, "links": {"self": {"href": "/api/v2/due-diligence/runs/run-01JINDIAO", "method": "GET"}}}
```

### 5.2 `GET /ping`

```http
GET /ping
```

返回示例：

```json
{"status": "Healthy"}
```

若 detached 后台 Run 正在执行，状态可能为 `HealthyBusy`。

## 6. 错误响应

业务异常统一返回：

```json
{"error": {"category": "entity", "code": "entity_not_found", "message": "Entity not found", "recoverable": false, "details": {}}}
```

| HTTP | `error.code`/场景 | 说明 |
| --- | --- | --- |
| `404` | `entity_not_found`、`run_not_found` | 企业或 Run 不存在；主体权限不匹配也返回 `run_not_found` |
| `409` | `entity_ambiguous`、`idempotency_conflict`、`detached_unavailable` | 主体歧义、幂等键冲突、detached 不可用 |
| `406` | — | `Accept` 同时不包含 JSON 或 SSE（仅 v1） |
| `422` | `request_invalid`、`evidence_review_failed`、`scenario_integrity`、`evaluation_integrity` | 请求/契约/场景校验失败；FastAPI 参数校验错误可能使用 `{"detail": [...]}` |
| `500` | `agent_execution_failed`、`risk_rule_failed`、`report_generation_failed`、`internal_error` | 内部执行失败；v2 已接受的 Run 通过状态查询和 `run.failed` 事件披露 |
| `504` | `agent_execution_timeout` | Agent 执行超时，`details` 提供执行者、阶段与配置的秒数；`recoverable=true` 不意味着自动重试。仅新版本捕获的超时使用此码，旧 `internal_error` 记录不追溯改写 |
| `503` | `source_unavailable` | 外部数据源暂不可用 |

来源缺口、`source_error`、`capability_absent` 或冲突未解时，系统会在对应模块的 `status` 和 `missing_fields` 中标记，必要时将 Run 标记为 `partial`；不得将其解释为“无风险”。

## 7. 调用流程示例

```bash
# 1. 创建
created=$(curl -sS -X POST http://localhost:8080/api/v2/due-diligence/runs \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -H 'Idempotency-Key: demo-001' \
  -H 'X-Hw-Agentgateway-User-Id: user-001' \
  -d '{"customerName":"乐视网信息技术（北京）股份有限公司","mode":"multi"}')

# 2. 使用返回的 links.events.href 订阅；断线后用最后 sequence 作为 after 重连
curl -N -H 'Accept: text/event-stream' \
  -H 'X-Hw-Agentgateway-User-Id: user-001' \
  'http://localhost:8080/api/v2/due-diligence/runs/<run_id>/events?after=0'

# 3. 收到 run.completed/run.partial 后获取最终结果
curl -sS -H 'Accept: application/json' \
  -H 'X-Hw-Agentgateway-User-Id: user-001' \
  'http://localhost:8080/api/v2/due-diligence/runs/<run_id>/result'
```

## 8. 用户反馈报告 Demo（已实现，默认关闭）

对应 [设计](../../openspec/changes/add-user-feedback-reporting-loop/design.md)、[任务状态](../../openspec/changes/add-user-feedback-reporting-loop/tasks.md) 和 [可重复演示](../reporting-feedback-demo.md)。支持显式开启的本地 Demo，以及经回环反向代理访问的单进程 ECS 公网联调模式。

### 8.1 启用与生效范围

设置 `JINDIAO_REPORTING_DEMO_ENABLED=true`；`JINDIAO_ENV` 必须是 `development` 或 `test`，`JINDIAO_STORAGE_BACKEND` 必须是 `memory` 或 `local`。不支持的组合在 Settings 初始化时拒绝启动；关闭时两个路由均返回 503。使用 `local` 可跨进程重启读取源 Run；`memory` 的 Run 归属和结果只在当前进程保留。

以 `--host 127.0.0.1 --workers 1 --no-proxy-headers` 启动。接口检查实际回环对端和 Host（localhost/127.0.0.1/::1），拒绝转发头及浏览器跨源 Origin；这些拒绝返回 403。本地检查不等于用户认证，不支持代理、公共访问或 AgentArts 云端反馈 Demo。

**ECS 公网联调**使用独立开关，保留上述本地 Demo 限制：

```dotenv
JINDIAO_ENV=integration
JINDIAO_STORAGE_BACKEND=local
JINDIAO_REPORTING_DEMO_ENABLED=false
JINDIAO_REPORTING_PUBLIC_ENABLED=true
JINDIAO_REPORTING_PUBLIC_ORIGIN=http://1.95.121.114
```

两个开关默认均关闭；任一受支持模式开启才初始化反馈存储。公网模式仅允许 `integration + local`，不支持 `production`。应用仍绑定回环、单 worker、`--no-proxy-headers`；Nginx 通过 `127.0.0.1:8080` 转发并保留原始 Host，不添加 `Forwarded` / `X-Forwarded-*`。请求 Host 必须精确匹配配置的 origin authority；浏览器 Origin（若携带）必须一致，`Sec-Fetch-Site: cross-site` 拒绝。

前端创建 Run、提交反馈和查询候选时必须使用同一组非空 `X-Hw-Agentgateway-User-Id`、`X-Hw-Agentarts-Session-Id`，均不超过 128 字符；不接受 `anonymous` owner。POST 另需 `Idempotency-Key`。身份头缺失返回 401，源 Run / 候选身份不匹配返回 404。旧匿名 Run 不满足该联调接口条件，需以明确身份重新创建。前端与 API 同源访问，无需 CORS 通配放行。

身份头继承现有 Run 隔离约定，**不是登录鉴权**；该模式用于受控联调，不是生产多用户认证方案。反馈仅创建待审候选，不执行反馈文本、不自动应用策略；HTTP activate/rollback 仍不存在。服务器原有模型和天眼查凭据不会发给前端。

唯一变更是 `gap_placement: appendix_only → section_and_appendix`：在相关章节结论后重复原附录已披露的缺口，保留附录、事实、分数、证据和 Mock 提示。文字仅为反馈背景，不进入 Prompt、Skill 指令或可执行代码；回放不调用模型或外部数据源。

Run 首次 accepted 持久化 `reporting_policy`，包含 `version/revision/policy/policy_sha256`；幂等重试、排队中和在途任务保持该绑定。独立 service 入口冻结一次；paired 两臂共享同一绑定。应用仅影响之后新受理的 Run。

报告策略版本、渲染器版本和 replay 可用性保存在内部回放产物，不扩展 `prototype-v1.meta`。策略基线为 `1.1.0`；候选为按 evolution ID 确定的唯一 `1.1.N`，N 不是连续发布计数。源 Run 不因反馈重新执行或重开 SSE。

### 8.2 提交反馈

`POST /api/v2/due-diligence/runs/{run_id}/feedback`，必须带 `Idempotency-Key`（非空且不超过 128 字符），并保持源 Run 的 owner/session 头。

```json
{
  "kind": "gap_disclosure_placement",
  "text": "司法缺口请在相关结论后披露。",
  "target_section_ids": ["judicial-risk"],
  "evidence_ids": [],
  "source": "user_review"
}
```

`kind` 只接受上例值；`text` 去除首尾空白后长 1–2000；目标章节 1–8 个且唯一；Evidence 最多 32 个且唯一；`source` 默认 `user_review`，非空且最长 80。引用必须属于源报告，不接受未知字段、自由策略或文件路径。

源 Run 必须为 completed/partial，`result_available=true`，且有通过 manifest、schema、引用、哈希校验的安全快照。未终态、失败、取消或没有有效快照均返回 409；不从旧结果猜造快照。新候选要求源绑定仍等于 active；同 owner/session/run/key 的同请求重试返回原候选（即使 active 后续改变），同键不同请求返回 409。

首次同步评测完成返回 201，重复成功/拒绝资源返回 200，无 evaluating/202 状态。返回字段为 `evolution_id/status/reason_codes/source_run_id/feedback/baseline_version/candidate_version/candidate_policy_sha256/evaluation_sha256/evaluation/active_revision/active_version/is_active/detail_url`。

只有用户每个目标章节及至少一个独立样本严格改善、全部样本保护检查通过且不退化才为 `awaiting_approval`。无适用缺口为 `rejected/no_applicable_gap`；仅有无法映射的缺口为 `rejected/unmapped_gap`；已是新策略为 `rejected/no_change`；目标章节无改善为 `source_target_not_improved`。候选创建不改变 active。

### 8.3 查询真实前后对比

前端接入详见 [Skill Evolution 详情接口：返回结构与前端接入](skill-evolution-detail.md)，包含完整字段、TypeScript 类型、按需加载示例、状态展示及错误处理。

`GET /api/v2/skill-evolutions/{evolution_id}` 继承源 Run 的 owner/session 校验，错配与未知资源均为 404。

默认省略全文。`?include=reports` 返回每例 before/after/diff；`?case_id=single-gap` 返回该例和全文，可选 ID 为 `source/normal/single-gap/multiple-gaps/absent/empty/mock/risk-review/global`。未知 ID 返回 422，不解释为文件路径。

`evaluation` 包含 `passed/reasons/fingerprint/elapsed_ms/cases`。每例包含实际报告哈希、分子、分母、前后 rate、`applicable/improved/protected/reasons`；分母为 0 时 rate 为 null、applicable=false。显式 oracle 与八个独立视图保存在 `config/report-replay-suite-v1.json`，加源案例实际渲染共九例。检查器解析 Markdown 缺口标记、原文和所属章节；移除合法新增块后必须逐字等于基线。

候选是不可变记录，应用后 `status` 仍为 awaiting_approval，实时 `is_active` 表示当前是否使用它；reset 后变回 false。该评测只证明披露位置改善，不证明风险召回、模型能力或 Agent 协作增益。

### 8.4 本地应用与恢复

没有 HTTP activate/rollback 路由（请求返回 404）。演示者在核对对比后执行：

```bash
uv run python -m jindiao.reporting.demo_cli --demo --artifact-root "$DEMO_ROOT" show
uv run python -m jindiao.reporting.demo_cli --demo --artifact-root "$DEMO_ROOT" apply "$EVOLUTION_ID" --reason '已核对对比'
uv run python -m jindiao.reporting.demo_cli --demo --artifact-root "$DEMO_ROOT" reset --reason '恢复基线'
```

`DEMO_ROOT` 必须与服务使用的 artifact root 相同。apply 验证文件哈希、候选策略/版本/revision、父绑定、评测和实现/suite 指纹，再重新执行真实回放并比对结果；失败保持 active。重复应用当前候选不重复推进 revision。reset 恢复 bootstrap 1.1.0 并递增 revision，保留历史候选与报告。

状态位于 `reporting-demo/state.json`，候选位于 `reporting-demo/candidates/<evolution_id>.json`。使用同目录临时文件、原子替换及非阻塞文件锁，损坏明确拒绝。没有 SQLite 注册表、双角色权限、完整历史审计、多写者事务或后台恢复；本机文件控制权是演示信任边界。

### 8.5 失败与隔离

| HTTP | 场景 |
| --- | --- |
| 401 | 公网联调模式缺少有效的 owner/session 身份头 |
| 403 | 非回环对端/Host、代理转发头、跨源 Origin |
| 404 | 源 Run/候选不存在，或 owner/session 不匹配 |
| 409 | 源报告未就绪/无有效快照、基线过期、幂等冲突 |
| 413 | 回放总输入或总输出各超过 32 MiB；返回 failed 候选及 `replay_limit_exceeded` |
| 422 | 字段、章节/Evidence 引用、查询参数或幂等键无效 |
| 429 | 本地写锁已占用（同时评测/应用/恢复） |
| 500 | 评测异常/10 秒预算到期或存储失败；评测可落盘时保留 failed 资源 |
| 503 | Demo 关闭；不支持的存储/环境配置在启动时直接拒绝 |

10 秒是每案例结束时检查的协作式预算，不是硬中断 SLA。failed 评测同键重试返回原 failed 资源及其 413/500，GET 可读取原因；文件系统失败无法保证候选落盘。

单快照上限为实际序列化 4 MiB。快照生成、脱敏重现、大小或专属写入失败不扩展公共结果，主报告仍可交付；此源报告反馈返回 409。主 result/manifest/RunRepository 保存失败按 Run 失败处理，不发布 report.completed，不返回未落盘的缓存成功结果。最终 replay/result/report/metrics 纳入同次 manifest；JSON、SSE、文件与仓库使用相同已保存结果。

v1 的旧 `skill_feedback` 保留 deprecated 兼容行为，不再解析旧 URI、生成格式计数候选或修改主报告。兼容 SSE 仍可携带 `skill_evolution.proposed` 事件，但其 payload 明确为 `rejected/use_feedback_api`，不表示产生新候选。v2 和 `/invocations` 的扁平请求已删除该字段，携带时直接返回 `422`；业务反馈请使用上面的独立反馈接口。
