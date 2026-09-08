# 数字字符串兼容与本地启动验证（2026-09-07）

## 修复范围

`RunCreateRequest` 在严格数值校验前，仅对 `amount` 和 `term` 的数字字符串做规范化；不改 v1，不关闭全局严格校验，不变更 ECS。

- `amount: "2"` 与数值 `2` 等价；输入单位仍为万元，报告输出为 20000 元。
- `term: "12"` 与整数 `12` 等价。字符串首尾空白允许；期限小数字面量（包括 `"12.0"`）不允许。
- 未填请省略或传 `null`；空字符串、布尔值、非正数、非法文本及非有限金额保持 422。
- 极大指数金额字符串保留原始文本进入校验，避免把无穷大带入 HTTP 错误响应造成 500。
- v2、invocations 和 BFF 共用入口规范化；数字字符串与数值的幂等哈希相同。OpenAPI 输入类型与文档已同步，内部序列化仍为数值。

## 自动化验证

- 修改前：新增兼容/schema/API/BFF 用例共 16 个按预期失败。
- 修改后：相关契约、Run API、BFF 测试 **167 passed**。
- 完整离线测试：**885 passed / 1 failed / 4 skipped**，覆盖率 **87.45%**。
- 唯一失败为既有冻结清单校验：`release/frozen-manifest-v1.json` 中 `pyproject.toml` 的历史哈希与当前 HEAD 不同；该文件在本轮未修改，未重写冻结清单或降低覆盖率门槛。
- 因沙箱回环网络限制跳过的 BFF TCP 流式测试已单独授权复跑：**1 passed**；三个需真实凭据的测试仍未启用。
- 四个相关 Python 文件的 Ruff、格式、mypy，以及 `git diff --check` 通过。

## 本地热重载与 HTTP 验证

原有 `make dev` 风格的 Uvicorn 服务监听 8000，已自动加载修复。`/ping` 返回 Healthy，OpenAPI 的两个字段包含 string 输入类型。使用带禁止探针字段的请求确认 `"2"` / `"12"` 不再产生字段类型错误，不创建真实 Run；超大金额字符串单独请求返回 422。

隔离离线 TCP 服务验证了创建 202、数值/字符串幂等、结果 200、SSE 200、报告金额 20000 元、期限 12 月及 10 个非法请求 422。Run 为 Mock 资料缺口对应的 `partial`，不是执行失败；未调用真实供应商。该临时服务验证后已停止。

## 一键脚本验证

用户随后要求执行 `bin/local`。本轮实际执行默认命令 `./bin/local`，首次镜像构建约 8 分钟，命令退出码 0，容器状态为 running / healthy；容器保留运行供继续联调。未修改启动脚本。

- 独立 Compose 项目：`jindiao-local`；首次检查没有同名项目，8080 端口空闲。
- 新建 `.env.local`，权限 0600，内容与 `deploy/local/mock.env.example` 一致，且已被 Git 忽略；原 `.env` 未覆盖。
- 默认使用 `offline_mock + deterministic_harness + mock`，绑定 `127.0.0.1:8080`，独立命名数据卷，不影响 8000 开发服务或 ECS。
- 首次构建需要安装锁定的 229 个依赖；已有一次 PyPI 超时重试恢复，DeepSearch 固定 commit 拉取成功。

启动与接口验证结果：

| 项目 | 结果 |
| --- | --- |
| 容器 | `jindiao-local-jindiao-1`，running / healthy |
| 镜像 | `jindiao:local`，`sha256:a61d1c27f3c94d26235a5bff29ec4d6df1607977df3d61c7f10215567ebbe85e` |
| 端口 | `127.0.0.1:8080 → 8080/tcp` |
| 数据卷 | `jindiao-local_local-artifacts` 挂载 `/app/artifacts` |
| 运行配置 | 容器内确认 `offline_mock / deterministic_harness / mock`，模型及天眼查凭据均未配置 |
| 代码一致性 | 容器实际导入的 `contracts/runs.py` 与工作区 SHA-256 均为 `a953aa2a1f93d17d069a1856d32238ff215008c92ecb84b40b00cf475df02b2b` |
| 健康检查 | `/ping` HTTP 200，Healthy |
| 创建 | `amount: "2"`、`term: "12"`，HTTP 202 |
| 幂等 | 同键改为数值 2、12，HTTP 202，返回同一 Run |
| 结果与事件 | result HTTP 200，events HTTP 200，存在 `report.completed` |
| 报告字段 | 申请金额 20000 元，期限 12 月 |
| 非法输入 | 6 个非法金额/期限请求均返回 422 |

本地 Mock 测试 Run：`3e5f360e0795406f91ff557ce006c478`，状态 `partial`（Mock 资料缺口），保存在独立数据卷。未创建真实供应商任务。公网 workbench 仍然连接 ECS，不会自动切换到本地。

本地接口文档：<http://127.0.0.1:8080/docs>。后续在项目根目录使用 `./bin/local status`、`./bin/local logs` 查看服务，使用 `./bin/local stop` 停止容器（保留数据卷）。
