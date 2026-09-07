# AgentArts 网关联调检查记录（2026-09-06）

## 当前结论

更新：用户已保存真实业务凭据，云端已切换真实模式。首次真实 single 暴露公开网页证据渲染缺陷，已发布窄范围热修复；真实流程验证结果见[真实业务调试记录](agentarts-live-workflow-debug-2026-09-06.md)。下文 Mock 与凭据待填写部分为较早阶段的历史证据。

用户重新登录并明确确认创建运行时、API Key 及 DEW 按需费用后，已于 2026-09-06 19:40:46（UTC+08:00）创建 `jindiao-demo`。控制台状态为正常，已回读最新版本及全部 Mock 配置。该状态不等同于应用接口健康。

真实网关认证后的健康、Run JSON/SSE，以及本地回环 BFF → 真实 AgentArts 的 single/multi Mock 链路已通过。匿名、CSRF、跨用户访问与登出撤销检查符合预期。未上传模型或天眼查凭据，未开启 LTS/SFS，未开放 ECS 公网明文端口。

后续已完成 Linux ARM64 BFF → 真实网关的 Mock 联调，并通过显式补充官方中间证书解决 Linux TLS 链缺失；没有关闭证书或主机名校验。真实 Qwen 与天眼查的本地直连小规模检查也已通过，但尚未验证云端完整真实业务流程。测试代理和一次性账号已退出，没有留下长期监听服务。运行中取消与沙箱回收后恢复仍未验收。

## 已创建云资源

- 运行时名称：`jindiao-demo`。
- 运行时 ID：`9d26808f-7e3c-4d4d-b240-6c3c41512cbb`。
- 区域：`cn-southwest-2`。
- 版本：`v1`；访问方式：`Latest`。
- 网关 origin：`https://defaultgw-mmytsytege.cn-southwest-2.huaweicloud-agentarts.com`。
- 调用入口：`/runtimes/jindiao-demo/invocations`。
- 工作负载身份：`agent-jindiao-demo`。
- API Key 名称：`jindiao-demo-api-key`；已在身份详情中确认存在，未输出其值。
- 配置：HTTP、8080、PREFIX_MATCH、公网出网、attached、memory、offline_mock、deterministic_harness、mock；不含真实业务凭据。

## TLS 与认证前置诊断

1. DNS 解析得到 `1.95.214.144` 与 `101.245.90.116`。
2. Python/httpx 默认信任库以及 Python 默认 OpenSSL 上下文均报 `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`。
3. 本次 OpenSSL 连接到 `101.245.90.116`，服务端证书列表只包含叶子证书，签发者为 `GlobalSign RSA OV SSL CA 2018`。没有把从网络取得的叶子证书设为信任根。
4. macOS `/usr/bin/curl` 验证通过（`ssl_verify_result=0`），无凭据请求返回 401。
5. 使用已安装的 `truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)`、保留证书和主机名校验的 httpx 请求也返回 401。

这表明 macOS 系统原生信任验证路径可用，而默认 Python 验证路径遇到证书链问题；不是 API Key 错误。没有关闭 TLS 校验，没有修改系统信任库，也没有更改项目全局 HTTPS 设置。后续 Linux 验证与修复见下文；macOS 成功不能代表 Linux 也成功。

用户填写的是原始 API Key。本轮仅在进程内补充 `Bearer `，未回显或改写密钥；专用文件仍被 Git 忽略、权限为 600。带认证的 `/ping?endpoint=Latest` 返回 200、`{"status":"Healthy"}`（约 0.85 秒）。

现在 BFF 支持 `JINDIAO_BFF_TLS_TRUST_STORE=system`，探针支持 `--tls-trust-store system`。默认仍为 `certifi`，两种模式都验证证书和主机名；不支持 insecure 模式，也不使用全局 SSL monkey-patch。`truststore==0.10.4` 已加入直接依赖及 BFF 独立依赖，锁文件已离线更新。后续已重新构建 `jindiao-bff:local-20260906-ca-chain`，不要使用旧镜像验收新增配置。

## 已验证证据

### 真实网关：直接调用

固定 `Latest`、同一 Session 与用户头，执行有界 Mock 请求：

| 检查 | 实测结果 |
| --- | --- |
| 创建 / 查询 / 结果 | 202 / 200 / 200，最终 `completed` |
| Run | `7c5550d05b2b4d9a894c82bf91747b2a` |
| SSE | 21 条，序号 1–21 连续，`run.accepted` → `run.completed` |
| 首个 data 帧 | 约 0.076 秒；此时 Run 已完成，不能据此证明运行中无缓冲 |
| 幂等重试 | 同键、同体返回同一 Run |
| `Last-Event-ID: 1` | 200，20 条事件，精确重放序号 2–21 |
| 取消接口 | 200；目标 `40ba1b5833e24c9f9c0ca39de9041729` 已完成，未验证运行中中止 |

修复后正式探针也实测通过（退出码 0）：

```bash
.venv/bin/python scripts/agentarts_prefix_probe.py \
  https://defaultgw-mmytsytege.cn-southwest-2.huaweicloud-agentarts.com \
  --custom-prefix /runtimes/jindiao-demo/invocations \
  --env-file .env.agentarts.local --endpoint Latest --tls-trust-store system
```

Run 为 `388d045cf8a34cf39645fe2d61887726`，ping/create/query/events/result/cancel 为 200/202/200/200/200/200；21 条连续事件。探针接受原始 Key 或完整 Bearer 值，拒绝非法字符，不打印凭据或业务响应全文。注意环境变量 `AGENTARTS_AUTHORIZATION` 优先于文件；重复执行会创建新的有界 Mock 测试 Run。

### 本地 BFF → 真实网关

临时 Uvicorn 仅绑定 `127.0.0.1` 随机端口，两个账号及独立身份密钥仅保存在内存中。最初通过显式系统 TLS transport 验证安全边界，随后去掉 transport 注入，使用正常 `create_app(settings)` 和 `tls_trust_store=system` 复验启动链路。

| 检查 | 实测结果 |
| --- | --- |
| 健康 / 两个账号登录 | 200；Cookie 为 HttpOnly、SameSite=Strict、无 Domain |
| 匿名创建 | 401 |
| 缺失 CSRF / 非同源 POST | 403 / 403 |
| single 创建、查询、结果 | 202 / 200 / 200；Run `adbad62a8b2d40ed8d77562be46fdee2` |
| 不可信浏览器 Authorization / Session | 不影响服务端认证；创建成功，响应隐藏云端 owner/session，Location 为本地相对路径 |
| 浏览器覆盖 endpoint | 422 |
| 另一账号查询、事件、结果、取消 | 均为 404 |
| SSE 首帧后主动断开 | 收到 sequence 1、`run.accepted`，约 0.084 秒 |
| 断开后携带游标重连 | 200，连续重放 sequence 2–21，末事件 `run.completed` |
| single 结果大小 | 27,287 bytes；未在响应 body/header 中发现已知 API Key |
| 幂等重试 | 返回同一 Run |
| multi 创建、事件、结果 | 202 / 200 / 200，25 条事件，末事件 `run.completed` |
| multi Run | `fda768bec3514c16ba7658def13fd5b0`；另一账号反向查询为 404 |
| 登出 / 旧 Cookie 再访问 | 204 / 401 |

未注入 transport 的复验 Run 为 `e3796306a2334242ac91150f936710d6`：登录 200、创建 202、查询/事件/结果 200、21 条连续事件、登出 204。两次临时 BFF 均已停止，未持久化测试密码。

以下为实际收到事件的字段摘录，不是完整业务载荷：

```json
{"event_type":"run.accepted","sequence":1}
{"event_type":"run.completed","sequence":21}
```

这证明了当前沙箱中跨 HTTP 请求与重放可用，不证明长期后台运行、跨沙箱持久化或平台回收后的恢复。

### 镜像与容器

- 尽调 ARM64 基线镜像 ID：`sha256:c1a82a2612049afa2436cbb0170bdada0b38ecd4a8813f7b4527e9d122f91719`。
- BFF ARM64 镜像 ID：`sha256:a59a71bd5ce46c346d06d67942fac28d9afd234b53dd2438a3edc1f9e7b6d2a8`。
- 原有尽调容器 `jindiao-arm64-20260906-171833` 为 `healthy`，仅发布 `127.0.0.1:18081 -> 8080/tcp`。

### 本地真实 HTTP 探针

```bash
.venv/bin/python scripts/agentarts_prefix_probe.py http://127.0.0.1:18081
```

- 退出码：0。
- ping 200、create 202、query 200、result 200、cancel 200。
- Run ID：`27a2f3ad8ce5464ea670192924396176`。
- 取消接口测试 Run ID：`53db7521e23548579277ddafbfc4d8ee`。
- SSE：21 条连续事件，首事件 `run.accepted`，末事件 `run.completed`，最后序号 21，`valid=true`。
- 业务使用既有 Mock 容器；该探针不能证明云网关无缓冲，也不能仅凭 cancel 200 证明运行中任务被中止。

### BFF 回归

```bash
.venv/bin/pytest --no-cov -o addopts='' -q \
  tests/unit/test_bff_security.py tests/unit/test_bff_packaging.py \
  tests/integration/test_bff.py tests/e2e/test_bff_runtime.py

.venv/bin/pytest --no-cov -o addopts='' -q tests/integration/test_bff_streaming.py
```

- 第一组：43 passed，退出码 0。
- 第二组：1 passed，退出码 0；通过真实回环 TCP 检查首帧、并发与断线清理。
- 无失败，存在依赖弃用告警。这些测试不访问真实云网关、模型或天眼查。

### 本轮 TLS 修复后的回归

先新增 8 个回归用例，观察到预期的 8 个失败，再实现配置和探针修复。最终执行：

```bash
.venv/bin/pytest --no-cov -o addopts='' -q \
  tests/unit/test_gateway_tls.py tests/unit/test_agentarts_prefix_probe.py \
  tests/unit/test_bff_security.py tests/unit/test_bff_packaging.py \
  tests/integration/test_bff.py tests/integration/test_bff_streaming.py \
  tests/e2e/test_bff_runtime.py
```

结果：60 passed，8 条既有依赖/弃用告警，退出码 0。改动涉及的 BFF、TLS、探针和测试通过 Ruff；mypy 对 10 个相关文件检查通过。这里是定向回归，不是全项目、云模型或 Linux 镜像验收。

## 后续验证：Linux TLS 与真实上游

### Linux ARM64 BFF → 真实网关

Linux / aarch64、OpenSSL 3.0.20 下，`certifi` 和 `system` 两种模式均复现证书链验证失败。将 [GlobalSign 官方中间证书](https://support.globalsign.com/ca-certificates/intermediate-certificates/organizationssl-intermediate-certificates)以只读文件补充到 certifi 上下文后，无凭据请求返回 401，证明 TLS 验证已通过。该中间证书先用 certifi 的既有公共根验证，结果 OK；未信任网关叶子证书或增加私有根。

新增 `JINDIAO_BFF_TLS_CA_FILE` 与探针 `--tls-ca-file`。保留原根库、`CERT_REQUIRED`、主机名校验，并关闭 partial-chain 信任；文件缺失或无效时拒绝启动，不能与 `system` 混用。公开 PEM 不打入镜像，来源、指纹、有效期见 [证书说明](../deploy/bff/certs/README.md)。优先推动网关侧提供完整证书链。

- ARM64 镜像：`jindiao-bff:local-20260906-ca-chain`。
- 镜像 ID：`sha256:83ebb482c81a3c2feb8e98add4dc2af8269ce5d2ec5af91afacb12187fed7af1`。
- 容器：只读、UID 10001、去掉全部 capabilities、no-new-privileges，宿主机仅绑定 `127.0.0.1` 随机端口。
- 使用正常 BFF `create_app`，没有替换上游 transport；凭据和一次性账户经标准输入传入进程，没有写入镜像、Docker 环境配置或命令参数。

| 检查 | 结果 |
| --- | --- |
| `/healthz` / Docker HEALTHCHECK / `pip check` | 200 / healthy / 退出 0 |
| 匿名创建 / 缺失 CSRF | 401 / 403 |
| single 创建、查询、事件、结果 | 202 / 200 / 200 / 200 |
| single Run | `e58be520a8264e8a9e92eb2f0d49e520`，21 条连续事件，末事件 completed |
| 另一账号查询、事件、结果 | 均为 404 |
| Last-Event-ID=1 重放 | 200，序号 2–21 |
| 同键幂等创建 | 202，同一 Run |
| multi 创建、事件、结果 | 202 / 200 / 200 |
| multi Run | `8ea815f444054f8c920104ba33628e40`，25 条事件，末事件 completed |
| 登出 / 撤销后的会话 | 204 / 401 |
| 检查已知 API Key | 未出现在所检查的响应 body/header 中 |

临时 BFF 容器已停止并自动删除，本地镜像保留。未上传 BFF 到 SWR，未修改原有尽调服务。此处仍是云端 Mock 业务数据，不能据此证明真实业务或跨沙箱恢复。

### 本地真实模型与天眼查直连

仅在本地进程读取 `.env`，不输出原始凭据或完整业务响应，不向 AgentArts 注入业务密钥。

- Qwen：`qwen-plus`，通过 DashScope OpenAI 兼容接口发出一条 `max_tokens=8` 的连通性请求；HTTP 200、有效 completion，输入 18 / 输出 1 token，约 0.72 秒。
- 天眼查：调用现有 MCP 连接验证器，单次调用超时 30 秒、零重试、总时限 90 秒；发现 30 个能力。对同盾科技（上海）有限公司读取基本信息 1 条、年报 1 条，均为 `verified_records`；不存在企业查询为 0 条、`verified_empty`，约 1.57 秒。
- 仅记录汇总信息。上述请求不经过云端运行时，不是完整 single/multi 尽调报告验收。

### 新增修复回归

先增加 6 个用例并观察预期失败，再实现 CA 文件加载与参数传递。上述定向回归命令最新结果为 **66 passed**、8 条依赖/弃用告警，退出码 0；涉及文件 Ruff 通过，最新 mypy 对 7 个改动相关文件检查通过。镜像构建及真实 Linux 容器联调均独立执行，不以本地单测替代云验证。

### 云端下一步状态

已回读运行时 `v1`，仍为 Mock。已打开现有运行时编辑页，准备 `live-smoke-0906` 草稿，**尚未点击“保存为新版本”**：

- 保留原镜像、API Key 认证、8080、PREFIX_MATCH、attached、memory；LTS/SFS/文件接口关闭。
- 设置 `MODEL_PROVIDER=OpenAI`、`MODEL_NAME=qwen-plus`、DashScope base URL、`JINDIAO_AGENT_RUNTIME_MODE=formal`、`JINDIAO_DATA_SOURCE_MODE=tianyancha`，禁用 degraded mock。
- 设置应用级有界测试预算：300 秒、24 次 LLM 请求、32 次工具调用、输入/输出/合计 token 120000/20000/140000、修复 1 轮、schema 重试 2 次。应用预算不等同于供应商账户的硬费用限额。
- `MODEL_API_KEY` 与 `TIANYANCHA_MCP_AUTHORIZATION` 均留空，待用户从本地 `.env` 手动填入控制台。不会将业务密钥放入聊天、BFF 或镜像。

待用户填好并保存后，回读新版本与访问方式，先以无 `scenario_id` 的真实 single 验证，再决定是否执行 multi。保存前原 `v1` 未变更；云端密钥使用与版本发布尚未完成。

## 尚未验收

1. 经 AgentArts 的真实模型与天眼查完整 single/multi 流程：本地直连已通过，当前已发布运行时仍为 Mock，业务凭据未注入。
2. 运行中取消、HealthyBusy、SFS/会话存储、长期后台任务与 sandbox 回收语义；不启用 detached。
3. 网关侧完整服务端证书链修复：Linux 客户端补充官方中间证书的兼容方案已验证，未改变平台证书配置。
4. 常驻 BFF 与正式账户：本轮使用一次性内存账号，未创建长期 `.env.bff.local` 或常驻服务。

后续配置真实模型与天眼查后先执行有界 single，再执行 multi；保留可切回的 Mock 版本，保持 detached 关闭。Linux 部署使用已核验的显式中间证书配置，不能关闭 TLS 校验。

操作说明见 [BFF 本地联调](deployment/bff.md) 和 [ARM64 交付记录](agentarts-arm64-delivery-2026-09-06.md)。
