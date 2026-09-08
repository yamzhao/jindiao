# Backend Bounded Generation Implementation Plan

**Goal:** Make normal formal backend generation fit the existing budget while preserving evidence gates and accurate failure accounting.

**Architecture / ADR (accepted implementation scope):** Keep one immutable acquired snapshot and one real single-investigator batch. Compact only its model-facing view using shared fact sets, evidence metadata, and losslessly reversible record tables. Strengthen the advertised tool Schema to match actual check scope/insufficient-evidence rules. Reserve model input/output budgets atomically before dispatch, reconcile actual usage after dispatch, and preserve failure cost through application boundaries. No fabricated decisions, silent evidence truncation, Mock fallback, or budget increase.

**Trade-offs:** Model input uses explicit pool/table references; prompts and regression fixtures must document the representation. Conservative UTF-8-byte admission may refuse a request that an exact tokenizer would allow, but must not silently spend beyond the budget. Unreported usage remains uncertain, not zero. Historical results are never rewritten.

**Tech Stack:** Python 3.11, openJiuwen, Pydantic, FastAPI, pytest, Docker Compose.

## Task 1 — Lossless model context (main agent)

Files: `src/jindiao/investigation/compact_context.py` (new), `snapshot_access.py`, investigator prompt/manifest, snapshot and single-investigator tests.

- [x] RED: construct a 400+ Evidence snapshot with repeated submodule facts; decode the proposed pools/tables and require equality to the existing redacted/aliased context, including absent-vs-null cells, zeros/false, conflicting records and source IDs.
- [x] GREEN: produce `format=snapshot-context-v2`, `fact_sets`, `evidence_table` and `evidence_metadata_table`; module references point only to these generated pools. Transform record lists only when serialized size decreases. Keep private keys out of source facts, avoiding marker collisions.
- [x] Verify meaningful size reduction, authorization boundaries, immutable canonical snapshot and real ReAct scripted execution.

## Task 2 — Atomic budget admission (budget task)

Files: `orchestration/base.py`, `budgeted_model.py`, budget ledger/model tests.

- [x] RED: calls exceeding remaining input/output/total capacity never reach the provider, including concurrent calls; cancellation/deadline/overshoot still account observed usage.
- [x] GREEN: reservation IDs, conservative serialized input estimate, bounded max_tokens, reconciliation/release and explicit uncertain usage. Preserve existing direct ledger APIs.
- [x] Verify scalar-only safe error details carry actual cost and budget usage without request content.

## Task 3 — Submission constraints (schema task)

Files: `investigation/blackboard.py`, submission tests.

- [x] RED/GREEN: per-check citation scope using shared schema definitions; match runtime inconclusive rules exactly. Test with jsonschema and actual tool.invoke. Keep exact batch membership, ID restoration and atomic rejection.

## Task 4 — Failure accounting and integration (main agent)

Files: `agents/single_investigator.py`, `application/formal_pipeline.py`, `service.py`, observability metrics, related tests; inspect `agent_team_model.py` for the separate model wrapper.

- [x] Preserve ledger cost/uncertainty when a framework swallows a tool/model error; combine prior acquisition with investigation exactly once.
- [x] RED/GREEN: failure artifacts and successful totals cannot silently become zero or reporting-only; keep real error semantics and no fabricated report.
- [x] Run targeted tests, full offline suite, types/lint and independent review; preserve known unrelated frozen-manifest failure.
- [x] Check no active Runs, update local container, verify installed package hashes and existing report/error retrieval plus SSE replay. No new supplier call without separate explicit authorization.

## Verification boundary

Offline/scripted provider tests are not advertised as real-generation acceptance. The last real Run `c99a8745ffc941049f9fc0cbdbef05f1` failed and remains unchanged. Full live stability needs a later explicitly authorized run after these regressions pass. Frontend work is out of scope for this backend-focused turn.

## Implementation result

Tasks 1–4 implemented and locally deployed. Additional review regressions closed unknown usage holds between phases, ordinary provider exceptions, literal-ID collisions, zero/missing acquisition counters and SDK hidden retries. Acquisition now uses a shared request-level runtime ledger rather than guessing request counts from optional SDK usage events. Full offline suite: 1096 passed, one pre-existing frozen-manifest failure, four skips (loopback SSE separately passed). Installed modules and existing report/error reads verified; no new paid Run.

See `docs/diagnostics/backend-bounded-generation-2026-09-08.md` for hashes, measurements and verification limits. Final live generation acceptance remains open.

One subsequent explicitly authorized live Run `1017bce4937c4b6a9476d3885ed587c1` failed admission before the second investigation model request. It acquired 517 Evidence, used 55090 reported tokens across 7 model requests and 41 MCP calls; accurate failure accounting passed but report generation did not. See `docs/diagnostics/local-real-bounded-generation-9-2026-09-08.md`. Preserve rejected-request estimates and frozen input for offline diagnosis before another paid retry; do not reinterpret the synthetic compression benchmark as live acceptance.
