# 参赛演示检查清单

- 状态：Frozen v1
- 范围：进阶题二与加分项
- 更新：2026-09-06（反馈链路；其余 Frozen v1 记录为历史验证）

## 演示前准备

- [x] 数据、规则、三个 Skills、离线模型配置和 60 条评测记录已写入 `release/frozen-manifest-v1.json`。
- [x] `.env.example` 只有空凭据占位，真实凭据仅放本机 `.env`。
- [x] `make check`、OpenSpec strict、纯 Mock JSON/SSE、隔离安装和仓库安全扫描已通过。
- [x] 天眼查授权聚合验证及完整 result 实时混合链路已通过。
- [ ] 在装有 Docker 的最终演示机执行容器冷启动（当前验证主机无 Docker CLI）。
- [ ] 演示前确认网络与天眼查额度；网络不可用时直接切换冻结 Mock 场景，不伪装为实时数据。

## 建议演示顺序

1. 用 30 秒说明架构：v2 Run 资源 API + v1 result 兼容接口、AgentTeams 并行取证、Reviewer 独立审证、确定性规则、DeepSearch Mock 补充。
2. 运行 `make mock-demo`，展示 `evidence-conflict` 场景的冲突检出、定向 RepairTask、人工复核分档和完整 Markdown。
3. 启动 `make dev`，对同一 URL 分别切换 `mode=single|multi`，再使用 `Accept: application/json` 和 `Accept: text/event-stream`，展示模式回显与最终 Result 语义一致。
4. 已配置授权时运行 `make verify-tianyancha`，只展示主体来源、能力数量、四领域路由数、记录状态和有效空结果，不展示凭据或原始响应；完整实时 result 演示还需设置 `JINDIAO_DATA_SOURCE_MODE=tianyancha`。
5. 对一个未指定 `scenario_id` 的公开企业调用 result；必要时设置 `allow_degraded_mock=true`，展示天眼查全部业务领域失败仍可生成八章报告、真实主体保持不变，以及 Markdown 顶部唯一 Mock 提示。
6. 打开四个 `skills/*/*/SKILL.md`，说明仓库无关挂载、输入输出 schema、示例、eval、版本与 changelog。
7. 运行 [独立目录反馈 Demo](reporting-feedback-demo.md)，展示 before/after/diff、九例实测、本地 apply 收据、新 Run 版本和 reset 后恢复证据；旧 `skill_feedback` 明确拒绝。仅证明展示位置改善。
8. 展示 `benchmarks/reference/v1-comparison.md` 和 `v1-records.jsonl`：10 场景 × 3 重复 × 2 模式，主质量分绝对提升 0.105，95% 配对区间不跨 0。

## 现场命令

```bash
make install
make check
make mock-demo
make benchmark
uv run python scripts/compare_agents.py --manifest benchmarks/manifest.json --output benchmarks/results/latest
make dev
```

天眼查授权只写 `.env`，随后执行：

```bash
# .env: JINDIAO_DATA_SOURCE_MODE=tianyancha
make verify-tianyancha
```

Docker 演示机执行：

```bash
docker compose build --no-cache
docker compose up -d
docker compose ps
curl -X POST http://127.0.0.1:8000/api/v1/due-diligence/result \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"enterprise":{"company_name":"金调双源制造有限公司"},"scenario_id":"evidence-conflict"}'
docker compose down
```

## 必须主动披露

- Markdown 在报告顶部集中提示一次 Mock 属性；结构化结果中每条 Mock Evidence 仍有 `is_mock=true` 与 `mock://` 引用。
- `verified_empty` 是成功核验为空，不能用 Mock 覆盖；`source_error` 不是“无风险”。
- 实时混合报告包含固定 Mock 补充时通常返回 `partial`，这是可信披露，不是运行失败。
- v1 协作增益只预声明质量为主指标。Mock 毫秒级时延噪声较大，不把它包装成可靠性能收益。
- 系统用于尽调辅助，不替代人工授信、审计或法律意见。

## 评分证据定位

| 评分点 | 证据 |
| --- | --- |
| agent-Core / DeepSearch | `src/jindiao/orchestration/`、`src/jindiao/deepsearch/`、`requirements.txt` |
| 多 Agent 协作增益 | `benchmarks/reference/`、`docs/evaluation/README.md` |
| 三个可复用 Skills | `skills/agent/`、`skills/team/` |
| 反馈自演进 | `src/jindiao/reporting/replay.py`、`demo_store.py`、`demo_cli.py`、`scripts/run_reporting_feedback_demo.py` |
| v2 Run 资源与 v1 result 兼容接口 | `src/jindiao/api/app.py`、`docs/api/README.md` |
| 单机无数据库 | `Dockerfile`、`compose.yaml`、`docs/deployment/README.md` |
| 开源可复现 | `README.md`、`Makefile`、`uv.lock`、`LICENSE`、`CONTRIBUTING.md` |
| 完整验收 | `docs/verification-report.md`、`release/frozen-manifest-v1.json` |
