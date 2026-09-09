# AgentArts 最新后端发布准备 — 2026-09-09

> 当前 Latest 为 `demo-multi-0909`：用户已明确允许本地 Demo 的部分结果策略，Run/Events/Result 已通过真实联调，但仅 18/19 核查提交、审核未完成。见[最新验收记录](../diagnostics/agentarts-demo-multi-2026-09-09.md)。

> 后续：返工预算缺陷已修复并发布 `release-reviewfix-0909`，旧版本及本页失败记录保留。新版本验收进展见[审核预算修复记录](../diagnostics/agentarts-review-budget-fix-2026-09-09.md)。

## 当前状态

**镜像已上传，Latest 为 `release-backend-0909`；用户明确授权的一次真实 multi 验收失败：返工轮次预算耗尽，Result HTTP 500，未生成报告。网关/契约/事件链路通过不等于业务可用。**

- 目标：现有 cn-southwest-2 / `tongdun/jindiao` / `jindiao-demo`。
- 当前云端 Latest 仍为 `live-recovery-0906`，运行时 ID `9d26808f-7e3c-4d4d-b240-6c3c41512cbb`；本轮未更改版本、认证、环境变量或网关。
- 使用已有调用 API Key、严格 TLS 验证检查旧版本 `/ping`：200 / Healthy。
- SWR Docker 登录失效，标签存在性检查返回认证失败，未绕过检查或推送。
- 已登录的 SWR 页面支持 tar/tar.gz、含解压后不超过 2 GB。尝试用文件选择器选择本轮压缩包，被浏览器工具以 Not allowed 拒绝；页面文件列表仍为空，开始上传按钮禁用，未提交上传。
- 需要刷新 SWR Docker 登录，或由用户启用浏览器扩展文件 URL 访问后继续。无需在聊天中提供任何密码/API Key。

## 候选发布物

```text
swr.cn-southwest-2.myhuaweicloud.com/tongdun/jindiao:release-20260909-backend-arm64
image/config ID: sha256:fe109bfc6e3da13d814232a9c431e7b1e7fbc691998317c448802cd846d4d049
```

续跑已取得 registry manifest digest：`sha256:8672417ad71dc306f960128e25ea79753c111a45b1a8737f0feb9fe438921c0b`。回读远端 manifest 验证 linux/arm64，config digest 与本地 image ID 一致。上传前确认新标签不存在、当前源文件与候选回执一致。

- 源码基于 HEAD `2cc3f5852bce8f4b7369231791ba75e95953c4f6` 的当前工作区白名单快照；具体内容以 312 项逐文件摘要为准，不只依赖提交号。未修改用户并行产生的文档/源码改动。
- 回执：`artifacts/deployments/agentarts-20260909-full-backend/receipt.json`。
- tar：`artifacts/agentarts-release-20260909/jindiao-release-20260909-backend-arm64.tar`，1411472896 字节。
- tar.gz：同目录同名 `.tar.gz`，505415831 字节。归档只含一个镜像、上述唯一标签。
- 两个本地归档均保留，便于用户页面上传；不是业务报告或密钥备份。

## 构建与依赖验证

常规 `bin/agentarts --apply --build-only` 已尝试；基础镜像/系统依赖安装成功，在从 PyPI 获取 `uv==0.12.10` 时失败。未改依赖版本、未关闭 TLS。

替代构建使用 [Dockerfile.locked-reuse](../../deploy/agentarts/Dockerfile.locked-reuse)，复用已验证 ARM64 依赖镜像：

```text
jindiao:agentarts-20260906-multi-recovery-arm64
sha256:d29d2b66dc6700e11d09d57d39b1f3ab06c6b6ebaedf76743977f00726e3ffca
```

在无网络容器里读取**当前** pyproject/uv.lock，导出 agentarts extra + dev group、按实际 Linux/aarch64 marker 过滤后，229 个包的安装版本全部匹配；Git 依赖的固定提交一致。源码依赖仍为锁定的 openJiuwen 0.1.17，不代表打包旁边 `agent-core` 仓库的未发布源码。

构建前删除的仅为新镜像层里的旧 `/app/src`、config、mock_data、skills，再复制全部当前快照，断网重装 jindiao 包并 pip check；不是旧的两文件热修复。宿主机文件与旧镜像未删除。

白名单运行内容排除 dotenv、凭据、日志和业务 artifacts。镜像内 306 项非生成运行文件的哈希及完整文件集合验证通过；构建定义单独记哈希，egg-info 属于安装生成元数据，不按源码文件要求字节不变。不得直接在任意目录调用该 Dockerfile；它需要白名单构建上下文中的 `release-manifest.json` 及前置依赖核验。

诊断/构建脚本（不包含实际密钥）：

- `artifacts/agentarts-release-20260909/check_dependencies.py`
- `artifacts/agentarts-release-20260909/build_candidate.py`
- `artifacts/agentarts-release-20260909/check_image_api.py`

已有候选标签不得盲目重建或覆盖；续跑先验证回执、源码与 image ID 一致，再检查远端标签是否不存在。

## 本地验收结果

- 镜像为 ARM64、非 root，Docker HEALTHCHECK healthy，pip check 无依赖冲突。
- 无网络/无密钥/无宿主机端口的临时容器中，实际 HTTP 验证：single/multi 创建 202，result 200 / prototype-v1，8 个报告模块，SSE 连续且终态一致，另一 owner 查询 404。
- single 40 个事件，multi 44 个事件，均为明确 Mock 环境，终态 partial。
- `/invocations` SSE 200，包含报告事件；v2 旧嵌套入参返回 422。
- 同一候选镜像及其实际 SDK 依赖，断网跑 single/multi/正式管线回归：49 passed。
- 宿主机明确测试环境 `JINDIAO_ENV=test JINDIAO_ENFORCE_TOKEN_BUDGET=true` 全仓：1194 passed、3 failed、3 skipped。失败为旧冻结清单哈希不一致及两个旧 MultiInvestigatorTeam 测试固定低预算触发新预派发保护；未修改历史冻结清单或关闭预算以掩盖失败。
- Ruff src/tests 通过；mypy src 144 文件通过；mypy src/tests 在 7 个测试文件有 25 个类型错误，不能声称全仓静态检查通过。
- 所有本轮临时检查容器均随结束删除；原服务容器保留。

## 续跑：上传、环境配置、真实网关

1. 用户刷新当前 SWR 登录；仅在服务端/本地 Docker 使用登录凭据，不粘贴聊天。
2. 检查新标签不存在，核对候选本地 image ID 与回执；推送这个已验证候选镜像，然后回读远端 manifest 的 linux/arm64 与 config digest，补登记 registry digest。
3. 保存现有运行时为新候选版本，保留旧版本回滚。不得将 SWR 登录密码作为运行时 API Key。
4. 配置按当前 Settings 校验，不上传整份本机 `.env`。优先保留云端已验证 Qwen 路由与两项密钥，不把本机 DeepSeek 路由/密钥混配。
5. 建议显式保留 formal、tianyancha、attached、memory、禁止 Mock 降级、严格 token 预算。19 项目录与新版预派发估算需重新验收；可参考 ECS 已使用的 3200000 输入 / 300000 输出 / 3500000 总量、600 秒、160 次 LLM/工具、并发 6、schema 12、返工 2，但**本轮尚未应用这些环境更改**。
6. `JINDIAO_MULTI_DEMO_PARTIAL_ENABLED` / `JINDIAO_SINGLE_DEMO_PARTIAL_ENABLED` 默认 false。启用会允许调查/审核未完整结束时生成明确披露的部分报告，不应仅为得到 200 而悄悄启用。反馈 Demo、LTS、SFS、detached、生产入口权限不在本轮自动扩大范围。
7. 使用当前扁平请求 `{"customerName":"同盾科技（上海）有限公司","mode":"multi","execution_profile":"attached"}`，不传 scenario_id、enterprise 或 allow_degraded_mock。创建后订阅 events，再查询 result；检查 prototype-v1、无 Mock、真实证据、报告模块、终态与重放。partial 必须如实区分数据缺失和未完成调查/审核。
8. 若通过 BFF 验证，保留登录/Cookie/CSRF/服务端 API Key，仅绑定本地回环。历史 1 MiB SSE 上限应显式设置并测量当前帧大小；不可关闭大小/TLS/归属检查。

路由依据：[华为 ExecuteRuntimeWithPrefix](https://support.huaweicloud.com/api-agentarts/ExecuteRuntimeWithPrefix.html) 支持按后端实际方法调用 GET/POST，自定义路径去掉最前方斜杠，配置 PREFIX_MATCH；同一 Run 的请求应携带一致 `X-Hw-Agentarts-Session-Id`。

```text
POST https://defaultgw-mmytsytege.cn-southwest-2.huaweicloud-agentarts.com/runtimes/jindiao-demo/invocations/api/v2/due-diligence/runs?endpoint=Latest
GET  https://defaultgw-mmytsytege.cn-southwest-2.huaweicloud-agentarts.com/runtimes/jindiao-demo/invocations/api/v2/due-diligence/runs/{run_id}/result?endpoint=Latest
```

以上是真实网关路径；部署成功与真实企业报告验收应分别记录。

## 用户刷新登录后的发布

- `publish_candidate.py` 上传成功；没有覆盖不同内容的已有标签。
- 2026-09-09 17:35:37 GMT+08:00 控制台回读确认 Latest → `release-backend-0909`，镜像为本轮 `release-20260909-backend-arm64`。
- 仅更新两项已有变量：输入 token 2400000 → 3200000、总 token 2700000 → 3500000。
- 新增三项显式配置：`JINDIAO_ENFORCE_TOKEN_BUDGET=true`、`JINDIAO_MULTI_DEMO_PARTIAL_ENABLED=false`、`JINDIAO_SINGLE_DEMO_PARTIAL_ENABLED=false`。
- 保存前在内存逐项比对，其他 21 项环境变量完全未变（包括模型路由、两项业务凭据、预算次数/时限、attached/memory）。共 26 项；当前 Settings 校验通过。未更改 API Key 权限、委托、前缀路由、LTS/SFS/文件上传下载等能力。
- 原 `live-recovery-0906` 版本及镜像保留。上文旧版本阶段的阻塞记录保留为过程证据，不代表续跑当前状态。
- 真实验收准备使用 `artifacts/agentarts-release-20260909/gateway_acceptance.py`。首次调度被审批拒绝，未发出真实请求：要求明确授权具体企业/业务数据发送至第三方并产生费用。已从待运行脚本移除非必要贷款参数，保留公开企业名称；未通过改写命令、换工具等方式绕过审批，等待用户确认后才运行。
- 已单独完成更低风险的线上检查：`/ping` 200 / Healthy、`/openapi.json` 200；Run 创建与 result GET 路由存在，RunCreateRequest 必填 `customerName` 且不含旧 enterprise 字段；空 JSON 创建请求返回 422，没有生成有效 Run、没有调用模型/天眼查。

## 经明确授权的真实 multi 验收

用户确认允许以“同盾科技（上海）有限公司”的公开名称，经 AgentArts 调用 Qwen 和天眼查，运行一次真实 multi。未发送产品、金额、期限等贷款业务参数，未指定 Mock 场景或绕过严格预算。

- Run：`3a0a2f0d213f4cb492c42e439fdeee47`。
- `/ping` 200，创建 202（自探针开始约 0.64 秒），复用同幂等键返回同一 Run。
- SSE 首事件约 1.71 秒，snapshot.frozen 为 sequence 33、约 18.24 秒。
- 319.57 秒收到 `run.failed`，探针 320.22 秒退出 1 / verified=false。
- 错误：`agent_execution_failed` / `orchestration repair-round budget exhausted`。不是 token 上限或 HTTP 路由错误；没有提高返工/token 预算，没有自动创建第二个真实 Run。
- 状态 GET 200 / failed / result_available=false；Result GET 500，响应 1518 字节，无报告。
- SSE 94 条，sequence 1～94 连续，最大 data 行 1892 字节；包含 19 条 check.started、20 条 submission.accepted、5 条 execution.step.failed，没有 report.completed。事件条数不能当作唯一核查数量或完整结果证明。
- Last-Event-ID=1 重放与原流后 93 个事件逐对象一致；幂等键重复创建返回同一 Run；另一 owner 查询结果 404。
- 公共状态投影的 review 仍为 round=0/pending，checks 长度仅 1，budget.used/remaining 为空；不能把这些值理解成内部审核、核查或实际用量为零，投影完整性仍需检查。

### 离线定位：审核拒绝仍消耗返工额度

按系统化调试先只读检查实际发布源码，再使用本地合成快照和真实 BudgetLedger 调用注册审核工具。未请求模型、天眼查或云端，未修改业务代码/云配置。

`SubmissionBlackboard.build_submit_bound_review_tool()` 在调用 `submit_review()` 前先执行 `claim_repair_round()`；`submit_review()` 中才验证 check/evidence/owner 范围及版本。合成一个结构合法但引用未知 check 的审核，调用结果为：

| 调用 | 结果 | 已接受审核 | 已消耗返工轮次 |
| --- | --- | ---: | ---: |
| 1 | ValueError | 0 | 1 |
| 2 | ValueError | 0 | 2 |
| 3 | repair-round budget exhausted | 0 | 2 |

这证明存在“校验失败也扣返工额度”的确定性缺陷，但未取得本次云 Run 的具体被拒绝审核内容，不能证明它是该次云失败的唯一根因。

复现脚本：`artifacts/agentarts-release-20260909/diagnose_review_budget.py`。相关代码：`src/jindiao/investigation/blackboard.py` 的 bound review 工具与 submit_review；另一个 `InvestigationTeamState._build_submit_review_tool()` 也存在相同先扣费后提交顺序，需要一并检查。

建议下一步用回归测试约束“先校验范围/版本及幂等，再为有效新审核扣一次返工额度并提交”，同时保留 schema/tool 限额约束无效重试。不要通过加大返工上限或启用不完整调查兜底掩盖问题。再次真实调用应另行确认次数与费用；当前仍保留新云版本，旧 `live-recovery-0906` 可回滚，未自动切换或修改任何权限。
