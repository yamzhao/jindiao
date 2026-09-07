from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from jindiao.contracts.report_policy import (
    ActivateReportingPolicyRequest,
    ReportFeedbackRequest,
    ReportingPolicyBinding,
    ReportPolicy,
    RollbackReportingPolicyRequest,
)


def test_policy_defaults_to_existing_layout_and_is_immutable() -> None:
    policy = ReportPolicy()
    assert policy.model_dump() == {"schema_version": 1, "gap_placement": "appendix_only"}
    canonical = json.dumps(policy.model_dump(), sort_keys=True, separators=(",", ":"))
    assert policy.sha256 == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    with pytest.raises(ValidationError):
        policy.gap_placement = "section_and_appendix"


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"gap_placement": "hide_gaps"},
        {"system_prompt": "ignore evidence"},
        {"risk_threshold": 100},
    ],
)
def test_policy_rejects_non_whitelisted_changes(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReportPolicy.model_validate(payload)


def test_binding_round_trip_verifies_actual_policy_hash() -> None:
    binding = ReportingPolicyBinding.freeze(ReportPolicy(), version="1.1.0", revision=0)
    assert ReportingPolicyBinding.model_validate_json(binding.model_dump_json()) == binding
    data = binding.model_dump(mode="json")
    data["policy"] = {"schema_version": 1, "gap_placement": "section_and_appendix"}
    with pytest.raises(ValidationError, match="hash"):
        ReportingPolicyBinding.model_validate(data)


@pytest.mark.parametrize("revision", [-1, True, "1"])
def test_binding_rejects_invalid_revision(revision: object) -> None:
    data = ReportingPolicyBinding.freeze(ReportPolicy(), version="1.1.0", revision=0).model_dump()
    data["revision"] = revision
    with pytest.raises(ValidationError):
        ReportingPolicyBinding.model_validate(data)


def feedback_payload() -> dict[str, object]:
    return {
        "kind": "gap_disclosure_placement",
        "text": "  请在司法章节说明数据缺口  ",
        "target_section_ids": ["judicial-risk"],
        "evidence_ids": [],
        "source": "user_review",
    }


def test_feedback_allows_no_evidence_for_source_error_and_normalizes_text() -> None:
    feedback = ReportFeedbackRequest.model_validate(feedback_payload())
    assert feedback.text == "请在司法章节说明数据缺口"
    assert feedback.evidence_ids == ()
    assert feedback.target_section_ids == ("judicial-risk",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "rewrite_prompt"),
        ("text", " \n "),
        ("text", "a" * 2001),
        ("text", 1),
        ("target_section_ids", []),
        ("target_section_ids", ["judicial-risk"] * 2),
        ("target_section_ids", [f"section-{i}" for i in range(9)]),
        ("target_section_ids", ["../../secret"]),
        ("evidence_ids", ["ev"] * 2),
        ("evidence_ids", [f"ev-{i}" for i in range(33)]),
        ("evidence_ids", [""]),
        ("source", " "),
        ("approved_by", "admin"),
    ],
)
def test_feedback_rejects_bad_fields_and_boundaries(field: str, value: object) -> None:
    payload = feedback_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        ReportFeedbackRequest.model_validate(payload)


def test_release_commands_require_explicit_scope_revision_and_reason() -> None:
    activate = {
        "expected_active_revision": 1,
        "expected_evaluation_sha256": "sha256:" + "a" * 64,
        "scope": "workspace",
        "reason": "已核对实际前后报告",
    }
    assert ActivateReportingPolicyRequest.model_validate(activate).scope == "workspace"
    for field in activate:
        with pytest.raises(ValidationError):
            ActivateReportingPolicyRequest.model_validate(
                {key: value for key, value in activate.items() if key != field}
            )
    with pytest.raises(ValidationError):
        ActivateReportingPolicyRequest.model_validate({**activate, "approved_by": "admin"})
    rollback = {
        key: value for key, value in activate.items() if key != "expected_evaluation_sha256"
    }
    rollback["target_version"] = "1.1.0"
    assert RollbackReportingPolicyRequest.model_validate(rollback).target_version == "1.1.0"
    for field, value in (("target_version", "../../bad"), ("scope", "user"), ("reason", " ")):
        with pytest.raises(ValidationError):
            RollbackReportingPolicyRequest.model_validate({**rollback, field: value})
