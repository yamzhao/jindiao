# AgentArts ARM64 镜像交付记录（2026-09-06）

## 当前结论

后续状态：运行时已经创建并完成 Mock 网关联调，随后发布真实配置与公开网页来源渲染热修复。最新镜像与验证结果见[真实业务调试记录](agentarts-live-workflow-debug-2026-09-06.md)。本文件保留首次 ARM64 交付阶段的原始记录。

ARM64 镜像已构建、验证并上传西南-贵阳一 SWR 的 `tongdun/jindiao`。AgentArts 托管表单已准备，尚未提交创建；真实网关、真实模型/天眼查端到端和 detached 平台生命周期均不在本轮已通过结论中。

镜像使用 2026-09-06 17:18（UTC+08:00）固定源码快照。构建期间工作区仍在进行 `add-user-feedback-reporting-loop` 开发，本镜像不包含其随后新增的报告策略改动，测试结论不代表当前最新工作树。

## 镜像标识

```text
swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:agentarts-20260906-171833-arm64
```

- 平台：`linux/arm64`，已从 SWR manifest 回读验证。
- SWR manifest digest：`sha256:016a0cde41aa768638dd9b386a705a09ec181f539e0cf6b582be3121b0de65b9`。
- 本地 image/config ID：`sha256:c1a82a2612049afa2436cbb0170bdada0b38ecd4a8813f7b4527e9d122f91719`。它与 manifest digest 是不同对象的摘要，不能互换。
- 本地镜像大小：1,336,135,525 bytes，包含现有 Dockerfile 安装的开发测试依赖；尚未做生产镜像瘦身。
- 源码快照压缩包 SHA-256：`8dbb861efd8fb4953391d154a0854085d6bd1a1ee95afffd3e3d8e43156afe9d`。
- 非 root 用户：`jindiao`；启动命令：`python -m uvicorn jindiao.api.app:app --host 0.0.0.0 --port 8080`。
- 使用 `buildx --platform linux/arm64 --load --provenance=false`；远端 manifest 为 Docker schema v2，没有附加 attestation index。
- 本轮没有把项目 `.env`、模型/TYC 凭据、Docker 登录凭据或历史运行产物加入镜像；没有将镜像仓库改为公开。

## 验证结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 原生 ARM64 buildx | 成功 | `build.log`、`build-metadata.json` |
| 非 root 启动、Docker HEALTHCHECK | running / healthy，`/ping` 返回 200 Healthy | 本轮容器检查 |
| ARM64 镜像完整离线测试 | 463 passed、3 skipped、0 failed、0 errors | `arm64-pytest-final.xml`、`.log` |
| 容器外真实 HTTP/SSE 请求 | 36 passed、0 failed | `arm64-http.json`、`.log` |
| Run 多路径探针直连 | create 202、query/result/cancel 200，21 条连续 SSE 事件，终态 completed | `arm64-prefix-probe.json` |
| 固定源码快照 Ruff / mypy | 通过；mypy 检查 199 个源文件 | 本轮命令输出 |
| 更新后的网关客户端探针 | 8 项单测及独立 Ruff/mypy 通过 | `tests/unit/test_agentarts_prefix_probe.py` |
| OpenSpec 严格校验 | 通过；任务进度 50/52 | `openspec-validate.log` |
| SWR push / manifest 回读 | 成功；digest 一致，linux/arm64 | `swr-push.log`、`swr-inspect.log`、`swr-manifest.json` |

以上证据位于本机工作区 `artifacts/agentarts-20260906-171833/`，不包含实际凭据。测试跳过的是需要显式凭据开关的 live single、live multi 和 live paired 测试。

首次容器完整测试因缺少 benchmark、发布 manifest、Dockerfile 和文档等测试夹具出现 11 项失败；补齐只读挂载后全量重跑通过。生产镜像未为此加入测试目录或发布文档。首轮失败日志也保留，避免与最终验证结果混淆。

HTTP 测试使用 `attached + memory + deterministic_harness + mock`，容器仅绑定 `127.0.0.1:18081`。覆盖 v1 JSON/SSE、v2 创建/查询/事件/结果、会话与归属校验、终态事件游标以及标准 invocations；不等于平台网关转发、真实长任务取消、沙箱回收或跨实例恢复验证。

## 待提交的 AgentArts 表单

| 字段 | 配置 |
| --- | --- |
| 区域 | 西南-贵阳一 `cn-southwest-2` |
| 名称 | `jindiao-demo` |
| 镜像 | 上述固定 ARM64 标签 |
| 委托 | 已有 `DefaultAgentArtsRuntimeAgency` |
| 入站网关 / 协议 | 已有 `defaultgw` / HTTP |
| 路由 | 前缀匹配 PREFIX_MATCH |
| 端口 / 启动命令 | 8080 / 留空，使用镜像 CMD |
| 入站认证 | API Key，拟创建名称 `jindiao-demo-api-key` |
| 日志、SFS、文件传输 | 未开启 |

首轮环境变量不含真实业务凭据：

```json
{
  "MODEL_PROVIDER": "offline_mock",
  "MODEL_NAME": "deterministic-mock",
  "JINDIAO_ENV": "integration",
  "JINDIAO_AGENT_RUNTIME_MODE": "deterministic_harness",
  "JINDIAO_DATA_SOURCE_MODE": "mock",
  "JINDIAO_EXECUTION_PROFILE": "attached",
  "JINDIAO_STORAGE_BACKEND": "memory",
  "JINDIAO_DETACHED_PROBE_PASSED": "false",
  "JINDIAO_ARTIFACT_ROOT": "/app/artifacts"
}
```

表单提示 API Key 凭据由 DEW 托管并按需计费。因此本轮停在最终提交前，等待用户确认创建运行时与该凭据；未创建新的 SFS、LTS 或 ECS 资源。

## 下一步门禁

1. 确认提交运行时；检查托管状态就绪并从详情获取真实调用 URL，不猜测网关地址。
2. 将网关认证放入本机不入库的 `.env.agentarts`，使用 `AGENTARTS_AUTHORIZATION`。不要复用 SWR 登录密码作为 AgentArts API Key。
3. 执行 PREFIX_MATCH 探针，再验证标准 invocations JSON/SSE、single/multi、断线游标、会话/归属、取消和 endpoint 版本。
4. 明确选择真实模型/TYC 的配置字段后注入，先做小范围限额真实调用，再做完整尽调联调。不要把完整开发 `.env` 直接上传。
5. 保持 detached 关闭，直至任务 7.6 的 HealthyBusy、共享存储和沙箱生命周期 POC 有独立证据。

本轮只关闭 OpenSpec 任务 7.3。任务 7.6 和 9.3 继续保持未完成，不将本地回归包装成云端通过。
