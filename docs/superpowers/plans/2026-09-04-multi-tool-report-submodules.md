# Multi-tool Tianyancha Report Submodules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every due-diligence domain call multiple available Tianyancha capabilities with bounded concurrency and expose every standard business submodule as traceable structured report data.

**Architecture:** Extend the existing capability routing configuration with canonical report-submodule definitions. `TianyanchaHybridToolset` resolves at most one preferred available tool for each submodule, executes those tools through one shared semaphore, preserves success/empty/error/fallback state per submodule, and merges their Evidence into one domain artifact. Report assembly keeps the eight-section public contract while each section gains a stable `submodules` map that the Markdown renderer expands into readable subsections.

**Tech Stack:** Python 3.11, asyncio, Pydantic v2, FastAPI contracts, Tianyancha MCP, pytest, Ruff, mypy.

---

### Task 1: Define canonical capability-to-submodule routing

**Files:**
- Modify: `src/jindiao/tianyancha/capabilities.py`
- Modify: `src/jindiao/tianyancha/__init__.py`
- Modify: `config/tianyancha-capability-routes.json`
- Test: `tests/unit/test_capability_service.py`

- [x] **Step 1: Write failing tests** proving that each configured domain exposes ordered submodules, chooses the first available preferred tool per submodule, and retains submodules whose capabilities are absent.
- [x] **Step 2: Run** `./.venv/bin/pytest --no-cov tests/unit/test_capability_service.py -q` and confirm failure because `ReportSubmoduleRoute` and `select_submodules` do not exist.
- [x] **Step 3: Implement** an immutable `ReportSubmoduleRoute` contract with `submodule_id`, `title`, `section_id`, `preferred_tool_names`, and `mock_keys`; add `submodules` to `DomainCapabilityRoute`; add a selector returning `(submodule, capability | None)` tuples.
- [x] **Step 4: Populate** the routing JSON with registration/governance, judicial, operating-risk, operating-status, relationship, and peer-analysis submodules from the reference report.
- [x] **Step 5: Re-run** the focused tests and require PASS.

### Task 2: Execute multiple tools without losing partial results

**Files:**
- Modify: `src/jindiao/orchestration/tianyancha_toolset.py`
- Modify: `src/jindiao/application/service.py`
- Test: `tests/unit/test_tianyancha_toolset.py`
- Test: `tests/integration/test_live_service.py`

- [x] **Step 1: Write failing tests** proving that two available submodules trigger two `call_tool` calls, a verified-empty submodule remains empty, one failed tool does not discard successful sibling results, and explicit degraded mode falls back only for the failed submodule.
- [x] **Step 2: Run** `./.venv/bin/pytest --no-cov tests/unit/test_tianyancha_toolset.py tests/integration/test_live_service.py -q` and confirm the expected single-tool behavior fails the new assertions.
- [x] **Step 3: Implement** one shared semaphore in `TianyanchaHybridToolset`, select one available tool per canonical submodule, and execute submodule calls with `asyncio.gather` while retaining deterministic route order.
- [x] **Step 4: Convert** every tool outcome into its own `CoverageItem`, Evidence tuple, non-risk acquisition Finding, sanitized ErrorRecord when needed, and structured submodule payload.
- [x] **Step 5: Preserve** source semantics: `verified_empty` is never Mock-covered; `capability_absent` may use the frozen supplement; `source_error` falls back only when `allow_degraded_mock=true`.
- [x] **Step 6: Re-run** focused unit and integration tests and require PASS.

### Task 3: Render complete report submodules

**Files:**
- Modify: `src/jindiao/reporting/markdown.py`
- Modify: `src/jindiao/reporting/assembler.py`
- Test: `tests/unit/test_reporting_pipeline.py`

- [x] **Step 1: Write a failing test** with `section.data.submodules` and assert that Markdown contains a heading for each submodule, source status, source tool, record count, and an explicit no-record message.
- [x] **Step 2: Run** `./.venv/bin/pytest --no-cov tests/unit/test_reporting_pipeline.py -q` and confirm the headings are absent.
- [x] **Step 3: Add** deterministic submodule rendering while retaining all existing Result fields and the single top-of-report Mock warning.
- [x] **Step 4: Add** the enterprise information overview to `report-summary.data` as counts by report section without introducing a ninth top-level section.
- [x] **Step 5: Re-run** reporting tests and require PASS.

### Task 4: Verify behavior and document the new contract

**Files:**
- Modify: `docs/product-design.md`
- Modify: `docs/api/README.md`
- Modify: `docs/agent-workflow-and-framework-capabilities.md`
- Modify: `docs/technical-stack.md`

- [x] **Step 1: Document** that each domain selects one preferred available MCP tool per canonical submodule, all calls use bounded shared concurrency, and per-submodule source status is public.
- [x] **Step 2: Run** focused tests for capability routing, Tianyancha toolset, live service, reporting, and multi-agent strategy.
- [x] **Step 3: Run** `./.venv/bin/ruff check src tests scripts`, `./.venv/bin/mypy src`, and the full credential-free `./.venv/bin/pytest` suite.
- [x] **Step 4: Run** one real `multi + JSON` request with the existing local `.env`; verify HTTP 200, more than one Tianyancha business capability is represented, all eight sections include structured submodules where applicable, and temporary AgentTeams resources are deleted.
- [x] **Step 5: Re-scan** the workspace to prove real credentials occur only in `.env`.

## Self-review

- The plan preserves the single public Result endpoint, eight top-level report sections, single-process deployment, deterministic scoring, explicit Mock disclosure, and `verified_empty` semantics.
- It does not add a database, vector service, new HTTP endpoint, autonomous scoring, or unbounded tool execution.
- Each production behavior is preceded by a focused failing test and followed by a focused and full verification command.
- The workspace is not currently a Git repository, so commit steps are intentionally omitted.
