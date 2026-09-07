## 1. Agent and Tool Contracts

- [x] 1.1 Add failing tests for DeepSearch facade delegation, missing-capability behavior and provider close lifecycle
- [x] 1.2 Extend `DeepSearchEvidenceAgent` to own bounded research and annual-report provider execution
- [x] 1.3 Add failing tests for annual-report Tool provider registration, typed invocation and bounded configuration
- [x] 1.4 Implement the openJiuwen annual-report Tool adapter and idempotent capability registration

## 2. Skill and AgentTeams Registration

- [x] 2.1 Add failing tests for the dedicated Skill package and member-scoped AgentSpec isolation
- [x] 2.2 Create the `tianyancha-annual-report-social-security` Skill package with schemas, examples and evaluations
- [x] 2.3 Configure the Jindiao Skill library and register the Tool only on the exact `deepsearch-agent` AgentSpec

## 3. Application Integration

- [x] 3.1 Add failing integration tests proving the hybrid toolset routes supplements through one DeepSearch facade and rejects ambiguous injection
- [x] 3.2 Refactor the hybrid toolset and live service wiring to use the facade without changing evidence or empty-result semantics
- [x] 3.3 Record the new Skill version and update capability documentation with current AgentTeams execution limits

## 4. Verification

- [x] 4.1 Run targeted tests and verify the new tests fail before their corresponding implementation and pass afterwards
- [x] 4.2 Run formatting, lint, source type checking, strict OpenSpec validation and the full project quality gate
