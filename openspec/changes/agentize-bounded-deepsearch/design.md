## Context

Jindiao has two related but separate mechanisms today. `BoundedEvidenceSupplementService` performs deterministic, gap-driven search with at most two rounds, while `TianyanchaAnnualReportProvider` directly fetches the latest eligible annual report and emits social-security Evidence. Both are invoked by `TianyanchaHybridToolset`, so the team member named `deepsearch-agent` does not own their execution. AgentTeams currently establishes and tears down the declared roster as a lifecycle gate; application code remains the authoritative executor and reviewer.

openJiuwen supports member-name-specific `DeepAgentSpec` entries, member-scoped Skill visibility and declarative Tool providers. Jindiao's packaged Skills are stored under the repository `skills/` tree, but that tree is not currently configured as the AgentTeams global Skill library.

## Goals / Non-Goals

**Goals:**

- Give one application-level DeepSearch agent facade ownership of bounded Web research and Tianyancha annual-report retrieval.
- Declare a matching `deepsearch-agent` AgentTeams spec with a narrow Tool whitelist and a dedicated annual-report Skill.
- Register the annual-report provider through an openJiuwen `BuiltinToolSpec` provider while keeping all hard safety checks in executable code.
- Route existing governance supplementation through the facade without changing Evidence, coverage, review or scoring contracts.
- Make Skill discovery deterministic by configuring Jindiao's repository Skill library before the team spec is built.

**Non-Goals:**

- Enabling unconstrained Internet research or adding a production general-Web search adapter.
- Letting an LLM choose the source allowlist, validate subjects, override time cutoffs or produce final scores.
- Replacing the current application-owned deterministic investigation loop with LLM-owned AgentTeams task execution. That requires a separate structured-output ingestion and lifecycle change.
- Changing the existing Tianyancha MCP authorization model or broadening its host allowlist.

## Decisions

### Separate Agent, Skill and Tool responsibilities

`DeepSearchEvidenceAgent` will be extended as the application execution facade. It will expose explicit methods for an `EvidenceGap` and for latest annual-report social-security retrieval. The facade owns provider/service invocation and close lifecycle, but it does not own scoring.

The `tianyancha-annual-report-social-security` Skill will describe triggers, valid input, `verified_empty` semantics and prohibited behavior. The executable Tool will construct and close `TianyanchaAnnualReportProvider` per invocation and return the typed outcome as public JSON. Host allowlisting, entity matching, report-year validation, publication cutoff and Evidence construction remain in the provider.

Alternative considered: embed HTTP behavior in `SKILL.md`. Rejected because prompt instructions cannot enforce network or evidence invariants.

### Use a member-name-specific AgentSpec

The team blueprint will add `agents["deepsearch-agent"]` instead of modifying the shared teammate template. Its configured Skills and Tools therefore remain invisible to governance, judicial, operations and reviewer members.

Alternative considered: add the Skill to the shared teammate spec and rely only on prompt role boundaries. Rejected because it grants broader capability visibility than required.

### Keep deterministic application execution in this change

`TianyanchaHybridToolset` will depend on the DeepSearch facade rather than directly on the supplement service and annual-report provider. Existing constructor inputs remain supported as compatibility adapters, but direct and facade injection cannot be combined. This removes duplicate execution ownership without requiring model credentials in single-agent, offline or test paths.

The current AgentTeams lifecycle gate remains unchanged. The registered Tool and Skill make the member specification truthful and ready for a later LLM task-execution change, while this change keeps evidence production deterministic.

Alternative considered: keep the team alive and have the LLM dispatch and ingest all specialist outputs immediately. Rejected for this change because there is no current structured artifact ingestion contract from teammate streams, and it would couple correctness to model availability.

### Configure one recursive Skill library root

Jindiao will register its repository `skills/` directory as the openJiuwen team Skill root before building a team spec. The existing recursive scanner can discover both `skills/agent/*` and `skills/team/*`; the deepsearch member's allow-list contains only its dedicated Skill.

## Risks / Trade-offs

- [AgentTeams member does not yet execute the registered Tool in production] → Keep the limitation explicit in documentation and tests; the application facade is the current execution owner.
- [Global Skill library configuration is process-wide] → Register one stable absolute Jindiao path idempotently and do not mutate it per request.
- [Tool and application facade could drift] → Both call the same `TianyanchaAnnualReportProvider` and share contract tests for output and error semantics.
- [Duplicate provider execution] → The toolset stores only one DeepSearch facade and rejects simultaneous facade plus raw dependency injection.
- [Extra abstractions add complexity for one provider] → Keep the facade thin and typed; its benefit is bounded ownership for future providers, not autonomous reasoning.

## Migration Plan

1. Add failing tests for member-scoped AgentSpec and Tool/Skill registration.
2. Add the dedicated Skill package and annual-report Tool adapter.
3. Extend the DeepSearch facade and route the hybrid toolset through it.
4. Update service construction, version metadata and capability documentation.
5. Run targeted and full quality gates. Rollback consists of restoring raw provider injection in `TianyanchaHybridToolset`; Evidence contracts and stored artifacts require no migration.

## Open Questions

- A later change must define the teammate structured-output protocol before the AgentTeams lifecycle gate can be replaced with actual evidence task execution.
- A production general-Web provider remains deferred; until one exists, only the Tianyancha annual-report Tool is exposed to the model-backed DeepSearch member.
