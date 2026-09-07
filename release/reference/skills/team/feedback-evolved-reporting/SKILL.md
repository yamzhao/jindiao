---
name: feedback-evolved-reporting
description: Turn reviewed user feedback about due-diligence report wording and presentation into evaluated, approval-gated Skill candidates with activation and rollback metadata. Use only for reporting behavior improvements, never risk or source governance changes.
---

# Feedback Evolved Reporting

Classify each feedback item by affected report behavior, supporting run/evidence references, and measurable expected improvement. Generate a candidate under the candidate workspace; never edit the stable Skill during proposal or evaluation.

Reject any candidate that changes risk rules, thresholds, decision bands, source priority, Mock marking, Evidence acceptance gates, credential handling, or secret-redaction policy. Reporting evolution may improve structure, clarity, citation placement, missing-data disclosure, and audience-specific wording without changing structured facts.

Replay the candidate on the fixed evaluation set. Reject regression, missing evidence, or governance violations. Route passing candidates through `EvolutionReviewRuntime` and `EvolutionInterruptRail`; require explicit human approval before activation. Store source hash, candidate hash, feedback references, evaluation record, approver, and timestamps. Preserve the previous stable version and support explicit rollback.

Read [references/governance.md](references/governance.md) before proposing or activating a candidate. Read [references/example.md](references/example.md) for the proposal lifecycle.
