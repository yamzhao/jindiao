# Jindiao 尽调 API

本文档对应 `jindiao/src/jindiao/api/app.py` 中实际注册的 HTTP 接口。接口返回 JSON 时使用 `application/json`；需要实时进度时使用 `text/event-stream`（SSE）。所有时间为 ISO 8601 UTC 字符串，日期为 `YYYY-MM-DD`。

> 当前版本没有注册 `/api/agent/components/export`。第 8 节记录已注册、默认关闭的两个本地反馈 Demo 接口；应用和恢复仅通过本地 CLI 执行。

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
| POST | `/api/v2/due-diligence/runs/{run_id}/feedback` | 本地 Demo 提交反馈并同步评测 | `201` / 重复 `200` |
| GET | `/api/v2/skill-evolutions/{evolution_id}` | 本地 Demo 查询真实对比 | `200 OK` |

推荐新客户端使用 v2 Run 生命周期；v1 仅用于兼容已有客户端。

浏览器接入请使用独立的 [BFF 安全代理](../deployment/bff.md)：提供登录会话、CSRF、服务端 API Key 注入以及上述五个 v2 Run 接口的受限代理。它不是本文件所述尽调 app 的内置登录模块，需要单独启动。2026-09-06 真实 Qwen + 天眼查 + AgentArts multi 已通过本地 BFF 完成 15 项核查、六角色审核及报告 GET/SSE/断点重放，使用 `live-recovery-0906`、有界 multi 预算和 1 MiB SSE 上限，详见[真实 multi 验收记录](../agentarts-real-multi-2026-09-06.md)。结果为无 Mock 的 `partial`（数据覆盖不足），不代表完整尽调结论或生产稳定性验收；前端尚未接入。

## 2. 通用请求模型

### 2.1 尽调请求体

v1 请求体为 `DueDiligenceRequest`；v2 创建请求体为该模型加上 `mode`、`execution_profile`、`session_id`。

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
| `skill_feedback` | object/null | 否 | `null` | 已 deprecated；有值时统一 `rejected/use_feedback_api`，不生成候选且不阻断主报告，见第 8 节 |
| `skill_feedback.source` | string | 是（对象存在时） | — | 反馈来源 |
| `skill_feedback.reference` | string | 是 | — | 可审计引用，如 `artifact://...` |
| `skill_feedback.text` | string | 是 | — | 反馈正文 |
| `skill_feedback.evidence_refs` | string[] | 是 | — | 至少一个证据/报告引用 |
| `mode`（仅 v2） | `single\|multi` | 否 | `multi` | 选择 investigation 拓扑 |
| `execution_profile`（仅 v2） | `attached\|detached` | 否 | `attached` | detached 只有部署探针通过后可用 |
| `session_id`（仅 v2） | string/null | 否 | `null` | 运行所属会话；也可由请求头提供 |

### 2.2 通用请求头

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
Idempotency-Key: demo-20260906-001
X-Hw-Agentgateway-User-Id: user-001
```

请求示例：

```json
{
  "enterprise": {
    "company_name": "金调绿洲科技有限公司",
    "region": "北京"
  },
  "report_as_of": "2026-08-31",
  "language": "zh-CN",
  "scenario_id": "normal-enterprise",
  "mode": "multi",
  "execution_profile": "attached",
  "allow_degraded_mock": false
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

### 3.4 获取 Run 结果

```http
GET /api/v2/due-diligence/runs/{run_id}/result
Accept: application/json
X-Hw-Agentgateway-User-Id: user-001
```

- 结果已生成：`200`，返回完整 `DueDiligenceResult`。
- 仍在运行：`202`，返回 `{"status":"running|accepted", "run_id":"...", "links": {...}}`。
- 运行失败：`500`，返回 `{"error": <ErrorRecord>}`。

完整结果顶层字段固定如下：

```text
meta, subject, decision, risk_summary, coverage, sections, findings, evidence,
agent_results, report_structure, context_snapshot, execution_cost,
comparison_metadata, agent_trace, collaboration, evaluation,
skill_evolution, report_markdown, errors
```

主要结果字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `meta` | object | request/run 标识、状态、模式、时间、模型、规则和 Skill 版本 |
| `subject` | object | 已解析主体（名称、统一社会信用代码、地区、来源、解析时间） |
| `decision` | object | `band`（`pass`/`manual_review`/`reject`）、分数、置信度、规则命中和报告日期 |
| `risk_summary` | object | admission/attention/non-risk 数量、总分和重点 Finding ID |
| `coverage` | object | 采集覆盖率、各来源状态计数和 `items` |
| `sections` | array | 8 个报告模块的状态、覆盖率、Finding/Evidence 引用及 `data` |
| `findings` / `evidence` | array | 结构化风险发现与可审计证据；引用通过 ID 关联 |
| `agent_results` / `agent_trace` | array | Agent 输出与执行轨迹摘要，不含思维链 |
| `report_structure` | object | 固定 8 个模块、48 个标准子模块的目录版本 |
| `context_snapshot` | object/null | 冻结事实快照摘要及共享采集成本 |
| `execution_cost` | object | `shared_acquisition_cost` 与 `investigation_cost` 两层成本 |
| `comparison_metadata` | object/null | formal/配对比较所需的拓扑、Prompt/目录/规则版本与哈希 |
| `collaboration` / `evaluation` | object | Agent 协作计数与评测指标 |
| `skill_evolution` | object | 未反馈时为 `not_proposed`；旧入口反馈为 `rejected/use_feedback_api`。新候选是第 8 节的独立资源 |
| `report_markdown` | string | 面向用户的 Markdown 报告 |
| `errors` | array | 运行期间的结构化错误/数据缺口 |

成功返回示例（为便于阅读只展开代表性数组项；字段名和枚举值与实际响应一致）：

```json
{
  "meta": {"request_id": "req-01JINDIAO", "run_id": "run-01JINDIAO", "status": "completed", "mode": "multi", "started_at": "2026-09-06T05:40:08.265456Z", "completed_at": "2026-09-06T05:40:08.279129Z", "duration_ms": 14, "scenario_snapshot_id": "normal-enterprise:v1.0.0:<sha256>", "is_mock": true, "degraded": false, "model_name": "deterministic-mock", "rule_version": "v1", "skill_versions": {"evidence-backed-due-diligence": "1.0.0"}},
  "subject": {"subject_id": "mock:normal-enterprise", "company_name": "金调绿洲科技有限公司", "unified_social_credit_code": "91110108MA01JD001A", "region": "北京", "registration_status": "存续", "source": "mock", "resolved_at": "2026-09-06T05:40:08.267396Z"},
  "decision": {"band": "pass", "score": 0, "confidence": 1.0, "rule_version": "v1", "rule_hits": [], "major_risk_finding_ids": [], "pending_review_items": [], "as_of_date": "2026-08-31"},
  "risk_summary": {"admission_count": 0, "attention_count": 0, "non_risk_count": 4, "total_score": 0, "top_finding_ids": []},
  "coverage": {"total_items": 4, "completed_items": 4, "ratio": 1.0, "status_counts": {"verified_records": 4, "verified_empty": 0, "capability_absent": 0, "source_error": 0, "degraded_mock": 0}, "items": []},
  "sections": [], "findings": [], "evidence": [], "agent_results": [],
  "report_structure": {"catalog_version": "report-catalog-v1", "module_count": 8, "submodule_count": 48, "modules": []},
  "context_snapshot": null,
  "execution_cost": {"shared_acquisition_cost": {"llm_requests": 0, "successful_llm_requests": 0, "provider_usage_requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "tool_calls": 0, "mcp_calls": 0, "schema_retries": 0, "repair_rounds": 0, "wall_time_ms": 0}, "investigation_cost": {"llm_requests": 0, "successful_llm_requests": 0, "provider_usage_requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "tool_calls": 0, "mcp_calls": 0, "schema_retries": 0, "repair_rounds": 0, "wall_time_ms": 0}},
  "comparison_metadata": null, "agent_trace": [], "collaboration": {"agent_count": 0, "task_count": 0, "parallel_task_count": 0, "conflicts_detected": 0, "repairs_requested": 0, "repairs_completed": 0},
  "evaluation": {"mode": "multi", "success": true, "metrics": {}}, "skill_evolution": {"status": "not_proposed", "active_version": "1.1.0", "candidate_version": null, "change_summary": null},
  "report_markdown": "# 企业信用与风控尽调报告\n...", "errors": []
}
```

以上为字段裁剪示例；实际 `sections`、`findings`、`evidence`、`modules` 等数组会按企业和数据源填充。`report_structure.module_count` 固定为 8，`submodule_count` 固定为 48。

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
{"enterprise": {"company_name": "金调绿洲科技有限公司"}, "report_as_of": "2026-08-31", "language": "zh-CN", "scenario_id": "normal-enterprise"}
```

`mode` 可选值为 `single`、`multi`，默认 `multi`。JSON 请求会等待并返回第 3.4 节的完整 `DueDiligenceResult`（状态 `200`）；设置 `Accept: text/event-stream` 时返回 SSE，最后一条 `report.completed` 的 `payload.result` 与 JSON 结果契约等价。非法 `mode` 或不支持的 `Accept` 会在创建运行前返回 `422` 或 `406`。

JSON 返回示例（完整字段定义见第 3.4 节）：

```json
{"meta": {"status": "completed", "mode": "single"}, "subject": {"subject_id": "mock:normal-enterprise", "company_name": "金调绿洲科技有限公司"}, "decision": {"band": "pass", "score": 0}, "risk_summary": {"total_score": 0}, "sections": [], "findings": [], "evidence": [], "report_markdown": "# 企业信用与风控尽调报告", "errors": []}
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
{"enterprise": {"company_name": "金调绿洲科技有限公司"}, "scenario_id": "normal-enterprise", "mode": "multi"}
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
| `503` | `source_unavailable` | 外部数据源暂不可用 |

来源缺口、`source_error`、`capability_absent` 或冲突未解时，系统会在结果中标记 coverage/`errors`，必要时将 Run 标记为 `partial`；不得将其解释为“无风险”。

## 7. 调用流程示例

```bash
# 1. 创建
created=$(curl -sS -X POST http://localhost:8080/api/v2/due-diligence/runs \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -H 'Idempotency-Key: demo-001' \
  -H 'X-Hw-Agentgateway-User-Id: user-001' \
  -d '{"enterprise":{"company_name":"金调绿洲科技有限公司"},"mode":"multi"}')

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

对应 [设计](../../openspec/changes/add-user-feedback-reporting-loop/design.md)、[任务状态](../../openspec/changes/add-user-feedback-reporting-loop/tasks.md) 和 [可重复演示](../reporting-feedback-demo.md)。仅支持显式开启的本地、单进程、单操作人比赛环境。

### 8.1 启用与生效范围

设置 `JINDIAO_REPORTING_DEMO_ENABLED=true`；`JINDIAO_ENV` 必须是 `development` 或 `test`，`JINDIAO_STORAGE_BACKEND` 必须是 `memory` 或 `local`。不支持的组合在 Settings 初始化时拒绝启动；关闭时两个路由均返回 503。使用 `local` 可跨进程重启读取源 Run；`memory` 的 Run 归属和结果只在当前进程保留。

以 `--host 127.0.0.1 --workers 1 --no-proxy-headers` 启动。接口检查实际回环对端和 Host（localhost/127.0.0.1/::1），拒绝转发头及浏览器跨源 Origin；这些拒绝返回 403。本地检查不等于用户认证，不支持代理、公共访问或 AgentArts 云端反馈 Demo。

唯一变更是 `gap_placement: appendix_only → section_and_appendix`：在相关章节结论后重复原附录已披露的缺口，保留附录、事实、分数、证据和 Mock 提示。文字仅为反馈背景，不进入 Prompt、Skill 指令或可执行代码；回放不调用模型或外部数据源。

Run 首次 accepted 持久化 `reporting_policy`，包含 `version/revision/policy/policy_sha256`；幂等重试、排队中和在途任务保持该绑定。独立 service 入口冻结一次；paired 两臂共享同一绑定。应用仅影响之后新受理的 Run。

结果 `meta` 披露 `skill_versions["feedback-evolved-reporting"]`、`reporting_policy_sha256`、`report_renderer_version`、`report_replay_available` 和 `report_replay_reason`。策略基线为 `1.1.0`；候选为按 evolution ID 确定的唯一 `1.1.N`，N 不是连续发布计数。源 Run 不因反馈重新执行或重开 SSE。

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
| 403 | 非回环对端/Host、代理转发头、跨源 Origin |
| 404 | 源 Run/候选不存在，或 owner/session 不匹配 |
| 409 | 源报告未就绪/无有效快照、基线过期、幂等冲突 |
| 413 | 回放总输入或总输出各超过 32 MiB；返回 failed 候选及 `replay_limit_exceeded` |
| 422 | 字段、章节/Evidence 引用、查询参数或幂等键无效 |
| 429 | 本地写锁已占用（同时评测/应用/恢复） |
| 500 | 评测异常/10 秒预算到期或存储失败；评测可落盘时保留 failed 资源 |
| 503 | Demo 关闭；不支持的存储/环境配置在启动时直接拒绝 |

10 秒是每案例结束时检查的协作式预算，不是硬中断 SLA。failed 评测同键重试返回原 failed 资源及其 413/500，GET 可读取原因；文件系统失败无法保证候选落盘。

单快照上限为实际序列化 4 MiB。快照生成、脱敏重现、大小或专属写入失败仅令 `report_replay_available=false`、`report_replay_reason=snapshot_unavailable`，主报告仍交付；此源报告反馈返回 409。主 result/manifest/RunRepository 保存失败按 Run 失败处理，不发布 report.completed，不返回未落盘的缓存成功结果。最终 replay/result/report/metrics 纳入同次 manifest；JSON、SSE、文件与仓库使用相同最终元数据。

v1、v2 和 `/invocations` 的旧 `skill_feedback` 已标记 deprecated，统一返回 `skill_evolution.status=rejected`、`reason_codes=["use_feedback_api"]`，不再解析旧 URI、生成格式计数候选或修改主报告。兼容 SSE 仍可携带 `skill_evolution.proposed` 事件，但其 payload 明确为 rejected，不表示产生新候选。旧 Python coordinator 同样明确拒绝。
