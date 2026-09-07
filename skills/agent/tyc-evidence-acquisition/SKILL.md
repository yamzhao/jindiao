---
name: tyc-evidence-acquisition
description: Acquire enterprise public-information evidence through Tianyancha MCP with exact entity anchoring, capability routing, source-state semantics, and traceable normalized output. Use for Tianyancha-backed company evidence collection, not for final credit decisions.
---

# Tianyancha Evidence Acquisition

Resolve the legal entity before collecting records. Prefer an exact unified social credit code; otherwise disambiguate name matches with region and registration status. Stop on unresolved ambiguity.

Fetch the company's capability manifest, then select only tools relevant to the requested domain. Treat source outcomes distinctly:

- `verified_records`: normalize each record into Evidence with tool, record, query time, supported fields, confidence, and raw reference.
- `verified_empty`: preserve the successful empty result and never replace it with Mock data.
- `capability_absent`: permit the caller's declared fixed-Mock fallback.
- `source_error`: expose the error; use Mock only when the request explicitly enables degraded demonstration mode.
- `degraded_mock`: mark the fallback prominently and retain the original source error.

Never place Authorization values, API keys, raw diagnostic headers, or private model reasoning in Evidence or logs. Submit Evidence and coverage state only; do not set a risk score or admission decision.

Read [references/contracts.md](references/contracts.md) when constructing programmatic input/output, and [references/example.md](references/example.md) for a minimal invocation.
