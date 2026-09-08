"""Record caller summaries as caller evidence, with no claim of external verification."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from jindiao.contracts.business import BusinessContext
from jindiao.contracts.evidence import Evidence, SourceType


def business_input_evidence(
    context: BusinessContext,
    *,
    subject_id: str,
    run_id: str,
    queried_at: datetime,
) -> tuple[Evidence, ...]:
    result = []
    for field, value in context.model_dump(mode="json").items():
        if value is None or value == [] or value == "":
            continue
        canonical = json.dumps([subject_id, field, value], ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        as_of = context.reporting_date
        if isinstance(value, dict):
            date_value = value.get("as_of_date") or value.get("period_end")
            as_of = date.fromisoformat(date_value) if isinstance(date_value, str) else None
        result.append(
            Evidence(
                evidence_id="input-" + digest[:24],
                claim=f"调用方提供的 {field} 摘要, 未独立核验",
                value={field: value},
                subject_id=subject_id,
                source_type=SourceType.USER_INPUT,
                queried_at=queried_at,
                as_of_date=as_of,
                confidence=1,
                is_mock=False,
                supports_fields=("business_context." + field,),
                raw_ref=f"request://{run_id}/business_context/{field}",
                content_hash="sha256:" + digest,
            )
        )
    return tuple(result)
