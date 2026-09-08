# AgentArts 镜像交付与剩余部署流程

状态：镜像交付入口已实现；本轮不自动创建/更新运行时、不切换云端版本。更新：2026-09-07。

## 1. 镜像入口

要求本机 Python 3.11+、本机 Docker Unix socket、buildx，以及能执行 ARM64 容器的原生环境或已配置的模拟环境。基础镜像与依赖构建需要网络。镜像以非 root 用户运行，默认 8080，包含 config/mock_data/skills；不要使用历史热修复 Dockerfile 重发最新工作区。

SWR 组织/仓库、区域、推拉权限和登录应事先完成；凭据通过 Docker 的安全登录流程管理，不作为脚本参数，不提交 `.docker/config.json` 或业务 dotenv。确认目标仓库/组织已存在。建议配置仓库的标签不可覆盖策略：脚本会检查推送前是否重名，但无法消除其他发布者并发写同一标签的竞态。

以下标签只是示例，执行时选一个真正未使用的新发布标签；禁止 `latest`：

```bash
./bin/agentarts --image swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:release-unique-arm64 --dry-run
```

仅本地构建并检查、不上传：

```bash
./bin/agentarts --image swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:release-local-check-arm64 --apply --build-only
```

构建、检查并推送到既有 SWR：

```bash
./bin/agentarts --image swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:release-publish-arm64 --apply
```

`--build-only` 与后续正式交付使用不同新标签；入口不复用已有本地标签，避免把未验证的旧镜像当作当前源码重新发布。

执行内容：当前工作区白名单快照 → `buildx --platform linux/arm64 --load --provenance=false --sbom=false` → 检查架构 → 无网络、无凭据、无宿主机端口的临时 Mock 容器健康检查、非 root 和 `pip check` → 推送 → 回读单架构 manifest，检查平台与 config digest，并记录 registry manifest digest。

临时检查容器在结束时删除，构建镜像保留。发布回执位于 `artifacts/deployments/<发布号>/receipt.json`，含源码文件摘要、本地 image/config ID 和推送后的 manifest digest。两种 digest 不可互换。仓库鉴权/网络异常无法确认标签不存在时，拒绝继续；不会关闭 TLS 验证或自动设为公开仓库。

成功提示 **image-delivered 仅表示镜像交付**，不是 AgentArts 运行时部署完成。SWR 推送失败或回读校验失败时，保持旧云版本不动；新标签可能已经上传，先核查仓库再使用另一个新标签重试。

## 2. 镜像交付后仍需完成的步骤

以下清单基于仓库已有联调记录；区域可用性、控制台字段、IAM 权限和具体接口须在实际部署时确认，脚本不会代办。

1. **确认区域与目标资源。** 确认 SWR 镜像与 AgentArts 区域兼容，选择更新既有运行时还是创建新运行时。记录运行时 ID、现用版本、网关、身份和回滚版本；新资源及托管凭据可能产生费用，先明确授权。
2. **准备工作负载身份/委托及镜像拉取权限。** 使用最小权限的既有配置或经授权创建。不要把 SWR 登录密码当作调用网关的 API Key，也不要在镜像内写入云账户凭据。
3. **选择本轮镜像。** 使用交付回执中已验证的 ARM64 标签，并核对 manifest digest。不要选旧的 `public-web-fix` / `multi-recovery` 热修复镜像来代表当前代码。
4. **配置运行时。** 采用该环境实际支持的 ARM64 计算规格、HTTP、端口 8080；启动命令使用镜像 CMD，避免 `--reload` 和多 worker。按记录采用 PREFIX_MATCH。确保 artifacts、SDK 日志和 home 目录对非 root 用户可写；不盲目开启只读根文件系统。
5. **独立注入运行配置与密钥。** 初次联调可选 `offline_mock + deterministic_harness + mock + attached + memory`。真实环境显式设置 `formal`、真实模型路由、模型密钥及天眼查授权，禁止 Mock 降级。不要把完整本机 `.env` 上传；验证当前版本所需预算，不直接套用历史低预算。
6. **配置网关认证与路由。** 在身份/网关侧配置 API Key 等实际支持的认证方式，调用凭据只由服务端持有。确认 PREFIX_MATCH 能转发 `/invocations` 和 v2 Run GET/POST/SSE 子路径，核对用户/Session 头。调用 origin、运行时前缀和 endpoint 必须从平台详情取得，不猜测 URL。
7. **保存候选版本并等待就绪。** 保留旧版本和现有路由。根据平台实际支持的候选版本/endpoint 访问方式先验证，再决定切换默认访问方式；镜像上传或控制台“正常”不等于业务健康。
8. **按层验收。** 先验证认证后的 `/ping`；Mock 配置可执行下方 PREFIX 探针。真实配置另行授权有界 single/multi 调用，检查无 Mock、允许正确 `partial`、报告可读、SSE 连续且有一致终态、游标重放及归属隔离。HTTP 200、cancel 200 或一轮成功不代表运行中取消及跨沙箱恢复通过。
9. **切换并记录。** 验收通过后切换默认版本/endpoint，回读确认版本、镜像和非敏感配置。保存回执和回滚方法；出现问题先停止新请求，再按平台能力回切旧版本。当前 memory 状态不承诺跨版本保留，应单独评估在途任务及历史结果。

后续自动化需要先验证 AgentArts 控制面 API/SDK、权限、版本和 endpoint 操作语义，再实现适配；当前代码没有把控制台操作包装成“已自动发布”。

## 3. 验证示例与边界

**仅限 Mock 配置**，将网关授权安全写入不入库的专用 dotenv：

```bash
.venv/bin/python scripts/agentarts_prefix_probe.py https://<实际网关域名> \
  --custom-prefix /runtimes/<实际运行时名称>/invocations \
  --env-file .env.agentarts.local --endpoint <实际访问方式>
```

文件字段为 `AGENTARTS_AUTHORIZATION`，支持原始 API Key 或 `Bearer <API-Key>`；不要把实际值写进命令行。探针固定带 Mock 场景，因此真实环境须按 [API 文档](../api/README.md) 使用无 `scenario_id` 的真实请求。原始响应/报告不要直接粘贴到公开日志。

TLS 始终验证证书和主机名；若遇到已有记录中的证书链问题，按 [BFF 部署](bff.md) 和 [证书说明](../../deploy/bff/certs/README.md) 配置经过核验的 trust store/CA 文件，禁止 `--insecure`。浏览器应用需要独立 BFF、HTTPS 同源入口及登录/CSRF 保护，本轮不部署它们。

保持 detached、SFS、LTS 等额外能力关闭，除非有独立需求、费用授权与验证。历史上已有 [真实网关 multi 验收](../agentarts-real-multi-2026-09-06.md)，但运行中取消、长期稳定性、跨沙箱恢复和生产多用户入口仍需分别验收；新镜像必须重新验证。
