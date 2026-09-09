# ECS single demo bounded reporting — 2026-09-09

## Root cause and scope

Run `1b37c269e501444c9c423a1814d5b3fb` spent about six minutes in a single
model call (07:30:33–07:36:33 UTC). It reported 10000 output tokens, all reasoning,
with zero content and zero tool calls, then continued. The old single path used
the entire remaining Run deadline and only handled missing checks after a normal
stream return. A runtime timeout bypassed that fallback.

The new explicit `JINDIAO_SINGLE_DEMO_PARTIAL_ENABLED` flag defaults to false.
When enabled, investigation is capped at min(180 seconds, half the remaining Run
allocation). Timeout returns only real accepted checks; missing check IDs and
incomplete self-check are disclosed. No missing results or self-check completion
are synthesized in this demo path. Strict/non-demo behavior is retained.

The single model wrapper tracks active chunk-consumer tasks and cancels only its
own pending streams before reporting, allowing usage reservations to settle.
When usage is incomplete, the existing partial-report policy prevents any new
report model allocation and renders validated facts and rule suggestions.

## Verification

- Failing regressions reproduced both the missing bounded single behavior and
  an unclosed child model stream. They pass with the fix, including a stream whose
  successive chunks are consumed by different tasks.
- 106 focused tests passed with `JINDIAO_ENV=test` and
  `JINDIAO_ENFORCE_TOKEN_BUDGET=true`. They include single/multi execution,
  partial-report no-dispatch behavior, feedback replay and deployment tests.
- In the default development environment, the existing private-peer allowance
  causes one feedback permissions test to fail (`192.0.2.1` expected denied).
  No Host, Origin or peer security gate was changed in this fix.

## Deployment

Release: `ecs-20260909-single-partial-01`. Existing `jindiao:deps-amd64` reused;
build is network-disabled with dependency installation disabled. Runtime env
backup: `/opt/jindiao/runtime/ecs.env.before-single-partial-01`.
Only the single demo flag is added; multi demo, strict token budget, model route,
public feedback configuration and other thresholds are retained.

Deployment published successfully with 1254 historical files verified. App image:
`sha256:bdc9864072fd2788fb4fb32cfce6f632479eb7c7795bc60cb27208997a98d118`.
Single investigator SHA-256 matches tested source:
`e58bfa75a0ae28e120b202c767ed2944766a0ef4eba3fd3aae7f66e050cb5c06`.
Public API maintenance gate was removed after successful config/code verification.

First live single Run: `4684d64dce8044f18470182da5cdc041`.
Result returned HTTP 200, partial, 8 report modules and 50863 Markdown characters.
Feedback returned HTTP 201 with passed evaluation, awaiting approval (not active):
`evo-31e861fccaf7e9ae37c7058effbb989e1fa2f254750613ab402cf27a016380fc`.

However, investigation ended normally after two reasoning-only responses at
07:53:33 UTC, and the report model then took another 207 seconds, twice returning
empty content. Release `ecs-20260909-single-partial-02` therefore skips further
report model calls for **all demo-partial single investigations**, even when usage
is complete. Fully completed single investigations keep the normal model report
path. Disclosures distinguish incomplete investigation from unknown provider usage.

107 focused tests pass in the explicit test environment, including known-usage
single partial no-dispatch and strict non-partial rejection regressions.
Release 02 published successfully; 1265 historical files verified, including the
first feedback candidate. Dependency image ID stayed unchanged. App image:
`sha256:8bb9c6f9d89553670c34e00b30ae8d42c4b52de010713d07b9438cbae2fba9a5`.
Pipeline SHA-256:
`c92b4e55c648aab6cebe6e738e37e4c1a287a8e2a06573fd6cc8676813a224a6`.
First feedback detail still returned HTTP 200 after deployment, evaluation passed
for 9 cases, awaiting approval, not active.

Second live single Run: `a2ea29b8737c49b0b9b4507ecfc2937f`.
Final acceptance passed:

- Service duration 167811ms (2m48s), report phase 45ms; completed at
  2026-09-09 08:05:30 UTC. Single ended without committing any check results after
  reasoning-only responses; the live case used early-exit partial reporting, while
  the 180-second forced timeout and child-task cleanup are covered by regression tests.
- Public `/result`: HTTP 200, mode single, status partial, 8 report modules,
  170 evidence entries and 50563 Markdown characters, `is_mock=false`.
- All 19 missing checks and incomplete self-check are disclosed. Risk count zero
  means no submitted risk findings, **not** a verified absence of risk.
- Provider usage was complete in this live case. Report phase made zero LLM
  requests and consumed zero new tokens; it rendered facts and rule suggestions.
- Same Run `/feedback`: HTTP 201, evaluation passed for 9 cases, awaiting approval,
  not active. Candidate:
  `evo-3de88d3a5c771df2bb545ee0e9ba127153492d2d76eb26324cfc81a5091a14d7`.
- Feedback detail GET returned HTTP 200; no candidate was approved or applied.
- Strict token budget, original dependency image and public feedback guards remain
  unchanged. The API maintenance gate is removed; old containers/volumes retained.
