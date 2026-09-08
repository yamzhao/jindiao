"""Evidence-backed display projection of accepted findings; never creates risks."""

from __future__ import annotations

import hashlib
import json
import re

from jindiao.contracts.evidence import Evidence
from jindiao.contracts.investigation import CheckResult, Finding, FindingStatus, RiskClass
from jindiao.contracts.product import CheckLabel, EvidenceTag, RiskFinding
from jindiao.investigation import CHECK_CATALOG


def evidence_label(item: Evidence) -> str:
    source = {
        "tianyancha": "天眼查",
        "public_web": "公开来源",
        "user_input": "调用方资料",
        "derived": "确定性计算",
        "mock": "录制模拟数据",
    }[item.source_type.value]
    return f"{source}·{item.source_title or item.source_tool or item.claim}"


def project_risks(
    findings: tuple[Finding, ...],
    evidence: tuple[Evidence, ...],
    checks: tuple[CheckResult, ...] = (),
) -> tuple[RiskFinding, ...]:
    """Merge only identical reviewed statements with identical evidence scope.

    A tool response can contain several independent incidents. Sharing a query or
    check id alone therefore never merges two cards.
    """
    by_id = {item.evidence_id: item for item in evidence}
    accepted_checks = {check.check_id for check in checks}
    cards: dict[str, RiskFinding] = {}
    for finding in findings:
        if finding.status is not FindingStatus.ACCEPTED or finding.risk_class is RiskClass.NON_RISK:
            continue
        if not finding.evidence_ids or not set(finding.evidence_ids) <= by_id.keys():
            raise ValueError("accepted risk has unknown evidence")
        values = finding.value if isinstance(finding.value, dict) else {}
        check_id = values.get("check_id")
        check_items: tuple[CheckLabel, ...] = ()
        if isinstance(check_id, str) and check_id in accepted_checks:
            check_items = (CheckLabel(id=check_id, label=CHECK_CATALOG.get(check_id).title),)
        title = values.get("title")
        title = title if isinstance(title, str) and title else finding.claim
        signature = json.dumps(
            [finding.subject_id, re.sub(r"\s+", "", finding.claim), sorted(finding.evidence_ids)],
            ensure_ascii=False,
        )
        identity = "risk-" + hashlib.sha256(signature.encode()).hexdigest()[:16]
        existing = cards.get(identity)
        if existing is not None:
            labels = {item.id: item for item in (*existing.check_items, *check_items)}
            cards[identity] = existing.model_copy(
                update={
                    "check_items": tuple(labels.values()),
                    "source_kind": "mixed" if labels else "fact",
                }
            )
            continue
        cards[identity] = RiskFinding(
            id=identity,
            title=title,
            source_kind="mixed" if check_items else "fact",
            check_items=check_items,
            risk_fact=finding.claim,
            evidence_tags=tuple(
                EvidenceTag(evidence_id=item, label=evidence_label(by_id[item]))
                for item in dict.fromkeys(finding.evidence_ids)
            ),
        )
    return tuple(cards.values())
