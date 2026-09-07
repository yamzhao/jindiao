from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.orchestration.tianyancha_toolset import TianyanchaHybridToolset
from jindiao.scenarios import ScenarioRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_live_service_optionally_wires_tianyancha_annual_report_provider(
    enabled: bool,
    tmp_path: Path,
) -> None:
    settings = Settings(
        model_provider="offline_mock",
        model_name="deterministic-test-model",
        data_source_mode="tianyancha",
        tianyancha_authorization=SecretStr("test-only-token"),
        tianyancha_annual_report_enabled=enabled,
        artifact_root=tmp_path,
    )
    service = DueDiligenceService(
        settings=settings,
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
    )

    toolset = service._create_toolset(use_live_source=True)

    assert isinstance(toolset, TianyanchaHybridToolset)
    assert toolset.deepsearch_agent_enabled is enabled
    assert toolset.annual_report_provider_enabled is enabled
    await toolset.aclose()
