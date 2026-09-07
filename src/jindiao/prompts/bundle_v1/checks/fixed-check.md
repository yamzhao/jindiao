执行运行载荷中指定的一个固定核查项。严格采用载荷给出的 check_id、必需/可选子模块、Evidence 要求、时间窗口、缺数策略、严重度策略和输出 schema 版本。

只使用只读快照返回的事实与 Evidence ID。根据证据提交一个 CheckResult；不能满足 no_risk 的证据门槛时提交 inconclusive，并列明 missing_evidence 或 conflicts。
