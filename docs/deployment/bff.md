# 后端安全代理（BFF）：本地联调

> 2026-09-09 当前 AgentArts Latest 为 `demo-multi-0909`，已直接通过真实网关 Run/Events/Result 联调，允许审核未闭环时返回明确披露的 partial。此轮未重新做浏览器/BFF 全链路验收；见[当前 Demo 策略与状态投影注意事项](../diagnostics/agentarts-demo-multi-2026-09-09.md)。

本方案仅交付后端，不要求前端源码，不开放公网明文端口。BFF 是独立 FastAPI 应用：`jindiao.bff.app:create_app`，不加载模型、天眼查或多 Agent 执行器。2026-09-06 本地 BFF → 真实 AgentArts → Qwen + 天眼查 multi 已通过 15 项核查、六角色审核、报告 GET、SSE 终态和断点重放，详见[真实 multi 验收记录](../agentarts-real-multi-2026-09-06.md)。约 149 秒返回无 Mock 的 `partial` 报告；这是流程打通，不是完整源覆盖或生产稳定性验收。

```text
未来同源前端 → BFF：登录 Cookie + POST CSRF
                   ↓ 服务端 Bearer API Key + 派生用户/Session
              AgentArts PREFIX_MATCH → 尽调 Run API
```

浏览器不保存云 API Key、不传云身份头，也不直接调用 AgentArts。现有尽调服务不能作为匿名公网入口；其身份头仅是可信部署边界内的归属信息。

## 1. 无云凭据也能运行的本地验收

在 `jindiao` 项目根目录执行：

```bash
.venv/bin/pytest --no-cov -o addopts='' -q \
  tests/unit/test_bff_security.py \
  tests/unit/test_bff_packaging.py \
  tests/integration/test_bff.py \
  tests/e2e/test_bff_runtime.py

.venv/bin/pytest --no-cov -o addopts='' -q tests/integration/test_bff_streaming.py
```

第一组包含实际尽调 ASGI 服务的 `single` / `multi` 离线契约测试；模型和数据源明确指定为 Mock。第二组启动两个绑定 `127.0.0.1` 随机端口的临时服务，验证 SSE 首帧在上游结束前到达、并发限制、客户端断线关闭上游并释放连接。测试结束自动停止服务；沙箱禁止 socket 时会明确 skip，应在允许本地回环网络的环境重跑。

这些测试不调用真实 AgentArts、模型或天眼查，不等同于云端联调成功。

## 2. 配置与启动独立 BFF

BFF 默认不读取项目的 `.env`，避免意外继承模型和天眼查密钥。它只读取 `JINDIAO_BFF_*` 环境变量；本地开发可由 Uvicorn 显式加载专用文件。

```bash
cp deploy/bff/.env.example .env.bff.local
chmod 600 .env.bff.local
.venv/bin/python -m jindiao.bff.password alice
```

最后一条命令隐藏输入密码并要求确认，输出包含 scrypt 哈希的用户 JSON；将 JSON 填入专用文件的 `JINDIAO_BFF_USERS`。不要把明文密码放到命令参数、源代码或聊天记录。

必要配置：

| 配置 | 用途 |
| --- | --- |
| `JINDIAO_BFF_GATEWAY_ORIGIN` | 真实网关 HTTPS origin，只有协议、主机和可选端口，不含路径 |
| `JINDIAO_BFF_RUNTIME_NAME` | 已托管运行时名称，BFF 拼接 `/runtimes/{name}/invocations` |
| `JINDIAO_BFF_API_KEY` | 该运行时 API Key，只有服务端持有；不使用 SWR 登录密码 |
| `JINDIAO_BFF_IDENTITY_KEY` | 独立随机密钥，至少 32 字符，用于派生用户、Session 和幂等标识；不要复用 API Key |
| `JINDIAO_BFF_PUBLIC_ORIGIN` | 当前固定为 `http://127.0.0.1:18082`；未来换成前端与 BFF 共用的 HTTPS origin |
| `JINDIAO_BFF_USERS` | 用户名到 scrypt 哈希的 JSON 对象；用户名唯一，不要复用已删除账号的名称 |
| `JINDIAO_BFF_ENDPOINT` | 可选、固定发布端点；浏览器不能修改 |
| `JINDIAO_BFF_TLS_TRUST_STORE` | `certifi`（默认）或 `system`；始终验证证书和主机名，不支持关闭校验 |
| `JINDIAO_BFF_TLS_CA_FILE` | 可选、可信部署者提供的额外 PEM 路径，仅支持 `certifi`；缺失或无效时拒绝启动 |

API Key、身份密钥、账户配置缺失或格式错误时拒绝启动。示例不包含可用默认密码。先在本地编辑器或密码管理器填好配置，再运行：

```bash
.venv/bin/python -m uvicorn jindiao.bff.app:create_app --factory \
  --env-file .env.bff.local --host 127.0.0.1 --port 18082 \
  --workers 1 --no-proxy-headers --no-access-log

# 在另一个终端检查：
curl --fail http://127.0.0.1:18082/healthz
```

健康响应为 `{"status":"ok"}`，仅代表代理进程运行，并不主动检查云网关。还没有 AgentArts 运行时和 API Key 时，请使用第 1 节自包含测试，不要把占位网关当作真实接入。

本机真实网关在默认 Python 信任路径下出现缺少中间证书的验证错误，macOS 系统信任验证已通过。针对此环境，在 `.env.bff.local` 中显式设置 `JINDIAO_BFF_TLS_TRUST_STORE=system`，并设置固定 `JINDIAO_BFF_ENDPOINT=Latest`。`JINDIAO_BFF_API_KEY` 填原始 Key，不带 `Bearer `；探针文件中的 `AGENTARTS_AUTHORIZATION` 则兼容原始 Key 和完整 Bearer 值。不要依靠修改系统信任库、全局 SSL monkey-patch 或 `verify=False`。

`system` 由 `truststore==0.10.4` 使用目标操作系统的验证实现；macOS 通过不代表 Linux 会自动补齐同一证书链。2026-09-06 在 Linux ARM64 上复现两种信任模式均失败后，通过只读挂载官方中间证书并显式设置 `JINDIAO_BFF_TLS_TRUST_STORE=certifi`、`JINDIAO_BFF_TLS_CA_FILE=/app/certs/globalsign-rsa-ov-2018.pem` 完成真实网关验证。加载时保留原公共根、证书与主机名验证，禁用 partial-chain 信任。此配置是对当前网关缺失中间链的兼容，不替代网关侧修复；来源和指纹见[证书说明](../../deploy/bff/certs/README.md)。

`development=true` 只允许回环 HTTP origin；其他地址必须 HTTPS。本地 Cookie 为 `jindiao_dev_session`（HttpOnly、SameSite=Strict，无 Secure）；HTTPS 模式使用 `__Host-jindiao_session`（另加 Secure、Path=/、无 Domain）。**本地 HTTP 模式不是公网发布配置。**

## 3. 后续前端的接口契约

所有接口相对同一 origin，不配置通配 CORS。POST 的 `Origin` 必须与配置精确一致；除登录外，POST 还必须携带当前会话的 `X-CSRF-Token`。

| 方法/路径 | 请求与响应 |
| --- | --- |
| `POST /auth/login` | JSON `{"username":"alice","password":"…"}`；200 设置 HttpOnly Cookie，返回 `user_id`、`csrf_token`、`expires_at`（Unix 秒） |
| `GET /auth/session` | 恢复当前用户与 CSRF；无会话或过期返回 401 |
| `POST /auth/logout` | Origin + CSRF；204，撤销服务端会话并删除 Cookie |
| `POST /api/v2/due-diligence/runs` | [原 v2 请求体](../api/README.md)，仅允许 `attached`，`mode=single\|multi`；200/202 返回 Run 和本地 Location |
| `GET /api/v2/due-diligence/runs/{id}` | 状态、进度、本地相对 links |
| `GET /api/v2/due-diligence/runs/{id}/events` | SSE，Cookie 认证，支持 `Last-Event-ID` 或 `?after=N` |
| `GET /api/v2/due-diligence/runs/{id}/result` | 200 结果，202 尚未就绪 |
| `POST /api/v2/due-diligence/runs/{id}/cancel` | Origin + CSRF，请求取消 |

创建示例（不含认证秘密）：

```json
{
  "customerName": "乐视网信息技术（北京）股份有限公司",
  "scenario_id": "normal-enterprise",
  "mode": "multi",
  "execution_profile": "attached"
}
```

真实数据模式应省略 `scenario_id`。请求体 `session_id` 被服务端覆盖；客户端提供的 `Authorization`、`X-Hw-*`、Cookie、转发 IP 都不会透传上游。Run 查询、结果、事件与取消在发请求前检查本地归属，其他用户统一得到 404。

重试创建时必须复用同一个 `Idempotency-Key`（最多 128 个字母、数字、点、下划线、冒号或短横线），且保持请求体不变；BFF 将该键按用户隔离。缺省时每次请求生成新键，可能创建新 Run。超时不代表云端未创建，不能改用新键盲目重试。BFF 不自动重试 POST。

浏览器后续可使用同源 Fetch / EventSource。只在内存中保留 CSRF，不把登录密码或 Cookie 复制到 localStorage。SSE 保留 id、事件名、data 和心跳；收到 `run.completed`、`run.partial`、`run.failed`、`run.cancelled` 后关闭 EventSource。收到 `proxy.error` 应关闭流、查询状态；若已是终态，通过结果 GET 获取报告，按真实状态显示完整、部分或失败；若仍运行，可用最后已处理游标有限重连。不要无限重放同一超限帧或再次创建 Run，不能直接显示“尽调完成”。HTTP 401 重新登录；404 不重试猜测 Run；429 遵守退避。展示报告 Markdown 时仍需前端 HTML 清理，BFF 不是 XSS 渲染器。

## 4. 限额与错误语义

- 会话默认 1 小时，固定过期；Run 归属默认保留 8 小时。重新登录同一账号后，未过期的已登记 Run 仍可访问。
- 登录同一实际连接 IP 每分钟 10 次，所有 IP 合计每分钟 60 次；不信任 `X-Forwarded-For`。启动必须保留 `--no-proxy-headers`。未来置于反向代理后限额会聚合到代理 IP，需另行设计可信代理与分布式限流。
- 每用户每分钟最多创建 3 次、认证请求 120 次；全局最多 32 个上游活动连接，每用户 4 个（SSE 包含在内）。这不是运行中 Run 数量或模型 token 的费用预算，运行时仍须自身限制执行成本。
- 创建 JSON 默认最大 64 KiB；上游 JSON 最大 16 MiB；单 SSE 帧最大 256 KiB。拒绝未知代理路径/查询参数，不开放管理、Tool、MCP、上传和反馈发布路由。
- 上游请求使用 `Accept-Encoding: identity`，拒绝不符合要求的压缩响应，避免在检查大小之前解压不受控数据。实际网关必须保留此协商行为，需在接云时验证。
- JSON 上游调用总时限默认 120 秒（包含响应头和响应体），SSE 总持续时间最多 30 分钟且不超过会话期限，另有上游读空闲超时。登出后不再转发后续数据，空闲流也受时限约束。
- 上游超时为 504；认证失败、重定向、5xx、无效响应为脱敏 502；上游 404/409/422/429 保留状态码但不透传原始错误体。上游异常 body、Set-Cookie、Location、响应凭据不会直接返回浏览器。成功 JSON/事件帧中若出现已知云 API Key 也会阻断；这不是任意敏感数据的自动识别保证。
- 流开始后的异常无法改 HTTP 状态码，会发送 `proxy.error`，不会伪造业务完成事件。断线关闭上游连接但**不等于取消 Run**，取消须调用 cancel 接口。

当前 `report.completed` 携带整份结果，真实报告可能触发默认单帧限制。本次真实 multi 实测报告 data 行 327418 字节，显式设置 `JINDIAO_BFF_MAX_EVENT_BYTES=1048576`（1 MiB，配置允许的硬上限）后，报告、终态和重放全部通过。默认仍为 256 KiB；不是无限放开响应大小，也不保证所有报告都小于 1 MiB。部署本次联调配置需显式启用该值；更大的报告应通过结果 GET 获取，后续评估精简报告事件协议。旧中断 Run 的原始帧未测得，不能把本次大小套用于历史 Run。

## 5. 容器交付与本地验证

独立 Dockerfile 只复制 BFF、共享契约和脱敏模块，使用非 root 用户，不打包 `.env`、模型 SDK、日志、证据或执行器。

2026-09-06 已完成 `linux/arm64` 构建：`jindiao-bff:local-20260906-ecui`，镜像 ID 为 `sha256:a59a71bd5ce46c346d06d67942fac28d9afd234b53dd2438a3edc1f9e7b6d2a8`，大小约 172 MB。Docker 健康状态为 `healthy`，离线容器协议探针 30 项通过，`pip check` 通过，缺少配置时拒绝启动。

早期验证使用无外网、无宿主机端口发布、只读文件系统、UID/GID `10001:10001` 的临时容器；网关替身和 BFF 只在容器内部回环通信，未访问真实 AgentArts。详见 [早期验收记录](../bff-local-integration-test-2026-09-06.md)。

后续已构建含 TLS 修复的 ARM64 镜像 `jindiao-bff:local-20260906-ca-chain`，ID `sha256:83ebb482c81a3c2feb8e98add4dc2af8269ce5d2ec5af91afacb12187fed7af1`，完成真实网关 single/multi Mock、SSE 游标重放、两用户隔离、CSRF、健康检查和 `pip check`。临时容器已停止并自动删除，本地镜像保留。后续需实际启动代理时可使用：

```bash
docker build -f deploy/bff/Dockerfile -t jindiao-bff:local .
docker run --rm --name jindiao-bff-local --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --env-file .env.bff.local -p 127.0.0.1:18082:8082 \
  --mount "type=bind,src=$PWD/deploy/bff/certs/globalsign-rsa-ov-2018.pem,dst=/app/certs/globalsign-rsa-ov-2018.pem,readonly" \
  jindiao-bff:local
```

不得省略 `127.0.0.1` 发布地址。镜像内监听 `0.0.0.0:8082` 仅为容器网络；宿主机只发布到回环。以上配置文件需自行配置，使用所挂载证书时须设置第 2 节的两个 TLS 变量。本次没有生成长期账号或替用户填入真实 API Key。`--env-file` 值对具有 Docker 管理权限的人可见，仅用于受控本地环境；正式部署需另行使用平台机密注入方案。

## 6. 明确未包含的生产能力

仅支持一个 worker；会话、限流、Run 归属全部是有界进程内存。重启后登出所有用户，未重新登记的旧 Run 返回 404。不能用于多副本负载均衡，也不承诺持久化恢复或跨沙箱恢复。账户停用或密钥轮换需更新配置并重启，不提供注册、找回密码、MFA 或管理员 API。

真正公网部署前需要：HTTPS 同源入口、组织 SSO/正式用户管理、共享权限与限额存储、依赖漏洞扫描、审计与费用预算，以及真实 AgentArts API Key、PREFIX_MATCH、Session 路由和沙箱生命周期验证。特别是 attached 创建/查询/SSE 跨请求行为，必须在真实平台验证，不能从本地测试推断可靠后台运行。

接云遵循 [AgentArts 自定义接口说明](https://support.huaweicloud.com/api-agentarts/ExecuteRuntimeWithPrefix.html)。本地安全边界、Linux TLS 与当前真实网关 Mock 联调已通过，不能替代云端真实业务源或平台生命周期验收。
