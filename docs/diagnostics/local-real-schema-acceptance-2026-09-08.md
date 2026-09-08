# 本地真实联调：报告 Schema 与工商字段映射

## 本次真实调用

用户明确授权后只创建一个 Run：`dabbc8e06a4b4b9cb408058cf5b7ca00`。幂等键 `local-real-huawei-20260908-uv3Wsg-report-schema-7`。

- 真实主体：华为技术有限公司；测试申报为流动资金贷款 12 万元、12 个月，不代表真实授信申请。
- 本地 18088 代理 → 本地 8080 后端；formal + tianyancha，qwen-plus，Mock 降级关闭。未修改 ECS。
- 供应商为 Tianyancha MCP 与 DashScope；300 秒、输入 300000 / 输出 100000 / 合计 400000 token 上限未变。
- 创建 202，事件流 200，约 158 秒后结果 200；19 项检查，七步完成，进度 7/7。
- `is_mock=false`，195 条证据（天眼查 189、调用方 3、公开来源 1、派生 2），风险卡片 1；所有证据的 Mock 标志均为 false。
- 报告 63888 字符，`schema_version=prototype-v1`；报告模型一次调用通过，无 Schema 修复、无 `generation_failed`。

成本以 `investigation.json.execution_cost` 为准：采集 20547 tokens / 6 次模型 / 25 次 MCP；核查 138975 tokens / 3 次模型（含一次提交 Schema 修正）；报告 25624 tokens / 1 次模型。总计 185146 tokens、10 次模型调用。`metrics.json.token_count` 目前仅反映报告阶段，不应误报为整次费用。

## 读取与持久化验证

真实运行完成后以及 `bin/restart.sh` 重启后，各执行 24 次并发 GET，交替访问 8080 和 18088，均为 200 且内容相同。76 个事件序号连续，SSE 的 `report.completed.result` 与 GET result 一致，Last-Event-ID 重放与原事件后缀完全一致。

- 规范化结果 SHA-256：`ac75eaac935d40f9d1ec9863414bad984274a8f1b828a2f0b2d58f7677ff7f9e`
- 原始 result.json：`3d6e16da70f36422f6c3df898f4520deaf2b82f9ebd1cb6fd2abf857d74ca8cf`
- metrics.json：`63f5581cf7b08ef889525dbe16475a7387de8e81dac3fbb9eec913f8e01f0269`
- trace.jsonl：`25384bddf455eb0d7f8841d107fd9f9297a8d3591ae010d27a5ed914b5a37ec0`

后面三个文件重启前后哈希不变。七步快照顺序及 Schema 有效、所有出现的来源标签都能解析到报告证据。前五步记录真实采集/核查 Agent；末两步当前由系统流程发事件，executor_ids 为空，不虚构 Agent 名称。

## 不能等同于原型完整验收

该报告仍为 partial。公司基本情况、财务分析、银行流水三个 analysis 字段为空，其余四个有生成文本。`generation_failed` 消失说明本次生成通过输出校验，不代表所有数据完整。

后续只读检查确认工商记录中的真实事实被漏映射：28 条证据使用 `{字段: ..., 值: ...}` 表格行，原投影器只处理常规字段对象。例如登记状态和注册资本已经采集到，并非供应商未披露。

按 TDD 追加本地修复（在上述真实 Run 之后，未改写该历史 Run）：

1. 仅对工商字段表做确定性映射，保留原始证据；重复同值允许，冲突值不采用最后一行覆盖。
2. 识别 `万人民币` 等明确人民币单位，外币、非有限数、缺失值不隐式换算。
3. 有字段级来源引用和已填标量时，不再因另一查询能力缺失把同一字段标为缺失；来源错误、分页不全、集合不完整等标记仍保留。

79 项相关回归、Ruff 和两文件 mypy 通过；失败用例先于修复运行。用本次保存的工商原始证据与公开引用关系离线回放，已恢复工商状态、成立日期、注册资本、实缴资本、法定代表人、行业、经营范围、注册地址八项，输入哈希不变。此回放没有模型调用，也不是新的完整报告生成。

随后通过 `bin/start.sh` 将这两处修复加载至本地容器，宿主机/容器哈希一致：投影器 `f09b06c95120ce689f78dd73bc3989fe98c99c0503e623c65c6d33603b4ac763`，组装器 `57ac26e89fe5eeefaf6e981881dbd68d7edee92d37a9378912a061c3c025bb3a`。重建后额外 24 次结果读取及事件回放仍通过，历史结果规范化哈希不变。

单元及契约全量回归：796 项中 795 通过，一项既有失败为 `test_frozen_release_manifest_matches_versioned_components`。当前与 HEAD 的 pyproject.toml 均为 `08865f981f745a06070a973fcf03e48c40fe57c5f83cbab754cafd9746f70d56`，而旧冻结清单要求另一个哈希；未为通过测试而改写清单。

## 剩余问题

- 财务三表已保存证据目前仅含“字段=总数”的记录，不能据此断言供应商没有财务数据；需继续核对原始多表响应与 Markdown 解析（当前解析器只取首表）。本轮没有用计数冒充财务指标。
- 工商映射修复后的完整新报告尚未进行新的收费验收；历史 Run 保持原样。
- 原型的人工章节编辑、资料补充、版本管理不在本次实现范围；已知前端结果状态未赋值的问题仍需前端修复，参见 `docs/deployment/local-workbench-debug.md`。

结果只读入口：<http://127.0.0.1:18088/api/v2/due-diligence/runs/dabbc8e06a4b4b9cb408058cf5b7ca00/result>。
