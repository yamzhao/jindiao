# 开发规范

## 基本原则

- 使用 Python 3.11.4 和 `uv`，不混用其他依赖锁定方式。
- 应用代码采用 `src` 布局，测试不得依赖未声明的工作目录。
- 业务契约用 Pydantic 模型表达；跨层传递不使用无结构字典。
- agent-Core 编排、DeepSearch 调用、业务分析与 API 适配保持清晰边界。
- 不引入外部数据库、缓存、消息队列或向量数据库服务。
- 新增或修改公共契约时，同步更新 `docs/api/` 和契约测试。
- 架构决策使用 `docs/adr/` 中的 ADR 记录，不只留在代码注释或会议纪要里。

## 分支与提交

- 分支命名：`feature/<topic>`、`fix/<topic>`、`docs/<topic>`。
- 提交信息建议遵循 Conventional Commits，例如 `feat: add result contract`。
- 每个变更应尽量单一职责，同时提交对应测试与文档。

## 提交前检查

```bash
make check
```

`make check` 依次执行 Ruff、mypy 和 pytest。

## 测试分层

- `tests/unit/`：纯组件、治理门禁、指标和脱敏。
- `tests/contract/`：Pydantic、来源状态、SSE 和跨引用契约。
- `tests/integration/`：场景快照、DeepSearch、AgentTeams 与 Reviewer。
- `tests/e2e/`：唯一 Result API 的 JSON/SSE 行为。

功能或修复应先增加失败测试，再实现最小修改。不得通过降低覆盖率、跳过用例或放宽 schema 绕过失败。

## 安全与数据

不要提交 `.env`、真实企业数据、授权头、模型提示、私有推理或 `artifacts/`。测试数据必须是虚构、固定、版本化且带哈希的场景。发现漏洞请遵循 `SECURITY.md` 私下披露。
