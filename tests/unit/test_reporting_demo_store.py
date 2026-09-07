from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_reporting_replay import source_snapshot

from jindiao.contracts.report_policy import ReportFeedbackRequest
from jindiao.reporting.demo_store import ReportingDemoStore


def feedback() -> ReportFeedbackRequest:
    return ReportFeedbackRequest(
        kind="gap_disclosure_placement", text="请就近披露", target_section_ids=("judicial-risk",)
    )


def test_candidate_does_not_apply_until_explicit_command_and_reset_preserves_it(
    tmp_path: Path,
) -> None:
    store = ReportingDemoStore(tmp_path)
    original = store.active()
    candidate, created = store.propose(
        source_snapshot(), feedback(), key="one", owner="alice", session="s"
    )
    assert created
    assert candidate.status == "awaiting_approval"
    assert store.active() == original
    repeated, created = store.propose(
        source_snapshot(), feedback(), key="one", owner="alice", session="s"
    )
    assert not created and repeated == candidate
    receipt = store.apply(candidate.evolution_id, reason="演示者确认")
    assert receipt.active.policy.gap_placement == "section_and_appendix"
    assert ReportingDemoStore(tmp_path).active() == receipt.active
    assert store.apply(candidate.evolution_id, reason="再次确认").active == receipt.active
    store.reset(reason="恢复演示")
    assert store.active().policy.gap_placement == "appendix_only"
    assert store.get(candidate.evolution_id) == candidate
    with pytest.raises(ValueError, match="stale"):
        store.apply(candidate.evolution_id, reason="旧基线")


def test_key_conflict_and_tampering_are_rejected(tmp_path: Path) -> None:
    store = ReportingDemoStore(tmp_path)
    candidate, _ = store.propose(
        source_snapshot(), feedback(), key="one", owner="alice", session=None
    )
    with pytest.raises(ValueError, match="idempotency"):
        store.propose(
            source_snapshot(),
            feedback().model_copy(update={"text": "不同反馈"}),
            key="one",
            owner="alice",
            session=None,
        )
    path = tmp_path / "candidates" / f"{candidate.evolution_id}.json"
    data = json.loads(path.read_text())
    data["payload"]["status"] = "rejected"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="hash"):
        store.apply(candidate.evolution_id, reason="不能通过")
    assert store.active().policy.gap_placement == "appendix_only"


def test_store_refuses_paths_and_symlinks(tmp_path: Path) -> None:
    store = ReportingDemoStore(tmp_path / "store")
    with pytest.raises(ValueError):
        store.get("../../secret")
    outside = tmp_path / "outside"
    outside.write_text("secret")
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "state.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        store.active()


def test_no_target_gap_creates_rejected_candidate(tmp_path: Path) -> None:
    store = ReportingDemoStore(tmp_path)
    request = feedback().model_copy(update={"target_section_ids": ("company-profile",)})
    candidate, _ = store.propose(source_snapshot(), request, key="one", owner="a", session=None)
    assert candidate.status == "rejected"
    with pytest.raises(ValueError):
        store.apply(candidate.evolution_id, reason="不能应用")


def test_unmapped_gap_has_explicit_reason(tmp_path: Path) -> None:
    from jindiao.reporting.markdown import MarkdownReportRenderer
    from jindiao.reporting.replay import ReplaySnapshot, load_suite

    snapshot = source_snapshot()
    view = load_suite()[-1].view
    snapshot = ReplaySnapshot.freeze(
        run_id="unmapped",
        view=view,
        binding=snapshot.binding,
        report=MarkdownReportRenderer().render(view),
    )
    candidate, _ = ReportingDemoStore(tmp_path).propose(
        snapshot, feedback(), key="one", owner="a", session=None
    )
    assert candidate.status == "rejected"
    assert candidate.reason_codes == ("unmapped_gap",)


@pytest.mark.parametrize("payload", ["[]", "{}", '{"sha256":"bad"}'])
def test_corrupt_state_is_explicitly_refused(tmp_path: Path, payload: str) -> None:
    (tmp_path / "state.json").write_text(payload)
    with pytest.raises(ValueError, match="demo file"):
        ReportingDemoStore(tmp_path).active()


@pytest.mark.parametrize("mutation", ["policy", "revision", "evaluation", "fingerprint"])
def test_apply_revalidates_candidate_even_when_envelope_hash_matches(
    tmp_path: Path, mutation: str
) -> None:
    from jindiao.contracts.report_policy import ReportPolicy
    from jindiao.reporting.replay import canonical, digest

    store = ReportingDemoStore(tmp_path)
    candidate, _ = store.propose(source_snapshot(), feedback(), key="one", owner="a", session=None)
    path = tmp_path / "candidates" / f"{candidate.evolution_id}.json"
    raw = json.loads(path.read_text())
    payload = raw["payload"]
    if mutation == "policy":
        payload["candidate"]["policy"] = ReportPolicy().model_dump(mode="json")
        payload["candidate"]["policy_sha256"] = ReportPolicy().sha256
    elif mutation == "revision":
        payload["candidate"]["revision"] += 20
    elif mutation == "evaluation":
        payload["evaluation"]["cases"][0]["after"] += "forged"
        payload["evaluation_sha256"] = digest(canonical(payload["evaluation"]))
    else:
        payload["evaluation"]["fingerprint"] = "outdated"
        payload["evaluation_sha256"] = digest(canonical(payload["evaluation"]))
    raw["sha256"] = digest(canonical(payload))
    path.write_text(canonical(raw))
    with pytest.raises(ValueError):
        store.apply(candidate.evolution_id, reason="拒绝错误候选")
    assert store.active().version == "1.1.0"


def test_legacy_coordinator_cannot_generate_format_count_candidates(tmp_path: Path) -> None:
    from jindiao.contracts.results import SkillEvolutionFeedback
    from jindiao.skills import ReportingSkillEvolutionCoordinator

    coordinator = ReportingSkillEvolutionCoordinator(
        stable_root=tmp_path / "missing-stable", artifact_root=tmp_path / "artifacts"
    )
    result = coordinator.propose(
        SkillEvolutionFeedback(source="old", reference="old", text="缺口", evidence_refs=("old",)),
        run_id="old-run",
    )
    assert result.status.value == "rejected"
    assert result.reason_codes == ("use_feedback_api",)
    assert not (tmp_path / "artifacts").exists()
