"""Generate the deterministic, fictional enterprise scenarios checked into the repo."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "mock_data" / "scenarios"
AS_OF_DATE = "2026-08-31"

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "normal-enterprise",
        "name": "乐视网信息技术（北京）股份有限公司",
        "alias": "金调绿洲科技",
        "credit_code": "91110108MA01JD001A",
        "legal_representative": "林海",
        "industry": "软件和信息技术服务业",
        "employee_count": 128,
        "revenue": [8200, 10100, 12600],
        "profit": [760, 980, 1260],
        "operations_status": "normal",
        "judicial_status": "clear",
        "decision": "pass",
        "score": 0,
        "notes_employee_count": 128,
    },
    {
        "id": "judicial-high-risk",
        "name": "金调震岳工程有限公司",
        "alias": "金调震岳工程",
        "credit_code": "91310115MA01JD002B",
        "legal_representative": "周峰",
        "industry": "土木工程建筑业",
        "employee_count": 74,
        "revenue": [15600, 13200, 9800],
        "profit": [820, 230, -1450],
        "operations_status": "pressure",
        "judicial_status": "high_risk",
        "decision": "reject",
        "score": 180,
        "notes_employee_count": 74,
    },
    {
        "id": "operational-abnormal",
        "name": "金调星火商贸有限公司",
        "alias": "金调星火商贸",
        "credit_code": "91440300MA01JD003C",
        "legal_representative": "陈晨",
        "industry": "批发业",
        "employee_count": 39,
        "revenue": [6900, 6100, 4300],
        "profit": [310, 40, -380],
        "operations_status": "abnormal",
        "judicial_status": "minor",
        "decision": "manual_review",
        "score": 45,
        "notes_employee_count": 39,
    },
    {
        "id": "evidence-conflict",
        "name": "金调双源制造有限公司",
        "alias": "金调双源制造",
        "credit_code": "91330200MA01JD004D",
        "legal_representative": "高原",
        "industry": "通用设备制造业",
        "employee_count": 86,
        "revenue": [11800, 12050, 11600],
        "profit": [890, 810, 620],
        "operations_status": "conflict",
        "judicial_status": "minor",
        "decision": "manual_review",
        "score": 20,
        "notes_employee_count": 112,
    },
    {
        "id": "stable-industrial",
        "name": "金调澄明工业有限公司",
        "alias": "金调澄明工业",
        "credit_code": "91320500MA01JD005E",
        "legal_representative": "沈清",
        "industry": "专用设备制造业",
        "employee_count": 208,
        "revenue": [18800, 20500, 23200],
        "profit": [1120, 1390, 1680],
        "operations_status": "conflict",
        "judicial_status": "clear",
        "decision": "manual_review",
        "score": 20,
        "notes_employee_count": 219,
    },
    {
        "id": "stable-services",
        "name": "金调远帆服务有限公司",
        "alias": "金调远帆服务",
        "credit_code": "91120100MA01JD006F",
        "legal_representative": "许航",
        "industry": "商务服务业",
        "employee_count": 61,
        "revenue": [5100, 5900, 6800],
        "profit": [330, 410, 520],
        "operations_status": "conflict",
        "judicial_status": "clear",
        "decision": "manual_review",
        "score": 20,
        "notes_employee_count": 67,
    },
    {
        "id": "judicial-restriction",
        "name": "金调云岑建设有限公司",
        "alias": "金调云岑建设",
        "credit_code": "91510100MA01JD007G",
        "legal_representative": "梁峻",
        "industry": "房屋建筑业",
        "employee_count": 93,
        "revenue": [17300, 14100, 10200],
        "profit": [730, 110, -920],
        "operations_status": "pressure",
        "judicial_status": "high_risk",
        "decision": "reject",
        "score": 180,
        "notes_employee_count": 93,
    },
    {
        "id": "operating-pressure",
        "name": "金调沧澜供应链有限公司",
        "alias": "金调沧澜供应链",
        "credit_code": "91370100MA01JD008H",
        "legal_representative": "唐澜",
        "industry": "供应链管理服务",
        "employee_count": 47,
        "revenue": [8700, 6900, 4800],
        "profit": [420, 50, -260],
        "operations_status": "abnormal",
        "judicial_status": "minor",
        "decision": "manual_review",
        "score": 45,
        "notes_employee_count": 47,
    },
    {
        "id": "ambiguous-north",
        "name": "金调同名实业(北方)有限公司",
        "alias": "金调同名实业",
        "credit_code": "91150100MA01JD009J",
        "legal_representative": "顾北",
        "industry": "金属制品业",
        "employee_count": 105,
        "revenue": [9200, 9800, 10400],
        "profit": [510, 590, 630],
        "operations_status": "normal",
        "judicial_status": "clear",
        "decision": "pass",
        "score": 0,
        "notes_employee_count": 105,
    },
    {
        "id": "ambiguous-south",
        "name": "金调同名实业(南方)有限公司",
        "alias": "金调同名实业",
        "credit_code": "91440100MA01JD010K",
        "legal_representative": "顾南",
        "industry": "金属制品业",
        "employee_count": 118,
        "revenue": [9900, 10700, 11600],
        "profit": [560, 640, 710],
        "operations_status": "normal",
        "judicial_status": "clear",
        "decision": "pass",
        "score": 0,
        "notes_employee_count": 118,
    },
)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def build_company(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": "company:registration",
        "as_of_date": AS_OF_DATE,
        "company_name": spec["name"],
        "unified_social_credit_code": spec["credit_code"],
        "registration_status": "存续",
        "established_on": "2016-05-18",
        "legal_representative": spec["legal_representative"],
        "registered_capital_cny": 50000000,
        "paid_in_capital_cny": 32000000,
        "industry": spec["industry"],
        "registered_address": "中国某市高新区创新大道 88 号",
        "business_scope": "技术服务、项目管理、国内贸易及供应链服务",
        "registration_changes": [
            {
                "record_id": "company:change:2024-01",
                "changed_on": "2024-01-12",
                "item": "注册资本",
                "before": "3000 万元",
                "after": "5000 万元",
            }
        ],
    }


def build_governance(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "as_of_date": AS_OF_DATE,
        "shareholders": [
            {
                "record_id": "governance:shareholder:1",
                "name": "金调产业投资控股有限公司",
                "ownership_percent": 62,
                "subscribed_capital_cny": 31000000,
            },
            {
                "record_id": "governance:shareholder:2",
                "name": spec["legal_representative"],
                "ownership_percent": 38,
                "subscribed_capital_cny": 19000000,
            },
        ],
        "ultimate_controller": spec["legal_representative"],
        "executives": [
            {"name": spec["legal_representative"], "position": "执行董事、总经理"},
            {"name": "孙宁", "position": "监事"},
        ],
        "outbound_investments": [
            {
                "record_id": "governance:investment:1",
                "company_name": f"{spec['alias']}供应链有限公司",
                "ownership_percent": 51,
                "status": "存续",
            }
        ],
        "branches": [],
    }


def build_judicial(spec: dict[str, Any]) -> dict[str, Any]:
    if spec["judicial_status"] == "high_risk":
        return {
            "as_of_date": AS_OF_DATE,
            "executions": [
                {
                    "record_id": "judicial:execution:1",
                    "case_no": "(2026)沪0115执1234号",
                    "filed_on": "2026-06-20",
                    "amount_cny": 6800000,
                    "status": "unresolved",
                }
            ],
            "dishonest_records": [
                {
                    "record_id": "judicial:dishonest:1",
                    "case_no": "(2026)沪0115执1234号",
                    "published_on": "2026-08-02",
                    "performance_status": "全部未履行",
                }
            ],
            "judgments": [
                {
                    "record_id": "judicial:judgment:1",
                    "case_type": "建设工程合同纠纷",
                    "amount_cny": 6800000,
                    "decided_on": "2026-05-18",
                }
            ],
            "restrictions_on_high_consumption": [
                {"record_id": "judicial:restriction:1", "published_on": "2026-07-15"}
            ],
        }
    judgments = []
    if spec["judicial_status"] == "minor":
        judgments = [
            {
                "record_id": "judicial:judgment:1",
                "case_type": "买卖合同纠纷",
                "amount_cny": 180000,
                "decided_on": "2025-11-06",
                "status": "resolved",
            }
        ]
    return {
        "as_of_date": AS_OF_DATE,
        "executions": [],
        "dishonest_records": [],
        "judgments": judgments,
        "restrictions_on_high_consumption": [],
    }


def build_operations(spec: dict[str, Any]) -> dict[str, Any]:
    years = (2023, 2024, 2025)
    operations = {
        "as_of_date": AS_OF_DATE,
        "employee_count": spec["employee_count"],
        "employee_count_record_id": "operations:employees:2025",
        "financial_indicators": [
            {
                "record_id": f"operations:financial:{year}",
                "year": year,
                "revenue_cny_10k": revenue,
                "net_profit_cny_10k": profit,
            }
            for year, revenue, profit in zip(years, spec["revenue"], spec["profit"], strict=True)
        ],
        "tax_credit_grade": "A" if spec["operations_status"] == "normal" else "B",
        "bidding_awards": [
            {
                "record_id": "operations:bid:1",
                "awarded_on": "2025-09-18",
                "amount_cny": 2400000,
            }
        ],
        "intellectual_property": {"patents": 7, "software_copyrights": 12},
        "abnormal_operations": [],
        "administrative_penalties": [],
    }
    if spec["operations_status"] == "abnormal":
        operations["abnormal_operations"] = [
            {
                "record_id": "operations:abnormal:1",
                "listed_on": "2026-03-15",
                "reason": "未按期公示年度报告",
                "removed_on": None,
            }
        ]
        operations["administrative_penalties"] = [
            {
                "record_id": "operations:penalty:1",
                "decided_on": "2026-05-09",
                "reason": "产品标识不规范",
                "amount_cny": 120000,
            }
        ]
    return operations


def build_peers(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "as_of_date": AS_OF_DATE,
        "industry": spec["industry"],
        "metrics": {
            "median_revenue_cny_10k": 9600,
            "median_net_margin_percent": 7.1,
            "median_employee_count": 91,
        },
        "peer_companies": [
            {
                "record_id": "peers:company:1",
                "company_name": "金调同行甲有限公司",
                "revenue_cny_10k": 10400,
            },
            {
                "record_id": "peers:company:2",
                "company_name": "金调同行乙有限公司",
                "revenue_cny_10k": 9200,
            },
        ],
    }


def build_expected(spec: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    subject_id = f"mock:{spec['id']}"
    findings: list[dict[str, Any]] = [
        {
            "finding_id": "finding-registration-active",
            "subject_id": subject_id,
            "domain": "identity",
            "claim": "企业登记状态为存续",
            "value": "存续",
            "risk_class": "non_risk",
            "severity": "info",
            "status": "accepted",
            "evidence_ids": ["ev-company-registration"],
        }
    ]
    rule_hits: list[dict[str, Any]] = []
    pending: list[str] = []
    if spec["judicial_status"] == "high_risk":
        findings.extend(
            [
                {
                    "finding_id": "finding-dishonest",
                    "subject_id": subject_id,
                    "domain": "judicial",
                    "claim": "企业存在失信被执行人记录",
                    "value": "全部未履行",
                    "risk_class": "admission",
                    "severity": "critical",
                    "status": "accepted",
                    "evidence_ids": ["ev-judicial-dishonest-1"],
                },
                {
                    "finding_id": "finding-consumption-restriction",
                    "subject_id": subject_id,
                    "domain": "judicial",
                    "claim": "企业存在限制高消费记录",
                    "value": True,
                    "risk_class": "admission",
                    "severity": "critical",
                    "status": "accepted",
                    "evidence_ids": ["ev-judicial-restriction-1"],
                },
                {
                    "finding_id": "finding-execution",
                    "subject_id": subject_id,
                    "domain": "judicial",
                    "claim": "企业存在未结被执行记录",
                    "value": {"amount_cny": 6800000},
                    "risk_class": "attention",
                    "severity": "high",
                    "status": "accepted",
                    "evidence_ids": ["ev-judicial-execution-1"],
                },
            ]
        )
        rule_hits = [
            {
                "rule_id": "admission.dishonest_subject",
                "finding_id": "finding-dishonest",
                "points": 80,
            },
            {
                "rule_id": "admission.consumption_restriction",
                "finding_id": "finding-consumption-restriction",
                "points": 80,
            },
            {
                "rule_id": "attention.unresolved_execution",
                "finding_id": "finding-execution",
                "points": 20,
            },
        ]
    elif spec["operations_status"] == "abnormal":
        findings.extend(
            [
                {
                    "finding_id": "finding-operation-abnormal",
                    "subject_id": subject_id,
                    "domain": "operations",
                    "claim": "企业被列入经营异常名录且尚未移出",
                    "value": "未按期公示年度报告",
                    "risk_class": "attention",
                    "severity": "high",
                    "status": "accepted",
                    "evidence_ids": ["ev-operations-abnormal-1"],
                },
                {
                    "finding_id": "finding-administrative-penalty",
                    "subject_id": subject_id,
                    "domain": "operations",
                    "claim": "企业存在行政处罚记录",
                    "value": "产品标识不规范",
                    "risk_class": "attention",
                    "severity": "medium",
                    "status": "accepted",
                    "evidence_ids": ["ev-operations-penalty-1"],
                },
                {
                    "finding_id": "finding-profit-decline",
                    "subject_id": subject_id,
                    "domain": "operations",
                    "claim": "近三年净利润持续下降并转亏",
                    "value": spec["profit"],
                    "risk_class": "attention",
                    "severity": "medium",
                    "status": "accepted",
                    "evidence_ids": ["ev-operations-financial-series"],
                },
            ]
        )
        rule_hits = [
            {
                "rule_id": "attention.operation_abnormal",
                "finding_id": "finding-operation-abnormal",
                "points": 25,
            },
            {
                "rule_id": "attention.administrative_penalty",
                "finding_id": "finding-administrative-penalty",
                "points": 10,
            },
            {
                "rule_id": "attention.profit_decline",
                "finding_id": "finding-profit-decline",
                "points": 10,
            },
        ]
    elif spec["operations_status"] == "conflict":
        findings.append(
            {
                "finding_id": "finding-employee-conflict",
                "subject_id": subject_id,
                "domain": "operations",
                "claim": "员工人数在结构化年报与补充材料中不一致",
                "value": {
                    "structured": spec["employee_count"],
                    "document": spec["notes_employee_count"],
                },
                "risk_class": "attention",
                "severity": "medium",
                "status": "unconfirmed",
                "evidence_ids": ["ev-operations-employees", "ev-corpus-employees"],
            }
        )
        rule_hits = [
            {
                "rule_id": "attention.unresolved_conflict",
                "finding_id": "finding-employee-conflict",
                "points": 20,
            }
        ]
        pending = ["conflict.employee_count"]
    decision = {
        "band": spec["decision"],
        "score": spec["score"],
        "rule_version": "v1",
        "rule_hits": rule_hits,
        "pending_review_items": pending,
        "as_of_date": AS_OF_DATE,
    }
    return findings, decision


def write_scenario(spec: dict[str, Any]) -> None:
    scenario_dir = ROOT / spec["id"]
    (scenario_dir / "corpus").mkdir(parents=True, exist_ok=True)
    (scenario_dir / "expected").mkdir(parents=True, exist_ok=True)
    findings, decision = build_expected(spec)
    payloads = {
        "company.json": json_bytes(build_company(spec)),
        "governance.json": json_bytes(build_governance(spec)),
        "judicial.json": json_bytes(build_judicial(spec)),
        "operations.json": json_bytes(build_operations(spec)),
        "peers.json": json_bytes(build_peers(spec)),
        "corpus/operating-notes.md": (
            f"# {spec['name']} 经营调研补充材料\n\n"
            f"数据时点：{AS_OF_DATE}。\n\n"
            f"访谈记录显示，公司当期在岗人员约 {spec['notes_employee_count']} 人。"
            "主营业务与工商登记范围一致，报告使用时需与结构化数据交叉核验。\n"
        ).encode(),
        "expected/findings.json": json_bytes(findings),
        "expected/decision.json": json_bytes(decision),
    }
    for path, content in payloads.items():
        output_path = scenario_dir / path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)

    files = []
    for path in sorted(payloads):
        files.append(
            {
                "path": path,
                "sha256": hashlib.sha256(payloads[path]).hexdigest(),
                "role": "expected" if path.startswith("expected/") else "runtime",
            }
        )
    manifest = {
        "schema_version": 1,
        "scenario_id": spec["id"],
        "enterprise_key": {
            "company_name": spec["name"],
            "unified_social_credit_code": spec["credit_code"],
            "aliases": [spec["alias"]],
        },
        "version": "v1.0.0",
        "as_of_date": AS_OF_DATE,
        "files": files,
    }
    (scenario_dir / "manifest.json").write_bytes(json_bytes(manifest))


def main() -> None:
    for scenario in SCENARIOS:
        write_scenario(scenario)


if __name__ == "__main__":
    main()
