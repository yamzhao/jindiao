# Minimal example

Input:

```json
{"subject":{"subject_id":"tyc:2962178558","company_name":"同盾科技（上海）有限公司","unified_social_credit_code":"91310104MA1FR5Q84D","source":"tianyancha","resolved_at":"2026-09-04T00:00:00Z"},"queried_at":"2026-09-04T00:00:00Z","report_as_of":"2026-09-04"}
```

If no eligible report exists within the bounded lookback, return `verified_empty` with the checked years. Do not convert that outcome into `source_error` or a zero employee count.
