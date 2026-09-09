# ECS 最新代码部署与反馈/六类建议验收（进行中）

## 当前结论

本轮 ECS 部署及公网反馈接口验收已完成。策略候选保持 `awaiting_approval`，未自动应用。

- 发布号：`ecs-20260908-feedback-suggestions-01`。
- 本地源码部署包已生成并上传，服务器 SHA-256 及部署预览校验通过。
- 312 个运行时文件与打包时工作区逐文件一致，包括未提交的六类建议修改。
- 源码包 SHA-256：`a90ff5e4c1e059ee1e5bef618cce615fee52ec6720c873b794cdb61b1adff1ad`。
- 用户明确允许本次维护：构建和切换期间公网 `/api` POST 临时返回 503；结束或失败后恢复原 Nginx 配置。
- 最终发布号：`ecs-20260908-public-feedback-03`；镜像 `jindiao:ecs-20260908-public-feedback-03`，ID `sha256:61ece1d19cad3ab718d132389812bb4a1f9f43e8e2cb25f2427841ede0303307`；主服务 healthy。
- 迁移回执：771 → 785 个历史/新增文件，校验摘要 `b7f676fc3eb5b184b03bef7ac37301b8a27848536be813b83895148e51b39e94`；原卷和旧容器保留。
- 已启动服务器后台维护/部署脚本，最后成功读取的回执状态是 `building`；旧 `jindiao-ecs` 仍 healthy。构建耗时集中在 Debian 官方源下载。
- 首版构建曾因 Docker 操作超时失败，旧服务保持健康；随后复用已验证依赖层离线构建成功并完成发布。维护 Nginx 配置已恢复原文。
- 用户随后从终端提供的状态：回执仍 `building`，构建容器 `goofy_bell` 已运行 15 分钟，主服务仍 healthy；索引下载完成，APT 正在下载 `libperl5.36`（partial 文件约 2.8 MiB）。这说明依赖安装有推进，仍不能据此判定部署完成。

## 服务器位置与恢复逻辑

服务器：`root@1.95.121.114`，发布目录：`/opt/jindiao/releases/ecs-20260908-feedback-suggestions-01`。

- `deployment.log`：后台部署及 Nginx 恢复输出。
- `receipt.json`：部署控制器回执。
- `deploy-exit.json`：控制器退出码（结束时生成）。
- `run_with_maintenance.py`：维护包装器；finally 中检查配置未被并发修改，恢复 `nginx-before.conf` 的原内容并测试、重载 Nginx。
- `nginx-before.conf`：本次维护前 `/etc/nginx/conf.d/xingyao.conf` 的原内容。

最后确认旧镜像 `jindiao:ecs-20260908-8d1a1c5`，原数据卷 `jindiao-ecs-data-ecs-20260908-8d1a1c5`。新镜像目标 `jindiao:ecs-20260908-feedback-suggestions-01`，新卷目标 `jindiao-ecs-data-ecs-20260908-feedback-suggestions-01`。控制器保留旧容器/原卷，切换失败会尝试回滚。

沿用服务器最新配置 `/opt/jindiao/runtime/ecs-20260908-8d1a1c5.env`：`deepseek-v4-flash-0731`、formal、天眼查、attached+local。现有 `JINDIAO_ENV=integration`，反馈 Demo 关闭；本轮未改变主服务反馈开关。凭据未下载。

## 已完成的本地验证

- 全量 pytest：1147 passed、1 failed、4 skipped，覆盖率 88.15%。
- 唯一失败为已有 `test_frozen_release_manifest_matches_versioned_components` 冻结清单摘要不符，未修改冻结清单或测试。
- 4 个 skip 中的 BFF 回环流式测试已取得网络权限单独补跑通过；另 3 个为显式开关控制的 live single/multi/paired 测试。
- 新增建议、反馈、部署、Run/result 相关测试均通过。
- 七个变更业务/部署文件 Ruff 和格式检查通过。
- mypy 检查 262 个文件：4 个测试文件共 12 个报错，业务源码未报错；详细输出保存在下方目录。不能报告全量类型检查通过。

本地证据：`artifacts/ecs-verification-20260908/`，包含测试日志、源码清单、部署回执和验证脚本。

## 真实验证结果

使用本次部署中的真实 Run `ace90f1aaad3420794da3fbe620b8f33`（华为技术有限公司，single，12 万元、12 月）完成验证：

1. 六类建议全部返回：额度 `10000` 元、利率标准定价说明、授信期限 `3` 月、贷款期限 `3` 月、法定代表人保证、按月付息到期还本；`suggestion_source=model`，无 `generation_failed`。
2. SSE/result：HTTP 200，partial，非 Mock；518 条证据、1 条风险、报告 154718 字符、77 个连续事件；实时/重放/断点与终态一致。
3. 公网 POST feedback：HTTP `201`，候选 `evo-ec0d8b05dcbf64982dabfa0ce4a9cd1443292e144f08733ed7142ede9247324d`，状态 `awaiting_approval`。
4. 公网 GET skill-evolution：HTTP `200`；默认摘要 9 个案例，`?include=reports` 返回完整前后报告；source 案例 0/64 → 64/64，全部保护检查通过。
5. 同幂等键重试 HTTP `200` 且响应体一致；同键不同正文 HTTP `409`；错误 owner/session HTTP `404`；缺身份 HTTP `401`；跨源 HTTP `403`。
6. 源 Run result 与 events 在反馈后保持不变；候选未激活，需人工审批后通过 CLI apply。

公网模式配置为 `integration + local + JINDIAO_REPORTING_PUBLIC_ENABLED=true`，origin 为 `http://1.95.121.114`；主服务保留回环绑定和单 worker。该模式继承身份头隔离，但身份头不是完整登录鉴权，适用于受控联调。
