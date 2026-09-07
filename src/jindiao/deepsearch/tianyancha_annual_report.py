"""Allowlisted Tianyancha annual-report social-security evidence provider."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import Field, field_validator, model_validator

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import Evidence, SourceStatus, SourceType

_BASE_URL = "https://www.tianyancha.com"
_SOURCE_TOOL = "tianyancha.annual_report.web"
_SUPPORTS_FIELDS = ("operations.annual_reports.social_security",)
_MISSING_MARKERS = (
    "暂无年报",
    "暂无年度报告",
    "未查询到相关年报",
    "年报不存在",
)
_COUNT_FIELDS = {
    "城镇职工基本养老保险": "pension_insured_count",
    "职工基本医疗保险": "medical_insured_count",
    "生育保险": "maternity_insured_count",
    "失业保险": "unemployment_insured_count",
    "工伤保险": "work_injury_insured_count",
}
_DETAIL_FIELDS = frozenset(
    {
        "单位参加城镇职工基本养老保险缴费基数",
        "单位参加失业保险缴费基数",
        "单位参加职工基本医疗保险缴费基数",
        "单位参加工伤保险缴费基数",
        "单位参加生育保险缴费基数",
        "参加城镇职工基本养老保险本期实际缴费金额",
        "参加失业保险本期实际缴费金额",
        "参加职工基本医疗保险本期实际缴费金额",
        "参加工伤保险本期实际缴费金额",
        "参加生育保险本期实际缴费金额",
        "单位参加城镇职工基本养老保险累计欠缴金额",
        "单位参加失业保险累计欠缴金额",
        "单位参加职工基本医疗保险累计欠缴金额",
        "单位参加工伤保险累计欠缴金额",
        "单位参加生育保险累计欠缴金额",
    }
)
_KNOWN_SOCIAL_FIELDS = frozenset(_COUNT_FIELDS) | _DETAIL_FIELDS
_COUNT_PATTERN = re.compile(r"([0-9][0-9,]*)\s*人")
_REPORT_YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})\s*年度报告")
_PUBLICIZED_PATTERN = re.compile(r"(20\d{2}-\d{2}-\d{2})\s*公示")


def _normalized_text(value: str) -> str:
    return "".join(value.split()).casefold()


def _is_allowlisted_url(value: str, *, require_root_path: bool = False) -> bool:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != "www.tianyancha.com"
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    if require_root_path and parsed.path.rstrip("/"):
        return False
    return not parsed.query and not parsed.fragment


class AnnualReportSocialSecurity(ContractModel):
    """Normalized social-security disclosure from one annual report."""

    report_year: int = Field(ge=2000, le=2100)
    publicized_at: date | None = None
    company_name: str = Field(min_length=1)
    unified_social_credit_code: str | None = None
    source_url: str
    source_title: str = Field(min_length=1)
    pension_insured_count: int | None = Field(default=None, ge=0)
    medical_insured_count: int | None = Field(default=None, ge=0)
    maternity_insured_count: int | None = Field(default=None, ge=0)
    unemployment_insured_count: int | None = Field(default=None, ge=0)
    work_injury_insured_count: int | None = Field(default=None, ge=0)
    reported_values: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_url")
    @classmethod
    def source_must_be_allowlisted(cls, value: str) -> str:
        if not _is_allowlisted_url(value):
            raise ValueError("annual report source URL is not allowlisted")
        return value


class AnnualReportSocialSecurityOutcome(ContractModel):
    """Provider outcome that distinguishes empty annual reports from source failures."""

    source_status: SourceStatus
    report: AnnualReportSocialSecurity | None = None
    evidence: tuple[Evidence, ...] = ()
    checked_years: tuple[int, ...] = Field(min_length=1)
    error: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> AnnualReportSocialSecurityOutcome:
        supported = {
            SourceStatus.VERIFIED_RECORDS,
            SourceStatus.VERIFIED_EMPTY,
            SourceStatus.SOURCE_ERROR,
        }
        if self.source_status not in supported:
            raise ValueError("unsupported annual report source status")
        if self.source_status is SourceStatus.VERIFIED_RECORDS:
            if self.report is None or not self.report.reported_values or not self.evidence:
                raise ValueError("verified annual report records require report data and evidence")
            if self.error is not None:
                raise ValueError("verified annual report records cannot carry an error")
        elif self.source_status is SourceStatus.VERIFIED_EMPTY:
            if (
                self.evidence
                or self.error is not None
                or (self.report is not None and self.report.reported_values)
            ):
                raise ValueError(
                    "verified empty annual report outcome cannot carry social data/evidence/error"
                )
        elif not self.error or self.evidence or self.report is not None:
            raise ValueError("annual report source error requires an error and no evidence")
        return self


class AnnualReportSocialSecurityProvider(Protocol):
    async def fetch_latest(
        self,
        subject: ResolvedSubject,
        *,
        queried_at: datetime,
        report_as_of: date,
    ) -> AnnualReportSocialSecurityOutcome: ...

    async def aclose(self) -> None: ...


class _AnnualReportHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self._cell_depth = 0
        self._cell_parts: list[str] = []
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in {"td", "th"}:
            self._cell_depth += 1
            if self._cell_depth == 1:
                self._cell_parts = []
        if tag.casefold() == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"td", "th"} and self._cell_depth:
            self._cell_depth -= 1
            if self._cell_depth == 0:
                value = " ".join(" ".join(self._cell_parts).split())
                if value:
                    self.cells.append(value)
                self._cell_parts = []
        if tag.casefold() == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value:
            return
        self.text_parts.append(value)
        if self._cell_depth:
            self._cell_parts.append(value)
        if self._title_depth:
            self.title_parts.append(value)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts)


class _UnexpectedAnnualReportPage(ValueError):
    pass


class _AnnualReportNotFound(ValueError):
    pass


class TianyanchaAnnualReportProvider:
    """Fetch the latest available annual report from one exact allowlisted site."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = _BASE_URL,
        max_lookback_years: int = 5,
        timeout_seconds: float = 15,
    ) -> None:
        normalized_base = base_url.rstrip("/")
        if not _is_allowlisted_url(normalized_base, require_root_path=True):
            raise ValueError("annual report base URL is not allowlisted")
        if max_lookback_years < 1:
            raise ValueError("max_lookback_years must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = normalized_base
        self._max_lookback_years = max_lookback_years
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "JindiaoDueDiligence/0.1 (+https://www.tianyancha.com/)",
            },
        )

    async def fetch_latest(
        self,
        subject: ResolvedSubject,
        *,
        queried_at: datetime,
        report_as_of: date,
    ) -> AnnualReportSocialSecurityOutcome:
        company_id = self._company_id(subject)
        checked_years: list[int] = []
        first_year = report_as_of.year - 1
        for report_year in range(
            first_year,
            first_year - self._max_lookback_years,
            -1,
        ):
            checked_years.append(report_year)
            source_url = f"{self._base_url}/annualReport/{company_id}/{report_year}"
            try:
                response = await self._client.get(source_url)
            except httpx.HTTPError:
                return self._error(tuple(checked_years), "annual_report_request_failed")

            if not _is_allowlisted_url(str(response.url)):
                return self._error(
                    tuple(checked_years),
                    "annual_report_url_not_allowlisted",
                )
            if response.status_code in {204, 404, 410}:
                continue
            if response.is_redirect or response.is_error:
                return self._error(
                    tuple(checked_years),
                    f"annual_report_http_{response.status_code}",
                )
            if any(marker in response.text for marker in _MISSING_MARKERS):
                continue

            try:
                report = self._parse_report(
                    response.text,
                    source_url=source_url,
                    expected_year=report_year,
                    subject=subject,
                )
            except _AnnualReportNotFound:
                continue
            except _UnexpectedAnnualReportPage as error:
                return self._error(tuple(checked_years), str(error))
            if report.publicized_at is not None and report.publicized_at > report_as_of:
                continue
            if not report.reported_values:
                return AnnualReportSocialSecurityOutcome(
                    source_status=SourceStatus.VERIFIED_EMPTY,
                    report=report,
                    checked_years=tuple(checked_years),
                )
            evidence = self._to_evidence(
                report,
                subject=subject,
                queried_at=queried_at,
                content=response.content,
                company_id=company_id,
            )
            return AnnualReportSocialSecurityOutcome(
                source_status=SourceStatus.VERIFIED_RECORDS,
                report=report,
                evidence=(evidence,),
                checked_years=tuple(checked_years),
            )

        return AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.VERIFIED_EMPTY,
            checked_years=tuple(checked_years),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _company_id(subject: ResolvedSubject) -> str:
        if subject.source is not SubjectSource.TIANYANCHA or not subject.subject_id.startswith(
            "tyc:"
        ):
            raise ValueError("annual report provider requires a Tianyancha subject")
        company_id = subject.subject_id.removeprefix("tyc:")
        if not company_id.isdigit():
            raise ValueError("annual report provider requires a numeric Tianyancha company id")
        return company_id

    @classmethod
    def _parse_report(
        cls,
        content: str,
        *,
        source_url: str,
        expected_year: int,
        subject: ResolvedSubject,
    ) -> AnnualReportSocialSecurity:
        parser = _AnnualReportHtmlParser()
        parser.feed(content)
        company_name = cls._paired_value(parser.cells, "企业名称")
        credit_code = cls._paired_value(parser.cells, "统一社会信用代码")
        if not cls._subject_matches(
            subject,
            company_name=company_name,
            credit_code=credit_code,
        ):
            raise _UnexpectedAnnualReportPage("annual_report_subject_mismatch")
        year_match = _REPORT_YEAR_PATTERN.search(parser.text)
        if year_match is None or int(year_match.group(1)) != expected_year:
            # Tianyancha may return HTTP 200 with the latest existing report when the
            # requested year does not exist. A subject-identified page with a different
            # (or absent) body report year means this candidate year is unavailable.
            raise _AnnualReportNotFound
        publicized_match = _PUBLICIZED_PATTERN.search(parser.text)
        publicized_at = date.fromisoformat(publicized_match.group(1)) if publicized_match else None
        reported_values = (
            cls._social_security_values(parser.cells) if "社保信息" in parser.text else {}
        )
        counts = {
            field_name: cls._insured_count(reported_values.get(label))
            for label, field_name in _COUNT_FIELDS.items()
        }
        title = parser.title or f"{company_name}{expected_year}年度报告 - 天眼查"
        return AnnualReportSocialSecurity(
            report_year=expected_year,
            publicized_at=publicized_at,
            company_name=company_name,
            unified_social_credit_code=credit_code,
            source_url=source_url,
            source_title=title,
            reported_values=reported_values,
            **counts,
        )

    @staticmethod
    def _paired_value(cells: list[str], label: str) -> str:
        for index, cell in enumerate(cells[:-1]):
            if _normalized_text(cell) == _normalized_text(label):
                return cells[index + 1]
        raise _UnexpectedAnnualReportPage("annual_report_page_unrecognized")

    @staticmethod
    def _subject_matches(
        subject: ResolvedSubject,
        *,
        company_name: str,
        credit_code: str,
    ) -> bool:
        if _normalized_text(company_name) != _normalized_text(subject.company_name):
            return False
        expected_code = subject.unified_social_credit_code
        return expected_code is None or _normalized_text(credit_code) == _normalized_text(
            expected_code
        )

    @staticmethod
    def _social_security_values(cells: list[str]) -> dict[str, str]:
        values: dict[str, str] = {}
        for index, cell in enumerate(cells[:-1]):
            label = next(
                (
                    candidate
                    for candidate in _KNOWN_SOCIAL_FIELDS
                    if _normalized_text(candidate) == _normalized_text(cell)
                ),
                None,
            )
            if label is not None:
                values[label] = cells[index + 1]
        return dict(sorted(values.items()))

    @staticmethod
    def _insured_count(value: str | None) -> int | None:
        if value is None:
            return None
        match = _COUNT_PATTERN.fullmatch(value.strip())
        return int(match.group(1).replace(",", "")) if match else None

    @staticmethod
    def _to_evidence(
        report: AnnualReportSocialSecurity,
        *,
        subject: ResolvedSubject,
        queried_at: datetime,
        content: bytes,
        company_id: str,
    ) -> Evidence:
        content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        identity = f"{subject.subject_id}|{report.report_year}|{content_hash}"
        evidence_id = "ev-web-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        return Evidence(
            evidence_id=evidence_id,
            claim=f"天眼查{report.report_year}年度报告社保信息",
            value=report.model_dump(mode="json"),
            subject_id=subject.subject_id,
            source_type=SourceType.PUBLIC_WEB,
            source_status=SourceStatus.VERIFIED_RECORDS,
            source_tool=_SOURCE_TOOL,
            source_record_id=f"{company_id}:{report.report_year}",
            queried_at=queried_at,
            as_of_date=report.publicized_at,
            confidence=0.9,
            is_mock=False,
            supports_fields=_SUPPORTS_FIELDS,
            raw_ref=report.source_url,
            source_chain=(report.source_url,),
            source_title=report.source_title,
            source_publisher="天眼查",
            content_hash=content_hash,
        )

    @staticmethod
    def _error(
        checked_years: tuple[int, ...],
        message: str,
    ) -> AnnualReportSocialSecurityOutcome:
        return AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.SOURCE_ERROR,
            checked_years=checked_years,
            error=message,
        )


__all__ = [
    "AnnualReportSocialSecurity",
    "AnnualReportSocialSecurityOutcome",
    "AnnualReportSocialSecurityProvider",
    "TianyanchaAnnualReportProvider",
]
