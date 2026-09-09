# ECS events/result 新收费 Run 验证

结论：按用户接受 `partial` 的后端接口标准，本次 ECS 新 Run 验证通过。不是读取本地历史成功报告代替 ECS 验证，也不代表所有企业或资料完整性均已验收。

## 执行范围

- 用户明确批准一次收费任务：华为技术有限公司，测试流动资金贷款 12 万元、12 个月，single。
- 运行 ID：`3dff5c977c014cbebd30df0e5bab9d9d`。
- 幂等键：`ecs-real-huawei-20260908-verify-events-result-01a07f16`，仅一次 POST；未重复创建。
- ECS 当前配置：`deepseek-v4-flash-0731`、formal+tianyancha、token 数值门槛关闭、600 秒超时、160 次模型请求上限。配置为用户既有部署，本次未修改。
- 在 ECS 中通过公网地址 `http://1.95.121.114/api/...` 创建并订阅，结果读取交替验证公网代理与 `127.0.0.1:8080` 直连。
- 完整报告和事件体仅在 ECS 内存中校验；本地只接收状态、计数、哈希与用量摘要。未下载完整企业报告，未重启或部署服务。

## 验收结果

| 项目 | 结果 |
| --- | --- |
| 创建 Run | HTTP 202 |
| 执行中的 result | HTTP 202 |
| 实时 events | HTTP 200，text/event-stream |
| 最终 result | HTTP 200，application/json |
| 状态 | partial / terminal |
| result_available | true |
| error | 无错误代码 |
| 进度 | 7/7，100% |
| is_mock | false |
| generation_failed | 无 |
| 报告字符数 | 152450 |
| 证据数 | 519 |
| 风险卡片数 | 1 |
| 事件数 | 75，序号连续 |
| 实时事件与终态重放 | 完全一致 |
| report.completed 中的结果与 GET result | 完全一致 |
| 断点回放 | Last-Event-ID 后缀精确一致 |
| 并发读取 | 12 次全部 HTTP 200、相同结果（4 个并发工作线程） |

报告约 130.3 秒产生；包含读取/回放检查的验证总耗时约 148.1 秒。

结果规范化 SHA-256：`53d1a5abcc2c6690df406e481aeeb507dee644313d7e9bc1e306f953d13ad826`。

## 实际计量

- 模型请求 9 次，成功/有供应商计量均为 9 次。
- 输入 209803 tokens，输出 15249 tokens，总计 **225052 tokens**。
- MCP 41 次；Schema 修正 0 次。
- `provider_usage_complete=true`。
- 金额和期限在 ECS 内验证为 120000 元、12 月，未由模型改变。

费用按供应商账单结算；此处仅记录后端实际用量。

## 使用边界

`partial` 可作为有报告可读取、可展示的终态，不应被前端直接当作执行失败。本次没有 generation_failed，但仍可能存在资料缺口；不把 partial 当成资料齐全。

公网 `/ping` 和 `/openapi.json` 当前返回前端 HTML，不能用它们的 HTTP 200 判断后端健康。本次后端健康通过 ECS 回环 `/ping` 验证；业务验收实际覆盖的是公网 `/api/v2/due-diligence/runs/...` 路由。

本次没有验证部署脚本执行过程、维护排空、回滚、重启恢复或前端页面渲染。

状态链接：<http://1.95.121.114/api/v2/due-diligence/runs/3dff5c977c014cbebd30df0e5bab9d9d>。
结果链接：<http://1.95.121.114/api/v2/due-diligence/runs/3dff5c977c014cbebd30df0e5bab9d9d/result>。
