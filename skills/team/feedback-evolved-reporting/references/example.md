# Current Demo lifecycle

Create a synthetic report with a judicial source_error, submit gap_disclosure_placement feedback for judicial-risk, then query include=reports. Inspect the source report and all eight fixed cases. Only actual placement improvement with unchanged mandatory content may yield awaiting_approval.

Run the local apply command with the same artifact root. A newly accepted Run must use the candidate version and match the evaluated after report; the source Run and completed SSE remain unchanged. Run reset, then verify another Run uses 1.1.0 and the original appendix-only report.

`example.input.json`, `example.output.json`, input/output schemas and `evals.json` illustrate the legacy reusable package ABI. They are not real evaluation evidence or current HTTP payloads. The output example explicitly rejects this obsolete path. Current v2 contracts live in `jindiao.contracts.report_policy.ReportFeedbackRequest`; measured output comes from the running Demo.
