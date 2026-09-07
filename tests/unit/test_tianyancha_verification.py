from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from jindiao.tianyancha import CapabilityRoutingConfig, McpCallResult
from jindiao.tianyancha.verification import verify_tianyancha_connection

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class VerificationClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        self.calls.append((name, arguments))
        if name == "search_companies" and arguments["query"] == "公开样本有限公司":
            return McpCallResult(
                structured_content={
                    "items": [
                        {
                            "id": "123",
                            "name": "公开样本有限公司",
                            "regStatus": "存续",
                        }
                    ]
                }
            )
        if name == "search_companies":
            return McpCallResult(structured_content={"items": []})
        if name == "get_company_capabilities":
            return McpCallResult(
                structured_content={
                    "tools": [
                        {"tool_name": "get_shareholder_info"},
                        {"tool_name": "get_annual_reports"},
                    ]
                }
            )
        if name == "get_company_basic_profile":
            return McpCallResult(text=("## 企业基础画像\n\n- 经营状态: 存续",))
        if name == "call_tool":
            return McpCallResult(structured_content={"items": [{"year": 2025}]})
        raise AssertionError(f"unexpected tool: {name}")


@pytest.mark.asyncio
async def test_safe_verification_covers_live_adapter_flow_without_returning_raw_data() -> None:
    client = VerificationClient()
    routing = CapabilityRoutingConfig.model_validate(
        {
            "schema_version": 1,
            "routes": {
                "governance": {"preferred_tool_names": ["get_shareholder_info"]},
                "operations": {"preferred_tool_names": ["get_annual_reports"]},
                "judicial": {"preferred_tool_names": ["get_risk_overview"]},
                "peers": {"preferred_tool_names": ["get_competitors"]},
            },
        }
    )

    summary = await verify_tianyancha_connection(
        client=client,
        company_name="公开样本有限公司",
        empty_query="不存在企业JINDIAOEMPTY",
        routing=routing,
        clock=lambda: NOW,
    )

    assert summary.model_dump(mode="json") == {
        "company_name": "公开样本有限公司",
        "subject_source": "tianyancha",
        "capability_count": 2,
        "route_counts": {
            "governance": 1,
            "judicial": 0,
            "operations": 1,
            "peers": 0,
        },
        "basic_record_count": 1,
        "basic_record_state": "verified_records",
        "internal_tool": "get_annual_reports",
        "internal_record_count": 1,
        "internal_record_state": "verified_records",
        "empty_candidate_count": 0,
        "empty_state": "verified_empty",
    }
    serialized = summary.model_dump_json()
    assert "企业基础画像" not in serialized
    assert "year" not in serialized
