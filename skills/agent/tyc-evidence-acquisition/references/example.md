# Minimal example

Input:

```json
{"enterprise":{"company_name":"示例企业"},"domain":"judicial","capability":"dishonest","allow_degraded_mock":false}
```

Expected behavior: anchor one subject, query the capability, then return either traceable records or an explicit empty/missing/error state. Do not infer “no risk” from source failure.
