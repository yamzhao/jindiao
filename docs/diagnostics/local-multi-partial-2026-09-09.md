# Local multi demo partial-report path

The earlier Reviewer bypass required all 19 check submissions. Run
`36921ce547554c53974c12da4d92a06f` committed only 11; the eight financial/operations
checks never committed, so the bypass could not run and the 574-second deadline expired.

## Scoped change

- `JINDIAO_MULTI_DEMO_PARTIAL_ENABLED` defaults to false and is enabled only in
  `deploy/local/compose.live.yaml` for this local demo.
- Multi investigation runs for at most 180 seconds, or half its remaining allocation
  if shorter. The remaining allocation is reserved for reporting; single is unchanged.
- Accepted specialist submissions are retained. Missing checks are not synthesized,
  and no empty Reviewer submission is fabricated.
- Partial execution retains cancelled/incomplete member status and missing task IDs.
  The report summary and Markdown disclose missing checks and incomplete review and
  recommend manual review. Existing evidence validation still applies to retained checks.
- Explicit cancellation, missing assignment plans and non-timeout execution errors
  are not converted into successful reports. This is not a guarantee against all
  provider failures and is not a production approval workflow.

Use `bin/start.sh` to rebuild/recreate after code or configuration changes;
`bin/restart.sh` alone does not load new code/configuration. Artifact volumes are retained.

## Verification

The deterministic regression reproduces one accepted check followed by an idle stream;
demo mode returns partial with 18 incomplete tasks and no reviews, while strict mode
raises deadline exceeded. Report assembly tests verify the disclosure survives into
the public summary and Markdown, and strict assembly still rejects missing review.

Live acceptance Run: `2c9fb65d34694b5292533bf519593ee6`.

- Started 2026-09-09 05:36:15 UTC; report generated 05:42:49 UTC (394.5 seconds).
- Both `127.0.0.1:8080` and `localhost:5173` GET `/result` returned HTTP 200.
- `meta.mode=multi`, `meta.status=partial`; 8 nonempty structured report modules,
  170 evidence entries, 52,409 Markdown characters, with explicit incomplete-review disclosure.
- This live Run submitted all 19 checks before the cap and used the review-skipped
  path. Persisted Reviewer status is `cancelled`, with no fabricated review task IDs.
  Missing-check timeout behavior is covered by the deterministic stalled-runtime test,
  not claimed as reproduced in this live run.
- `provider_usage_complete=false` remains visible in metrics; no usage completeness
  or missing provider costs were fabricated.
- 78 focused tests passed. The formal pipeline's 15 tests passed with
  `JINDIAO_ENFORCE_TOKEN_BUDGET=true`; 3 of those tests still fail under the local
  observe-only budget setting because they assert strict usage rejection. This
  change does not alter the pre-existing usage-completeness policy.
- Ruff and `git diff --check` passed for the changed code.
