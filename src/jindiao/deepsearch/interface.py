"""Stable local-search contracts that isolate the application from SDK types."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from jindiao.contracts.base import ContractModel


class DeepSearchQuery(ContractModel):
    text: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=10)


class DeepSearchHit(ContractModel):
    hit_id: str = Field(min_length=1)
    scenario_snapshot_id: str = Field(min_length=1)
    path: str = Field(pattern=r"^corpus/.+")
    fragment: str = Field(pattern=r"^chunk-\d{4,}$")
    content: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    raw_ref: str = Field(pattern=r"^mock://")


class DeepSearchProvider(Protocol):
    async def aopen(self) -> None: ...

    async def search(self, query: DeepSearchQuery) -> tuple[DeepSearchHit, ...]: ...

    async def aclose(self) -> None: ...


__all__ = ["DeepSearchHit", "DeepSearchProvider", "DeepSearchQuery"]
