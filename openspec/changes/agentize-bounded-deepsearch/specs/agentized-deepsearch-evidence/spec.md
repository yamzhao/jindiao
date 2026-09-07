## ADDED Requirements

### Requirement: Dedicated DeepSearch execution owner
The system SHALL route bounded supplemental research and Tianyancha annual-report social-security retrieval through a dedicated DeepSearch evidence agent facade, and the facade SHALL return typed evidence outcomes without assigning a risk score or final decision.

#### Scenario: Governance gap is delegated
- **WHEN** a Tianyancha governance submodule produces an eligible partial-coverage EvidenceGap
- **THEN** the hybrid toolset delegates the gap to the DeepSearch evidence agent and merges the returned Evidence and coverage metadata

#### Scenario: Annual-report supplementation is delegated
- **WHEN** live Tianyancha governance investigation enables annual-report supplementation
- **THEN** the hybrid toolset delegates latest eligible annual-report retrieval to the DeepSearch evidence agent exactly once

### Requirement: Member-scoped Skill and Tool registration
The AgentTeams blueprint SHALL define a member-name-specific `deepsearch-agent` specification containing only approved DeepSearch Skills and Tools, and other specialist members SHALL NOT inherit those capabilities from the shared teammate template.

#### Scenario: Annual-report capability is enabled
- **WHEN** a team spec is built with Tianyancha annual-report supplementation enabled
- **THEN** `deepsearch-agent` exposes the dedicated annual-report Skill and Tool while the shared teammate template exposes neither

#### Scenario: Annual-report capability is disabled
- **WHEN** a team spec is built with Tianyancha annual-report supplementation disabled
- **THEN** `deepsearch-agent` does not expose the annual-report Tool or its source-specific Skill

### Requirement: Skill and Provider separation
The system SHALL package source-specific invocation policy as an agent Skill and SHALL expose executable provider behavior through a registered Tool provider. Security and evidence-admission controls MUST remain in executable Provider or Tool code rather than relying on Skill instructions.

#### Scenario: Skill package is validated
- **WHEN** the `tianyancha-annual-report-social-security` Skill package is loaded
- **THEN** its input schema, output schema, examples and evaluation cases validate successfully as an agent-scoped Skill

#### Scenario: Tool provider is resolved
- **WHEN** the annual-report `BuiltinToolSpec` is built by openJiuwen
- **THEN** it resolves to a callable Tool backed by `TianyanchaAnnualReportProvider`

### Requirement: Annual-report Tool preserves source semantics
The annual-report Tool SHALL accept a resolved Tianyancha subject, query time and report cutoff, SHALL enforce bounded provider configuration, and SHALL return the provider's `verified_records`, `verified_empty` or `source_error` outcome without inventing records.

#### Scenario: No eligible annual report exists
- **WHEN** the provider checks all configured eligible years and finds no report
- **THEN** the Tool returns `verified_empty` with the checked years and no Evidence

#### Scenario: Provider rejects an invalid subject
- **WHEN** the Tool receives a subject that is not an exact numeric Tianyancha subject
- **THEN** the invocation fails validation rather than accessing an unapproved URL

### Requirement: Single execution path and lifecycle
The hybrid toolset SHALL own one DeepSearch facade, SHALL reject ambiguous simultaneous facade and raw dependency injection, and SHALL close facade-owned providers when the toolset closes.

#### Scenario: Compatibility dependencies are supplied
- **WHEN** callers provide the existing supplement service or annual-report provider without a facade
- **THEN** the toolset creates one DeepSearch facade and routes all supplemental calls through it

#### Scenario: Conflicting injection is supplied
- **WHEN** callers provide a DeepSearch facade together with a raw supplement service or annual-report provider
- **THEN** construction fails with a clear configuration error

### Requirement: Existing evidence pipeline remains authoritative
Evidence returned by the DeepSearch facade SHALL continue through `EvidenceStore` and independent review, and deterministic report assembly SHALL remain the only owner of scores and final decisions.

#### Scenario: DeepSearch returns public-Web Evidence
- **WHEN** the facade returns admissible public-Web Evidence
- **THEN** it is deduplicated, reviewed and traced through the existing evidence pipeline using its original source status and references
