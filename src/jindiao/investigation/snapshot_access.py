"""Task-scoped, read-only ToolCards over one frozen enterprise snapshot."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from openjiuwen.core.foundation.tool import ToolCard, tool
from pydantic import AwareDatetime, Field, JsonValue, model_validator

from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.base import ContractModel
from jindiao.reporting.catalog import REPORT_CATALOG
from jindiao.security import redact_json


class SnapshotReadGrant(ContractModel):
    """The exact snapshot slice assigned to one investigation task."""

    task_id: str = Field(min_length=1)
    check_ids: tuple[str, ...] = Field(min_length=1)
    allowed_submodule_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_scope(self) -> SnapshotReadGrant:
        if len(self.check_ids) != len(set(self.check_ids)):
            raise ValueError("snapshot grant check ids must be unique")
        if len(self.allowed_submodule_ids) != len(set(self.allowed_submodule_ids)):
            raise ValueError("snapshot grant submodule ids must be unique")
        unknown = set(self.allowed_submodule_ids) - set(REPORT_CATALOG.submodule_ids)
        if unknown:
            raise ValueError(f"snapshot grant contains unknown submodules: {sorted(unknown)}")
        return self


class SnapshotReadAuditRecord(ContractModel):
    run_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    submodule_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    allowed: bool
    denial_reason: str | None = None
    occurred_at: AwareDatetime


class ReadSnapshotSubmoduleInput(ContractModel):
    snapshot_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    submodule_id: str = Field(min_length=1)


class ReadAssignedSnapshotContextInput(ContractModel):
    pass


class ReadSnapshotEvidenceInput(ReadSnapshotSubmoduleInput):
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> ReadSnapshotEvidenceInput:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("snapshot Evidence ids must be unique")
        return self


class SnapshotReadBudget(Protocol):
    async def claim_snapshot_read(self, operation: str) -> None: ...


class SnapshotReadToolset:
    """Expose only normalized, grant-authorized snapshot data to one Agent."""

    def __init__(
        self,
        *,
        snapshot: EnterpriseContextSnapshot,
        run_id: str,
        agent_id: str,
        grants: tuple[SnapshotReadGrant, ...],
        budget_ledger: SnapshotReadBudget | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not run_id.strip() or not agent_id.strip():
            raise ValueError("snapshot reader requires run_id and agent_id")
        task_ids = tuple(item.task_id for item in grants)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("snapshot read grants must have unique task ids")
        self.snapshot = snapshot
        self.run_id = run_id
        self.agent_id = agent_id
        self._grants = {item.task_id: item for item in grants}
        self._budget_ledger = budget_ledger
        self._clock = clock
        self._audit_records: list[SnapshotReadAuditRecord] = []

    @property
    def audit_records(self) -> tuple[SnapshotReadAuditRecord, ...]:
        return tuple(self._audit_records)

    async def read_submodule(
        self,
        *,
        snapshot_id: str,
        task_id: str,
        submodule_id: str,
    ) -> dict[str, JsonValue]:
        self._authorize(
            snapshot_id=snapshot_id,
            task_id=task_id,
            submodule_id=submodule_id,
            operation="snapshot.submodule.read",
        )
        context = self.snapshot.submodule(submodule_id)
        if self._budget_ledger is not None:
            await self._budget_ledger.claim_snapshot_read("read_snapshot_submodule")
        self._record(
            task_id=task_id,
            operation="snapshot.submodule.read",
            submodule_id=submodule_id,
            evidence_ids=(*context.evidence_ids, *context.supplemental_evidence_ids),
            allowed=True,
        )
        return self._safe_dict(
            {
                "snapshot_id": self.snapshot.snapshot_id,
                "snapshot_sha256": self.snapshot.snapshot_sha256,
                "subject_id": self.snapshot.subject.subject_id,
                "report_as_of": self.snapshot.report_as_of.isoformat(),
                "submodule_id": context.submodule_id,
                "availability": context.availability.value,
                "completeness": context.completeness.value,
                "facts": context.facts,
                "evidence_ids": list(context.evidence_ids),
                "supplemental_evidence_ids": list(context.supplemental_evidence_ids),
                "unresolved_gap_ids": list(context.unresolved_gap_ids),
                "conflict_evidence_ids": list(context.conflict_evidence_ids),
            }
        )

    async def read_assigned_context(self) -> dict[str, JsonValue]:
        """Read every unique submodule already authorized for this Agent in one call."""

        if self._budget_ledger is not None:
            await self._budget_ledger.claim_snapshot_read("read_assigned_snapshot_context")

        evidence_by_id = {item.evidence_id: item for item in self.snapshot.evidence}
        scope: dict[str, dict[str, list[str]]] = {}
        authorized_check_ids: list[str] = []
        for grant in self._grants.values():
            authorized_check_ids.extend(grant.check_ids)
            for submodule_id in grant.allowed_submodule_ids:
                entry = scope.setdefault(
                    submodule_id,
                    {"task_ids": [], "check_ids": []},
                )
                entry["task_ids"].append(grant.task_id)
                entry["check_ids"].extend(grant.check_ids)
                context = self.snapshot.submodule(submodule_id)
                self._record(
                    task_id=grant.task_id,
                    operation="snapshot.assignment.read",
                    submodule_id=submodule_id,
                    evidence_ids=(*context.evidence_ids, *context.supplemental_evidence_ids),
                    allowed=True,
                )

        submodule_order = {
            submodule_id: index for index, submodule_id in enumerate(REPORT_CATALOG.submodule_ids)
        }
        submodules: list[dict[str, object]] = []
        for submodule_id in sorted(scope, key=submodule_order.__getitem__):
            context = self.snapshot.submodule(submodule_id)
            evidence_ids = tuple(
                dict.fromkeys(
                    (
                        *context.evidence_ids,
                        *context.supplemental_evidence_ids,
                        *context.conflict_evidence_ids,
                    )
                )
            )
            submodules.append(
                {
                    "submodule_id": submodule_id,
                    "authorized_task_ids": list(dict.fromkeys(scope[submodule_id]["task_ids"])),
                    "authorized_check_ids": list(dict.fromkeys(scope[submodule_id]["check_ids"])),
                    "availability": context.availability.value,
                    "completeness": context.completeness.value,
                    "facts": context.facts,
                    "evidence_ids": list(context.evidence_ids),
                    "supplemental_evidence_ids": list(context.supplemental_evidence_ids),
                    "conflict_evidence_ids": list(context.conflict_evidence_ids),
                    "unresolved_gap_ids": list(context.unresolved_gap_ids),
                    "evidence_items": [
                        self._evidence_reference_view(evidence_by_id[evidence_id])
                        for evidence_id in evidence_ids
                        if evidence_id in evidence_by_id
                    ],
                }
            )
        return self._safe_dict(
            {
                "snapshot_id": self.snapshot.snapshot_id,
                "snapshot_sha256": self.snapshot.snapshot_sha256,
                "subject_id": self.snapshot.subject.subject_id,
                "report_as_of": self.snapshot.report_as_of.isoformat(),
                "authorized_check_ids": list(dict.fromkeys(authorized_check_ids)),
                "submodules": submodules,
            }
        )

    async def read_evidence(
        self,
        *,
        snapshot_id: str,
        task_id: str,
        submodule_id: str,
        evidence_ids: tuple[str, ...],
    ) -> dict[str, JsonValue]:
        self._authorize(
            snapshot_id=snapshot_id,
            task_id=task_id,
            submodule_id=submodule_id,
            operation="snapshot.evidence.read",
            evidence_ids=evidence_ids,
        )
        context = self.snapshot.submodule(submodule_id)
        authorized_ids = {
            *context.evidence_ids,
            *context.supplemental_evidence_ids,
            *context.conflict_evidence_ids,
        }
        unauthorized = set(evidence_ids) - authorized_ids
        if unauthorized:
            reason = f"Evidence is outside the task submodule: {sorted(unauthorized)}"
            self._record(
                task_id=task_id,
                operation="snapshot.evidence.read",
                submodule_id=submodule_id,
                evidence_ids=evidence_ids,
                allowed=False,
                denial_reason=reason,
            )
            raise ValueError(reason)
        evidence_by_id = {item.evidence_id: item for item in self.snapshot.evidence}
        unknown = set(evidence_ids) - set(evidence_by_id)
        if unknown:
            reason = f"unknown snapshot Evidence: {sorted(unknown)}"
            self._record(
                task_id=task_id,
                operation="snapshot.evidence.read",
                submodule_id=submodule_id,
                evidence_ids=evidence_ids,
                allowed=False,
                denial_reason=reason,
            )
            raise ValueError(reason)
        if self._budget_ledger is not None:
            await self._budget_ledger.claim_snapshot_read("read_snapshot_evidence")
        items = [self._evidence_view(evidence_by_id[item]) for item in evidence_ids]
        self._record(
            task_id=task_id,
            operation="snapshot.evidence.read",
            submodule_id=submodule_id,
            evidence_ids=evidence_ids,
            allowed=True,
        )
        return self._safe_dict(
            {
                "snapshot_id": self.snapshot.snapshot_id,
                "snapshot_sha256": self.snapshot.snapshot_sha256,
                "submodule_id": submodule_id,
                "items": items,
            }
        )

    def build_tools(self) -> tuple[Any, Any, Any]:
        """Build Agent-owned tools; no external data capability is registered."""

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{self.agent_id}.assigned-snapshot-context-read",
                name="read_assigned_snapshot_context",
                description=(
                    "Read all unique normalized snapshot submodules and Evidence already "
                    "authorized for this Agent in one bounded call."
                ),
                input_params=ReadAssignedSnapshotContextInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def read_assigned_snapshot_context() -> dict[str, JsonValue]:
            return await self.read_assigned_context()

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{self.agent_id}.snapshot-submodule-read",
                name="read_snapshot_submodule",
                description="Read one task-authorized submodule from the immutable snapshot.",
                input_params=ReadSnapshotSubmoduleInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=True,
            )
        )
        async def read_snapshot_submodule(
            snapshot_id: str,
            task_id: str,
            submodule_id: str,
        ) -> dict[str, JsonValue]:
            return await self.read_submodule(
                snapshot_id=snapshot_id,
                task_id=task_id,
                submodule_id=submodule_id,
            )

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{self.agent_id}.snapshot-evidence-read",
                name="read_snapshot_evidence",
                description="Read normalized Evidence for one task-authorized submodule.",
                input_params=ReadSnapshotEvidenceInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=True,
            )
        )
        async def read_snapshot_evidence(
            snapshot_id: str,
            task_id: str,
            submodule_id: str,
            evidence_ids: tuple[str, ...],
        ) -> dict[str, JsonValue]:
            return await self.read_evidence(
                snapshot_id=snapshot_id,
                task_id=task_id,
                submodule_id=submodule_id,
                evidence_ids=evidence_ids,
            )

        return (
            read_assigned_snapshot_context,
            read_snapshot_submodule,
            read_snapshot_evidence,
        )

    def _authorize(
        self,
        *,
        snapshot_id: str,
        task_id: str,
        submodule_id: str,
        operation: str,
        evidence_ids: tuple[str, ...] = (),
    ) -> None:
        reason: str | None = None
        if snapshot_id != self.snapshot.snapshot_id:
            reason = "snapshot id does not match the bound immutable snapshot"
        else:
            grant = self._grants.get(task_id)
            if grant is None:
                reason = "task is not assigned to this snapshot reader"
            elif submodule_id not in grant.allowed_submodule_ids:
                reason = "submodule is outside the assigned task scope"
        if reason is None:
            return
        self._record(
            task_id=task_id,
            operation=operation,
            submodule_id=submodule_id,
            evidence_ids=evidence_ids,
            allowed=False,
            denial_reason=reason,
        )
        raise ValueError(reason)

    def _record(
        self,
        *,
        task_id: str,
        operation: str,
        submodule_id: str,
        evidence_ids: tuple[str, ...],
        allowed: bool,
        denial_reason: str | None = None,
    ) -> None:
        self._audit_records.append(
            SnapshotReadAuditRecord(
                run_id=self.run_id,
                snapshot_id=self.snapshot.snapshot_id,
                agent_id=self.agent_id,
                task_id=task_id,
                operation=operation,
                submodule_id=submodule_id,
                evidence_ids=evidence_ids,
                allowed=allowed,
                denial_reason=denial_reason,
                occurred_at=self._clock(),
            )
        )

    @classmethod
    def _safe_dict(cls, value: object) -> dict[str, JsonValue]:
        safe = redact_json(value)
        if not isinstance(safe, dict):
            raise TypeError("snapshot Tool output must be an object")
        return safe

    @classmethod
    def _evidence_view(cls, evidence: object) -> dict[str, JsonValue]:
        from jindiao.contracts.evidence import Evidence

        item = Evidence.model_validate(evidence)
        return cls._safe_dict(
            {
                "evidence_id": item.evidence_id,
                "claim": item.claim,
                "value": item.value,
                "source_type": item.source_type.value,
                "source_status": item.source_status.value,
                "source_tool": item.source_tool,
                "source_record_id": item.source_record_id,
                "queried_at": item.queried_at.isoformat(),
                "as_of_date": item.as_of_date.isoformat() if item.as_of_date else None,
                "confidence": item.confidence,
                "is_mock": item.is_mock,
                "supports_fields": list(item.supports_fields),
                "source_ref": item.raw_ref,
                "source_title": item.source_title,
                "source_publisher": item.source_publisher,
                "content_hash": item.content_hash,
            }
        )

    @classmethod
    def _evidence_reference_view(cls, evidence: object) -> dict[str, JsonValue]:
        """Return citation metadata without duplicating facts in the bulk context."""

        view = cls._evidence_view(evidence)
        view.pop("value", None)
        return view


__all__ = [
    "ReadAssignedSnapshotContextInput",
    "SnapshotReadAuditRecord",
    "SnapshotReadGrant",
    "SnapshotReadToolset",
]
