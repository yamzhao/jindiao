---
name: evidence-backed-due-diligence
description: Coordinate a multi-agent enterprise due-diligence investigation using shared Evidence/Finding contracts, independent review, targeted repair, and deterministic final decisions. Use for reviewed team investigations, not free-form report drafting.
---

# Evidence Backed Due Diligence

Freeze one Run Context containing the resolved subject, data snapshot, rule version, Skill versions, deadline, concurrency, tool-call budget, and repair budget. Every member must use that same context.

The Leader creates a capability-aware task DAG for governance, judicial/compliance, operations/peers, permitted DeepSearch fallback, and independent review. Run dependency-free domain tasks concurrently. Specialists may submit only proposed Findings, Evidence, coverage, and errors; they cannot set scores or final decisions.

The Reviewer independently checks subject identity, source, date, amount, status, duplicates, support links, and cross-agent conflicts. Only supported, conflict-free Findings become `accepted`. Create targeted RepairTasks that name the responsible agent, requested fields, and required evidence. Stop at the repair budget and retain unresolved Findings as `unconfirmed`.

Invoke deterministic risk rules and the result/report assembler only after the final ReviewDecision. Never expose private reasoning; expose task status, evidence counts, conflicts, repairs, timing, and errors.

Read [references/protocol.md](references/protocol.md) when implementing a team adapter, and [references/example.md](references/example.md) for a minimal team run.
