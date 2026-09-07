# 可重复本地报告反馈 Demo

本 Demo 使用固定虚构企业和司法 source_error，证明已有缺口的展示位置能经过真实评测、手动应用并影响下一次报告。只在本地单进程、单操作人环境启用，不修改工作区已有 active，不依赖模型、天眼查或外部服务。

## 一条命令完成验收

在项目目录执行（输出目录必须尚不存在）：

```bash
uv run python scripts/run_reporting_feedback_demo.py --output "$(mktemp -d)/feedback-demo"
```

脚本通过进程内 ASGI HTTP 调用实际 Run/feedback/detail 路由，通过独立子进程运行 CLI。它固定报告时间和合成来源状态，不监听端口，不读取 `.env` 文件；使用输出目录里的 `artifacts/` 和 local RunRepository。输出中的 `verified=true` 表示脚本断言通过，不表示真实网络部署或外部数据验证通过。

| 输出 | 用途 |
| --- | --- |
| `summary.json` | 九例数量、源 Run 未变、四阶段版本、最终基线状态 |
| `before.md` / `after.md` / `diff.patch` | 源安全视图上的实际前后报告与差异 |
| `evaluation.json` | 源案例和八个独立固定案例的全文、指标与保护检查 |
| `apply-receipt.json` / `reset-receipt.json` | CLI 返回的 active binding、reason 和时间 |
| `source-*` / `before-apply-*` / `after-apply-*` / `after-reset-*` | 各阶段 Run 与 Result JSON |
| `artifacts/<run_id>/` | 安全 replay、result、report、metrics、trace、manifest |
| `artifacts/reporting-demo/` | 原子保存的当前状态和不可变候选文件 |
| `artifacts/run-state/` | local RunRepository 和事件存储 |

脚本验证：提交反馈后新 Run 仍为 1.1.0；apply 后新 Run 的报告等于实测 after，风险决策和证据相同；reset 后新 Run 恢复 1.1.0 与原报告；源 Run 的结果和事件序列始终不变。候选版本是 `1.1.N`，N 由候选 ID 计算，不能假设是 1.1.1。重复指定同一输出目录会拒绝，避免覆盖证据。

## 手动核对和服务演示

对脚本返回目录中的状态运行（将路径替换为实际输出，使用规范化绝对路径）：

```bash
DEMO_ROOT='<输出目录>/artifacts'
uv run python -m jindiao.reporting.demo_cli --demo --artifact-root "$DEMO_ROOT" show
```

脚本已执行 reset，旧候选因 revision 变化不能重新应用。需要再次演示时使用新的输出目录，或为新受理 Run 重新提交反馈。

手动服务请先准备一个专用空目录，并保持 `DEMO_ROOT` 与后续 CLI 一致：

```bash
DEMO_ROOT="$(uv run python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="jindiao-feedback-")).resolve())')"
JINDIAO_ENV=development JINDIAO_STORAGE_BACKEND=local \
JINDIAO_REPORTING_DEMO_ENABLED=true JINDIAO_ARTIFACT_ROOT="$DEMO_ROOT" \
MODEL_PROVIDER=offline_mock MODEL_NAME=deterministic-mock \
JINDIAO_DATA_SOURCE_MODE=mock JINDIAO_AGENT_RUNTIME_MODE=deterministic_harness \
uv run uvicorn jindiao.api.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

不要使用 `make dev` 的全网绑定来展示反馈管理。接口只允许回环对端、允许的 Host 和同源 Origin，并拒绝转发头；这不是生产认证，也不支持经过 BFF/AgentArts 代理访问反馈路由。关闭 Demo 返回 503；环境或存储组合不支持时拒绝启动。

创建 Run 时使用相同的 `X-Hw-Agentgateway-User-Id` / `X-Hw-Agentarts-Session-Id` 归属上下文，等 completed/partial 后提交：

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v2/due-diligence/runs/$RUN_ID/feedback" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: placement-1' \
  -H 'X-Hw-Agentgateway-User-Id: demo' -H 'X-Hw-Agentarts-Session-Id: demo' \
  -d '{"kind":"gap_disclosure_placement","text":"司法缺口请就近披露","target_section_ids":["judicial-risk"]}'
curl -sS "http://127.0.0.1:8000/api/v2/skill-evolutions/$EVOLUTION_ID?include=reports" \
  -H 'X-Hw-Agentgateway-User-Id: demo' -H 'X-Hw-Agentarts-Session-Id: demo'
uv run python -m jindiao.reporting.demo_cli --demo --artifact-root "$DEMO_ROOT" apply "$EVOLUTION_ID" --reason '已核对真实对比'
uv run python -m jindiao.reporting.demo_cli --demo --artifact-root "$DEMO_ROOT" reset --reason '恢复基线'
```

只有原报告已披露且映射到目标章节的缺口才能改善。正常无缺口报告返回 rejected 是预期行为。上面服务命令不注入司法失败；一条命令的脚本专门注入合成 source_error，保证完整可重复对比。

## 判读范围

八个独立案例固定为 normal、single-gap、multiple-gaps、absent、empty、mock、risk-review、global；suite 保存显式 oracle 与哈希，用户反馈不会生成或修改固定集。分母为零时 rate=null/applicable=false。合法新增块必须保留原文和章节，移除后恢复原报告；任何风险分、证据或 Mock 提示改动均拒绝。

本地 JSON 状态只保留当前应用/恢复收据，候选文件保留完整反馈、快照和评测。没有完整历史审计、双角色权限、SQLite 注册表或 HTTP 发布路由。旧 skill_feedback 和旧 Python coordinator 明确 rejected/use_feedback_api；包内旧 schema/示例仅保留兼容 ABI，不是当前 Demo 的 API 或评分证据。

可复用 Skill 包仍标记 ABI 1.0.0；实际报告策略在 Run 接受时绑定为 1.1.0 或候选 1.1.N。原 Frozen v1 Skill 包按原哈希归档在 `release/reference/skills/team/feedback-evolved-reporting/`；更新的说明不改写原 benchmark 记录，也不能据此重新宣称旧协作增益。

本轮测试与边界见 [验收记录](reporting-feedback-verification.md)，字段和错误码见 [API 文档](api/README.md)。
