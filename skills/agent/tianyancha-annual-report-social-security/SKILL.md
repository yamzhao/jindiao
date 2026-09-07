---
name: tianyancha-annual-report-social-security
description: Retrieve the latest eligible Tianyancha annual-report social-security disclosure for an already resolved enterprise. Use only as bounded annual-report evidence supplementation; a missing report is a valid verified_empty result.
---

# Tianyancha Annual-Report Social Security

Use this Skill only after the enterprise has been resolved to an exact Tianyancha subject whose ID has the form `tyc:<numeric-company-id>`. Invoke `tianyancha_annual_report_social_security` with the complete resolved subject, the current query timestamp, and the report cutoff date.

The Tool owns all executable controls: it may access only the exact HTTPS host `www.tianyancha.com`, starts with the most recent report year eligible for the cutoff, applies a bounded lookback, rejects subject/year/content mismatches, and closes its HTTP client after each invocation. Do not construct annual-report URLs yourself and do not substitute search snippets or another website.

Treat outcomes exactly:

- `verified_records`: submit the Tool's Evidence unchanged for `operations.annual_reports.social_security`. This is partial `annual_reports` coverage and does not represent a complete financial annual report.
- `verified_empty`: report that no eligible annual report or no public social-security disclosure was found. This is valid coverage and is not an error.
- `source_error`: retain the error as source state. Do not infer zero insured employees and do not replace it with Mock data.

Never infer headcount from one insurance count, never turn “企业选择不公示” into zero, and never set a risk score or final credit decision. Return only the typed provider outcome and its traceable Evidence.

Read [references/contracts.md](references/contracts.md) for the input/output boundary and [references/example.md](references/example.md) for a minimal invocation.
