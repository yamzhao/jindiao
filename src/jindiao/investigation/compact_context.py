"""Lossless pools/tables for the private investigator model view, not audit storage."""

from __future__ import annotations

import copy
import json
from typing import cast

from pydantic import JsonValue


def _key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _table(records: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    columns = list(dict.fromkeys(key for record in records for key in record))
    missing: list[JsonValue] = [
        [i, j]
        for i, record in enumerate(records)
        for j, key in enumerate(columns)
        if key not in record
    ]
    table: dict[str, JsonValue] = {
        "columns": cast(JsonValue, columns),
        "rows": [[record.get(key) for key in columns] for record in records],
    }
    if missing:
        table["missing"] = missing
    return table


def compact_assigned_context(context: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Factor repeated facts and citation metadata without discarding any source value.

    All representation markers live in generated wrapper objects; literal source
    keys such as records_table or $ref cannot be confused with these wrappers.
    """
    packed = copy.deepcopy(context)
    fact_sets: dict[str, JsonValue] = {}
    fact_ids: dict[str, str] = {}
    evidence_rows: list[JsonValue] = []
    metadata: list[dict[str, JsonValue]] = []
    metadata_ids: dict[str, int] = {}
    seen_evidence: dict[str, str] = {}
    modules = cast(list[dict[str, JsonValue]], packed["submodules"])
    for module in modules:
        facts = cast(dict[str, JsonValue], module.pop("facts"))
        fingerprint = _key(facts)
        if fingerprint not in fact_ids:
            fact_id = f"f{len(fact_ids)}"
            fact_ids[fingerprint] = fact_id
            entry: dict[str, JsonValue] = {"fields": facts}
            records = facts.get("records")
            if isinstance(records, list) and records and all(isinstance(r, dict) for r in records):
                table = _table(cast(list[dict[str, JsonValue]], records))
                if len(_key(table)) < len(_key(records)):
                    entry["fields"] = {
                        key: value for key, value in facts.items() if key != "records"
                    }
                    entry["records_table"] = table
            fact_sets[fact_id] = entry
        module["facts_ref"] = fact_ids[fingerprint]
        refs: list[str] = []
        for item in cast(list[dict[str, JsonValue]], module.pop("evidence_items")):
            identity = cast(str, item["evidence_id"])
            refs.append(identity)
            fingerprint = _key(item)
            if identity in seen_evidence:
                if seen_evidence[identity] != fingerprint:
                    raise ValueError("one Evidence ID cannot have conflicting metadata")
                continue
            seen_evidence[identity] = fingerprint
            meta = {key: value for key, value in item.items() if key != "evidence_id"}
            meta_key = _key(meta)
            if meta_key not in metadata_ids:
                metadata_ids[meta_key] = len(metadata)
                metadata.append(meta)
            evidence_rows.append([identity, metadata_ids[meta_key]])
        module["evidence_ref_ids"] = cast(JsonValue, refs)
    packed.update(
        format="snapshot-context-v2",
        fact_sets=fact_sets,
        evidence_table={"columns": ["evidence_id", "metadata_index"], "rows": evidence_rows},
        evidence_metadata_table=_table(metadata),
    )
    return packed
