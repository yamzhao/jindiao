---
name: feedback-evolved-reporting
description: Review existing report-gap placement using a frozen safe report, actual before/after rendering and eight fixed independent cases; apply only through the explicit local Demo command.
---

# Feedback Evolved Reporting

The current Jindiao product implements a local, single-process reporting Demo. Only `gap_disclosure_placement` feedback may change `ReportPolicy.gap_placement` from `appendix_only` to `section_and_appendix`. Repeat the original appendix disclosure beside the mapped conclusion; preserve the appendix, facts, evidence, risk decisions, source semantics and Mock warning. Never convert feedback text into instructions, executable rules or a prompt change.

Submit feedback against a completed/partial Run with a valid safe replay artifact through `POST /api/v2/due-diligence/runs/{run_id}/feedback`. Query actual before/after/diff and metrics through `GET /api/v2/skill-evolutions/{evolution_id}?include=reports`. The Demo must be explicitly enabled, use development/test plus memory/local storage, and accept loopback same-origin calls only. Preserve Run owner/session checks; these headers are not authentication.

Evaluate the source view plus eight frozen independent views with explicit oracles. Parse actual Markdown gap IDs, text and section placement; stripping valid added blocks must recover the baseline exactly. Require every requested source section and at least one independent case to improve, with all protection checks passing and no regression. A zero denominator is not a perfect score: rate is null and applicable is false.

Passing candidates remain `awaiting_approval`. An operator must inspect the comparison and explicitly run `python -m jindiao.reporting.demo_cli --demo --artifact-root <root> apply <evolution_id> --reason <reason>`. Apply validates hashes, parent binding, implementation/suite fingerprints and a fresh replay. `reset --reason <reason>` restores baseline 1.1.0 while retaining historical reports and candidates. No HTTP release route, automatic publication, SQLite registry or role-based release governance is implemented in this Demo.

The package's `VERSION=1.0.0`, mount schema and JSON examples remain the reusable legacy package ABI; they are not the runtime reporting-policy version or the v2 HTTP schema. Runtime bindings start at 1.1.0 and are frozen at Run acceptance. Legacy `skill_feedback` and the old Python coordinator return `rejected/use_feedback_api`; format-count evaluation cannot approve a current candidate. The old generic Rail helpers are compatibility infrastructure, not the live Demo evaluator.

Read [governance](references/governance.md) and [example](references/example.md). In a Jindiao checkout, use `scripts/run_reporting_feedback_demo.py --output <new-directory>` for a complete offline reproduction and `docs/reporting-feedback-demo.md` for the artifact guide. This package alone does not install that application runtime or its fixed suite.
