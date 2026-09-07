from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.tianyancha import (
    CapabilityRoutingConfig,
    CompanyCapabilityService,
    McpCallResult,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class FakeClient:
    def __init__(self, result: McpCallResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        self.calls.append((name, arguments))
        return self.result


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:22822",
        company_name="示例科技有限公司",
        unified_social_credit_code="91110000EXAMPLE01",
        region="北京市",
        registration_status="存续",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


@pytest.mark.asyncio
async def test_capability_service_fetches_actual_tool_names_and_caches_by_subject() -> None:
    result = McpCallResult.model_validate(
        {
            "structured_content": {
                "tools": [
                    {
                        "tool_name": "get_shareholder_list_v2",
                        "description": "获取企业股东与出资信息",
                        "input_schema": {"type": "object"},
                    },
                    {
                        "tool_name": "get_execution_cases_v3",
                        "description": "获取被执行案件",
                        "input_schema": {"type": "object"},
                    },
                ]
            }
        }
    )
    client = FakeClient(result)
    service = CompanyCapabilityService(client, ttl=timedelta(minutes=10), clock=lambda: NOW)

    first = await service.get(subject())
    second = await service.get(subject())

    assert first is second
    assert first.tool_names == ("get_execution_cases_v3", "get_shareholder_list_v2")
    assert client.calls == [
        (
            "get_company_capabilities",
            {"company_id": "22822", "company_name": "示例科技有限公司"},
        )
    ]


@pytest.mark.asyncio
async def test_capability_service_parses_markdown_manifest() -> None:
    result = McpCallResult(
        text=(
            "| tool_name | 能力说明 |\n"
            "| --- | --- |\n"
            "| company_judgment_list | 裁判文书与诉讼案件 |\n",
        )
    )

    manifest = await CompanyCapabilityService(
        FakeClient(result),
        ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    ).get(subject())

    assert manifest.tool_names == ("company_judgment_list",)


@pytest.mark.asyncio
async def test_capability_service_ignores_markdown_separator_rows_from_live_manifest() -> None:
    result = McpCallResult(
        text=(
            "| tool_name | 能力说明 |\n"
            "| --- | --- |\n"
            "| --- | --- |\n"
            "| tool_name | 能力说明 |\n"
            "| get_annual_reports | 企业年报 |\n",
        )
    )

    manifest = await CompanyCapabilityService(
        FakeClient(result),
        ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    ).get(subject())

    assert manifest.tool_names == ("get_annual_reports",)


def test_routing_config_selects_only_tools_present_in_manifest(tmp_path: Path) -> None:
    config_path = tmp_path / "routes.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routes": {
                    "governance": {
                        "preferred_tool_names": ["get_shareholder_list_v2"],
                        "keywords": ["股东", "出资"],
                    },
                    "judicial": {
                        "preferred_tool_names": ["guessed_missing_tool"],
                        "keywords": ["执行", "裁判"],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    routing = CapabilityRoutingConfig.from_file(config_path)
    manifest = CompanyCapabilityService.parse_result(
        subject(),
        McpCallResult.model_validate(
            {
                "structured_content": {
                    "tools": [
                        {
                            "tool_name": "get_shareholder_list_v2",
                            "description": "股东与出资信息",
                        },
                        {
                            "tool_name": "get_execution_cases_v3",
                            "description": "被执行案件",
                        },
                    ]
                }
            }
        ),
        fetched_at=NOW,
    )

    assert [tool.name for tool in routing.select(manifest, "governance")] == [
        "get_shareholder_list_v2"
    ]
    assert [tool.name for tool in routing.select(manifest, "judicial")] == [
        "get_execution_cases_v3"
    ]
    assert routing.select(manifest, "peers") == ()


def test_default_routing_config_maps_representative_live_capabilities() -> None:
    routing = CapabilityRoutingConfig.from_file(Path("config/tianyancha-capability-routes.json"))
    manifest = CompanyCapabilityService.parse_result(
        subject(),
        McpCallResult.model_validate(
            {
                "structured_content": {
                    "tools": [
                        {"tool_name": "get_shareholder_info"},
                        {"tool_name": "get_risk_overview"},
                        {"tool_name": "get_annual_reports"},
                        {"tool_name": "get_competitors"},
                    ]
                }
            }
        ),
        fetched_at=NOW,
    )

    assert [tool.name for tool in routing.select(manifest, "governance")] == [
        "get_shareholder_info"
    ]
    assert [tool.name for tool in routing.select(manifest, "judicial")] == ["get_risk_overview"]
    assert [tool.name for tool in routing.select(manifest, "operations")] == ["get_annual_reports"]
    assert [tool.name for tool in routing.select(manifest, "peers")] == ["get_competitors"]


def test_routing_selects_one_preferred_tool_per_report_submodule_and_keeps_gaps(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "submodule-routes.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routes": {
                    "governance": {
                        "preferred_tool_names": [],
                        "keywords": [],
                        "submodules": [
                            {
                                "submodule_id": "registration",
                                "title": "工商登记信息",
                                "section_id": "company-profile",
                                "preferred_tool_names": [
                                    "get_company_registration_info",
                                    "get_registration_snapshot",
                                ],
                                "mock_keys": ["company"],
                            },
                            {
                                "submodule_id": "shareholders",
                                "title": "股东信息",
                                "section_id": "company-profile",
                                "preferred_tool_names": ["get_shareholder_info"],
                                "mock_keys": ["shareholders"],
                            },
                        ],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    routing = CapabilityRoutingConfig.from_file(config_path)
    manifest = CompanyCapabilityService.parse_result(
        subject(),
        McpCallResult.model_validate(
            {
                "structured_content": {
                    "tools": [
                        {"tool_name": "get_registration_snapshot"},
                        {"tool_name": "get_company_registration_info"},
                    ]
                }
            }
        ),
        fetched_at=NOW,
    )

    selected = routing.select_submodules(manifest, "governance")

    assert [route.submodule_id for route, _ in selected] == [
        "registration",
        "shareholders",
    ]
    assert [tool.name if tool else None for _, tool in selected] == [
        "get_company_registration_info",
        None,
    ]
    assert selected[1][0].mock_keys == ("shareholders",)


def test_default_routing_declares_all_reference_report_submodules() -> None:
    routing = CapabilityRoutingConfig.from_file(Path("config/tianyancha-capability-routes.json"))

    actual = {
        domain: {item.submodule_id for item in route.submodules}
        for domain, route in routing.routes.items()
    }

    assert actual["governance"] >= {
        "registration",
        "shareholders",
        "executives",
        "external_investments",
        "branches",
        "registration_changes",
        "beneficial_owners",
        "legal_representative_roles",
    }
    assert actual["judicial"] >= {
        "consumption_restrictions",
        "dishonest_enforcement",
        "executions",
        "judicial_documents",
        "hearing_notices",
        "court_notices",
        "judicial_auctions",
    }
    assert actual["operations"] >= {
        "liquidation",
        "operation_abnormalities",
        "administrative_penalties",
        "equity_freezes",
        "chattel_mortgages",
        "equity_pledges",
        "licenses",
        "qualifications",
        "tax_credit",
        "customs_registration",
        "bidding",
        "annual_reports",
    }
    assert actual["peers"] >= {
        "competitors",
        "regional_industry_counts",
        "regional_industry_growth",
        "industry_benchmarks",
    }
