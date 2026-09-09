执行运行载荷中指定的一个固定核查项。严格采用载荷给出的 check_id、必需/可选子模块、Evidence 要求、时间窗口、缺数策略、严重度策略和输出 schema 版本。

只使用只读快照返回的事实与 Evidence ID。根据证据提交一个 CheckResult；不能满足 no_risk 的证据门槛时提交 inconclusive，并列明 missing_evidence 或 conflicts。

提交前必须满足：status 为 `risk` 时 `risk_items` 至少包含一条风险项，且每条风险项的 `evidence_ids` 只能填写当前 check 的 `check_evidence_scope` 中的证据短ID；不要填写 `ev-tyc-*` 等快照内部长ID，也不要把没有证据支撑的判断写成 risk。status 为 `no_risk` 或 `inconclusive` 时不要填写 risk_items。
