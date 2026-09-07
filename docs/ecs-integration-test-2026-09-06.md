# ECS 远端集成测试（2026-09-06）

## 环境与范围

- 目标：用户指定 ECS `1.95.121.114`，CentOS 7、x86_64、Linux 3.10、约 8 GB 内存。
- 发布目录：`/opt/jindiao/releases/20260906-1543`。
- 保留此前 `/root/jindiao-agentarts-20260906-1324`，没有覆盖旧发布目录。
- 主机原有 Python 环境未修改；使用独立 Docker 24.0.9 daemon、Unix socket `/run/jindiao-docker.sock` 和数据目录 `/opt/jindiao/docker`。
- 基础镜像：`python:3.11-slim-bookworm`，Python 3.11.16、glibc 2.36。
- 最终镜像：`jindiao:ecs-20260906-1543`，AMD64，镜像 ID `sha256:695ac7ded9d22a5dd1a11c579f4e2f052836573ea6d6a2ee77c254b9c8e3fd17`。
- 容器：`jindiao-ecs`，`running / healthy`，非 root 用户 `jindiao`，重启策略 `unless-stopped`。
- 运行 profile：`attached` + `local`，确定性 Mock 数据和模型；没有上传本机 `.env`、SSH 密码或真实供应商凭据。
- 本轮是 ECS AMD64 容器和真实 HTTP 传输测试，不是 AgentArts PREFIX_MATCH、ARM64、SFS 或真实模型/天眼查验收。

## 可复现部署

本机原生依赖安装受旧 glibc 限制，因此容器承载现代用户空间。系统安装源与 Python 包源支持构建参数，公开依赖仍由 `uv.lock` 冻结，DeepSearch 固定到公开 Git commit。

以下命令在服务器发布目录执行，使用本次独立 daemon：

```bash
cd /opt/jindiao/releases/20260906-1543
export DOCKER_HOST=unix:///run/jindiao-docker.sock
export PATH=/opt/jindiao/tools/docker:$PATH

DOCKER_BUILDKIT=0 docker build --network host \
  --build-arg DEBIAN_MIRROR=mirrors.huaweicloud.com \
  --build-arg PYPI_INDEX_URL=https://mirrors.huaweicloud.com/repository/pypi/simple \
  -t jindiao:ecs-20260906-1543 .

docker run -d --name jindiao-ecs --restart unless-stopped \
  --network host \
  --mount type=volume,src=jindiao-ecs-artifacts,dst=/app/artifacts \
  --env-file deploy/agentarts/ecs-mock.env.example \
  jindiao:ecs-20260906-1543 \
  python -m uvicorn jindiao.api.app:app --host 127.0.0.1 --port 8080
```

以上 `docker run` 是首次启动命令；已有同名容器时先检查状态，不要重复运行或删除持久化卷。运行用户为镜像内非 root 用户。独立 Docker daemon 禁用了 bridge 和 iptables 管理，所以使用 host 网络并显式绑定回环地址；没有开放公网 Docker socket 或应用端口。

客户端通过 SSH 隧道访问：

```bash
ssh -N -L 18080:127.0.0.1:8080 root@1.95.121.114
curl http://127.0.0.1:18080/ping
```

用户/会话请求头仅是可信网关集成契约，不是独立身份认证。公网开放前需要配置认证入口，并禁止客户端伪造网关注入的身份头。

## 测试方法

```bash
python scripts/remote_integration_probe.py http://127.0.0.1:8080 \
  --output /opt/jindiao/test-results/http-integration.json

python scripts/remote_persistence_probe.py capture http://127.0.0.1:8080 \
  /opt/jindiao/test-results/http-integration.json /opt/jindiao/test-results/restart-baseline.json

# 仅重启本次部署的容器；完成健康检查后再执行 verify。
docker restart jindiao-ecs

python scripts/remote_persistence_probe.py verify http://127.0.0.1:8080 \
  /opt/jindiao/test-results/http-integration.json /opt/jindiao/test-results/restart-baseline.json
```

探针依赖 Python 3.11 和 httpx；本服务器通过容器内 Python 执行，避免使用 CentOS 的系统 Python。测试摘要只包含测试状态、Run ID、事件数量和响应哈希，不记录原始企业证据、请求密钥或错误响应内容。

## 已修复的验证问题

1. Dockerfile 原先仅安装依赖，没有安装项目包；现使用冻结 lock 安装依赖，再安装本项目。
2. `/invocations` 非法 JSON/缺少企业字段现在返回 422，不再返回 500；不支持的 Accept 在创建 Run 前返回 406。
3. SSE 在发送响应头之前完成 Run 归属校验，越权访问返回 404。
4. 游标已到终态事件时，SSE 正常关闭空流，不再永久等待。
5. 下载源的超时问题通过可选镜像源、延长超时和降低下载并发处理，没有绕过锁定版本或禁用 TLS 校验。
6. 配置目录原先从 `site-packages` 位置推导，导致独立安装包无法启动；新增 `JINDIAO_PROJECT_ROOT=/app` 显式部署资源根目录，并覆盖报告目录、核查目录和 Skill 目录。
7. 重启时重复回放已折叠事件，曾使 multi 冲突场景修复次数从 2 变成 4；现从快照 `latest_sequence` 后继续回放。
8. 重启后重试已完成 Run 的幂等创建，曾重新发送 accepted 事件并回退状态；现返回已有 Run，不产生新事件。

以上业务修复均补充先失败、后通过的回归测试。一次冲突场景探针失败来自测试脚本使用了错误的企业名称，已按 fixture manifest 校正，未削弱后端主体一致性校验。

## 验证结果

| 验证层 | 最终结果 | 证据文件 |
| --- | --- | --- |
| ECS 镜像构建/启动 | AMD64 镜像构建成功；`/ping` 为 Healthy；容器 healthy | `build-20260906-1543.log` |
| ECS 全量 pytest | 463 passed、3 skipped，覆盖率 86.52%，47.56 秒 | `ecs-pytest.xml`、`ecs-pytest.log` |
| ECS Ruff / mypy / 依赖检查 | 全通过；mypy 201 个文件；pip check 无损坏依赖 | `ecs-quality.log` |
| ECS 本机真实 HTTP/SSE | 36/36 通过 | `http-final-integration.json` |
| 本地→SSH 隧道→ECS | 36/36 通过 | `http-local-tunnel.json` |
| 容器重启 | 4 个 Run 的完整状态、结果、事件哈希相同；幂等重试无变化 | `restart-final-baseline.json`、`restart-final-verification.json` |
| PREFIX 探针直连模式 | create/query/events/result/cancel 成功；**未经过 AgentArts 网关** | `local-prefix-probe.json` |

远端证据目录为 `/opt/jindiao/test-results`，已同步到本机 `artifacts/ecs-20260906/`。本地隧道报告由本机生成。

HTTP 测试覆盖：single/multi × 正常/证据冲突，v1 JSON/SSE，v2 创建/查询/事件/结果，幂等重复与冲突，用户与会话隔离，Last-Event-ID 回放，终态游标关闭，终态重复取消，`/invocations` JSON/SSE envelope 和非法输入。快速 Mock 的取消调用通过不等同于验证了真实慢任务/外部工具中断。

重启验证针对**已完成的 Run**和同一个 ECS 本地持久化卷，不意味着运行中的 Agent 可断点继续，也不意味着跨沙箱 detached 已可靠实现。

本地代码回归：463 passed、3 skipped，覆盖率 86.52%；Ruff、mypy（含新探针脚本）和 OpenSpec 严格校验通过。3 个跳过项分别是 live single、live multi、live paired comparison，属于显式凭据/实时联调门禁，不代表真实供应商调用已验证。

本次镜像含开发测试依赖，未压缩镜像约 1.27 GB；它是远端集成测试交付包，生产镜像可另行分离测试依赖和构建缓存。

## 仍需目标环境验证

- ARM64 buildx 和 AgentArts 托管镜像启动。
- AgentArts PREFIX_MATCH 多路径转发、认证和 SSE 连接行为。
- HealthyBusy、后台任务、SFS/session 存储、沙箱回收和 detached 恢复语义。
- 真实模型、天眼查 MCP 和年报补充链路。

CentOS 7 与 Docker 24.0.9 是本轮兼容性测试环境，不应直接当作长期生产基线；生产部署应迁移到受维护的操作系统与容器运行时。
