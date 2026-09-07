# 报告策略基线夹具

`baseline-view.json` 来自现有 `test_reporting_pipeline.assemble_complete_view()` 的固定合成输入；`baseline-report.md` 在增加策略参数前由原 Markdown renderer 生成。均不包含真实企业取数或用户反馈。

这两份文件只用于验证已有报告不被削弱：默认渲染与显式 `appendix_only` 必须逐字等于 golden，不能在测试失败时直接用候选输出覆盖它。JSON 保留 Python 序列化的 `1.0` 等数值表示，避免无关的整数/浮点转换改变自由字段的展示文本。

该夹具不是设计要求的八个独立回归样本，更不是源用户报告。独立 suite 位于 `config/report-replay-suite-v1.json`，内含八个预先固定的安全视图、显式 oracle 与 cases_sha256；真实回放加上源报告共九例。不能用此单一 golden 或通过测试的数量宣称演进效果。
