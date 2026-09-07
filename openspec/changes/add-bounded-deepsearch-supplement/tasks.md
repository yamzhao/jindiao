## 1. Coverage and source contracts

- [x] 1.1 Add contract tests for independent coverage completeness, gap reasons, and public-web Evidence metadata
- [x] 1.2 Implement backward-compatible coverage and Evidence contract extensions
- [x] 1.3 Add failing tests for Tianyancha pagination metadata extraction and partial coverage classification
- [x] 1.4 Preserve pagination metadata in normalized batches and classify explicit truncation without changing source status

## 2. Bounded supplemental research

- [x] 2.1 Add failing tests for structured gaps and bounded initial/rewrite query planning
- [x] 2.2 Implement EvidenceGap contracts and deterministic query planner
- [x] 2.3 Add failing tests for search-as-lead, fetched-document validation, URL deduplication, and two-round termination
- [x] 2.4 Implement the supplement Provider protocol and bounded research service that emits only traceable public-web Evidence

## 3. Tianyancha integration

- [x] 3.1 Add failing integration tests for optional partial-coverage supplementation and unchanged no-provider behavior
- [x] 3.2 Inject the optional supplement service into TianyanchaHybridToolset and merge supplemental Evidence/coverage without overwriting Tianyancha state
- [x] 3.3 Add reviewer regression coverage for conflicts between Tianyancha and public-web Evidence

## 4. Documentation and verification

- [x] 4.1 Update the DeepSearch capability document with the implemented bounded chain, limits, and deferred real-Web adapter
- [ ] 4.2 Run targeted tests, formatting, type checking, and the full project quality gate

## 5. Tianyancha annual-report social-security Provider

- [x] 5.1 Add failing contract tests for exact-host allowlisting, bounded latest-year selection, missing reports, subject validation, and social-security parsing
- [x] 5.2 Implement the HTTP Provider and normalize five insurance counts plus original disclosure values into traceable public-web Evidence
- [x] 5.3 Inject the optional Provider into live service configuration and the governance company-profile submodule without changing Mock execution
- [x] 5.4 Document the Provider boundaries, output shape, empty-result semantics, and runtime controls
- [x] 5.5 Run targeted tests, format/lint, source type checking, strict OpenSpec validation, and record the existing unrelated full-gate limitations

Verification note: the Provider/toolset/service targeted suite passes (27 tests) and isolated Settings tests pass (10 tests); repository-wide Ruff lint and `mypy src` pass; strict OpenSpec validation passes. Full formatting still reports the pre-existing `team_runtime.py`/`test_team_runtime.py` pair. Full pytest under the local `.env` was stopped at 54% after 11 existing model-runtime/benchmark failures and timeouts, consistent with the previously recorded environment-dependent gate limitation; no annual-report test failed.
