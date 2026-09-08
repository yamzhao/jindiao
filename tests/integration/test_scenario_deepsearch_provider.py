# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

from pathlib import Path

import pytest

from jindiao.application.errors import ScenarioIntegrityError
from jindiao.contracts.entities import EnterpriseInput
from jindiao.deepsearch import (
    DeepSearchCustomLocalAdapter,
    DeepSearchLifecycleError,
    DeepSearchQuery,
    ScenarioDeepSearchProvider,
    build_mock_ref,
)
from jindiao.scenarios import ScenarioRepository, ScenarioSnapshot


def snapshot() -> ScenarioSnapshot:
    return ScenarioRepository(Path("mock_data/scenarios")).load(
        "normal-enterprise",
        EnterpriseInput(company_name="乐视网信息技术（北京）股份有限公司"),
    )


def provider() -> ScenarioDeepSearchProvider:
    return ScenarioDeepSearchProvider(snapshot(), chunk_size=80, chunk_overlap=10)


def test_mock_reference_is_stable_and_scope_checked() -> None:
    frozen = snapshot()

    first = build_mock_ref(frozen, "corpus/operating-notes.md", "chunk-0001")
    second = build_mock_ref(frozen, "corpus/operating-notes.md", "chunk-0001")

    assert first == second
    assert first == ("mock://normal-enterprise/v1.0.0/corpus/operating-notes.md#chunk-0001")
    with pytest.raises(ScenarioIntegrityError):
        build_mock_ref(frozen, "company.json", "chunk-0001")
    with pytest.raises(ScenarioIntegrityError):
        build_mock_ref(frozen, "corpus/operating-notes.md", "../expected")


@pytest.mark.asyncio
async def test_provider_indexes_only_current_snapshot_corpus_and_has_lifecycle() -> None:
    search = provider()

    with pytest.raises(DeepSearchLifecycleError):
        await search.search(DeepSearchQuery(text="在岗人员"))

    await search.aopen()
    await search.aopen()
    hits = await search.search(DeepSearchQuery(text="在岗人员", top_k=2))

    assert hits
    assert all(hit.path.startswith("corpus/") for hit in hits)
    assert all(hit.scenario_snapshot_id == search.scenario_snapshot_id for hit in hits)
    assert hits[0].raw_ref.startswith("mock://normal-enterprise/v1.0.0/corpus/")
    assert "128 人" in hits[0].content

    await search.aclose()
    with pytest.raises(DeepSearchLifecycleError):
        await search.search(DeepSearchQuery(text="在岗人员"))


@pytest.mark.asyncio
async def test_deepsearch_custom_local_adapter_returns_expected_record_shape() -> None:
    search = provider()
    adapter = DeepSearchCustomLocalAdapter(search, top_k=3)

    await adapter.aopen()
    results = await adapter.aresults("主营业务")
    await adapter.aclose()

    assert results
    assert set(results[0]) == {
        "knowledge_base_id",
        "file_id",
        "title",
        "content",
        "score",
        "raw_ref",
    }
    assert results[0]["knowledge_base_id"] == search.scenario_snapshot_id


@pytest.mark.asyncio
async def test_same_query_never_returns_another_scenario_corpus() -> None:
    repository = ScenarioRepository(Path("mock_data/scenarios"))
    normal = ScenarioDeepSearchProvider(
        repository.load(
            "normal-enterprise",
            EnterpriseInput(company_name="乐视网信息技术（北京）股份有限公司"),
        )
    )
    conflict = ScenarioDeepSearchProvider(
        repository.load(
            "evidence-conflict",
            EnterpriseInput(company_name="金调双源制造有限公司"),
        )
    )
    await normal.aopen()
    await conflict.aopen()

    normal_hits = await normal.search(DeepSearchQuery(text="在岗人员"))
    conflict_hits = await conflict.search(DeepSearchQuery(text="在岗人员"))

    assert normal_hits and conflict_hits
    assert all("128 人" in hit.content and "112 人" not in hit.content for hit in normal_hits)
    assert all("112 人" in hit.content and "128 人" not in hit.content for hit in conflict_hits)
    assert {hit.scenario_snapshot_id for hit in normal_hits}.isdisjoint(
        {hit.scenario_snapshot_id for hit in conflict_hits}
    )
