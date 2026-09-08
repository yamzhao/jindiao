# 线上前端 + 本地后端联调

验证时间：2026-09-08。仅在本机增加代理，不修改 ECS Nginx、线上前端或线上后端。

## 使用入口

打开 <http://127.0.0.1:18088/workbench>，不要继续在公网地址提交本地测试。

- 页面及静态资源来自 `http://1.95.121.114/workbench`，使用线上原始构建，不改前端 bundle。
- `/api`、`/invocations`、`/ping`、`/docs`、`/openapi.json` 转发到 `bin/local` 的后端容器（服务名 `jindiao:8080`）。
- 页面和 API 对浏览器同源，无需放开 CORS；SSE 关闭代理缓冲，保留长连接。
- 代理只发布 `127.0.0.1:18088`，后端继续发布 `127.0.0.1:8080`。代理通过独立 Compose 网络访问后端。
- 线上资源通道只允许 GET/HEAD，不转发本地请求头、凭据或请求体。SPA 路径固定取线上 `/workbench`，不传本地 Run ID 和查询参数。
- 下方历史验收使用无凭据 Mock。当前 `bin/start.sh` 默认真实模型/天眼查配置；如需重现历史 Mock 验收，显式使用 `--mock`。公网原地址仍连接 ECS，未切换到本机。

后续已实际将本地后端切换为 `formal + tianyancha + qwen-plus`，Mock 降级关闭；本地 8080 与代理 18088 的健康检查通过，历史 Mock 报告仍保留原标记。此次切换未发起收费尽调，不将下方历史 Mock 浏览器记录当作真实业务验收。

本地浏览器存储与公网域名隔离；工作台中的四条初始案例是前端预置数据，不代表本地后端已存在对应 Run。请新建测试案件。

## 启动与维护

先执行 `./bin/start.sh`（真实模式，需要项目 `.env` 中的授权）。代理容器在历史联调时已创建，名称 `jindiao-workbench-debug`，是否运行以 `docker ps` 为准。

首次创建代理，在项目根目录执行：

```bash
docker run -d --name jindiao-workbench-debug \
  --network jindiao-local_default \
  --publish 127.0.0.1:18088:80 \
  --restart unless-stopped \
  --mount "type=bind,src=$PWD/deploy/local/workbench.nginx.conf,dst=/etc/nginx/conf.d/default.conf,readonly" \
  nginx@sha256:dc5069ad14f19660b141b21236140b91656bf89bbc3e2417c70ae650cd66104c
```

代理已存在时不要重复 `docker run`，使用：

```bash
docker start jindiao-workbench-debug
docker logs --tail 30 jindiao-workbench-debug
docker stop jindiao-workbench-debug
```

修改代理配置后先校验再重载：

```bash
docker exec jindiao-workbench-debug nginx -t
docker exec jindiao-workbench-debug nginx -s reload
```

`bin/stop.sh` 只停止后端，代理需单独停止；`bin/restart.sh` 只重启后端，不重新构建或加载新的环境配置。后端重建后代理通过 Docker DNS 重新解析服务名，不依赖固定容器 IP。修改代码、配置或切换真实/Mock 模式时执行 `bin/start.sh`；真实请求必须使用真实企业标识并省略 `scenario_id`。

## 历史 Mock 浏览器验证

通过 Chrome 打开本地入口，在页面填写以下 Mock 数据并点击“发起并开始执行”：

- 客户：乐视网信息技术（北京）股份有限公司
- 信用代码：`91110108MA01JD001A`（仓库 Mock 标识，不是真实企业核验结果）
- 金额：`2`，期限：`12`，单智能体
- 客户经理：本地联调测试；支行：测试支行

实际创建 Run：`e8508fc451454c72886de17ad1c61eb3`。代理访问日志确认浏览器 POST 创建返回 202，浏览器 GET events 返回 200；直接访问本地 8080 可查询同一 Run，证明没有把创建请求发到 ECS。数字字符串类型错误未再出现。

结果接口返回 200，`meta.is_mock=true`、`status=partial`、`report_markdown` 为 12196 字符的字符串；申请金额 20000 元、期限 12 月。`partial` 对应 Mock 资料缺口。

代理 `/ping` 返回 200，且带 `X-Jindiao-Debug-Backend: local-8080`；页面资源带 `X-Jindiao-Debug-Frontend: ecs-static`。对页面路径的 POST 返回 403，不会写入 ECS。

## 新发现的前端问题：报告状态未写入

网络联通和后端报告生成已验证，但详情页尚不能正常展示，出现：

```text
Cannot read properties of null (reading 'report_markdown')
```

浏览器堆栈指向 `assets/index-c5b1f127.js:3441:7721` 的 `C7o` 报告组件。该组件直接读取 `runResult.report_markdown`，没有加载状态/空值保护。创建入口的 SSE 回调只更新事件列表和进度，结束后未写入 `runResult`，也没有发出 GET result；只有另一条分支尝试获取结果。因此本地表格被前端标记为“异常 · 需处理”，但后端实际已产生可读报告。

建议修复前端：终态后读取 `/api/v2/due-diligence/runs/{run_id}/result` 并更新结果状态（202 则继续等待），或正确消费完成事件中的报告；报告组件在结果就绪前显示加载状态。不能仅因事件流结束就假定报告对象已赋值。

本轮只定位该前端问题，未修改线上 bundle、前端源码或后端结果契约。
