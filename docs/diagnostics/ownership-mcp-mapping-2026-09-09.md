# 股权 MCP 最小映射修复（2026-09-09）

## 真实查询范围

用户授权后，使用项目现有天眼查凭据，对同盾科技（上海）有限公司进行主体搜索、能力发现，以及以下四个工具各一次查询：

- `get_shareholder_info`：page=1、page_size=20。
- `get_actual_controller`：默认参数。
- `get_beneficial_owners`：page=1、page_size=20。
- `get_company_people`：企业名称定位。

未调用报告模型、未启动新的 multi、未更新云端部署。当前企业能力清单未列出 `get_external_investments` / `get_suppliers_and_customers`，未强行调用；相关中文映射不宣称完成真实响应验收。

不含认证信息的原始响应保存在本地忽略目录 `artifacts/diagnostics/ownership-mcp-20260909/responses.json`；本地映射结果为同目录 `mapped-ownership.json`。版本化测试使用匿名化名称，保留真实表头、重复列和数值格式。

## 根因及修复边界

1. 中文名称、持股比例、认缴出资没有适配到已有英文产品字段；现在仅在事实投影层创建映射副本，不改冻结证据。
2. 实控人表有两个相同的“人员ID”表头，旧解析器跳过整表，只留下两条控制路径，随后路径被错误当成两个人。现在允许重复表头：值一致则保留，值冲突则不选择该字段；控制路径不再生成人员占位记录。
3. 使用已披露且关系数/节点数/路径长度一致的路径填充现有 `control_depth`；它是本次查得的最大路径边数，不代表已穷尽全部穿透路径。
4. 实控人不再自动充当受益所有人。供应商/客户来源必须明确披露关联关系，才能进入关联交易。

## 真实响应本地重放结果

| 既有字段 | 结果 |
|---|---|
| shareholders | 1 名，杭州基线数字科技有限公司，100%，认缴 137323390 元，CNY |
| actual_controllers | 1 名，FutureMinds HK Limited；原始比例 1.0 的单位未明确，数值比例保持 null，原值写入既有 identification_basis |
| beneficial_owners | 1 名，保留来源“未能穿透识别……视同为受益所有人”的认定依据；不将“完整持股比例=13”强行解释为确定持股百分比 |
| control_depth | 2（本次已披露路径） |

人员画像另返回 3 名主要人员。现有产品 Schema 没有通用主要人员数组，本次不新增字段、不将高管冒充股东或受益所有人，也不新增自动调用路线；该响应仅保留作来源格式对照。

未修改 `contracts/product.py`，未新增/删除/重命名任何公开字段。修改前后 `ProductResult.model_json_schema()` 的排序 JSON SHA-256 均为 `8054f7293ad263b50d8ac7857cc1ef01f1d11295d32655ef5083815cf2b0ce2d`（基于本轮开始时用户工作区的 Schema）。

## 验证

- 7 个缺陷回归测试先全部因原缺陷失败，再通过；另补充 3 项兼容性/边界验证。
- 新增测试及事实投影、归一化、MCP gateway、固定核查组装合计 66 项通过（专项测试使用 `--no-cov`，不作为全项目覆盖率结论）。
- 扩展到报告生成、预算及 formal pipeline 的 167 项测试：164 通过、3 失败。失败均位于 `test_formal_pipeline.py` 的模型用量缺失处理断言（两个 acquisition 参数用例及一个 investigation unknown_usage 用例）；用只读导入器加载本轮两个修改模块的 HEAD 版本，同样复现这 3 项失败，未改写工作区。该既有问题未在股权映射任务中修复。
- 两个改动源码文件的 mypy 检查通过；改动文件 Ruff 检查通过。
- 保存的真实响应本地重放断言通过，无需再次消耗 MCP/模型调用。

该修复适用于后续使用新代码生成的报告；不会自动改写既有 Run 的持久化 Result。云端发布及新 Run 端到端验证尚未执行。
