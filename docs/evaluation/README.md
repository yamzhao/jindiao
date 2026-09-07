# Single / Multi 配对评测

- 回归清单：`benchmarks/manifest.json`
- deterministic 回归：`make benchmark`
- CLI：`uv run python scripts/compare_agents.py --manifest benchmarks/manifest.json --output benchmarks/results/latest`
- 凭证化 paired smoke：`JINDIAO_RUN_LIVE_PAIRED=1 JINDIAO_LOG_LEVEL=WARNING .venv/bin/pytest -q --no-cov -s tests/integration/test_live_paired_comparison.py`

## 公平比较定义

`mode=single|multi` 只表示 investigation 拓扑。Enterprise Context Agent、DeepSearch Agent、天眼查 MCP 和年报 Tool 属于共享 acquisition，不计入任一臂的调查 Agent 数。

`PairedComparisonRunner` 对每个 case/repetition：

1. 只执行一次 Enterprise Context Agent、允许的 DeepSearch 补充和 `ContextFreezer`；
2. 生成一个不可变 `EnterpriseContextSnapshot`；
3. 在分叉前校验 `ComparisonFingerprint`；
4. 让 single 和 multi 分别读取同一个 snapshot ID / SHA-256；
5. 给两臂数值相同的总 investigation budget；
6. 分别保留成功、partial、schema 失败、超时和预算耗尽样本。

指纹要求两臂的代码/契约版本、报告时点、provider/model、采样参数、公共 Prompt 核心哈希、15 项 CheckCatalog、8/48 ReportCatalog、分析预算、规则与 evaluator 版本一致。允许不同的只有 mode、协作拓扑和已记录的角色 Prompt。

成本分为：

- `shared_acquisition_cost`：Context Agent、DeepSearch Agent、MCP、年报 Tool、共享模型与 Token，只按 pair 记录一次；
- `single_investigation_cost`：一个调查 Agent 的模型、Token、schema 重试、快照读取和时延；
- `multi_investigation_cost`：Leader、4 个专业 Agent、Reviewer、通信和返工共用唯一账本的全部实际成本。

## 正式门禁

只有以下条件全部满足，pair 才可标记 `formal_eligibility.eligible=true`：

- 两臂指纹等价并引用同一个冻结快照；
- 两臂都是 `formal_agent_run=true` 的非 fake/offline 模型运行；
- 两臂都有成功 LLM request、provider usage 和非零 Token；
- 两臂都提交完整且 Evidence 合法的 15 项固定核查；
- 两臂实际开销没有超过同一预注册预算。

`eligible=true` 只说明该 pair 可作为正式样本，不等于已经证明 multi 有协作增益。正式增益结论还必须完成 manifest 预注册的全部场景、重复次数、主指标、权重和阈值，且不能事后删除失败样本或更改口径。阈值未达到时必须输出“未证明协作增益”。

## 指标与公开产物

质量总分公式保持：

```text
coverage × 0.30
+ evidence_support × 0.25
+ risk_quality × 0.20
+ conflict_detection × 0.15
+ report_structure × 0.10
```

同时报告固定核查覆盖、Evidence 充分性、schema/任务成功率、首条有效提交和端到端时延、LLM 请求、输入/输出/总 Token、冲突、复核和返工。每个分项保留原始计数，加载记录时会重算并拒绝不一致数据。evaluator-only `expected/` 只能在业务运行结束后读取。

输出目录包含 `records.jsonl`、`summary.json`、`comparison.md`、`failures.json` 和各 Run 的脱敏 artifacts。公开产物只记录 Prompt/目录版本与哈希、受限判断摘要、Evidence ID、快照哈希和 usage；不记录 Prompt 正文、私有 reasoning / chain-of-thought、密钥或未脱敏 MCP/网页响应。

## 两类现有证据

### Deterministic regression v1

当前冻结清单包含 10 个虚构场景、随机种子 20260903、温度 0、3 次重复和 2 种模式，共 60 条记录。`scripts/compare_agents.py` 是 `jindiao.evaluation.benchmark.main` 的薄入口，不包含第二套评分公式。

冻结参考结果：single 质量 0.65625，multi 质量 0.76125，绝对差 0.105，配对差值 95% 区间 `[0.046624, 0.163376]`；成功率均为 0.8。multi 平均检出冲突 0.3 次/样本并返工 0.6 次/样本。

这些记录来自 `formal_agent_run=false` 的 deterministic harness，用于验证契约、评分、冲突/返工逻辑和失败样本保留，不能作为真实模型 Agent 协作增益证据。冻结副本在 [`benchmarks/reference/`](../../benchmarks/reference/)，并受 [`release/frozen-manifest-v1.json`](../../release/frozen-manifest-v1.json) SHA-256 校验。

### 2026-09-05 formal paired smoke

已完成一组凭证化、真实模型 paired smoke：

| 项目 | 结果 |
| --- | --- |
| Pair | `pair:e3db8bc1c6d9693629f6d5c2` |
| Snapshot | `snapshot:b713bc490afe3b8954da2108` |
| Snapshot SHA-256 | `b713bc490afe3b8954da21087574c4d94a51681ccb66b28dfaf60f44d1646613` |
| 模型 | OpenAI-compatible `qwen-plus` |
| 共享核查 / 预算 | 15 项；每臂最多 1,500,000 总 Token |
| Single 实际成本 | 19 次 LLM；1,124,467 Token |
| Multi 实际成本 | 43 次 LLM；828,676 Token |
| 门禁 | `formal_eligibility.eligible=true` |

两臂完成全部 15 项核查，且使用同一 snapshot、模型、目录和分析预算。该单组 smoke 证明真实 Agent 执行、资源计量和公平门禁成立；因为没有完成预注册的多场景重复试验，它不产生“multi 优于 single”的正式结论。
