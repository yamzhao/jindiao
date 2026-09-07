from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from openjiuwen.agent_teams.paths import global_skills_dir
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec
from pydantic import JsonValue

from jindiao.agents.deepsearch_agent import DeepSearchEvidenceAgent, DeepSearchTask
from jindiao.agents.specialists import (
    GovernanceAgent,
    JudicialComplianceAgent,
    OperationsPeerAgent,
)
from jindiao.application import RunContext, Settings
from jindiao.application.errors import AgentExecutionError, EvidenceReviewError
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import (
    CoverageItem,
    Evidence,
    SourceStatus,
    SourceType,
)
from jindiao.contracts.investigation import Finding, FindingStatus, RiskClass, Severity
from jindiao.deepsearch import DeepSearchHit, DeepSearchQuery, MockFallbackService
from jindiao.orchestration import RunBudget
from jindiao.orchestration.base import DomainInvestigation
from jindiao.orchestration.evidence_store import EvidenceStore
from jindiao.orchestration.planner import CapabilityAwarePlanner
from jindiao.orchestration.repair import RepairCoordinator
from jindiao.orchestration.reviewer import EvidenceReviewer
from jindiao.orchestration.team_spec import build_due_diligence_team_spec
from jindiao.scenarios import ScenarioRepository
from jindiao.tianyancha import SourceStateDecision

NOW = datetime(2026, 9, 3, tzinfo=UTC)
AS_OF = date(2026, 8, 31)


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="mock:test",
        company_name="金调测试有限公司",
        source=SubjectSource.MOCK,
        resolved_at=NOW,
    )


def evidence(
    evidence_id: str,
    value: object,
    *,
    field: str = "operations.employee_count",
    raw_ref: str | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim="员工人数",
        value=cast(JsonValue, value),
        subject_id="mock:test",
        source_type=SourceType.MOCK,
        source_status=SourceStatus.CAPABILITY_ABSENT,
        queried_at=NOW,
        as_of_date=AS_OF,
        confidence=1,
        is_mock=True,
        supports_fields=(field,),
        raw_ref=raw_ref or f"mock://test/v1/operations.json#{evidence_id}",
    )


def finding(finding_id: str, evidence_id: str, value: object = 86) -> Finding:
    return Finding(
        finding_id=finding_id,
        subject_id="mock:test",
        domain="operations",
        claim="企业员工人数已采集",
        value=cast(JsonValue, value),
        risk_class=RiskClass.NON_RISK,
        severity=Severity.INFO,
        evidence_ids=(evidence_id,),
    )


def artifact(*items: Evidence, findings: tuple[Finding, ...] = ()) -> DomainInvestigation:
    return DomainInvestigation(
        task_id="task-operations",
        domain="operations",
        findings=findings,
        evidence=items,
        coverage_items=(
            CoverageItem(
                domain="operations",
                capability="employee_count",
                status=SourceStatus.CAPABILITY_ABSENT,
                fallback_reason="mock_used",
            ),
        ),
    )


def test_team_spec_declares_investigation_roles_and_bounded_review() -> None:
    spec = build_due_diligence_team_spec(
        team_name="jindiao-test",
        model_name="test-model",
        max_review_rounds=2,
    )

    assert spec.team_name == "jindiao-test"
    assert spec.leader.member_name == "leader"
    assert {item.member_name for item in spec.predefined_members} == {
        "corporate-agent",
        "judicial-compliance-agent",
        "financial-operations-agent",
        "related-peer-agent",
        "reviewer-agent",
    }
    assert spec.default_max_review_rounds == 2
    assert spec.dispatch_mode == "scheduled"
    assert spec.enable_task_verification is False
    assert spec.storage.type == "sqlite"
    assert spec.storage.params.get("database") != ":memory:"
    assert "Investigation Leader" in spec.leader.prompt
    member_prompts = {item.member_name: item.prompt for item in spec.predefined_members}
    assert "Corporate Investigator" in member_prompts["corporate-agent"]
    assert "profitability-decline" in member_prompts["financial-operations-agent"]
    assert "Independent Reviewer" in member_prompts["reviewer-agent"]
    assert spec.metadata["role_permissions"] == {
        "leader": ["submit_check_assignments", "read_investigation_progress"],
        "corporate-agent": [
            "read_assigned_snapshot_context",
            "submit_check_result",
        ],
        "judicial-compliance-agent": [
            "read_assigned_snapshot_context",
            "submit_check_result",
        ],
        "financial-operations-agent": [
            "read_assigned_snapshot_context",
            "submit_check_result",
        ],
        "related-peer-agent": [
            "read_assigned_snapshot_context",
            "submit_check_result",
        ],
        "reviewer-agent": ["read_check_submissions", "submit_review"],
    }


def test_team_spec_binds_runtime_tools_to_each_exact_member() -> None:
    spec = build_due_diligence_team_spec(
        team_name="jindiao-runtime-tools",
        model_name="test-model",
        max_review_rounds=2,
        investigation_runtime_key="run:agent-teams",
        model_provider="OpenAI",
        model_base_url="https://model.invalid/v1",
        model_api_key="test-key",
    )

    assert set(spec.agents) == {"leader", "teammate"}
    assert spec.agents["teammate"].card is None
    for role_type in ("leader", "teammate"):
        assert spec.agents[role_type].tools == [
            BuiltinToolSpec(
                type="jindiao.investigation_runtime_tools",
                params={"runtime_key": "run:agent-teams"},
            )
        ]
    member_model_names = {item.member_name: item.model_name for item in spec.predefined_members}
    assert len(set(member_model_names.values())) == 5
    assert all(name and name.endswith(member) for member, name in member_model_names.items())


def test_investigation_team_never_inherits_annual_report_skill_or_tool() -> None:
    spec = build_due_diligence_team_spec(
        team_name="jindiao-deepsearch",
        model_name="test-model",
        max_review_rounds=2,
        tianyancha_annual_report_enabled=True,
        tianyancha_annual_report_lookback_years=3,
        tianyancha_annual_report_timeout_seconds=9,
    )

    assert "deepsearch-agent" not in spec.agents
    assert all(not item.skills and not item.tools for item in spec.agents.values())
    assert global_skills_dir() == Path("skills").resolve()


def test_investigation_team_omits_source_specific_skill_when_disabled() -> None:
    spec = build_due_diligence_team_spec(
        team_name="jindiao-no-annual-report",
        model_name="test-model",
        max_review_rounds=2,
        tianyancha_annual_report_enabled=False,
    )

    assert "deepsearch-agent" not in spec.agents
    assert all(not item.skills and not item.tools for item in spec.agents.values())


def test_capability_aware_plan_assigns_available_mock_and_skipped_tasks() -> None:
    plan = CapabilityAwarePlanner().create_plan(
        subject=subject(),
        domain_capabilities={
            "governance": ("shareholders",),
            "judicial": (),
            "operations": (),
            "peers": ("industry_peers",),
        },
        mock_domains={"judicial"},
        budget=RunBudget(
            max_tool_calls=20,
            max_concurrency=3,
            timeout_seconds=60,
            max_repair_rounds=2,
        ),
    )

    by_domain = {task.domain: task for task in plan.tasks}
    assert by_domain["governance"].assigned_agent == "governance-agent"
    assert by_domain["judicial"].assigned_agent == "deepsearch-agent"
    assert by_domain["judicial"].capability == "mock_fallback"
    assert by_domain["operations"].status.value == "skipped"
    assert by_domain["operations"].skipped_reason == "capability_and_mock_absent"
    reviewer = by_domain["review"]
    assert set(reviewer.dependencies) == {
        "investigate-governance",
        "investigate-judicial",
        "investigate-peers",
    }
    assert plan.max_concurrency == 3
    assert plan.max_tool_calls == 20


@pytest.mark.asyncio
async def test_specialists_enforce_domain_boundaries_and_return_structured_artifacts() -> None:
    class Toolset:
        async def investigate(
            self,
            context: object,
            resolved_subject: ResolvedSubject,
            domain: str,
        ) -> DomainInvestigation:
            del context, resolved_subject
            return artifact(evidence("ev-1", 86), findings=(finding("finding-1", "ev-1"),))

    toolset = Toolset()
    governance = GovernanceAgent(toolset=toolset)
    judicial = JudicialComplianceAgent(toolset=toolset)
    operations = OperationsPeerAgent(toolset=toolset)

    assert governance.allowed_domains == frozenset({"governance"})
    assert judicial.allowed_domains == frozenset({"judicial"})
    assert operations.allowed_domains == frozenset({"operations", "peers"})
    run_context = RunContext.from_settings(
        request_id="req-specialist-harness",
        run_id="run-specialist-harness",
        scenario=ScenarioRepository(Path("mock_data/scenarios")).load_template("normal-enterprise"),
        settings=Settings(model_name="deterministic-test-model"),
        skill_versions={},
    )
    result = await operations.investigate(run_context, subject(), "operations")
    assert result.findings[0].evidence_ids == ("ev-1",)
    with pytest.raises(AgentExecutionError, match="domain"):
        await governance.investigate(run_context, subject(), "judicial")


@pytest.mark.asyncio
async def test_deepsearch_agent_consumes_only_policy_allowed_fallback_tasks() -> None:
    class Provider:
        def __init__(self) -> None:
            self.queries: list[DeepSearchQuery] = []

        async def aopen(self) -> None:
            return None

        async def search(self, query: DeepSearchQuery) -> tuple[DeepSearchHit, ...]:
            self.queries.append(query)
            return (
                DeepSearchHit(
                    hit_id="hit-1",
                    scenario_snapshot_id="test:v1:abc",
                    path="corpus/note.md",
                    fragment="chunk-0001",
                    content="员工人数 112 人",
                    score=1,
                    raw_ref="mock://test/v1/corpus/note.md#chunk-0001",
                ),
            )

        async def aclose(self) -> None:
            return None

    provider = Provider()
    agent = DeepSearchEvidenceAgent(service=MockFallbackService(provider))
    allowed = DeepSearchTask(
        source=SourceStateDecision(
            status=SourceStatus.CAPABILITY_ABSENT,
            mock_fallback_allowed=True,
        ),
        domain="operations",
        capability="employee_count",
        query="员工人数",
        allow_degraded_mock=False,
    )

    result = await agent.investigate(allowed, subject(), queried_at=NOW)

    assert result.evidence[0].source_type is SourceType.MOCK
    assert result.coverage_items[0].status is SourceStatus.CAPABILITY_ABSENT
    denied = allowed.model_copy(
        update={
            "source": SourceStateDecision(
                status=SourceStatus.VERIFIED_EMPTY,
                mock_fallback_allowed=False,
            )
        }
    )
    with pytest.raises(AgentExecutionError, match="fallback"):
        await agent.investigate(denied, subject(), queried_at=NOW)
    assert len(provider.queries) == 1


@pytest.mark.asyncio
async def test_evidence_store_is_idempotent_merges_lineage_and_rejects_collisions() -> None:
    store = EvidenceStore(subject_id="mock:test")
    first = artifact(
        evidence("ev-a", 86, raw_ref="mock://test/v1/a.json#1"),
        findings=(finding("finding-a", "ev-a"),),
    )
    duplicate_fact = artifact(
        evidence("ev-b", 86, raw_ref="mock://test/v1/b.md#1"),
        findings=(finding("finding-b", "ev-b"),),
    )

    normalized_first = await store.ingest("operations-agent", first)
    normalized_again = await store.ingest("operations-agent", first)
    normalized_second = await store.ingest("deepsearch-agent", duplicate_fact)

    assert normalized_again == normalized_first
    assert normalized_second.findings[0].evidence_ids == ("ev-a",)
    assert len(store.evidence) == 1
    assert set(store.evidence[0].source_chain) == {
        "mock://test/v1/a.json#1",
        "mock://test/v1/b.md#1",
    }

    collision = artifact(evidence("ev-a", 112, raw_ref="mock://test/v1/c.md#1"))
    with pytest.raises(EvidenceReviewError, match="collision"):
        await store.ingest("deepsearch-agent", collision)


def test_reviewer_accepts_backed_finding_and_blocks_cross_agent_conflict() -> None:
    reviewer = EvidenceReviewer()
    ev_structured = evidence("ev-structured", 86)
    ev_document = evidence("ev-document", 112, raw_ref="mock://test/v1/note.md#1")
    backed = finding("finding-structured", "ev-structured", 86)
    conflicting = finding("finding-document", "ev-document", 112)

    clean = reviewer.review(
        subject=subject(),
        findings=(backed,),
        evidence=(ev_structured,),
        report_as_of=AS_OF,
    )
    assert clean.findings[0].status is FindingStatus.ACCEPTED
    assert clean.conflicts == ()

    reviewed = reviewer.review(
        subject=subject(),
        findings=(backed, conflicting),
        evidence=(ev_structured, ev_document),
        report_as_of=AS_OF,
    )
    assert len(reviewed.conflicts) == 1
    assert reviewed.conflicts[0].field == "operations.employee_count"
    assert all(item.status is FindingStatus.UNCONFIRMED for item in reviewed.findings)
    assert reviewed.decision.accepted_finding_ids == ()
    assert reviewed.decision.unresolved_issue_ids == (reviewed.issues[0].issue_id,)


def test_reviewer_blocks_tianyancha_public_web_field_conflict() -> None:
    tyc_evidence = Evidence(
        evidence_id="ev-tyc-employee-count",
        claim="天眼查年报员工人数",
        value=86,
        subject_id="mock:test",
        source_type=SourceType.TIANYANCHA,
        source_status=SourceStatus.VERIFIED_RECORDS,
        source_tool="get_annual_reports",
        queried_at=NOW,
        as_of_date=AS_OF,
        confidence=1,
        is_mock=False,
        supports_fields=("operations.employee_count",),
        raw_ref="mcp://tianyancha/get_annual_reports#record=2025",
    )
    web_evidence = Evidence(
        evidence_id="ev-web-employee-count",
        claim="公开网页员工人数",
        value=112,
        subject_id="mock:test",
        source_type=SourceType.PUBLIC_WEB,
        source_status=SourceStatus.VERIFIED_RECORDS,
        source_tool="deepsearch.web_fetch",
        queried_at=NOW,
        as_of_date=AS_OF,
        confidence=0.8,
        is_mock=False,
        supports_fields=("operations.employee_count",),
        raw_ref="https://example.gov.cn/annual-report/2025",
        source_chain=("https://example.gov.cn/annual-report/2025",),
        source_title="企业年度报告",
        source_publisher="市场监督管理局",
        content_hash="sha256:" + "a" * 64,
    )

    reviewed = EvidenceReviewer().review(
        subject=subject(),
        findings=(
            finding("finding-tyc", tyc_evidence.evidence_id, 86),
            finding("finding-web", web_evidence.evidence_id, 112),
        ),
        evidence=(tyc_evidence, web_evidence),
        report_as_of=AS_OF,
    )

    assert len(reviewed.conflicts) == 1
    assert reviewed.conflicts[0].field == "operations.employee_count"
    assert set(reviewed.conflicts[0].evidence_ids) == {
        tyc_evidence.evidence_id,
        web_evidence.evidence_id,
    }
    assert all(item.status is FindingStatus.UNCONFIRMED for item in reviewed.findings)


def test_reviewer_checks_evidence_sufficiency_time_amount_and_duplicates() -> None:
    bad_evidence = evidence("ev-bad", {"amount_cny": -1}).model_copy(
        update={"as_of_date": date(2027, 1, 1)}
    )
    duplicated = finding("finding-duplicate", "ev-bad")
    reviewed = EvidenceReviewer().review(
        subject=subject(),
        findings=(duplicated, duplicated.model_copy(update={"finding_id": "finding-copy"})),
        evidence=(bad_evidence,),
        report_as_of=AS_OF,
    )

    issue_types = {item.issue_type for item in reviewed.issues}
    assert {"future_evidence", "invalid_amount", "duplicate_finding"} <= issue_types
    assert all(item.status is not FindingStatus.ACCEPTED for item in reviewed.findings)


def test_repair_tasks_are_targeted_and_stop_at_budget() -> None:
    ev_a = evidence("ev-a", 86)
    ev_b = evidence("ev-b", 112)
    reviewed = EvidenceReviewer().review(
        subject=subject(),
        findings=(finding("finding-a", "ev-a", 86), finding("finding-b", "ev-b", 112)),
        evidence=(ev_a, ev_b),
        report_as_of=AS_OF,
    )
    coordinator = RepairCoordinator(max_rounds=1)

    tasks = coordinator.create_tasks(reviewed.issues, attempt=1)

    assert len(tasks) == 1
    assert tasks[0].target_agent == "operations-peer-agent"
    assert tasks[0].requested_fields == ("operations.employee_count",)
    assert set(tasks[0].required_evidence) == {"ev-a", "ev-b"}
    assert coordinator.create_tasks(reviewed.issues, attempt=2) == ()
    exhausted = coordinator.finalize_unresolved(reviewed.findings, reviewed.issues)
    assert all(item.status is FindingStatus.UNCONFIRMED for item in exhausted)
