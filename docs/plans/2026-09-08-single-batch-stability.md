# Single Investigation Stability Implementation Plan

> Execute inline using the executing-plans and test-driven-development workflows. Preserve the current dirty feature branch; no commits, ECS writes or budget increases.

**Goal:** Produce and repeatedly retrieve a real local report within the existing 400000-token budget, with explicit failures rather than fabricated completion.

**Architecture:** Keep the real ReActAgent and frozen snapshot. Replace the single investigator's one-check submission loop with one bounded, atomic full-catalog submission. The submission includes the existing deterministic completeness self-check; every model-authored decision still passes Run binding, scope and evidence validation. Stop the Agent after accepted completion via openJiuwen's public Rail interface. Other roles retain their existing tools.

**Tech Stack:** Python 3.11, openJiuwen 0.1.17, Pydantic 2, FastAPI, pytest, Docker Compose.

## Decision (ADR)

Status: implemented under the user's backend-debugging request; real verification in progress.

Observed evidence: Run `78700eb0bd284a3a8bd8b7112a93b02a` completed acquisition, then only submitted `registration-status-normal` and `registration-change-anomaly`; successive investigation requests carried 97468 and 97909 input tokens. A prompt requesting parallel one-check calls did not cause batching. Raising the 30-second timeout to 300 seconds moved failure to the input budget.

Decision: make batching part of the Tool schema, require the exact owned check set, validate the whole batch before mutation, and complete the Agent after this verified final submission. This is a submission-protocol change, not rule-based replacement of Agent judgments.

Alternatives: raising the budget increases user-approved spending; truncating evidence risks unsupported judgments; prompt-only parallelization already failed. Neither is used here.

Trade-off: an invalid batch is retried as a whole. Bounded schema retries and atomic rejection protect integrity; tests must cover missing, duplicate, foreign and invalid-evidence results. No missing decisions are synthesized.

## Task 1: Regression tests

Files: `tests/unit/test_submission_blackboard.py`, `tests/unit/test_single_investigator_agent.py`.

- [x] Add tests for all-owned-check batch schema, atomic rejection, idempotency and retained evidence gates.
- [x] Change scripted Agent fixtures to two calls: read context, submit full result list. Limit model requests to two and provide no final natural-language response; real ReAct execution must still complete.
- [x] Test incomplete batches never mark self-check complete and invalid schemas can be corrected once.
- [x] Run the targeted tests and observe failures before implementation.

## Task 2: Bounded final submission

Files: `src/jindiao/investigation/blackboard.py`, `src/jindiao/agents/single_investigator.py`, `src/jindiao/prompts/bundle_v1/investigation/roles/single-investigator.md`, prompt manifest.

Implemented by `SubmissionBlackboard.build_submit_bound_check_results_tool` under the blackboard lock:

```python
drafts = SubmitBoundCheckResultsInput.model_validate({"results": results}).results
check_ids = [item.check_id for item in drafts]
if len(check_ids) != len(set(check_ids)) or set(check_ids) != expected:
    raise ValueError("batch must contain every owned check exactly once")
```

The existing `_bind_check_result`, `_validate_result_scope`, `_validate_evidence_gate` and `_check_receipt` validate every item and prepare all receipts; submission dictionaries are mutated only after the complete batch passes. No second evidence-validation implementation was introduced.

Register a run-local `AgentRail.before_model_call` that calls `ctx.request_force_finish({"output": "All fixed checks submitted and completeness verified", "result_type": "answer"})` only after the complete batch was accepted. Do not force finish on missing/invalid submissions.

- [x] Keep timeout at 300 seconds and token budgets unchanged.
- [x] Run targeted tests, lint, types and the full offline suite; document unrelated baseline failures separately.

## Task 3: Real and retrieval verification

- [x] Check no nonterminal Runs before applying the new local image.
- [x] Verify container source hashes and effective budgets.
- [x] Run one scoped real Huawei single request through local port 18088; no Mock identifiers/scenario overrides.
- [x] Run `dabbc8e06a4b4b9cb408058cf5b7ca00` is non-Mock, contains source evidence, and has no generation failure. Repeated/concurrent reads and exact SSE replay passed before and after restart. Data completeness and UI acceptance remain separate (see the follow-up below).
- [ ] If another failure occurs, investigate that evidence before further modifications; never claim a successful report from mere 202 or health responses.

## Follow-up: bounded writing context

The first batch-enabled real Run `5c4681cd4aba42a3bb46f021409529e3` completed all checks after one schema correction, but its report-writing budget reservation rejected the redundant full context. It returned verified public facts with explicit `generation_failed` gaps, not a completed writing pass.

Added test-first writing-context compaction in `product_generator.py`: private short Evidence IDs are restored before existing validation; duplicate relationship graphs use a reference in the model input; empty values and audit-only metadata are omitted from the writing request; evidence rows share one column list. Public facts, graphs, full citations, provenance and report schema remain unchanged. Zeros/false values are preserved. Unknown model citations remain rejected.

Offline measurement against that real result: context 60837 bytes; output schema 4547 bytes; with 3000-byte instruction margin 68384 bytes, below its 74600 remaining input budget. No token guard or spending limit was relaxed.

- [x] Test context size reduction, immutable public facts, zero/false retention, graph reference reuse, restored analysis/suggestion/risk citations and rejection of unknown citations.
- [x] Full offline suite: 925 passed, 1 existing frozen-manifest failure, 4 skips; coverage 87.51%. Loopback BFF streaming was separately verified with local-network permission.
- [ ] Verify batch+compact real Run `6362504a4090471690457e629ced6a1a`, then repeat/concurrent/read-after-restart acceptance without additional supplier calls.

That Run completed investigation in two model requests and successfully called the report model, but report text was rejected by the numeric gate. Total observed cost was 181808 tokens; no budget increase occurred. The failure was retained as explicit generation gaps, not hidden.

The numeric gate had independently reproducible representation bugs: string comparison rejected 120000 vs 120000.0 and ISO vs Chinese date notation; globally removing commas could misread a JSON numeric list as one amount. Added red/green regressions, Decimal value comparison and structural JSON number extraction. Opposite signs, invented values and invalid citations remain rejected. All invalid analysis sections and their unsupported numbers are now included in the bounded model-repair feedback rather than only the first generic error. The report prompt requests qualitative wording when a numeric restatement would require conversion or new calculations.

- [x] Numeric equality/date/grouping and multi-section repair-feedback regressions.
- [ ] Verify real Run `5de2ff8ddfec4e29b945a7ad7a6741ff` with these corrections, then perform read stability/persistence checks.

## Follow-up: prototype status consistency (no new supplier calls)

That Run subsequently failed on cross-check Evidence scope. Added private compact citation IDs, explicit per-check citation scope, restoration before canonical validation, and per-check error feedback; scope checks remain strict. A latest-version paid probe has not been executed after the approval boundary stopped it.

The prototype review then exposed persisted terminal Runs with 0/0 progress despite seven completion snapshots. Implemented product-step progress projection and read-only restoration from existing history, preserving stored reports and other counters. Added explicit Agent timeout error/HTTP 504 handling.

- [x] Red/green tests for unique step counting, failed-step exclusion, concurrent read recovery without rewriting history, and timeout cleanup/HTTP mapping.
- [x] Related regression 92 passed; full offline suite 937 passed / one pre-existing frozen-manifest failure / four skips; coverage 87.64%. Loopback streaming separately passed.
- [x] Existing real report: 24 concurrent GETs before and 24 after container recreation; same result hash, exact SSE/result agreement and replay. Old persisted files unchanged; derived progress now 7/7 while status remains partial.
- [ ] Latest real report generation without generation_failed, still requiring a newly confirmed supplier call. Do not treat the historical report's unchanged generation_failed result as a new successful generation.

## Follow-up: report model Schema alignment

Run `731a4d66c24e4c589cdf33c28206018d` used the scoped-citation investigator and returned a non-Mock public result, but all seven analysis modules retained `generation_failed`. Its report-model repair log records `model suggested unsupported application or pricing fields`. The running image still advertised the full application fields through the old `SuggestedValues` model, while `apply` only accepted five non-pricing suggestion fields.

The local source already narrowed `SuggestedValues` and its prompt to those five fields plus citations, preserving application/pricing values on the server. On resuming, the regression suite exposed one remaining mismatch: multiple repayment methods were schema-valid but rejected by `apply`. Added `max_length=1` after observing that test fail; the schema now advertises the same bound and rejects conflicting arrays before application.

- [x] Related generator/product/result/progress regression: 82 tests passed; Ruff and mypy passed.
- [x] Existing Run `731a…` read-only baseline: 24 equal concurrent result reads through ports 8080/18088; 75 contiguous events; exact report/SSE agreement and cursor replay; progress 7/7. Canonical result SHA-256 `d38961166afe5399ec43fb05a26f569d77ff6ae0142782f7d8774db488fb4bb5`.
- [x] Applied the repaired Schema using `bin/start.sh` after confirming no active Runs. Container/local generator SHA-256 both `c61cf0b24e4a34113d1987a0d11a74439e7028f3013ee9ecd9e8ce44cbf3beac`; schema has only the supported six properties and `repayment_methods.maxItems=1`.
- [x] Repeated the same 24 concurrent reads after recreation: unchanged historical hash, 7/7 progress and exact SSE replay. Real settings remain formal+tianyancha, qwen-plus, 300 seconds, 300000 input / 100000 output / 400000 total tokens, no Mock fallback.
- [x] After the user's explicit confirmation, Run `dabbc8e06a4b4b9cb408058cf5b7ca00` completed in about 158 seconds, with one report-model call and no generation_failed; 185146 total tokens. Historical retrieval stability was not used as a substitute for this new generation.

The new probe with fixed key `local-real-huawei-20260908-uv3Wsg-report-schema-7` was rejected before process execution by automatic approval. No new Run or provider request was made. The approval boundary requires explicit user confirmation of sending this test payload (Huawei Technologies Co., Ltd.; working-capital loan test amount 12万元, term 12 months) to Tianyancha MCP and DashScope qwen-plus under the existing spending limits. Do not bypass or indirectly execute the blocked probe.

The user subsequently explicitly approved that exact payload/destinations/budget. The same previously unexecuted key was used once for `dabbc8…`. It returned HTTP 200 with real evidence and passed 24 concurrent reads before and 24 after restart, 76-event exact replay, and unchanged artifact hashes.

## Follow-up: cached registration field-table projection

Read-only completeness checks found a distinct mapping bug: registration facts were stored as `字段/值` rows, so existing structured-field projection left disclosed business facts blank. Added red/green tests and a registration-only adapter for eight known scalar fields, explicit CNY units, unknown/foreign/non-finite money rejection, and no last-row-wins choice for conflicting values. Field-level capability-absence gaps can be resolved only by populated scalars with actual source references; source errors and collection completeness remain untouched.

- [x] 79 related tests, Ruff and mypy passed.
- [x] Replayed this Run's 28 cached registration records and saved public field lineage locally: eight fields recovered, original input hash unchanged, zero provider calls.
- [ ] New end-to-end report generation including this later mapping change has not been attempted. Do not overwrite or advertise the existing Run as a newly regenerated report.
- [ ] Investigate financial-response/table parsing: saved financial records currently contain only the summary count. Source absence is not proven. No new supplier call is authorized beyond the single completed Run.

Follow-up investigation and local parser implementation completed in `docs/superpowers/plans/2026-09-08-full-local-acceptance.md`: multi-table and mixed-payload regressions pass; latest real financial retrieval remains unverified because original raw responses were not persisted. Frontend blank-page and direct-route failures were reproduced, but the actual frontend source is missing. See `docs/diagnostics/local-full-acceptance-progress-2026-09-08.md` for current acceptance boundaries.

Detailed boundaries and hashes: `docs/diagnostics/local-real-schema-acceptance-2026-09-08.md`.
