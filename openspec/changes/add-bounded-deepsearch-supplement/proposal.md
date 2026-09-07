## Why

天眼查查询当前只能表达“有记录、空、能力缺失或错误”，无法区分完整结果与分页截断、字段缺失、时间跨度不足等“有记录但不完整”的情况。DeepSearch 也只用于本地 Mock 兜底，不能根据真实证据缺口执行受控补充研究。

## What Changes

- 将数据源健康状态与证据覆盖完整性分离，显式记录 `complete`、`partial`、`unknown` 及缺口原因。
- 从天眼查返回的分页元数据、子模块字段和报告时点生成结构化 Evidence Gap，而不是把任意非空结果视为完整。
- 增加面向 Evidence Gap 的有界查询规划：确定性生成原子查询，首轮无有效补证时最多执行一次改写补搜。
- 增加稳定的补充研究 Provider 契约和离线可测实现；Provider 只返回可溯源候选，不直接生成 Finding、风险分或报告。
- 将补充候选映射为统一 Evidence，经过 Evidence Store 和 Reviewer 后才能支持结论。
- 保留 `verified_empty` 的原始语义；补充来源与天眼查结果并存，不覆盖或伪装天眼查状态。
- 为后续真实 Web Search/Fetch Provider 预留来源类型、URL、发布时间和内容哈希边界，但首期不引入外部网络凭据。
- 增加生产可用的天眼查年报社保 Provider：仅访问 `https://www.tianyancha.com/annualReport/{company_id}/{year}`，从报告时点向前有界查找最近可用年报，并把社保披露补入企业基本信息。
- 无年报或最近年报没有社保信息时返回 `verified_empty`；来源异常、越出白名单或主体不匹配时返回 `source_error`，不把空白误判为零。

## Capabilities

### New Capabilities

- `bounded-evidence-supplement`: 天眼查完整性检测、结构化证据缺口、受限查询分解/改写、至多两轮补搜和补充 Evidence 接入。

### Modified Capabilities

无。当前仓库尚未将既有 change 的规格同步为主规格，本变更通过新增能力描述增量行为。

## Impact

- 影响 `contracts/evidence.py`、天眼查 normalizer/source-state/toolset、DeepSearch 适配层、应用配置、多智能体编排、Reviewer、结果与事件契约。
- 需要新增契约、单元、集成和回归测试，并扩展文档中的 DeepSearch 能力说明。
- 不改变唯一 HTTP 业务端点，不引入数据库、向量服务或新的独立进程。
- 离线 Mock benchmark 保持可复现；天眼查年报直取 Provider 已接入，通用 Web Search/Fetch Provider 后续仍通过同一稳定接口接入。
