# 本地状态与既有报告读取稳定性验证

本轮按原型的执行过程展示需求继续修复后端，未创建新 Run、未调用模型或天眼查、未修改 ECS 或原型前端。

## 已修复

1. `RunResource.progress` 现在按七个产品步骤的最新状态去重统计。计划产生后为 0/7；只把 completed 步骤计入成功数。重复快照和旧序列不会重复计数，失败或取消不冒充成功。
2. 重启/重建后从既有 `execution.*` 历史恢复派生进度，只重放这部分派生状态；不重复累加原有报告、复核和返工计数。并发首次查询使用每 Run 的恢复锁。
3. Agent 执行超时归为 `agent_execution_timeout`，结果接口返回 HTTP 504，包含 agent_id、phase 和 timeout_seconds，且 recoverable=true。主动取消仍是取消，未知异常仍保留安全通用错误。旧历史错误不会被改写。

## 自动化验证

- 测试驱动：四个进度/恢复用例和两个超时用例先失败后通过。
- 相关回归：92 passed。
- 完整离线回归：937 passed / 1 failed / 4 skipped，覆盖率 87.64%。唯一失败仍为未修改的 pyproject.toml 与旧冻结清单哈希不一致。
- 因沙箱回环限制跳过的 BFF 流式测试单独复跑：1 passed。
- Ruff、mypy（本轮十个相关文件）通过；没有调低校验门槛或改写冻结清单。

## 已有真实报告的 HTTP 验证

Run：`6362504a4090471690457e629ced6a1a`，来自前序真实调用，不是本轮重新生成。

更新前与本地容器重建后，分别在 8080 直连接口和 18088 联调代理间交替执行 24 次并发结果读取，全部 HTTP 200、内容一致，共 48 次。75 个 SSE 事件连续，`report.completed` 中的结果与 GET result 完全一致；Last-Event-ID 断点重放与原事件后缀完全一致。

| 项目 | 更新前 | 更新后 |
| --- | --- | --- |
| Run 状态 | partial | partial |
| result_available | true | true |
| 总体进度 | 0/0，0% | 7/7，100% |
| 结果规范化 SHA-256 | `870014785e6209b1b62ad0431a348bcf89b179ec9c1abce0fa151665bb6d1592` | 相同 |
| 原始 metadata / events / result 文件 | 原文件 | SHA-256 全部不变 |

原始文件哈希：

- metadata.json：`694184d5ace4d850187178f4a9f6463e3cf348c84ab2d721668d163504fb233b`
- events JSONL：`e1451a3c0388063c45545af50b9acec55d16cd341834d0b15ffed3de98a3b8ed`
- result.json：`abdb744fb77d57ef5963fa75912d8a4a252d5918dc045751c2b917cb7886bd8c`

## 验收边界

这证明了既有数据的读取、事件重放和状态恢复一致性，不代表新的真实报告生成已经通过。该历史报告仍保留七个章节的 generation_failed 标记，本轮没有隐藏或修正历史业务结果。100% 表示执行步骤结束，不代表资料完整或无风险。

本地仍为 formal + tianyancha，300 秒 Agent 超时、输入 300000 / 输出 100000 / 总计 400000 token 预算保持不变，服务及代理健康。最新版本的整条真实生成验收仍等待明确的收费调用授权。

接口：`http://127.0.0.1:18088/api/v2/due-diligence/runs/6362504a4090471690457e629ced6a1a/result`。
