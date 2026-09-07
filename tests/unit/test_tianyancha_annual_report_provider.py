from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import SourceStatus, SourceType
from jindiao.deepsearch import TianyanchaAnnualReportProvider

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:2962178558",
        company_name="同盾科技\uff08上海\uff09有限公司",
        unified_social_credit_code="91310104MA1FR5Q84D",
        region="上海市",
        registration_status="开业",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


def annual_report_html(
    *,
    year: int = 2025,
    publicized_at: str = "2026-06-29",
    company_name: str = "同盾科技\uff08上海\uff09有限公司",
    credit_code: str = "91310104MA1FR5Q84D",
    include_social_security: bool = True,
) -> str:
    social_security = ""
    if include_social_security:
        social_security = """
        <h2>社保信息</h2>
        <table>
          <tr><td>城镇职工基本养老保险</td><td>54人</td>
              <td>职工基本医疗保险</td><td>54人</td>
              <td>生育保险</td><td>54人</td></tr>
          <tr><td>失业保险</td><td>54人</td>
              <td>工伤保险</td><td>企业选择不公示</td></tr>
        </table>
        <table>
          <tr><td>单位缴费基数</td>
              <td>单位参加城镇职工基本养老保险缴费基数</td>
              <td>企业选择不公示</td></tr>
          <tr><td>参加工伤保险本期实际缴费金额</td>
              <td>企业选择不公示</td></tr>
          <tr><td>单位参加失业保险累计欠缴金额</td><td>0万元</td></tr>
        </table>
        """
    return f"""
    <html>
      <head><title>{company_name}{year}年报 - 天眼查</title></head>
      <body>
        <h1>{company_name}</h1>
        <div>{year} 年度报告 {publicized_at} 公示</div>
        <h2>基本信息</h2>
        <table>
          <tr><td>企业名称</td><td>{company_name}</td>
              <td>统一社会信用代码</td><td>{credit_code}</td></tr>
        </table>
        {social_security}
      </body>
    </html>
    """


@pytest.mark.asyncio
async def test_provider_reads_latest_report_social_security_as_public_web_evidence() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, text=annual_report_html())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TianyanchaAnnualReportProvider(client=client, max_lookback_years=3)

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 9, 4),
    )
    await provider.aclose()

    assert requested_urls == ["https://www.tianyancha.com/annualReport/2962178558/2025"]
    assert outcome.source_status is SourceStatus.VERIFIED_RECORDS
    assert outcome.checked_years == (2025,)
    assert outcome.report is not None
    assert outcome.report.report_year == 2025
    assert outcome.report.publicized_at == date(2026, 6, 29)
    assert outcome.report.pension_insured_count == 54
    assert outcome.report.medical_insured_count == 54
    assert outcome.report.maternity_insured_count == 54
    assert outcome.report.unemployment_insured_count == 54
    assert outcome.report.work_injury_insured_count is None
    assert outcome.report.reported_values["工伤保险"] == "企业选择不公示"
    assert (
        outcome.report.reported_values["单位参加城镇职工基本养老保险缴费基数"] == "企业选择不公示"
    )
    assert outcome.report.reported_values["单位参加失业保险累计欠缴金额"] == "0万元"
    assert len(outcome.evidence) == 1
    evidence = outcome.evidence[0]
    assert evidence.source_type is SourceType.PUBLIC_WEB
    assert evidence.source_tool == "tianyancha.annual_report.web"
    assert evidence.raw_ref == requested_urls[0]
    assert evidence.supports_fields == ("operations.annual_reports.social_security",)
    assert evidence.as_of_date == date(2026, 6, 29)
    assert evidence.content_hash is not None


@pytest.mark.asyncio
async def test_provider_uses_latest_available_report_within_bounded_lookback() -> None:
    requested_years: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        year = int(request.url.path.rsplit("/", 1)[-1])
        requested_years.append(year)
        if year == 2025:
            return httpx.Response(404)
        return httpx.Response(
            200,
            text=annual_report_html(year=2024, publicized_at="2025-05-31"),
        )

    provider = TianyanchaAnnualReportProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_lookback_years=3,
    )

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 9, 4),
    )
    await provider.aclose()

    assert requested_years == [2025, 2024]
    assert outcome.source_status is SourceStatus.VERIFIED_RECORDS
    assert outcome.checked_years == (2025, 2024)
    assert outcome.report is not None
    assert outcome.report.report_year == 2024


@pytest.mark.asyncio
async def test_provider_handles_tianyancha_200_fallback_for_a_missing_year() -> None:
    requested_years: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        year = int(request.url.path.rsplit("/", 1)[-1])
        requested_years.append(year)
        return httpx.Response(
            200,
            text=annual_report_html(year=2024, publicized_at="2025-05-31"),
        )

    provider = TianyanchaAnnualReportProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_lookback_years=3,
    )

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 9, 4),
    )
    await provider.aclose()

    assert requested_years == [2025, 2024]
    assert outcome.source_status is SourceStatus.VERIFIED_RECORDS
    assert outcome.report is not None
    assert outcome.report.report_year == 2024


@pytest.mark.asyncio
async def test_provider_skips_report_publicized_after_report_as_of() -> None:
    requested_years: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        year = int(request.url.path.rsplit("/", 1)[-1])
        requested_years.append(year)
        if year == 2025:
            return httpx.Response(200, text=annual_report_html())
        return httpx.Response(
            200,
            text=annual_report_html(year=2024, publicized_at="2025-05-31"),
        )

    provider = TianyanchaAnnualReportProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_lookback_years=2,
    )

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 5, 1),
    )
    await provider.aclose()

    assert requested_years == [2025, 2024]
    assert outcome.source_status is SourceStatus.VERIFIED_RECORDS
    assert outcome.report is not None
    assert outcome.report.report_year == 2024


@pytest.mark.asyncio
async def test_provider_treats_missing_annual_reports_as_normal_verified_empty() -> None:
    requested_years: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_years.append(int(request.url.path.rsplit("/", 1)[-1]))
        return httpx.Response(404)

    provider = TianyanchaAnnualReportProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_lookback_years=3,
    )

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 9, 4),
    )
    await provider.aclose()

    assert requested_years == [2025, 2024, 2023]
    assert outcome.source_status is SourceStatus.VERIFIED_EMPTY
    assert outcome.report is None
    assert outcome.evidence == ()
    assert outcome.error is None


@pytest.mark.asyncio
async def test_provider_treats_identified_200_page_without_a_report_as_empty() -> None:
    identity_only_html = """
    <html><head><title>企业年报 - 天眼查</title></head><body>
      <h2>基本信息</h2>
      <table><tr><td>企业名称</td><td>同盾科技\uff08上海\uff09有限公司</td>
        <td>统一社会信用代码</td><td>91310104MA1FR5Q84D</td></tr></table>
    </body></html>
    """
    provider = TianyanchaAnnualReportProvider(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=identity_only_html))
        ),
        max_lookback_years=2,
    )

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 9, 4),
    )
    await provider.aclose()

    assert outcome.source_status is SourceStatus.VERIFIED_EMPTY
    assert outcome.checked_years == (2025, 2024)
    assert outcome.report is None
    assert outcome.error is None


@pytest.mark.asyncio
async def test_provider_preserves_report_presence_when_social_section_is_absent() -> None:
    provider = TianyanchaAnnualReportProvider(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    text=annual_report_html(include_social_security=False),
                )
            )
        )
    )

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 9, 4),
    )
    await provider.aclose()

    assert outcome.source_status is SourceStatus.VERIFIED_EMPTY
    assert outcome.report is not None
    assert outcome.report.report_year == 2025
    assert outcome.report.reported_values == {}
    assert outcome.evidence == ()


@pytest.mark.asyncio
async def test_provider_rejects_subject_mismatch_as_source_error() -> None:
    provider = TianyanchaAnnualReportProvider(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    text=annual_report_html(
                        company_name="其他企业有限公司",
                        credit_code="91110000OTHER00001",
                    ),
                )
            )
        )
    )

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 9, 4),
    )
    await provider.aclose()

    assert outcome.source_status is SourceStatus.SOURCE_ERROR
    assert outcome.report is None
    assert outcome.evidence == ()
    assert outcome.error == "annual_report_subject_mismatch"


@pytest.mark.asyncio
async def test_provider_rejects_response_outside_exact_allowlisted_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.tianyancha.com":
            return httpx.Response(
                302,
                headers={"Location": "https://attacker.example/annualReport/2962178558/2025"},
            )
        return httpx.Response(200, text=annual_report_html())

    provider = TianyanchaAnnualReportProvider(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
    )

    outcome = await provider.fetch_latest(
        subject(),
        queried_at=NOW,
        report_as_of=date(2026, 9, 4),
    )
    await provider.aclose()

    assert outcome.source_status is SourceStatus.SOURCE_ERROR
    assert outcome.error == "annual_report_url_not_allowlisted"
    assert outcome.evidence == ()


def test_provider_rejects_non_allowlisted_base_url() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        TianyanchaAnnualReportProvider(base_url="https://tianyancha.example")


@pytest.mark.asyncio
async def test_provider_default_client_ignores_ambient_proxy_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1::1")

    provider = TianyanchaAnnualReportProvider()

    await provider.aclose()
