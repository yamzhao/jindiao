# 原型完整验收进展（2026-09-08）

结论：后端多表解析已修复并加载到本地服务；完整端到端验收仍未通过。本轮未创建新 Run、未新增天眼查或模型调用、未修改 ECS。历史真实报告不被改写。

## 财务解析修复与验证

旧 `_markdown_records` 先过滤出全部竖线行，再在第一张表后返回。不同列数的后表丢失；相同列数的后表会错误沿用第一张表头。其结果可能只有“总数”，或把后表头当成业务记录。

本次修改 `src/jindiao/tianyancha/normalizer.py`、`gateway.py`：

- 按原始行顺序扫描每张表，保留表边界和各自列名，支持单元格中的转义竖线。
- `字段/值` 中的“总数”转为分页元数据，不计作财务明细；只有正计数、没有明细时标记 `count_only` / missing_fields / partial，并停止盲目翻页。明确零计数为 verified_empty，计数保留在 source_metadata。
- 结构化业务明细与 Markdown 计数并存时保留业务明细，不能被计数覆盖。已有 page/page_size 但没有 total_count 时合并 Markdown 总数。目录、链接及分页元数据不能冒充业务记录。

TDD 先复现失败再修改。独立代码审查发现混合载荷和分页合并边界，均补了回归。最终相关 85 项通过，包含 normalization → ProductFactProjector 的真实代码链路，以及网关第二页获取（使用离线客户端，不是收费供应商测试）。

最新完整离线回归：975 passed / 1 failed / 4 skipped。唯一失败为既有 `test_frozen_release_manifest_matches_versioned_components`：当前与 HEAD 的 pyproject.toml 哈希相同，旧冻结清单不同；未改写清单。一个回环 SSE 测试因沙箱跳过后，单独获准复跑通过。三个收费 live 测试显式关闭。

Ruff check/format 通过；mypy 对本次两份源文件及 normalizer 测试通过。扩大到整个 gateway 测试文件时，原有去重用例的五处 JsonValue 字典类型错误仍存在，未顺手修改无关测试。

## 本地部署与稳定读取

首次 `bin/start.sh` 因 Docker Hub 的 Python 基础镜像元数据超时失败，旧健康服务未被替换；同配置重试成功。验证的是实际导入的 `/usr/local/lib/python3.11/site-packages/jindiao/`，不只是 `/app/src`：

- normalizer.py：`fe663af6ba6e037c7153365ca66f412ecfb87f6a064e3d260db69deed27a487e`
- gateway.py：`ea0c99bf0d3d762af7c3ca1ef8e1b406b7c9f62287b176cfa3adc8a7d82274be`

实际安装的报告 Schema、工商投影器、报告组装器也与上一轮已修复版本一致。运行保持 formal+tianyancha、qwen-plus、300 秒、无 Mock 降级；未改预算。

对已有真实 Run `dabbc8e06a4b4b9cb408058cf5b7ca00`，更新后在 8080 与 18088 间交替 24 次并发读取，全部 HTTP 200 且一致；76 条 SSE 事件连续，完成事件结果与 GET 完全一致，Last-Event-ID 后缀回放完全一致。

历史结果规范化哈希仍为 `ac75eaac935d40f9d1ec9863414bad984274a8f1b828a2f0b2d58f7677ff7f9e`。这只证明更新后持久化与读取没有回归，不代表历史报告已经使用最新解析器重新生成。

## 浏览器验收：未通过

实际本地页面 `http://127.0.0.1:18088/workbench/50c54f008aed41df881c743aa2d526b7` 空白，浏览器控制台再次记录：

```text
TypeError: Cannot read properties of null (reading 'report_markdown')
C7o (assets/index-c5b1f127.js:3441:7721)
```

由已观察到的 `/workbench/{run_id}` 路由打开已有成功 Run `dabbc8…` 时，前端跳回 `/workbench`，没有加载它的报告。当前工作区未找到此 xingyao 前端源码，bundle 也未声明 sourceMappingURL。未修改线上/本地压缩 bundle 或用伪造结果掩盖异常。

前端源码到位后的明确修复点：

1. 详情初始化按 run_id 查询状态及结果，不以浏览器本地案件列表为唯一入口。
2. result 尚未就绪时不读取 `report_markdown`，展示加载/失败状态；HTTP 202 不当作完成。
3. 终态正确处理 `report.completed` 的结果，或 GET `/api/v2/due-diligence/runs/{run_id}/result` 并写入组件状态；失败终态展示后端错误，不进入空报告组件。
4. 验证七步快照、报告与风险卡片来自同一 Run，刷新、回放后仍可展示。

页面最近这条 `50c54…` 还独立存在 `entity_not_found`：后端没有完成主体解析；表格信用代码为 `1`，不是此前 amount/term 的数字字符串错误。真实测试请填真实统一社会信用代码，或留空按企业名称查询，不要填写占位代码。

## 剩余验收条件

- 需要实际前端源码路径或仓库，才能修复并验收页面。当前原型 HTML 不是该线上应用的源码。
- 需获准执行最新代码的一次真实生成：华为技术有限公司、测试流动资金贷款 12 万元/12 个月，Tianyancha MCP + DashScope qwen-plus，300 秒/400000 token 上限。上一轮仅一次的明确授权已执行，未擅自追加。
- 原始 MCP 响应未持久化，旧记录只有解析后证据，因此无法离线还原被旧解析器丢掉的财务明细。必须区分“解析测试通过”和“供应商真实财务明细已获取”。
- 人工填写、资料补充、报告版本等独立产品流程仍不在本次后端稳定读取修复范围；不以原型演示数字补齐真实缺口。

## 继续请求后的只读检查

用户要求继续白屏修复及一次真实生成后，进一步只读检查了本地 `Documents/td`、Downloads 和 ECS。ECS Nginx 明确从 `/home/dist` 提供前端，另有 `/home/dist.zip`；部署目录只有 HTML、静态资源和编译 bundle，未发现 package.json、TSX/JSX/Vue 源文件或 source map。未修改服务器文件或服务。

本地实际安装包仍与上文五份已修复代码哈希一致，无活跃 Run。已为本轮准备固定幂等键 `local-real-huawei-20260908-uv3Wsg-full-acceptance-8`，但自动审批在脚本执行前拒绝了新收费请求：要求用户明确确认将本次企业/测试贷款参数发送至 Tianyancha MCP 与 DashScope 并可能产生费用。因此没有创建该 Run，没有新增供应商调用，也没有通过浏览器或其他方式绕过审批。

当前继续条件仍为：提供实际 xingyao 前端源码；明确批准本次精确测试参数、外部目的地和费用范围。不能把已定位白屏原因视为已实现修复。

随后用户明确回复“允许”，本轮幂等键已执行且仅执行一次，Run 为 `c99a8745ffc941049f9fc0cbdbef05f1`。最新代码真实生成未通过：批量核查校验被拒后修正，约 213 秒触发 token 预算错误，没有新报告。已落盘 token 为下界 212685，失败末轮 usage 未完整留存；失败指标中的零值不可信。参见 `docs/diagnostics/local-real-full-acceptance-8-2026-09-08.md`。不得复用该次授权再创建另一个收费 Run。
