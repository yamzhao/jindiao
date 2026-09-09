# DeepSeek local backend validation plan

**Goal:** Switch the existing local OpenAI-compatible route to deepseek-v4-flash-0731 and, as explicitly requested, disable local numeric token-budget enforcement while retaining accounting and other execution guards.

**Architecture:** Add host-configured `enforce_token_budget` (default true). Copy it through Settings → immutable RunPolicy → RunBudget and comparison fingerprint. False skips only input/output/total token admission and post-use limits, including cross-phase subtraction/report guards. Usage uncertainty checks, evidence validation, timeout, model/tool counts, bounded per-call output and no-Mock semantics remain intact.

**Trade-off:** False is observe-only for token totals and removes the former 400000-token cost bound. It does not bypass the provider's context limits or guarantee complete source data. This is a local opt-in; no ECS writes.

## TDD and rollout

- [x] Add tests for settings propagation, oversized model requests and recorded over-limit usage, unchanged default strict mode, remaining deadline/request guards, prior-cost/report handling and fingerprint mode distinction.
- [x] Observe failing tests, then implement the small conditional guards in settings/context/base/formal_pipeline/product_model.
- [x] Set `.env` MODEL_NAME=deepseek-v4-flash-0731 and JINDIAO_ENFORCE_TOKEN_BUDGET=false only; preserve route and credentials. Add strict default to .env.example and document current threshold formula.
- [x] Run related/full offline tests with credentials disabled or injected fake providers. Do not infer runtime success from health.
- [x] Verify no active local Run, rebuild using bin/start.sh, verify effective installed model/flag and retained limits.
- [x] One approved real Run `e6352988490d4d95a5f8da15f8585cc3` returned a new non-Mock report, HTTP 200 and no generation_failed. 48 concurrent reads across restart and exact SSE replay passed. Status remains partial and financial values remain incomplete; see `docs/diagnostics/local-deepseek-observe-10-2026-09-08.md`.
