from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jindiao.contracts.entities import (
    CandidateSubject,
    EnterpriseInput,
    ResolvedSubject,
    SubjectSource,
)


def test_enterprise_input_requires_name_or_credit_code() -> None:
    with pytest.raises(ValidationError):
        EnterpriseInput()

    assert EnterpriseInput(company_name=" 示例科技有限公司 ").company_name == "示例科技有限公司"
    assert EnterpriseInput(unified_social_credit_code="91110000EXAMPLE01").company_name is None


def test_candidate_match_score_is_bounded() -> None:
    candidate = CandidateSubject(
        subject_id="tyc:123",
        company_name="示例科技有限公司",
        match_score=0.95,
    )

    assert candidate.match_score == 0.95
    with pytest.raises(ValidationError):
        CandidateSubject(
            subject_id="tyc:bad",
            company_name="错误分数企业",
            match_score=1.1,
        )


def test_resolved_subject_is_immutable_and_source_aware() -> None:
    subject = ResolvedSubject(
        subject_id="tyc:123",
        company_name="示例科技有限公司",
        unified_social_credit_code="91110000EXAMPLE01",
        region="北京市",
        registration_status="存续",
        source=SubjectSource.TIANYANCHA,
        resolved_at=datetime(2026, 9, 3, tzinfo=UTC),
    )

    assert subject.source == "tianyancha"
    with pytest.raises(ValidationError):
        subject.company_name = "不可修改"
