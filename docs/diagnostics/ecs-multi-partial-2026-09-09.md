# ECS multi partial-report deployment — 2026-09-09

## Deployment

- Target: `1.95.121.114`, main container `jindiao-ecs`.
- Previous application image: `jindiao:ecs-20260909-config-restore-01`.
  Its Settings class had no `multi_demo_partial_enabled` field.
- Published release: `ecs-20260909-multi-partial-01`, source commit `eee875d`.
- Existing dependency image reused unchanged: `jindiao:deps-amd64`,
  `sha256:14dffb6e58b022d63fe05967a38c37a516a5ccdf2d331b1cb8b0f2bd725bf688`.
  Build used `--network none`, `JINDIAO_INSTALL_DEPS=false`; no dependency installation.
- Runtime env source: `/opt/jindiao/runtime/ecs.env`; only added
  `JINDIAO_MULTI_DEMO_PARTIAL_ENABLED=true`. Strict token budget remains enabled,
  total timeout 600 seconds and concurrency 6 unchanged.
- Original env retained as `/opt/jindiao/runtime/ecs.env.before-multi-partial-01`.
- Public API was temporarily gated with nginx 503 during deployment; no active
  Runs were present. Original nginx configuration was restored and validated.
- Used preflight port 18083 because the default 18081 was already occupied.
  The unrelated preflight container was left untouched.
- Deployment receipt reports `published`, with 1171 historical files verified.
  Old container and data volume retained for rollback.
- New app image ID:
  `sha256:6a252c259170ffbe32f3ef22ebfcd39bbcee895d37cef61d859cdd338c80d9b1`.
- Investigator code SHA-256 matches local verified source:
  `01be4a557ca7778ce0cff56425dfc7d7b30fab319835cf07abaadd3e7cbc40ee`.
- Public feedback GET guard probe returned `404 evolution_not_found`, not Host,
  origin or demo-disabled rejection. No feedback was created by the probe.

## Verification

- 50 focused deployment/team/report/pipeline tests passed with strict token budget.
- First live multi Run: `947ac8f1f42540f78413de6fce737144` failed after 185.6s:
  demo shutdown cancelled an unfinished model request and strict usage completeness
  blocked reporting. Recorded tokens were 698805, below the 3500000-token cap;
  this was missing usage, not token limit exhaustion.
- Runtime limits inspected: input 3200000, output 300000, total 3500000,
  LLM requests 160, tools 160, concurrency 6, schema retries 12, repairs 2, timeout 600s.
  `JINDIAO_ENFORCE_TOKEN_BUDGET` is absent from the env, so the code default true applies.
- Follow-up release `ecs-20260909-multi-partial-02` keeps these limits and permits
  only deterministic reporting after an actual demo-partial team result with unknown
  usage. It forbids constructing any subsequent report model; real provider usage
  counts remain unchanged. The service-level regression checks this no-dispatch
  boundary and verifies a partial report with explicit incomplete-usage disclosure.
- 56 focused tests pass for the follow-up (including strict non-partial rejection).
  Release 02 published successfully;
  1205 historical files verified. New image ID:
  `sha256:5eb5ce4c8155ff5df36ba63bb3aaeaedd46481a83bcad0b4e9eef3813dc341e5`.
  Original dependency image and all budget thresholds remain unchanged.
- User explicitly authorized cancelling single Run
  `e01b64e07eac403a9e302400acd9448d`; cancellation was confirmed before switching.
- Second live multi Run: `69070b719cde488b9933b8da4b6c1006` passed public `/result`
  acceptance: HTTP 200, `mode=multi`, `status=partial`, 8 nonempty report modules,
  170 evidence entries, 52319 Markdown characters; `is_mock=false`.
- Completed at 2026-09-09 07:27:32 UTC; service duration 180741ms (about 3 minutes).
  All 19 checks committed, but independent review was not completed. Markdown and
  summary explicitly disclose skipped/incomplete review and incomplete provider usage.
- The live unknown-usage fallback was exercised: total LLM requests 40, provider
  usage requests 39, `provider_usage_complete=false`; reporting added **zero** LLM
  requests and zero tokens. It rendered validated facts and deterministic rule
  suggestions, not new model analysis or fabricated Reviewer approval.
- Strict budget, 3500000-token cap and the original dependency image were preserved.

This is an opt-in demo partial-report path, not independent Reviewer approval.
Existing failed Runs are not rewritten or automatically retried by deployment.
