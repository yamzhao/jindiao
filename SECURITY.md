# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the repository maintainers. Do not include real enterprise records, API keys, Tianyancha authorization values, model credentials, or unredacted run artifacts in a public issue. Include the affected version, reproduction steps, impact, and a minimal sanitized example.

## Supported scope

The current competition release is a single-machine demonstration. Security fixes target the latest commit. It is not a production credit-decision service and must not be exposed to untrusted networks without an external authentication and authorization layer.

## Secret handling

Secrets are injected through environment variables and `.env` is ignored by Git. Structured traces use an attribute allowlist, remove private reasoning fields, and redact common authorization/token formats. Before publishing artifacts, still inspect them for business-sensitive content because redaction cannot infer every organization-specific identifier.

## Data handling

Bundled companies and reports are fictional Mock data. Do not commit real due-diligence responses. Runtime artifacts are written below `artifacts/` and ignored by default. Delete or archive them according to the operator's local retention policy.
