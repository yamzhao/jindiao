## Why

The bounded DeepSearch chain and the Tianyancha annual-report provider currently execute inside the governance toolset, while `deepsearch-agent` is only a roster entry whose runtime is stopped after team creation. This hides research ownership, prevents member-scoped Skill and Tool governance, and would create duplicate execution if AgentTeams were enabled later.

## What Changes

- Promote bounded supplemental research to an independently addressable `deepsearch-agent` responsibility with structured evidence-gap input and `SupplementOutcome` output.
- Register executable DeepSearch capabilities as member-scoped Tools, including a Tianyancha annual-report social-security Tool backed by the existing provider.
- Add dedicated agent Skills for bounded Web research and Tianyancha annual-report social-security retrieval; Skills define invocation policy while Tools retain network, allowlist, subject, time and evidence controls.
- Configure the exact `deepsearch-agent` member with only those Skills and Tools.
- Route application-layer evidence gaps and annual-report supplementation through one DeepSearch agent facade, removing duplicate ownership from the governance toolset while retaining deterministic execution in offline and single-agent modes.
- Preserve `verified_empty`, `source_error`, Evidence Store ingestion, independent review and deterministic score ownership.

## Capabilities

### New Capabilities

- `agentized-deepsearch-evidence`: Member-scoped DeepSearch task execution, Skill visibility, Tool registration, deterministic safety boundaries and integration with the evidence pipeline.

### Modified Capabilities


## Impact

- Affects DeepSearch contracts and agents, Tianyancha orchestration, AgentTeams specification/runtime configuration, Skill packages, service wiring and tests.
- Adds no new external dependency and does not broaden the Tianyancha host allowlist.
- The application-facing investigation contract remains `DomainInvestigation`; report scoring and final decisions remain outside the DeepSearch agent.
