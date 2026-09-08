# 最新代码真实生成验证：未通过

用户明确确认企业参数、外部目的地及可能产生费用后，仅执行一次真实 Run：`c99a8745ffc941049f9fc0cbdbef05f1`。

幂等键：`local-real-huawei-20260908-uv3Wsg-full-acceptance-8`。后续检查确认该键只对应一个 Run，无活跃任务。没有二次提交、提高预算、Mock 降级或 ECS 修改。

## 测试范围和版本

- 华为技术有限公司，测试流动资金贷款 12 万元、12 个月；信用代码不填，按公司名称解析。
- 本地 18088 代理连接本地 8080 后端；Tianyancha MCP + DashScope qwen-plus。
- 配置保持 300 秒、输入 300000 / 输出 100000 / 合计 400000 tokens，不增加限额。
- 实际安装包已核对：多表解析器 `fe663af6ba6e037c7153365ca66f412ecfb87f6a064e3d260db69deed27a487e`；gateway `ea0c99bf0d3d762af7c3ca1ef8e1b406b7c9f62287b176cfa3adc8a7d82274be`。报告 Schema、工商映射和缺口标记修复同样在实际安装包中。
- 本轮另复跑 normalizer、product_fact_projector、product_generator 的 59 项离线回归，全部通过；这不能替代真实生成验收。

## 实际结果

创建返回 202，SSE 返回 200。约 19.5 秒采集完成；119.15 秒完成一轮大上下文核查响应。213.41 秒 Run 失败：

```json
{"code":"agent_execution_failed","message":"orchestration token budget exhausted"}
```

状态 `failed`，`result_available=false`，结果接口 HTTP 500，进度 0/7；没有 `report.completed` 事件，也没有 result.json 或新报告。不能沿用旧 Run 的成功结果声称此次通过。

## 失败链路

运行日志记录首次整批提交的三个拒绝原因：

| 核查项 | 原因 |
| --- | --- |
| cash-flow-pressure | inconclusive 未提供具体 Evidence 缺口 |
| revenue-anomaly | 引用超出该核查允许范围的 Evidence |
| receivables-cashflow-divergence | inconclusive 未提供具体 Evidence 缺口 |

整批提交被原子拒绝后，模型再次处理完整上下文进行修正，随后预算记账报错。没有放宽证据范围、自动补造缺口或合成遗漏判断。

核查阶段已落盘的两次模型调用输入分别为 7374、162378 tokens，输出分别为 17、4615。六次采集/补充调用加这两次核查完成事件合计：输入 207456、输出 5229、总计 **212685 tokens**。

这是已落盘完成事件的下界，不是最终总用量。`BudgetedModel.invoke` 在供应商返回之后才调用 `BudgetLedger.record_llm_usage`；末轮在此处抛出预算错误，未成为 `model.request.completed` 事件。失败产物 `metrics.json` 又记录 token_count=0、tool_calls=0，因此不能据此声称零费用或精确总用量；最终费用需以供应商账单为准，也不能凭配置上限断言实际用量绝不超过阈值。

## 失败接口的只读验证

- 8080/18088 交替八次并发读取，均返回相同 HTTP 500 及上述明确错误。
- 53 条事件序号连续，最后为 run.failed；Last-Event-ID=27 的回放与原后缀完全一致。
- 这仅证明错误响应和留痕一致，不证明后端业务数据稳定生成。

留痕哈希：

- error.json：`54cebbf33577e37ddd6368c94fb4d83018cec2bea68a359c78530ae92c1764ef`
- metrics.json：`a3947902df29871650216af2b584ca5e0fab56a865209e6a27ea0db9fc1b907a`
- trace.jsonl：`776894797b63ec097db8a94005d7deced1af220bfb8cf5e1c65ce489963b7338`

## 下一步边界

不再追加收费请求。需要先评估并离线验证：核查上下文无损去重/按核查约束组织，减少整批修正时重复发送；将可表达的引用范围和 inconclusive 缺口约束前移到工具 Schema；请求发出前预留 token 预算，并在预算失败时仍保存实际供应商 usage。该架构/计量工作尚未在本轮实现，不应以继续重试代替。

前端白屏修复仍需要实际 xingyao 源码。已确认 ECS 只有 `/home/dist` 编译产物，未改写。完整端到端验收仍未通过。
