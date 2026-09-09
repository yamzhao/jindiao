# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from jindiao.application.errors import EntityAmbiguousError, EntityNotFoundError
from jindiao.contracts.entities import EnterpriseInput, SubjectSource
from jindiao.contracts.runs import RunCreateRequest
from jindiao.tianyancha import McpCallResult, TianyanchaEntityResolver

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class FakeClient:
    def __init__(self, result: McpCallResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        self.calls.append((name, arguments))
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", ["companies", "companyList", "records", "results"])
async def test_resolver_accepts_provider_specific_company_list_envelopes(envelope: str) -> None:
    client = FakeClient(
        McpCallResult(
            structured_content={
                "data": {
                    envelope: [
                        {
                            "company_id": "2962178558",
                            "company_name": "同盾科技（上海）有限公司",
                            "unified_social_credit_code": "91310104MA1FR5Q84D",
                        }
                    ]
                }
            }
        )
    )

    subject = await TianyanchaEntityResolver(client, clock=lambda: NOW).resolve(
        EnterpriseInput(company_name="同盾科技（上海）有限公司")
    )

    assert subject.subject_id == "tyc:2962178558"


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_code", ["ACTUAL-CODE", None])
async def test_flat_request_anchors_by_name_and_uses_provider_credit_code(
    actual_code: str | None,
) -> None:
    client = FakeClient(
        McpCallResult(
            structured_content={
                "items": [
                    {"id": "1", "name": "另一企业", "creditCode": "SUBMITTED-CODE"},
                    {"id": "2", "name": "目标企业", "creditCode": actual_code},
                ]
            }
        )
    )
    request = RunCreateRequest(customerName=" 目标企业 ", uscc="SUBMITTED-CODE")

    subject = await TianyanchaEntityResolver(client, clock=lambda: NOW).resolve(
        request.to_execution_request().enterprise
    )

    assert subject.subject_id == "tyc:2"
    assert subject.company_name == "目标企业"
    assert subject.unified_social_credit_code == actual_code
    assert client.calls == [("search_companies", {"query": "目标企业", "page": 1, "page_size": 20})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names,error",
    [
        (["另一企业"], EntityNotFoundError),
        (["目标企业", "目标企业"], EntityAmbiguousError),
    ],
)
async def test_flat_request_uscc_cannot_override_missing_or_ambiguous_name(
    names: list[str], error: type[Exception]
) -> None:
    client = FakeClient(
        McpCallResult(
            structured_content={
                "items": [
                    {"id": str(index), "name": name, "creditCode": f"CODE{index}"}
                    for index, name in enumerate(names)
                ]
            }
        )
    )
    request = RunCreateRequest(customerName="目标企业", uscc="CODE0")

    with pytest.raises(error):
        await TianyanchaEntityResolver(client, clock=lambda: NOW).resolve(
            request.to_execution_request().enterprise
        )


@pytest.mark.asyncio
async def test_resolver_anchors_unique_credit_code_from_structured_result() -> None:
    client = FakeClient(
        McpCallResult(
            structured_content={
                "items": [
                    {
                        "id": "22822",
                        "name": "示例科技有限公司",
                        "creditCode": "91110000EXAMPLE01",
                        "base": "北京市",
                        "regStatus": "存续",
                    },
                    {
                        "id": "99881",
                        "name": "示例科技集团有限公司",
                        "creditCode": "91110000EXAMPLE02",
                        "base": "北京市",
                        "regStatus": "存续",
                    },
                ]
            }
        )
    )
    resolver = TianyanchaEntityResolver(client, clock=lambda: NOW)

    subject = await resolver.resolve(
        EnterpriseInput(unified_social_credit_code="91110000example01")
    )

    assert subject.subject_id == "tyc:22822"
    assert subject.company_name == "示例科技有限公司"
    assert subject.source is SubjectSource.TIANYANCHA
    assert subject.resolved_at == NOW
    assert client.calls == [
        (
            "search_companies",
            {"query": "91110000EXAMPLE01", "page": 1, "page_size": 20},
        )
    ]


@pytest.mark.asyncio
async def test_resolver_uses_region_then_active_status_to_disambiguate_name() -> None:
    client = FakeClient(
        McpCallResult(
            structured_content={
                "items": [
                    {
                        "id": "1",
                        "name": "同名企业有限公司",
                        "creditCode": "CODE1",
                        "base": "上海市",
                        "regStatus": "注销",
                    },
                    {
                        "id": "2",
                        "name": "同名企业有限公司",
                        "creditCode": "CODE2",
                        "base": "北京市",
                        "regStatus": "存续",
                    },
                ]
            }
        )
    )

    subject = await TianyanchaEntityResolver(client, clock=lambda: NOW).resolve(
        EnterpriseInput(company_name="同名企业有限公司", region="北京市")
    )

    assert subject.subject_id == "tyc:2"
    assert subject.region == "北京市"


@pytest.mark.asyncio
async def test_resolver_parses_markdown_candidates() -> None:
    text = """
| 企业ID | 企业名称 | 统一社会信用代码 | 地区 | 经营状态 |
| --- | --- | --- | --- | --- |
| 123 | 乐视网信息技术（北京）股份有限公司 | 91110108MA01JD001A | 北京市 | 存续 |
"""
    client = FakeClient(McpCallResult(text=(text,)))

    candidates = await TianyanchaEntityResolver(client, clock=lambda: NOW).search(
        EnterpriseInput(company_name="乐视网信息技术（北京）股份有限公司")
    )

    assert len(candidates) == 1
    assert candidates[0].subject_id == "tyc:123"
    assert candidates[0].match_score == 0.9


@pytest.mark.asyncio
async def test_resolver_rejects_ambiguous_and_missing_exact_matches() -> None:
    items = [
        {"id": "1", "name": "同名企业", "regStatus": "存续"},
        {"id": "2", "name": "同名企业", "regStatus": "存续"},
    ]
    ambiguous = TianyanchaEntityResolver(
        FakeClient(McpCallResult.model_validate({"structured_content": {"items": items}})),
        clock=lambda: NOW,
    )
    missing = TianyanchaEntityResolver(
        FakeClient(McpCallResult.model_validate({"structured_content": {"items": items}})),
        clock=lambda: NOW,
    )

    with pytest.raises(EntityAmbiguousError) as captured:
        await ambiguous.resolve(EnterpriseInput(company_name="同名企业"))
    assert captured.value.details["candidate_ids"] == ["tyc:1", "tyc:2"]
    with pytest.raises(EntityNotFoundError):
        await missing.resolve(EnterpriseInput(company_name="另一家企业"))
