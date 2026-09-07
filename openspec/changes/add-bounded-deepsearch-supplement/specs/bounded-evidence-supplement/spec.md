## ADDED Requirements

### Requirement: Source health and coverage completeness are independent
The system SHALL preserve the existing source status while separately recording whether evidence coverage is complete, partial, or unknown and why.

#### Scenario: Successful response is paginated
- **WHEN** a Tianyancha query succeeds with records and explicit metadata shows additional records remain
- **THEN** the source status SHALL remain `verified_records` and coverage completeness SHALL be `partial` with reason `pagination_truncated`

#### Scenario: Successful empty response
- **WHEN** a Tianyancha query succeeds with zero records
- **THEN** the source status SHALL remain `verified_empty` and no supplemental result SHALL overwrite that status

### Requirement: Evidence gaps are structured and bounded
The system SHALL represent each supplemental research need as a structured gap bound to one resolved subject, domain, submodule, topic, supports-field set, and gap reason.

#### Scenario: Partial submodule creates a gap
- **WHEN** a report submodule has partial coverage
- **THEN** the system SHALL create at most one gap for that submodule using only its declared domain, title, capability, and supports fields

### Requirement: Supplemental queries are constrained
The system SHALL generate deterministic atomic queries for each gap and SHALL enforce per-gap and global query limits.

#### Scenario: Initial query decomposition
- **WHEN** a valid gap is planned
- **THEN** the planner SHALL generate no more than two distinct initial queries anchored by the resolved company name or unified social credit code

#### Scenario: Query rewrite
- **WHEN** the initial round yields no admissible fetched evidence
- **THEN** the planner SHALL generate no more than one distinct rewritten query for a second and final round

### Requirement: Search snippets are discovery leads only
The system MUST NOT convert a search-result snippet directly into Evidence.

#### Scenario: Search result cannot be fetched
- **WHEN** a search candidate has a title, URL, and snippet but its page cannot be fetched
- **THEN** the candidate SHALL NOT produce Evidence

### Requirement: Supplemental evidence is traceable and subject-scoped
The system SHALL convert only fetched documents that match the resolved subject and report time boundary into `public_web` Evidence with URL, source identity, fetch time, and content hash.

#### Scenario: Fetched official document matches subject
- **WHEN** a fetched document contains the resolved company name or unified social credit code and is not newer than the report date
- **THEN** the system SHALL emit public-web Evidence bound to the gap's supports fields

#### Scenario: Fetched document belongs to another entity
- **WHEN** a fetched document contains neither resolved subject anchor
- **THEN** the system SHALL discard the document without emitting Evidence

#### Scenario: Fetched document is newer than report date
- **WHEN** the document has a publication date later than `report_as_of`
- **THEN** the system SHALL discard the document without emitting Evidence

### Requirement: Supplemental research stops deterministically
The system SHALL stop after at most two search rounds and SHALL preserve unresolved gaps instead of continuing autonomously.

#### Scenario: Second round remains empty
- **WHEN** neither the initial nor rewritten query yields admissible fetched evidence
- **THEN** the outcome SHALL contain zero supplemental Evidence, the executed query history, and an unresolved gap

### Requirement: Existing execution remains compatible
The system SHALL make supplemental research optional and SHALL not require network access for existing Mock runs or tests.

#### Scenario: No supplement provider is configured
- **WHEN** the current Tianyancha toolset runs without a supplement service
- **THEN** the system SHALL preserve existing evidence, findings, coverage status, API shape compatibility, and offline behavior

### Requirement: Tianyancha annual-report access is exact and allowlisted
The system SHALL build annual-report requests only from a resolved Tianyancha company ID and SHALL accept content only from HTTPS responses whose exact host is `www.tianyancha.com`.

#### Scenario: Annual-report URL is constructed
- **WHEN** the resolved subject ID is `tyc:{company_id}` and live annual-report supplementation is enabled
- **THEN** the Provider SHALL request only `https://www.tianyancha.com/annualReport/{company_id}/{year}` URLs

#### Scenario: Response leaves the allowlist
- **WHEN** a redirect or final response URL uses another scheme or host
- **THEN** the Provider SHALL emit no Evidence and SHALL return `source_error`

### Requirement: The latest available report is selected within a bounded window
The system SHALL start with the latest annual-report year eligible at `report_as_of`, check years in descending order, and stop at the first available report or the configured lookback limit.

#### Scenario: Latest eligible report is missing
- **WHEN** the first annual-report year returns a verified not-found response or an identity-matched HTTP 200 page whose body contains another/no report year, and the next year exists
- **THEN** the Provider SHALL use the next year's report and record both checked years

#### Scenario: No annual report exists
- **WHEN** no report exists within the configured lookback window
- **THEN** the Provider SHALL return `verified_empty` without Evidence or a run-blocking error

### Requirement: Annual-report social-security data is identity and time checked
The system SHALL require the report year and resolved company identity to match, SHALL reject reports publicized after `report_as_of`, and SHALL normalize the five insurance participant counts plus the original disclosure values.

#### Scenario: Social-security disclosure is present
- **WHEN** the latest admissible annual report contains a social-security section
- **THEN** the Provider SHALL emit one `public_web` Evidence item supporting `governance.company_profile.social_security` with the URL, title, publisher, publicized date, query time, and SHA-256 content hash

#### Scenario: A social-security value is undisclosed
- **WHEN** a participant count or payment field says `企业选择不公示`
- **THEN** the system SHALL preserve that exact text and SHALL NOT coerce it to zero

#### Scenario: Annual report exists without social-security data
- **WHEN** the latest admissible report exists but has no social-security section
- **THEN** the Provider SHALL retain report-presence metadata, emit no Evidence, and return `verified_empty`

#### Scenario: Annual-report subject does not match
- **WHEN** the page company name or unified social credit code conflicts with the resolved subject
- **THEN** the Provider SHALL emit no Evidence and SHALL return `source_error`

### Requirement: Annual-report supplementation enriches only enterprise basic information
The system SHALL add the normalized annual-report social-security disclosure as an optional company-profile submodule without overwriting employee count, Tianyancha MCP state, findings, or risk scores.

#### Scenario: Provider is enabled in live mode
- **WHEN** the governance investigation runs with the annual-report Provider enabled
- **THEN** the company-profile section SHALL include `annual_report_social_security` records, Evidence references, checked years, and independent coverage status

#### Scenario: Offline or configured-disabled execution
- **WHEN** the request uses Mock/scenario execution or annual-report supplementation is disabled
- **THEN** the system SHALL make no annual-report HTTP request and SHALL preserve the existing 48-submodule output
