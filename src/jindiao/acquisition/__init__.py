"""Shared acquisition catalog and lazy snapshot-freezer compatibility export."""

from typing import TYPE_CHECKING

from .catalog import ACQUISITION_CATALOG, load_acquisition_catalog

if TYPE_CHECKING:
    from .context_freezer import ContextFreezer

__all__ = ["ACQUISITION_CATALOG", "ContextFreezer", "load_acquisition_catalog"]


def __getattr__(name: str) -> object:
    if name == "ContextFreezer":
        from .context_freezer import ContextFreezer

        return ContextFreezer
    raise AttributeError(name)
