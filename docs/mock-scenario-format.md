# Mock 场景格式

## 目标

`mock_data/scenarios/<scenario_id>/` 以企业为边界保存固定、可重现的尽调输入。运行时只能读取 manifest 中标记为 `runtime` 的文件；`expected/` 是评分器专用的标准答案区。

## 目录约定

```text
mock_data/scenarios/<scenario_id>/
├── manifest.json
├── company.json
├── governance.json
├── judicial.json
├── operations.json
├── peers.json
├── corpus/
│   └── *.md
└── expected/
    ├── findings.json
    └── decision.json
```

## Manifest v1

`manifest.json` 的机读 JSON Schema 位于 [`mock_data/scenarios/schema.json`](../mock_data/scenarios/schema.json)，应用内的权威契约是 `jindiao.scenarios.ScenarioManifest`。关键字段如下：

| 字段 | 规则 |
| --- | --- |
| `schema_version` | 当前固定为整数 `1` |
| `scenario_id` | 小写 kebab-case，必须与目录名相同 |
| `enterprise_key` | 至少含企业名称或统一社会信用代码，可声明别名 |
| `version` | `v1`、`v1.1` 或 `v1.1.0` 形式；内容改变时必须升版 |
| `as_of_date` | ISO 8601 日期，表示整个场景的数据时点 |
| `files` | 相对 POSIX 路径、小写 SHA-256 和访问角色；路径必须唯一 |

`role=expected` 的文件必须放在 `expected/` 下，而 `expected/` 下的文件也必须标记为 `expected`。绝对路径、`..`、反斜杠和未在清单中的文件均被拒绝。

## 版本与哈希规则

1. 同一 `scenario_id + version` 是不可变输入；任一业务数据、语料或预期结果改变都要升版。
2. SHA-256 基于文件原始字节计算，不做 JSON 重排、换行或 Unicode 归一化。
3. 应用加载时验证所有 runtime 文件并生成内容寻址的 `scenario_snapshot_id`。
4. 评分器在运行结束后由独立 loader 验证并读取 expected 文件，禁止将它们放入 Agent 上下文或 DeepSearch 索引。
