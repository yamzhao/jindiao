"""Shared configuration for public Pydantic contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Strict immutable base model for facts exchanged across components."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


__all__ = ["ContractModel"]
