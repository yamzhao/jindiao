"""Deterministic evidence deduplication with complete source lineage."""

from __future__ import annotations

import json
from collections import defaultdict

from jindiao.contracts.evidence import Evidence, SourceType

_SOURCE_PRIORITY = {
    SourceType.TIANYANCHA: 0,
    SourceType.MOCK: 1,
    SourceType.DERIVED: 2,
}


def _fact_key(evidence: Evidence) -> str:
    return json.dumps(
        {
            "subject_id": evidence.subject_id,
            "claim": evidence.claim,
            "value": evidence.value,
            "supports_fields": sorted(evidence.supports_fields),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class EvidenceMerger:
    """Merge only semantically identical facts and retain every source reference."""

    @staticmethod
    def merge(items: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        groups: dict[str, list[Evidence]] = defaultdict(list)
        for item in items:
            groups[_fact_key(item)].append(item)

        merged: list[Evidence] = []
        for key in sorted(groups):
            group = sorted(
                groups[key],
                key=lambda item: (
                    _SOURCE_PRIORITY[item.source_type],
                    item.raw_ref,
                    item.evidence_id,
                ),
            )
            primary = group[0]
            source_chain: list[str] = []
            for item in group:
                for reference in (*item.source_chain, item.raw_ref):
                    if reference not in source_chain:
                        source_chain.append(reference)
            merged.append(primary.model_copy(update={"source_chain": tuple(source_chain)}))
        return tuple(merged)


__all__ = ["EvidenceMerger"]
