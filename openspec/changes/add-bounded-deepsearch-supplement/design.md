## Context

Jindiao 的 48 个报告子模块已经提供稳定的领域分解，因此不需要再引入 DeepSearch 的完整大纲、章节规划和报告生成。当前缺口位于证据采集层：天眼查非空响应一律被视为 `verified_records`，Normalizer 不保留分页信息；DeepSearch 只检索冻结 Mock 语料并输出 `mock://` Evidence。

本变更必须保持单机、单进程、唯一 result API、确定性评分、`verified_empty` 不被覆盖以及离线 benchmark 可复现等约束。外部网络和凭据不能成为默认测试或演示的必要条件。

## Goals / Non-Goals

**Goals:**

- 在不改变数据源健康状态语义的前提下表达证据覆盖完整性和缺口原因。
- 从天眼查分页元数据识别“有记录但可能截断”的子模块。
- 将每个缺口限制性地分解成少量查询，并在首轮没有可用正文证据时最多改写补搜一次。
- 只把经过正文获取、主体锚定和时间校验的补充材料转换为统一 Evidence。
- 通过依赖注入使真实 DeepSearch Web Provider 与离线 Fake Provider 使用同一契约。
- 将天眼查最近可用年报中的社保披露作为企业基本信息的独立、可关闭 Provider 接入。
- 保留现有 Mock 回退、Reviewer、规则引擎和报告组装路径。

**Non-Goals:**

- 不接入完整 DeepSearch Query Understanding、Outliner、Planner、Action Pool 或报告写作工作流。
- 不让搜索摘要直接支持 Finding，不允许补充链设置风险分。
- 不内置或申请通用 Web 搜索、Jina、向量库或 Embedding 凭据；天眼查年报使用确定性官方页面直取，不经过搜索引擎。
- 不把补充网页结果解释为对天眼查 `verified_empty` 的覆盖。
- 不改变公开 HTTP 端点或要求客户端提供新的必填字段。

## Decisions

### 1. 来源状态与覆盖完整性使用正交字段

`SourceStatus` 继续表示天眼查或补充来源本次调用是否成功、有无记录及是否降级。`CoverageCompleteness` 新增 `complete`、`partial`、`unknown`；`CoverageGapReason` 描述分页截断、字段缺失、时间不足、来源单一或来源失败。这样不会破坏 `verified_empty` 和 `source_error` 的既有含义。

备选方案是新增 `partial_records` SourceStatus，但它混合了来源健康和业务完整性，无法表达“调用成功且部分覆盖”，因此不采用。

### 2. Normalizer 保留而不猜测分页元数据

Normalizer 从常见 envelope 中读取 page、page size、total 和 has-more；无法确认时保持空值。只有 `total > returned_count` 或 `has_more=true` 才确定性标记分页缺口，不根据“恰好返回 20 条”等启发式规则自动判定。

### 3. 复用现有子模块作为查询分解边界

每个 `ReportSubmoduleRoute` 已经包含稳定 ID 和人类可读标题。补充查询以单个 `EvidenceGap` 为输入，最多生成两条首轮查询和一条补搜查询，查询必须绑定合法 domain、submodule 和 supports-field；不生成新的报告章节或研究计划。

### 4. 补充研究采用两阶段 Provider 契约

Provider 暴露 `search(query)` 与 `fetch(candidate, goal)`。Search 结果只是线索；只有 Fetch 返回正文、URL 和内容哈希后，服务才可产生 `public_web` Evidence。Provider 负责外部 DeepSearch/Web 适配，应用服务负责轮次、预算、主体校验、去重和 Evidence 映射。

完整 DeepSearch Search Agent 返回自然语言 prediction/messages，直接接入会迫使业务层解析内部轨迹并绕过 Evidence 契约，因此不采用。

### 5. 补搜只在首轮没有合格正文证据时执行一次

首轮按确定性查询并发或顺序执行；候选 URL 去重后，每轮每个缺口最多抓取两个页面。若没有页面通过主体和报告时点校验，执行一次改写查询；之后无论成功与否都停止并保留缺口。

该模式借鉴 DeepSearch Brief 的“阻断缺口只补搜一次”，但不复用其大纲、写作和报告状态。

### 6. 离线默认运行行为保持不变

`TianyanchaHybridToolset` 通过可选的 supplement service 注入新能力。未注入时不发起网络调用，现有 API、Mock 演示和 benchmark 输出保持兼容。注入后只对明确的 `partial` Coverage 运行补充链。

### 7. 天眼查年报使用专用的确定性直取 Provider

天眼查主体解析已经产生稳定的 `tyc:{company_id}`，因此年报社保补充不需要查询分解、查询改写或搜索引擎。Provider 从 `report_as_of.year - 1` 开始，默认最多向前检查 5 个年度，只构造 `https://www.tianyancha.com/annualReport/{company_id}/{year}`。请求 URL、响应最终 URL 都必须是 HTTPS 且主机精确等于 `www.tianyancha.com`；不跟随重定向越出白名单。

页面正文必须同时通过报告年度、企业名称、统一社会信用代码和公示日期校验。解析五类参保人数以及缴费基数、本期实际缴费金额、累计欠缴金额的原始披露文本；`企业选择不公示` 保持为原文和 `None` 数值，不转换为零。404、410、主体已识别但没有正文报告年度的页面，以及天眼查以 HTTP 200 回显另一年度报告的情况，均表示当前候选年度不存在并继续向前检查；窗口内均不存在时返回 `verified_empty`。反爬页、协议错误、主体错配等返回 `source_error`，但作为补充来源不会中止其他尽调数据的组装。

该 Provider 仅在实时天眼查模式默认启用，Mock 模式、显式 `scenario_id`、离线测试和 benchmark 不会访问网络；可通过配置关闭或调整 1–10 年的回看窗口。

## Risks / Trade-offs

- [第三方页面可能错误或被 Prompt Injection 污染] → Provider 输出仅作为不可信正文；应用层要求主体锚定、来源 URL、内容哈希，Reviewer 后续再审证。
- [网页内容晚于报告时点] → 有发布时间且晚于 `report_as_of` 的页面不得成为 Evidence；发布时间缺失时完整性保持 `unknown`。
- [DeepSearch 内部模块不是稳定公共 API] → Jindiao 只依赖自有 Protocol；真实适配器隔离在 `deepsearch/`，不让 SDK 类型穿透。
- [补搜增加延迟和调用成本] → 全局和逐缺口限制 query/fetch 数，轮次固定为最多两轮。
- [分页元数据结构差异] → 仅识别明确字段，未知结构不猜测，契约测试覆盖常见 snake/camel case。
- [补充来源与天眼查冲突] → 两类 Evidence 并存，由现有 Reviewer 冲突逻辑处理，不静默替换。
- [站点 HTML 结构或反爬策略变化] → 只接受带主体与报告锚点的已识别页面；无法识别时标为 `source_error`，不伪装成无年报。
- [参保人数被误解为员工总数] → 使用独立 `governance.company_profile.social_security` 字段，不覆盖从业人数，也不参与风险评分。

## Migration Plan

1. 先新增带默认值的覆盖和 Evidence 可选字段，确保旧结果可反序列化。
2. 扩展天眼查 Normalizer 与子模块 Coverage 生成，默认不启用补充 Provider。
3. 新增查询规划、两轮服务和 Provider Protocol，以 Fake Provider 完成离线测试。
4. 将可选服务接入 TianyanchaHybridToolset，并补充文档、事件和回归测试。
5. 在实时模式注入天眼查年报社保 Provider；发生问题时关闭 `JINDIAO_TIANYANCHA_ANNUAL_REPORT_ENABLED` 即可回滚，不影响 MCP 主链。
6. 通用 DeepSearch Web Search/Fetch Provider 仍留待独立变更。

## Open Questions

- 当前年报 Provider 的生产白名单已冻结为精确域名 `www.tianyancha.com`；其他通用网页补证的来源分级仍需独立冻结。
- 不同天眼查工具的真实分页字段名称和最大可查询页数需要授权联调后补充。
- 非官方负面事实是否必须双来源确认，留给通用 Web Provider 阶段制定。
