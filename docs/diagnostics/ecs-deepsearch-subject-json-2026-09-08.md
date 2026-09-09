# ECS DeepSearch subject JSON compatibility hotfix

## Root cause

On ECS run `4fac7f170875416a9e48c78496abaec9`, the GLM-compatible model emitted the `subject` tool argument as a JSON string. The annual-report adapter passed it directly to `AnnualReportToolInput`, which requires a `ResolvedSubject` object. Three tool attempts failed validation and the DeepSearch agent reached its iteration limit without submitting `supplement:annual_reports:4bae448b1f09f056`.

## Fix

`DeepSearchAgent` now decodes a string `subject` exactly once when it is valid JSON and requires the decoded value to be an object. Existing Pydantic validation and exact subject/cutoff authorization checks remain in place. Malformed, non-object, double-encoded, incomplete, unknown-field, and wrong-subject inputs are rejected without invoking the provider tool.

## Verification

- Local DeepSearch unit/contract tests: `18 passed`.
- ECS candidate image: `jindiao:ecs-20260908-deepsearch-subject-json-01`.
- Candidate source hash in both application copies: `68c9f61b0377855675bd8cb835b6be5a8e29aba5dbbec22a866cefcee506f1d1`.
- Main container is healthy and loopback `/ping` returns `{"status":"Healthy"}`.
- Existing container retained as `jindiao-ecs-before-subject-json-20260908`; persistent artifact volume reused.
- No paid business Run was created during deployment.
