# Contracts

Validate requests with `input.schema.json` and responses with `output.schema.json`. Evidence IDs must be stable within a run. Every record must retain `subject_id`, `source_status`, `supports_fields`, `queried_at`, and `raw_ref`. Mock Evidence must set `source_type=mock`, `is_mock=true`, and use a `mock://` reference.
