# ADR-0001：浏览器通过 BFF + API Key 访问 AgentArts

## 状态

用户已选择“后端代理 + API Key”，并明确本轮只完成后端与本地联调、不开放公网明文端口。实现与测试见 `docs/deployment/bff.md`；真实网关验证仍待进行。

## 背景

浏览器不能持有共享 AgentArts API Key；现有运行时信任网关身份头，不能直接作为匿名公网业务入口。仓库没有现成前端或登录模块。运行时镜像与反馈功能仍独立演进，因此新增单独启动的 BFF，不改变已发布镜像或现有业务路由。

## 决策

```text
浏览器（同源 Cookie + CSRF）→ HTTPS 入口 → BFF（认证、限流、Run 归属）
                                             ↓ API Key + 服务端 Session/用户标识
                                         AgentArts PREFIX_MATCH → 现有尽调服务
```

- 复用 FastAPI/httpx；BFF 是独立 ASGI app，不加载模型、天眼查或尽调执行器。
- 当前测试账号使用 scrypt 密码哈希、随机不透明会话、HttpOnly/Secure/SameSite=Strict Cookie。没有默认账号和密码；配置缺失拒绝启动。
- 所有业务接口需有效会话；POST 还需精确 Origin 与会话 CSRF。拒绝任意客户端云认证/身份/Session 头，云凭据只从服务端配置注入。
- 仅开放 v2 Run create/query/events/result/cancel；无任意 URL/path 代理，无管理、上传、模型、MCP 接口。浏览器不选择云 endpoint。
- 本地保存 Run→用户映射，越权在发出上游请求前返回统一 404；服务端派生稳定且隔离的上游 Session/幂等键，覆盖请求体 session_id。
- JSON 有界读取并重建 Run links；上游错误与重定向不透传响应体、Cookie 或凭据。SSE 按事件帧有界转发，保留游标与心跳，断线释放上游连接，不伪造完成事件。
- 限制登录尝试、每用户创建速率、请求体、JSON、SSE 帧、全局及每用户活动连接数。请求与响应 body 不记录到日志。
- 同源部署，不开放通配 CORS；生产必须 HTTPS。HTTP 仅允许显式开发模式下的回环地址。

## 边界与代价

本期面向单进程竞赛联调：会话、限流及 Run 归属保存在有界内存中，重启后会话失效且旧 Run 不再可访问（fail closed），只支持一个 worker。不能承诺持久化恢复、多副本或长期账户管理。后续正式产品应复用组织 SSO，并把认证/归属/限额迁移到共享存储。

已有 ECS 可以承载 BFF，但公网发布前必须配置 HTTPS 入口；只提供 SSH 隧道时不会开放公网明文接口。本期交付后端接口与接入文档，不开发前端页面或 JS 客户端，不声称页面已接好。

## 备选方案

- 浏览器直传共享 API Key：拒绝，密钥可被提取并绕过页面消耗上游额度。
- 仅 Nginx 注入 API Key：不足，缺少应用登录、归属与消费限制。
- BFF + IAM：可行，但本期按用户选择使用 API Key，减少签名集成成本。

## 参考

- https://support.huaweicloud.com/api-agentarts/ExecuteRuntimeWithPrefix.html
- https://support.huaweicloud.com/api-agentarts/agentarts_07_0005.html
- https://www.python-httpx.org/async/
