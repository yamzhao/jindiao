# BFF 本地安全联调记录（2026-09-06）

最新检查时间：2026-09-06 18:53 CST。范围为独立后端代理，未开放公网明文端口，未创建或修改 AgentArts 资源，未调用真实模型或天眼查。工作区还有报告反馈闭环的并行修改；镜像基于下述最小源码快照，测试计数反映各自命令执行时的工作区状态。

## 18:53 续验：Docker 与 TCP 已完成

| 验证 | 最新结果 |
| --- | --- |
| BFF 定向 pytest（包括真实 TCP） | **44 passed，0 skipped** |
| BFF Ruff / mypy | 全部通过，mypy 检查 11 个文件 |
| Docker 构建 | `linux/arm64`，`jindiao-bff:local-20260906-ecui` |
| Docker 镜像 ID | `sha256:a59a71bd5ce46c346d06d67942fac28d9afd234b53dd2438a3edc1f9e7b6d2a8` |
| 镜像大小 | 172,357,213 bytes |
| Docker health | `healthy` |
| 容器内真实 TCP 协议探针 | **30 项通过**；网关为离线替身，不是真实 AgentArts |
| 运行边界 | `network=none`，无宿主机端口发布，只读文件系统，`10001:10001`，capabilities 全部移除，no-new-privileges |
| 依赖与启动门禁 | `pip check`: No broken requirements found；无配置启动返回非零并报告 ValidationError |
| 最新全仓 pytest（普通 sandbox） | **577 passed，4 skipped**（3 项 opt-in live，1 项 sandbox TCP；TCP 已在定向获批命令中通过） |
| 最新全仓 Ruff / mypy | 非 BFF 反馈闭环代码仍有 2 项 Ruff、16 项 mypy；本任务未改动这些文件 |

容器探针验证了：健康、无匿名入口、无 Swagger、登录、Origin/CSRF、服务端身份覆盖、Run 创建、相对 Location/links、API Key 不返回、上游 Cookie 不透传、跨用户访问在上游前阻断、结果、SSE 首帧/无缓冲、并发限制、断线关闭上游、连接槽复用、取消、登出。容器中未打包 `.env`、agent runtime 和模型 SDK。

构建使用 `/private/tmp/jindiao-bff-20260906-ECUIjS` 中 118.50 kB 最小上下文，仅包含 Dockerfile、依赖清单和明确允许的源码。源码已归档为 `artifacts/bff-docker-20260906-ecui/source.tar.gz`，SHA-256：`69f3527c804c177351ef121d33796dd01f66be3a54771e55640e23630ba30427`。该目录还保存测试脚本与结果摘要，不含测试密码或真实凭据。探针凭据仅在进程环境/临时容器中存在，容器结束后自动删除；镜像保留在本机，没有上传。

验证结束后 `docker ps` 只保留原有 `jindiao-arm64-20260906-171833`（127.0.0.1:18081）；本任务未修改原服务。

构建命令（在最小上下文执行）：

```bash
docker build --platform linux/arm64 -f deploy/bff/Dockerfile \
  -t jindiao-bff:local-20260906-ecui .
```

之前全仓 pytest 的反馈策略失败已在其他并行改动后消失；下方保留早期检查作为历史，不再代表最新测试状态。真实云网关联调、正式用户管理和公网发布仍未执行。

## 18:33 早期验收结果（历史）

| 验证 | 本次结果 |
| --- | --- |
| BFF 单元、代理安全、打包边界、实际尽调 ASGI 契约 | 43 passed |
| BFF Ruff / format | 通过，11 个源文件和测试文件已格式化 |
| BFF mypy | `Success: no issues found in 11 source files` |
| 最终版本真实 TCP 流复验 | 1 skipped：sandbox 禁止监听回环 socket |
| 上轮真实 TCP 流验证 | 1 passed：上游保持未完成时首帧到达、并发返回 429、断线清理与连接复用；之后增加的请求总时限/响应编码加固尚待 TCP 复验 |
| 本轮最后一次全仓 pytest | 576 passed、1 failed、4 skipped |
| 本轮最后一次全仓 Ruff | 44 项，集中在其他正在修改的报告反馈相关代码／测试及导入格式，未改动它们 |
| 本轮最后一次全仓 mypy | 3 个非 BFF 文件中 16 项错误，未改动它们 |
| BFF Docker 镜像 | 模板与复制边界测试已交付；工具审批服务额度限制使 Docker 检查未执行，未构建/启动镜像 |
| 真实 AgentArts | 未执行，不能将本地网关替身当作云端联调 |

## 定向复验命令

```bash
.venv/bin/pytest --no-cov -o addopts='' -q \
  tests/unit/test_bff_security.py \
  tests/unit/test_bff_packaging.py \
  tests/integration/test_bff.py \
  tests/e2e/test_bff_runtime.py \
  tests/integration/test_bff_streaming.py

.venv/bin/ruff check src/jindiao/bff \
  tests/unit/test_bff_security.py tests/unit/test_bff_packaging.py \
  tests/integration/test_bff.py tests/integration/test_bff_streaming.py \
  tests/e2e/test_bff_runtime.py

.venv/bin/python -m mypy src/jindiao/bff \
  tests/unit/test_bff_security.py tests/unit/test_bff_packaging.py \
  tests/integration/test_bff.py tests/integration/test_bff_streaming.py \
  tests/e2e/test_bff_runtime.py
```

关键断言覆盖：无会话 401、CSRF/Origin 403、越权 Run 在调用上游前 404、伪造身份头不透传、用户级幂等隔离、登出旧 Cookie 失效、限流和容量边界、相对 links、SSE 游标/心跳/错误、已知 API Key 不返回、上游 Cookie 不跨请求重放、分块请求/响应大小限制、总时限与错误脱敏。畸形 Unicode、NaN 和不符合 identity 协商的压缩响应也有回归。

真实尽调 ASGI 测试覆盖 single/multi 的创建、幂等、状态、终态事件、报告、跨账号隔离与终态游标重放；使用 `offline_mock`、`deterministic_harness`、`data_source_mode=mock`，不产生真实服务调用费用。

## 18:33 全仓未通过的检查（历史）

执行命令：

```bash
JINDIAO_RUN_LIVE_SINGLE=0 JINDIAO_RUN_LIVE_MULTI=0 JINDIAO_RUN_LIVE_PAIRED=0 \
  .venv/bin/pytest --no-cov -o addopts='' -q --tb=short
.venv/bin/ruff check src tests scripts/agentarts_prefix_probe.py --output-format concise
.venv/bin/python -m mypy src tests
```

最终 pytest 失败项为 `tests/e2e/test_result_api.py::test_feedback_generates_isolated_skill_candidate_and_stream_event`：旧测试期望 `awaiting_approval`，正在更新的反馈策略已返回 `rejected / use_feedback_api`。这不经过 BFF，未为本任务修改此行为或测试。

mypy 剩余文件为 `tests/unit/test_reporting_run_binding.py`、`tests/e2e/test_user_feedback_reporting_loop.py`、`src/jindiao/api/reporting_demo.py`；涉及可空值、宽泛类型、配置构造等。Ruff 涉及这些功能的导入顺序、行长与异步测试中的阻塞子进程。由于其他任务仍在改动，后续应重新跑全仓检查，而不是沿用本记录的数量。

## 交付与后续门禁

[启动、配置与接口说明](deployment/bff.md)；[架构决策](adr/0001-agentarts-browser-bff.md)。

当前只支持单 worker、有界内存会话与 Run 归属；不承诺持久化、多实例或云沙箱后台可靠性。本地 TCP、镜像构建与健康检查已补齐。接入真实 AgentArts 仍需运行时地址和服务端 API Key，以及 PREFIX_MATCH/Session/SSE/生命周期验证。
