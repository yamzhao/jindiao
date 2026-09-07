# AgentArts Browser BFF Implementation Plan

> 执行方式：当前会话按测试优先逐项实施，不委派子代理；用户已请求实现整个访问层。架构与约束见 ADR-0001。

**Goal:** 浏览器在不接触云 API Key 的情况下，安全创建、查询、订阅和取消 AgentArts Run。

**Architecture:** 独立 BFF app + 服务端账号/会话 + 受限异步代理；单 worker、有界内存，重启失效。尽调服务不改动。

**Tech Stack:** FastAPI、httpx、Python scrypt/secrets/HMAC、pytest。前端仅约定未来同源 Fetch/EventSource 契约，本轮不开发页面。

## 1. 配置与认证

- [x] 新增 `tests/unit/test_bff_security.py`：HTTPS、无默认密钥、scrypt 校验、会话过期/登出、限流边界。
- [x] 执行 `.venv/bin/pytest --no-cov tests/unit/test_bff_security.py`，确认缺少功能导致失败。
- [x] 实现 `src/jindiao/bff/config.py` 和 `security.py`：配置加载、随机会话、常量时间验证、限定容量；新增账号哈希 CLI，不将密码作为命令参数。
- [x] 原命令重跑通过，再运行 BFF 范围 Ruff/mypy。

## 2. 受限代理

- [x] 新增 `tests/integration/test_bff.py`：未登录 401、CSRF 403、伪造云头不透传、非法路径不访问上游、用户间 Run 404、幂等/Session 稳定、JSON 和 SSE、超限/超时/错误脱敏。
- [x] 先用缺失 app 的断言确认失败；实现 `src/jindiao/bff/app.py`、`proxy.py`。
- [x] 路由固定为 `/auth/login`、`/auth/session`、`/auth/logout`、`/api/v2/due-diligence/runs` 及单 Run 的查询、events、result、cancel。
- [x] 使用实际尽调 ASGI app 验证单／多 Agent 离线契约；加入真实 TCP 首帧与断线清理测试，上轮已运行通过。
- [x] 增加回归并修复：上游 Cookie 重放、JSON 请求总时限、转义密钥识别、非法 Unicode/NaN、未协商压缩响应。
- [x] 最终版本真实 TCP 用例在获批的回环网络环境复验通过，BFF 定向测试合计 44 passed；不以 ASGITransport 替代实时性证据。

## 3. 后端交付（按用户最新范围调整）

- [x] 交付后端接口契约与启动文档；用户明确前端未准备好，因此不新增页面、JS 客户端或公网 Nginx 配置。
- [x] 新增 `deploy/bff/Dockerfile`、最小依赖、`.env.example` 和 `docs/deployment/bff.md`；无默认秘密，只示范回环发布。
- [x] 更新 `docs/api/README.md` 入口；不修改未提供的外部前端仓库。
- [x] 定向测试通过；执行全量回归与 Ruff/mypy，记录反馈闭环开发中的非 BFF 问题，详见 `docs/bff-local-integration-test-2026-09-06.md`。
- [x] 独立 BFF ARM64 镜像已构建，Docker healthy；只读、无外网、无端口发布、非 root 的容器内 30 项协议探针通过，pip check 通过，临时容器已自动清理。镜像 `jindiao-bff:local-20260906-ecui`。

真实 AgentArts、前端和 HTTPS 公网部署不在本轮执行范围。后续须先具备 API Key 运行时及真实调用地址，再验证路由、身份、SSE 与沙箱生命周期；本地 Mock 不计作云端成功。

## 安全验收断言

```python
assert anonymous_run.status_code == 401
assert bad_csrf.status_code == 403
assert another_user_run.status_code == 404
assert upstream_request.headers["authorization"] == "Bearer " + server_key
assert upstream_request.headers["x-hw-agentgateway-user-id"] != client_forged_id
assert server_key not in browser_response.text
assert "secure" in login_response.headers["set-cookie"].lower()
```

不自动提交 Git：当前仓库含大量既有未跟踪文件，避免混入其他任务变更。
