# 企业信用与风控尽调报告

> [!WARNING]
> **Mock 数据提示**: 本报告包含固定版本化 Mock 演示数据, 不代表目标企业真实信息。

- 企业名称: 金调绿洲科技有限公司
- 统一社会信用代码: 91110108MA01JD001A
- 报告数据时点: 2026-08-31
- 风险总分: 80
- 决策分档: 拒绝
- 置信度: 88.00%
- 规则版本: risk-rules-v1

## 报告摘要

- 章节状态: complete
- 调查覆盖率: 100.00%
- confidence: 0.88
- coverage_ratio: 1.0
- decision_band: reject
- information_overview: {"section_counts": {}, "status_counts": {}, "total_submodules": 0}
- risk_score: 80

结论与风险:
- [非风险事实] identity 结论 finding-company (Finding: finding-company; Evidence: ev-company)
- [非风险事实] governance 结论 finding-governance (Finding: finding-governance; Evidence: ev-governance)
- [准入类] judicial 结论 finding-judicial (Finding: finding-judicial; Evidence: ev-judicial)
- [关注类] operations 结论 finding-operations (Finding: finding-operations; Evidence: ev-operations)
- [非风险事实] peers 结论 finding-peers (Finding: finding-peers; Evidence: ev-peers)

章节证据:
- [ev-company] company.registration_status 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-company
- [ev-governance] governance.shareholders 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-governance
- [ev-judicial] judicial.dishonest 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-judicial
- [ev-operations] operations.abnormal 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-operations
- [ev-peers] peers.metrics 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-peers

## 风险摘要

- 章节状态: complete
- 调查覆盖率: 100.00%
- admission_count: 1
- attention_count: 1
- non_risk_count: 3
- rule_version: risk-rules-v1
- total_score: 80

结论与风险:
- [准入类] judicial 结论 finding-judicial (Finding: finding-judicial; Evidence: ev-judicial)
- [关注类] operations 结论 finding-operations (Finding: finding-operations; Evidence: ev-operations)

章节证据:
- [ev-judicial] judicial.dishonest 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-judicial
- [ev-operations] operations.abnormal 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-operations

规则命中与追溯:
- admission.dishonest_subject: +80; Finding=finding-judicial; Evidence=ev-judicial; 存在失信被执行人记录

## 企业基本信息

- 章节状态: complete
- 调查覆盖率: 100.00%
- company_name: 金调绿洲科技有限公司
- region: 某市
- registration_status: 存续
- unified_social_credit_code: 91110108MA01JD001A

结论与风险:
- [非风险事实] identity 结论 finding-company (Finding: finding-company; Evidence: ev-company)
- [非风险事实] governance 结论 finding-governance (Finding: finding-governance; Evidence: ev-governance)

章节证据:
- [ev-company] company.registration_status 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-company
- [ev-governance] governance.shareholders 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-governance

## 司法风险

- 章节状态: complete
- 调查覆盖率: 100.00%

结论与风险:
- [准入类] judicial 结论 finding-judicial (Finding: finding-judicial; Evidence: ev-judicial)

章节证据:
- [ev-judicial] judicial.dishonest 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-judicial

## 经营风险

- 章节状态: complete
- 调查覆盖率: 100.00%

结论与风险:
- [关注类] operations 结论 finding-operations (Finding: finding-operations; Evidence: ev-operations)

章节证据:
- [ev-operations] operations.abnormal 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-operations

## 经营情况

- 章节状态: complete
- 调查覆盖率: 100.00%
- revenue_cny_10k: 12600

结论与风险:
- [关注类] operations 结论 finding-operations (Finding: finding-operations; Evidence: ev-operations)

章节证据:
- [ev-operations] operations.abnormal 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-operations

## 关联信息

- 章节状态: complete
- 调查覆盖率: 100.00%

结论与风险:
- [非风险事实] governance 结论 finding-governance (Finding: finding-governance; Evidence: ev-governance)

章节证据:
- [ev-governance] governance.shareholders 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-governance

## 同类企业分析

- 章节状态: complete
- 调查覆盖率: 100.00%
- median_revenue_cny_10k: 9600

结论与风险:
- [非风险事实] peers 结论 finding-peers (Finding: finding-peers; Evidence: ev-peers)

章节证据:
- [ev-peers] peers.metrics 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-peers

## 证据与来源说明


- [ev-company] company.registration_status 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-company
- [ev-governance] governance.shareholders 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-governance
- [ev-judicial] judicial.dishonest 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-judicial
- [ev-operations] operations.abnormal 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-operations
- [ev-peers] peers.metrics 已核验; 来源=Mock; 状态=degraded_mock; 查询时间=2026-09-03T00:00:00+00:00; 引用=mock://normal-enterprise/v1/data.json#ev-peers

## 数据缺口与人工复核

- 待人工确认: 确认历史案件履行状态

## 免责声明

本报告用于企业信用与风控尽调辅助, 不替代人工授信、审计或法律意见。
