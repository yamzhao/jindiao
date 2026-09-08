# 有界生成修复后的真实验证：预检拒绝，未通过

## 本次授权与结果

用户明确允许将华为技术有限公司、测试流动资金贷款 12 万元／12 个月发送至 Tianyancha MCP 与 DashScope qwen-plus，保持 300 秒及输入 300000／输出 100000／合计 400000 tokens 配置。

仅创建一次 Run：`1017bce4937c4b6a9476d3885ed587c1`。
幂等键：`local-real-huawei-20260908-uv3Wsg-bounded-generation-9`，只对应该 Run。未调整预算、未启用 Mock、未修改 ECS、未追加第二个收费任务。

创建返回 202，SSE 返回 200。约 21.17 秒采集完成，记录 517 条证据、41 次 MCP 调用。核查 Agent 完成一次读取快照的模型请求后，下一次大请求在发出前被本地预算检查拒绝。约 23.5 秒返回终态：

```json
{
  "status": "failed",
  "result_available": false,
  "error": "orchestration token budget exhausted before model request"
}
```

结果接口 HTTP 500，没有新报告、没有 report.completed，不能用旧报告代替本次验收。

## 计量验证

模型完成事件、metrics.json.execution_cost 与 API error.details.execution_cost 三处一致：

- llm_requests / successful_llm_requests / provider_usage_requests：均为 7。
- input_tokens：54476。
- output_tokens：614。
- total_tokens：55090。
- mcp_calls：41。
- provider_usage_complete：true。
- unknown_usage_requests、reserved_input_tokens、reserved_output_tokens：均为 0。

核查阶段已发生的一次请求输入为 16648、输出为 17；没有发生修正请求。被拒绝的大请求没有派发，因此其用量不包含在以上数字中。

这验证了调用前拦截与失败计量，但没有验证报告生成成功。

## 输入估算的当前证据边界

已用输入 54476，整次运行剩余输入额度为 245524。当前估算器使用完整序列化 UTF-8 字节数及协议余量，而不是供应商实际 tokenizer 结果。预检判定下一次请求无法在剩余额度内派发；不能将这个估算当作实际已消耗 tokens。

本次日志和失败产物未保存被拒请求的具体 estimated_input_tokens/requested_output_tokens，也未持久化完整冻结快照。当前 BudgetLedger._exhaust 抛出时包含这些请求估算，但框架结束后的错误重建仅保留账本状态，丢失了具体拒绝明细。日志只有计量、状态和计数，无法离线还原这次模型输入。

因此目前不能确定主要瓶颈是实际事实/工具 Schema 仍过大，还是保守估算过严。此前“420 条证据／4 个重复子模块压缩 86.32%”是构造样本，不代表这次 517 条证据的实际压缩比例。不可据此直接调低估算系数或提高预算。

下一步应先持久化安全的预检明细和可回放的冻结输入，再测量 facts、证据元数据、Schema、协议余量各自占比，完成离线回归后再考虑新的真实验证。本轮未作未经验证的后端行为修改。

## 接口与留痕

- 本地 8080 与代理 18088 均返回相同 HTTP 500、错误原因和完整计量。
- 52 条 SSE 事件连续；Last-Event-ID=27 的后缀重放精确一致。
- 历史结果不变，本次失败不被包装为成功。

哈希：

- error.json：`d3ec3d45ca998286d4b791a4522bce782a3f3d95f6326d3a73edb9b2d129e716`
- metrics.json：`580eff194773d4eda4bf917a2f1ec6db2b40a1ce0a47c7db0a090e9684a288b0`
- trace.jsonl：`6d3a68013ec613648d61068c95b5fa6b95a3c263254549286884b8a1a80f5300`

本地状态接口：<http://127.0.0.1:18088/api/v2/due-diligence/runs/1017bce4937c4b6a9476d3885ed587c1>。
