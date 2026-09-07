"""Run one credential-free multi-agent scenario and print the complete Result."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.results import DueDiligenceRequest
from jindiao.scenarios import ScenarioRepository


async def main() -> None:
    settings = Settings(
        model_provider="offline_mock",
        model_name="deterministic-mock",
        mock_data_root=Path("mock_data"),
        artifact_root=Path("artifacts"),
    )
    service = DueDiligenceService(
        settings=settings,
        scenarios=ScenarioRepository(settings.mock_data_root / "scenarios"),
    )
    result = await service.run(
        DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="金调双源制造有限公司"),
            scenario_id="evidence-conflict",
        )
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
