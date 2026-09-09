# 中文风险建议与报告计数一致性 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将客户建议改为基于已审核风险的 1–2 句中文，并阻止结构化风险数量与 Markdown 数量不一致的结果输出。

**Architecture:** 保留 `ai_suggestion` 的机器枚举兼容性；在最终装配时以风险卡片、资料完整度和既有决策生成中文建议，不复制运行诊断。报告、风险卡片和版本回放共享最终视图，渲染前检查引用与计数，结果契约检查 Markdown 的摘要计数。

**Tech Stack:** Python 3.11、Pydantic、pytest。

---

### Task 1: 回归用例

**Files:** Create `tests/unit/test_product_summary.py`; modify `tests/contract/test_product_contracts.py`.

1. 构造含工商变更、实际控制人不明等风险的最终报告，断言建议包含具体风险、进一步核查、信贷策略，至多两句且无英文字母。
2. 覆盖零风险但资料不足、完整无风险、拒绝推进、英文风险标题及降级诊断场景。
3. 构造五条卡片，断言 `summary.risk_count == 5`、Markdown 为五项、回放前后不变。
4. 为不一致视图和错误 Markdown 计数增加拒绝用例。
5. Run `.venv/bin/pytest --no-cov tests/unit/test_product_summary.py tests/contract/test_product_contracts.py`，确认新增功能用例失败。

### Task 2: 最小实现

**Files:** Create `src/jindiao/reporting/product_summary.py`; modify `src/jindiao/reporting/product_assembler.py`, `src/jindiao/reporting/product_markdown.py`, `src/jindiao/contracts/product.py`.

1. 基于卡片中文标题或核查目录中文标签，选择代表性风险组成第一句；没有已审核风险时明确核查范围和资料限制。
2. 第二句包括核查事项与既有决策对应的候选信贷策略，不新增事实、批准结论或未经规则校验的额度。
3. 保留降级诊断在内部调查产物，不把英文核查编号、预算或模型失败信息拼接到客户建议。
4. 在 Markdown 渲染前调用视图校验；结果契约拒绝正文摘要的计数与结构化计数冲突。
5. 重跑 Task 1 命令，预期全部通过。

### Task 3: 联动与验证

**Files:** Modify `tests/unit/test_fixed_check_result_assembly.py`, `tests/unit/test_formal_pipeline.py`, `docs/api/README.md`.

1. 更新旧的“客户报告必须包含内部诊断”断言，转为验证中文资料限制与内部产物保留诊断。
2. API 文档说明建议语义、机器枚举的中文显示映射、同一 run_id 计数来源。
3. Run `.venv/bin/pytest --no-cov tests/unit tests/contract tests/e2e tests/integration`（无外网真实调用），并运行改动文件的 Ruff 和类型检查。
4. 检查 diff，仅保留本任务改动；不提交、不部署、不修改历史结果。

### 排查边界

本地 279 份 prototype-v1 结果中，接口计数、卡片数量、Markdown 摘要计数均一致。当前工作区不含截图页面前端源码，因此新增约束和回归测试不能替代对截图对应部署版本、同一运行结果与前端状态的排查。

### 验证记录

- 新增回归用例首先出现 10 项预期失败，修复后与产品契约测试合计 18 项通过。
- 本地完整非真实外部调用测试：1213 通过、7 失败、1 跳过；跳过的回环网络流式测试获准后单独重跑通过。
- 7 项失败在改动前提交 `2cc3f58` 的独立临时副本中全部复现：冻结发行清单校验 1 项、模型用量记录 3 项、多智能体预算 2 项、反馈访问权限 1 项。本次未修改这些既有问题。
- 改动文件的 Ruff 检查、格式检查、类型检查及差异空白检查通过。
- 未修改历史报告，未提交或部署。截图运行的实际页面差异仍需对应运行标识及部署/前端源码核验。
