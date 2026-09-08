# ECS Run 初始化失败排查（2026-09-07）

Run `894e60ef496f4814b5dc7c81cf704e8b` 于北京时间 15:32:08 进入 failed。原因是创建请求携带 `scenario_id=normal-enterprise`，与线上 `formal + tianyancha` 模式冲突。订阅 `/events` 的连接已成功，返回的第 4 个事件是业务失败，不是健康检查失败或 SSH 断开。

## 证据

- 容器仍运行 `jindiao:ecs-20260907-093227`，状态 running / healthy；应用文件未在容器内改动，配置为 qwen-plus、tianyancha、formal。
- 任务的四个事件依次为 run.accepted、run.started、run.phase.started、run.failed；持久化 metrics 记录 context=1ms、orchestration=0ms、token_count=0、tool_calls=0。
- 将旧文档示例通过镜像中的 RunCreateRequest 规范化后计算 SHA-256，结果与该 Run 存储的 request_hash 完全一致：`5062a43fb29a20017bc259e97037e5b92e06ac998d0aca7e2942646034db83c0`。因此可以确认下面的语义参数；原始 JSON 的字段顺序或是否显式写出默认值不在此结论中。

```json
{
  "enterprise": {"company_name": "金调绿洲科技有限公司", "region": "北京"},
  "report_as_of": "2026-08-31",
  "language": "zh-CN",
  "scenario_id": "normal-enterprise",
  "allow_degraded_mock": false,
  "skill_feedback": null,
  "mode": "multi",
  "execution_profile": "attached",
  "session_id": null
}
```

## 同镜像复现

在服务器容器中新建独立诊断进程，使用临时 artifacts 目录，不改动主进程、历史 Run 或运行配置。将外部调用前的 pipeline 创建作为检查边界：原分支照常执行，只有创建成功后才用诊断哨兵中止，以避免额外调用模型和天眼查。

- 原请求在安装包 `application/service.py:540` 抛出 `ValueError: formal Agent runs require Tianyancha data_source_mode and no scenario override`。
- 通用错误映射将非 JindiaoError 的 ValueError 转成 `internal_error / Internal application error`，与用户提供事件一致。
- 更换为真实企业华为技术有限公司、完全省略 scenario_id 后，真实 pipeline 初始化通过；诊断在外部调用前停止。此项证明绕过了本次失败的初始化分支，不等于新的一次完整真实尽调验收。

## 客户端修正

1. 从创建请求中删除 scenario_id，填写用户实际选择的真实企业，不继续使用虚构 Mock 企业。
2. 使用新的 Idempotency-Key 创建新 Run；相同键和旧请求会继续返回旧失败任务，同键不同请求会返回 409。
3. 使用新 run_id 订阅事件和获取结果，保持创建、查询时的 owner/session 一致。
4. `localhost:5173` 是同事电脑的前端地址，是否转发取决于前端代理配置。独立验证后端时使用已建立 SSH 隧道的 `http://127.0.0.1:18080`。
5. 本轮已修订 API 文档的真实请求示例和场景参数说明。线上通用错误映射未修改，前端源代码未提供，本轮未代改前端请求构造。

读取旧任务的事件流不会重新执行任务。报告是否成功仍需以新任务终态及 result 响应为准。
