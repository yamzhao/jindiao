from __future__ import annotations

import pytest
from pydantic import ValidationError

from jindiao.deepsearch import DeepSearchHit, DeepSearchProvider, DeepSearchQuery


class FakeProvider:
    async def aopen(self) -> None:
        return None

    async def search(self, query: DeepSearchQuery) -> tuple[DeepSearchHit, ...]:
        return (
            DeepSearchHit(
                hit_id="hit-1",
                scenario_snapshot_id="normal-enterprise:v1.0.0:abc",
                path="corpus/operating-notes.md",
                fragment="chunk-0001",
                content=f"matched: {query.text}",
                score=0.9,
                raw_ref=("mock://normal-enterprise/v1.0.0/corpus/operating-notes.md#chunk-0001"),
            ),
        )

    async def aclose(self) -> None:
        return None


def accepts_provider(provider: DeepSearchProvider) -> DeepSearchProvider:
    return provider


@pytest.mark.asyncio
async def test_public_deepsearch_interface_contains_no_sdk_types() -> None:
    provider = accepts_provider(FakeProvider())

    hits = await provider.search(DeepSearchQuery(text="员工人数", top_k=3))

    assert hits[0].content == "matched: 员工人数"
    assert set(hits[0].model_dump()) == {
        "hit_id",
        "scenario_snapshot_id",
        "path",
        "fragment",
        "content",
        "score",
        "raw_ref",
    }


def test_deepsearch_contracts_reject_invalid_limits_scores_and_extra_sdk_fields() -> None:
    with pytest.raises(ValidationError):
        DeepSearchQuery(text="", top_k=3)
    with pytest.raises(ValidationError):
        DeepSearchQuery(text="query", top_k=0)
    with pytest.raises(ValidationError):
        DeepSearchHit.model_validate(
            {
                "hit_id": "hit-1",
                "scenario_snapshot_id": "snapshot-1",
                "path": "corpus/file.md",
                "fragment": "chunk-0001",
                "content": "content",
                "score": 1.1,
                "raw_ref": "mock://scenario/v1/corpus/file.md#chunk-0001",
                "deepsearch_internal_document": {},
            }
        )
