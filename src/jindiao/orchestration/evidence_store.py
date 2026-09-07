"""Run-local idempotent Evidence Store with source-lineage preservation."""

from __future__ import annotations

import asyncio
import hashlib
import json

from jindiao.application.errors import EvidenceReviewError
from jindiao.contracts.evidence import Evidence
from jindiao.tianyancha import EvidenceMerger

from .base import DomainInvestigation


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _fact_key(item: Evidence) -> str:
    return _canonical(
        {
            "subject_id": item.subject_id,
            "claim": item.claim,
            "value": item.value,
            "supports_fields": sorted(item.supports_fields),
        }
    )


class EvidenceStore:
    """Validate agent artifacts before merging them into shared run state."""

    def __init__(self, *, subject_id: str) -> None:
        self._subject_id = subject_id
        self._by_id: dict[str, Evidence] = {}
        self._fact_primary: dict[str, str] = {}
        self._artifact_cache: dict[tuple[str, str], tuple[str, DomainInvestigation]] = {}
        self._lock = asyncio.Lock()

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    async def ingest(
        self,
        agent_id: str,
        artifact: DomainInvestigation,
    ) -> DomainInvestigation:
        digest = hashlib.sha256(_canonical(artifact.model_dump(mode="json")).encode()).hexdigest()
        cache_key = (agent_id, artifact.task_id)
        async with self._lock:
            cached = self._artifact_cache.get(cache_key)
            if cached is not None:
                if cached[0] != digest:
                    raise EvidenceReviewError(
                        "agent artifact idempotency collision",
                        details={"agent_id": agent_id, "task_id": artifact.task_id},
                    )
                return cached[1]

            aliases: dict[str, str] = {}
            normalized_evidence: list[Evidence] = []
            for item in artifact.evidence:
                self._validate_subject(item.subject_id, agent_id)
                existing_by_id = self._by_id.get(item.evidence_id)
                if existing_by_id is not None and existing_by_id != item:
                    raise EvidenceReviewError(
                        "evidence id collision",
                        details={"agent_id": agent_id, "evidence_id": item.evidence_id},
                    )
                key = _fact_key(item)
                primary_id = self._fact_primary.get(key)
                if primary_id is None:
                    primary_id = item.evidence_id
                    self._fact_primary[key] = primary_id
                    self._by_id[primary_id] = item.model_copy(
                        update={
                            "source_chain": tuple(dict.fromkeys((*item.source_chain, item.raw_ref)))
                        }
                    )
                else:
                    primary = self._by_id[primary_id]
                    self._by_id[primary_id] = EvidenceMerger.merge((primary, item))[0]
                aliases[item.evidence_id] = primary_id
                if self._by_id[primary_id] not in normalized_evidence:
                    normalized_evidence.append(self._by_id[primary_id])

            available = set(aliases)
            normalized_findings = []
            for finding in artifact.findings:
                self._validate_subject(finding.subject_id, agent_id)
                unknown = set(finding.evidence_ids) - available
                if unknown:
                    raise EvidenceReviewError(
                        "finding references evidence outside its agent artifact",
                        details={"finding_id": finding.finding_id, "evidence_ids": sorted(unknown)},
                    )
                normalized_findings.append(
                    finding.model_copy(
                        update={
                            "evidence_ids": tuple(
                                dict.fromkeys(aliases[item] for item in finding.evidence_ids)
                            )
                        }
                    )
                )
            normalized = artifact.model_copy(
                update={
                    "evidence": tuple(normalized_evidence),
                    "findings": tuple(normalized_findings),
                }
            )
            self._artifact_cache[cache_key] = (digest, normalized)
            return normalized

    def _validate_subject(self, subject_id: str, agent_id: str) -> None:
        if subject_id != self._subject_id:
            raise EvidenceReviewError(
                "agent artifact subject does not match the resolved subject",
                details={"agent_id": agent_id},
            )


__all__ = ["EvidenceStore"]
