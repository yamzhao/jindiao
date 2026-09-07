# Team protocol

Use roles `leader`, `governance-agent`, `judicial-compliance-agent`, `operations-peer-agent`, `deepsearch-agent`, and `reviewer-agent`. Task dependencies must reference declared task IDs. Evidence is merged idempotently by fact while preserving every source reference. Findings that lack resolvable Evidence or participate in unresolved conflicts remain unconfirmed and cannot enter deterministic scoring.
