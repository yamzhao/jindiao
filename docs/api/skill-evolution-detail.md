# Skill Evolution 详情接口：返回结构与前端接入

状态：按当前实现整理（本地 Demo / ECS 受控联调，非生产发布接口）

最后更新：2026-09-09

关键词：skill-evolutions、报告反馈、前后对比、前端接入

## 1. 接口用途与请求

```http
GET /api/v2/skill-evolutions/{evolution_id}
Accept: application/json
X-Hw-Agentgateway-User-Id: <创建源 Run 时的 owner>
X-Hw-Agentarts-Session-Id: <创建源 Run 时的 session>
```

查询一次报告反馈生成的候选策略及其回放评测，供前端展示“反馈内容、评测结果、报告前后差异、当前是否生效”。GET 不执行评测、不修改源报告、不应用候选，也不是通用的 Skill 源码或版本详情接口。

`evolution_id` 和 `detail_url` 从 `POST /api/v2/due-diligence/runs/{run_id}/feedback` 的响应取得。不要用 Run ID 替代，也不要从旧的 `skill_evolution.proposed` SSE 事件推导候选。

| 参数 | 类型 | 必填 | 行为 |
| --- | --- | --- | --- |
| `evolution_id`（path） | string | 是 | 当前格式为 `evo-` 加 64 位小写十六进制字符；前端应作为不透明 ID 保存 |
| `include`（query） | `"reports"` | 否 | 仅支持此值；返回各案例的 `before/after/diff`；传空字符串或其他值返回 422 |
| `case_id`（query） | string | 否 | 筛选单个案例，且自动包含其 `before/after/diff`；无需再带 `include` |

| 请求形式 | `evaluation.cases` | 报告全文 |
| --- | --- | --- |
| 无 query | 全部案例（当前评测套件为 9 例） | 不包含 |
| `?include=reports` | 全部案例 | 包含 |
| `?case_id=source` | 仅源报告案例 | 包含 |
| `?include=reports&case_id=single-gap` | 仅指定案例 | 包含 |

当前案例 ID：`source`（本次源 Run）、`normal`、`single-gap`、`multiple-gaps`、`absent`、`empty`、`mock`、`risk-review`、`global`。后八项为独立回放样本，不是另八次用户 Run。前端优先使用默认响应中的 `case_id` 生成选项。

注意：只有存在 `evaluation` 时才筛选、校验 `case_id`。未知 ID 此时返回 `422 unknown_case_id`；若 `evaluation=null`，即使传入未知 ID，也会返回 200 和空评测，不要依赖此参数检测候选有效性。

请求须保持源 Run 的 owner/session，GET 无需 `Idempotency-Key`。公网联调模式要求两头非空、长度均不超过 128，owner 不得为 `anonymous`；本地模式允许匿名/缺省身份，但也必须与源 Run 一致。身份头仅用于现有 Run 隔离，不能替代登录鉴权。部署开启方式与同源/代理限制见 [API 总览 §8.1](README.md#81-启用与生效范围)。

当前实现的开发环境例外：`development` 下对端 IP 满足 Python `ipaddress.is_private` 也会通过对端检查（用于本地 Docker 桥接），且本地 HTTP 5173 端口的 localhost/回环 Origin 被允许。其他 Host、转发头等检查仍执行；不能笼统理解为“所有非回环地址一定返回 403”，也不能据此认为接口适合公网生产暴露。

## 2. 返回数据结构

成功 HTTP 状态为 `200 OK`，响应是下列对象本身，**没有 `data` 或 `code` 包装层**。即使候选业务状态是 `failed`，读取已保存候选成功仍返回 200。

### 2.1 TypeScript 类型

```ts
export type EvolutionStatus = "awaiting_approval" | "rejected" | "failed";

export interface ReportFeedback {
  kind: "gap_disclosure_placement";
  text: string;
  target_section_ids: string[];
  evidence_ids: string[];
  source: string;
}

export interface CaseEvaluation {
  case_id: string;
  before_sha256: string;
  after_sha256: string;
  denominator: number;
  applicable: boolean;
  before_numerator: number;
  after_numerator: number;
  before_rate: number | null;
  after_rate: number | null;
  improved: boolean;
  protected: boolean;
  reasons: string[];
  // 默认请求中字段缺席，不是 null；仅请求全文时出现。
  before?: string;
  after?: string;
  diff?: string;
}

export interface ReplayEvaluation {
  passed: boolean;
  reasons: string[];
  fingerprint: string;
  elapsed_ms: number;
  cases: CaseEvaluation[];
}

export interface SkillEvolutionDetail {
  evolution_id: string;
  status: EvolutionStatus;
  reason_codes: string[];
  source_run_id: string;
  feedback: ReportFeedback;
  baseline_version: string;
  candidate_version: string;
  candidate_policy_sha256: string;
  evaluation_sha256: string | null;
  evaluation: ReplayEvaluation | null;
  active_revision: number;
  active_version: string;
  is_active: boolean;
  detail_url: string;
}
```

### 2.2 顶层字段

| 字段 | 含义与前端用途 |
| --- | --- |
| `evolution_id` | 候选唯一 ID；作为详情页路由、请求标识 |
| `status` | 创建时的候选结论：待人工确认 / 被拒绝 / 评测失败；不是实时发布状态 |
| `reason_codes` | 候选级原因码；拒绝/失败提示优先使用此字段，可为空数组 |
| `source_run_id` | 产生反馈的源 Run；可用于返回源报告页面 |
| `feedback` | 已保存反馈，`text/source` 已经过后端脱敏；页面仍按不可信文本展示 |
| `baseline_version` | 源 Run 冻结的报告策略版本，即本次 before 的基线 |
| `candidate_version` | 候选策略版本；当前为 `1.1.N`，N 可非常长且不是连续发布次数，必须按字符串处理 |
| `candidate_policy_sha256` | 候选策略内容摘要，不是候选身份标识；不同候选可具有相同策略摘要 |
| `evaluation_sha256` | 完整已保存评测的摘要，未生成评测时为 null |
| `evaluation` | 评测详情；评测异常等情形下为 null；被拒绝的候选也可能有完整评测 |
| `active_revision` | 查询时当前工作区策略修订号；apply/reset 可推进它，不是当前候选自身的 revision |
| `active_version` | 查询时当前工作区生效策略版本，不一定等于基线或候选版本 |
| `is_active` | 查询时当前策略绑定是否与此候选完全一致；生效标识以它为准 |
| `detail_url` | 相对 API 路径，不是前端页面路由；默认不附带 query |

`feedback.target_section_ids` 是用户关注的章节；`evidence_ids` 是关联证据 ID，允许空数组；`source` 默认 `user_review`。章节 ID 应来自源报告，不能把旧报告示例中的章节 ID 硬编码到所有结果格式。

除 `active_revision/active_version/is_active` 随查询读取当前状态外，候选记录及评测不可变。接口不返回 `created_at`、owner/session、候选策略完整对象或可下载文件路径；不要从内部存储模型扩展 HTTP 契约。

摘要通常为 `sha256:` 加 64 位小写十六进制字符。`evaluation_sha256` 对应完整评测，**不是**默认省略全文或筛选单例后的响应摘要；不要直接对响应 `evaluation` 做 JSON 哈希来比对。

### 2.3 评测与案例字段

| 字段 | 含义 |
| --- | --- |
| `evaluation.passed` | 整个评测是否通过；筛选单例后仍是整体结论 |
| `evaluation.reasons` | 整体评测失败原因；可能与候选级 `reason_codes` 不同，不要互相覆盖 |
| `evaluation.fingerprint` | 评测实现和套件指纹，用于追溯，不是版本显示名 |
| `evaluation.elapsed_ms` | 创建候选时的整套回放耗时（毫秒），不是本次 GET 耗时或实时进度 |
| `evaluation.cases` | 当前响应中的案例集合；筛选后不能用其长度/通过数重新计算整套结论 |
| `cases[].before_sha256/after_sha256` | 对应实际前后报告文本摘要，即使省略全文仍返回 |
| `cases[].denominator` | 应在所属章节披露的缺口-章节配对数；一个缺口映射多个章节可计多次 |
| `cases[].before_numerator/after_numerator` | 前/后报告中检查器识别出的合法就近披露配对数 |
| `cases[].before_rate/after_rate` | 分子 ÷ 分母，数值通常在 0–1；展示为百分比；分母为 0 时为 null |
| `cases[].applicable` | 是否存在可评测的缺口配对；分母为 0 时为 false |
| `cases[].improved` | 适用且 after 分子严格大于 before 分子；false 不等于发生退化 |
| `cases[].protected` | 本例内容保护检查是否通过，包括正文一致性、缺口块合法性和 Mock 提示检查 |
| `cases[].reasons` | 本例保护检查原因；失败详情展示在对应案例内 |
| `cases[].before/after` | 基线/候选策略渲染出的 Markdown 全文；只有请求全文才有 |
| `cases[].diff` | 服务端生成的 unified diff 纯文本，文件标签为 `before.md/after.md`；无差异时可为空字符串 |

通过条件是：源报告每个目标章节严格改善、至少一个独立样本严格改善、所有案例无退化且保护检查通过。并非“每例 improved=true”；不适用案例仍参与保护检查。该评测仅证明缺口披露位置改善，不能称为“模型准确率提升”或“风险召回提升”。

### 2.4 JSON 示例（单案例全文模式）

下例对应 `?case_id=source`，仅用于说明结构：ID、摘要、版本和耗时是占位示例，报告文本也已缩写，不能用于摘要校验或真实回放。实际响应中的 ID/摘要是完整值，before/after 是全文。默认请求则返回全部案例，并移除每例的 `before/after/diff` 三个键。

```json
{
  "evolution_id": "evo-<64位小写十六进制>",
  "status": "awaiting_approval",
  "reason_codes": [],
  "source_run_id": "run-example",
  "feedback": {
    "kind": "gap_disclosure_placement",
    "text": "请在相关结论后披露数据缺口",
    "target_section_ids": ["external_verification"],
    "evidence_ids": [],
    "source": "user_review"
  },
  "baseline_version": "1.1.0",
  "candidate_version": "1.1.12345678901234567890123456789",
  "candidate_policy_sha256": "sha256:<64位小写十六进制>",
  "evaluation_sha256": "sha256:<64位小写十六进制>",
  "evaluation": {
    "passed": true,
    "reasons": [],
    "fingerprint": "sha256:<64位小写十六进制>",
    "elapsed_ms": 35,
    "cases": [
      {
        "case_id": "source",
        "before_sha256": "sha256:<64位小写十六进制>",
        "after_sha256": "sha256:<64位小写十六进制>",
        "denominator": 1,
        "applicable": true,
        "before_numerator": 0,
        "after_numerator": 1,
        "before_rate": 0.0,
        "after_rate": 1.0,
        "improved": true,
        "protected": true,
        "reasons": [],
        "before": "<基线报告 Markdown 全文>",
        "after": "<候选报告 Markdown 全文>",
        "diff": "--- before.md\n+++ after.md\n<差异内容>"
      }
    ]
  },
  "active_revision": 0,
  "active_version": "1.1.0",
  "is_active": false,
  "detail_url": "/api/v2/skill-evolutions/evo-<64位小写十六进制>"
}
```

## 3. 前端页面与交互建议

1. 提交反馈后保存 `evolution_id/detail_url`，进入详情页，默认 GET 加载摘要和案例列表。POST 同步完成评测，没有 `evaluating`、202 或需持续轮询的评测进度。
2. 顶部展示反馈、基线版本 → 候选版本，以及两个独立标签：“候选结论”和“当前是否生效”。
3. 展示整体评测结论和原因；`evaluation=null` 时显示“暂无可用评测结果”，隐藏指标与对比入口，不要访问 `evaluation.cases`。
4. 案例表展示缺口就近披露率、分子/分母、改善标识、保护检查。`source` 可置顶；选中案例时按 `case_id` 查找，不要依赖固定数组下标。
5. 首次打开对比面板请求 `?case_id=source`；切换案例请求对应 ID。默认摘要列表与单例全文分别保存，避免用只有一例的响应覆盖整张案例列表。
6. before/after 用左右对比或标签页展示；diff 用纯文本代码区或 unified diff 组件。接口不提供独立报告下载 URL。
7. 提供手动刷新生效状态入口。只有演示者通过本地 CLI apply/reset 后，重新 GET 才能看到最新状态；当前没有 HTTP activate/rollback，不应放置实际调用这些路由的“应用/回滚”按钮。

| 数据条件 | 建议文案 |
| --- | --- |
| `status=awaiting_approval` | 评测通过，候选待人工确认（若已生效，可将候选结论简写为“评测通过”） |
| `status=rejected` | 候选未通过；展示 `reason_codes` |
| `status=failed` | 评测执行失败；展示 `reason_codes`，不当作 GET 网络失败 |
| `is_active=true` | 当前生效 |
| `is_active=false` | 当前未生效；不能推断“从未应用”或“可立即应用” |
| `applicable=false` 或 rate 为 null | 不适用 / —，不能展示为 0% |
| `improved=false && protected=true` | 未改善、保护检查通过；结合适用性解释，不能直接标红为退化 |

候选应用后 `status` 仍为 `awaiting_approval`；reset 后 `is_active` 变为 false，历史候选仍可查看。应用仅影响之后新受理的 Run，源 Run 的结果、结构化事实和 SSE 不会重新生成。before 是源基线，不是查询时 active 策略的重新渲染结果。

### 3.1 请求示例

下面函数复用第 2 节类型。实际项目可在返回处增加运行时结构校验；TypeScript 类型断言本身不校验服务端数据。

```ts
export async function getSkillEvolution(
  evolutionId: string,
  identity: { ownerId: string; sessionId: string },
  options: { caseId?: string; includeReports?: boolean; signal?: AbortSignal } = {},
): Promise<SkillEvolutionDetail> {
  const url = new URL(
    `/api/v2/skill-evolutions/${encodeURIComponent(evolutionId)}`,
    window.location.origin,
  );
  if (options.caseId !== undefined) url.searchParams.set("case_id", options.caseId);
  if (options.includeReports) url.searchParams.set("include", "reports");

  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      "X-Hw-Agentgateway-User-Id": identity.ownerId,
      "X-Hw-Agentarts-Session-Id": identity.sessionId,
    },
    cache: "no-store",
    signal: options.signal,
  });
  // 同时兼容 detail、error 对象，以及代理返回非 JSON 的情形。
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const code = typeof payload?.detail === "string"
      ? payload.detail
      : payload?.error?.code ?? `http_${response.status}`;
    throw Object.assign(new Error(code), {
      status: response.status,
      code,
      payload,
    });
  }
  return payload as SkillEvolutionDetail;
}

export function formatRate(rate: number | null): string {
  return rate === null ? "不适用" : `${(rate * 100).toFixed(1)}%`;
}

// 页面先加载摘要，再按需取全文：
// const summary = await getSkillEvolution(evolutionId, identity);
// const detail = await getSkillEvolution(evolutionId, identity, { caseId: "source" });
// const source = detail.evaluation?.cases.find(item => item.case_id === "source");
// const hasReports = typeof source?.before === "string"
//   && typeof source?.after === "string" && typeof source?.diff === "string";
```

页面需要捕获请求错误并按下一节映射提示；切换案例时使用 `AbortController` 或请求序号防止旧响应覆盖新选择。全文缓存键建议包含 owner、session、evolution ID、case ID；会话变化时清理旧数据。`active_*` 与 `is_active` 单独刷新，不应因为全文不可变而永久缓存整份响应。

Markdown 渲染应禁用原始 HTML 或采用项目现有的安全清洗流程，限制危险链接协议；反馈与原因文本也必须转义。不要直接把 `before/after/diff` 写入 `innerHTML`。无差异时 `diff=""` 是有效结果，应显示“无文本差异”，不是“全文未加载”。

## 4. 错误响应与原因码

### 4.1 GET 的 HTTP 错误

此路由直接抛出的错误形如 `{"detail":"evolution_not_found"}`；参数类型校验可能是 `{"detail":[...]}`；源 Run 校验可能返回统一的 `{"error":{"code":"run_not_found", ...}}`。前端不能只解析一种错误包装。

| HTTP | 常见 code / detail | 前端处理 |
| --- | --- | --- |
| 401 | `reporting_identity_required` | 检查并恢复创建源 Run 时的身份上下文；不要盲目重试 |
| 403 | `reporting_demo_local_only`、`reporting_demo_proxy_not_supported`、`reporting_public_invalid_host`、`reporting_demo_same_origin_only` | 提示当前访问方式不受支持，检查同源、Host 与联调代理配置 |
| 404 | `evolution_not_found` 或 `run_not_found` | 统一提示“记录不存在或无权访问”，不能据此区分资源是否存在；候选 ID 非法、候选校验失败也可能是 404 |
| 422 | `unknown_case_id` 或参数校验 `detail` 数组 | 从摘要案例列表重新选择；检查 `include` 值，不进行自动重试 |
| 503 | `reporting_demo_disabled` | 提示报告反馈功能未开启 |
| 500 / 其他 5xx | 未处理的存储/服务异常或代理错误 | 展示通用错误，允许有限次数重试，保留 HTTP 状态便于排查；不假定一定有 JSON body |

POST 文档中的 409/413/429 不是本 GET 路由的正常业务分支。已保存的 `failed` 候选仍可 GET 200 读取其原因，失败资源也可能因源 Run 丢失/权限不符而变为不可查询。

### 4.2 业务原因码（HTTP 200 中也可能存在）

| 原因码 | 常见位置 | 建议解释 |
| --- | --- | --- |
| `no_applicable_gap` | `reason_codes` | 源报告没有适用的数据缺口 |
| `unmapped_gap` | `reason_codes` | 有缺口但无法映射到相关章节 |
| `no_change` | `reason_codes` | 基线已使用该披露策略，无需变化 |
| `source_target_not_improved` | `reason_codes` / `evaluation.reasons` | 源报告未在每个目标章节获得严格改善 |
| `no_independent_improvement` | 同上 | 独立样本中没有严格改善 |
| `regression_or_protection_failed` | 同上 | 存在退化或保护检查未通过，查看案例原因 |
| `structured_facts_changed` | 同上 | 回放过程中结构化事实发生变化 |
| `mandatory_content_changed` | `cases[].reasons` | 移除合法新增缺口块后，正文仍不一致 |
| `invalid_gap_blocks` | `cases[].reasons` | 缺口块内容、位置或标记不合法 |
| `mock_disclosure_changed` | `cases[].reasons` | Mock 数据提示数量发生变化 |
| `replay_limit_exceeded` | `reason_codes` | 回放输入或输出超过限制 |
| `replay_deadline_exceeded` | `reason_codes` | 回放超过协作式时间预算 |
| `replay_failed` | `reason_codes` | 评测执行异常 |

原因码按字符串兼容扩展；未知码显示通用提示并保留原码供排查，不应导致页面崩溃。候选级特殊原因可能覆盖评测级原因，应分别展示。

## 5. 联调验收清单与实现依据

- 默认响应没有 `before/after/diff`，但保留报告摘要和指标；展开单例后出现三个全文字段。
- 单例查询只返回一个 case，但 `passed/reasons/fingerprint/elapsed_ms` 仍属于原完整评测。
- `evaluation=null`、rate 为 null、diff 为空字符串均能正常展示。
- 能区分 HTTP 请求失败、`status=failed`、`status=rejected`；兼容两类错误包装和非 JSON 错误。
- CLI apply 后刷新显示 `is_active=true`，且候选 status 不变；reset 后刷新显示 false。
- 全文中不允许执行 HTML/脚本；切换案例和身份时不会串数据。

实现依据：

- [HTTP 路由与响应字段](../../src/jindiao/api/reporting_demo.py)：`get_evolution`、`_response`、访问边界。
- [候选存储与发布状态](../../src/jindiao/reporting/demo_store.py)：`DemoCandidate`、`propose/apply/reset`。
- [评测数据结构与计算](../../src/jindiao/reporting/replay.py)：`CaseEvaluation`、`ReplayEvaluation`、`evaluate_case/replay`。
- [反馈字段契约](../../src/jindiao/contracts/report_policy.py)：`ReportFeedbackRequest`。
- [端到端测试](../../tests/e2e/test_user_feedback_reporting_loop.py)：权限隔离、单例全文、失败隔离、本地和公网联调流程。

返回字段当前由路由手工组装（`response_model=None`），不能假定 OpenAPI 会自动提供完整响应 schema；后续实现变更时应同步本页类型和字段表。

本次验证（2026-09-09）：示例 JSON、15 个顶层字段和相对链接校验通过；运行 `.venv/bin/python -m pytest tests/e2e/test_user_feedback_reporting_loop.py -q --no-cov`，29 项中 28 项通过、1 项失败。失败点为 `test_demo_permissions_references_and_readiness` 最后的对端边界断言：预期 `192.0.2.1` 返回 403，实际为 200。当前环境该地址 `is_private=true`，命中 development 私有地址放行分支，与测试预期不一致。本次仅交付文档，未修改该实现或测试。
