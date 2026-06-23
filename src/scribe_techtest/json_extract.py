from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .constants import JSON_FIELD_KEYS, TARGET_FIELDS
from .utils import clean


def extract_fields_from_file(path: str | Path) -> tuple[dict[str, str], dict[str, str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return extract_fields_from_json(data)


def extract_fields_from_json(data: Any) -> tuple[dict[str, str], dict[str, str]]:
    occurrence = select_occurrence_object(data)
    values_by_key = flatten_values(occurrence)
    fields: dict[str, str] = {}
    evidence_keys: dict[str, str] = {}
    for field in TARGET_FIELDS:
        value = ""
        used_key = ""
        for key in JSON_FIELD_KEYS[field]:
            candidates = values_by_key.get(key, [])
            if candidates:
                value = candidates[0]
                used_key = key
                break
        fields[field] = value
        evidence_keys[field] = used_key
    return fields, evidence_keys


def select_occurrence_object(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("@graph"), list):
        candidates = [item for item in data["@graph"] if isinstance(item, dict)]
        for item in candidates:
            flat = flatten_values(item)
            if flat.get("dwc:occurrenceID") or flat.get("occurrenceID"):
                return item
        for item in candidates:
            flat = flatten_values(item)
            if flat.get("dwc:scientificName") or flat.get("scientificName"):
                return item
        return candidates[0] if candidates else data
    return data


def flatten_values(data: Any) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            if "@value" in obj and len(obj) <= 3:
                return
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    direct = stringify_value(value)
                    if direct:
                        add_value(key, direct)
                    visit(value)
                else:
                    add_value(key, stringify_value(value))
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    def add_value(key: str, value: str) -> None:
        value = clean(value)
        if not value:
            return
        keys = key_aliases(key)
        for alias in keys:
            if value not in values[alias]:
                values[alias].append(value)

    visit(data)
    return dict(values)


def key_aliases(key: str) -> list[str]:
    key = clean(key)
    aliases = [key]
    if ":" in key:
        aliases.append(key.rsplit(":", 1)[-1])
    if "/" in key or "#" in key:
        tail = key.replace("#", "/").rstrip("/").rsplit("/", 1)[-1]
        aliases.append(tail)
    return list(dict.fromkeys([alias for alias in aliases if alias]))


def stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("@value", "value", "label", "name", "@id"):
            text = clean(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        parts = [stringify_value(item) for item in value]
        return "; ".join([part for part in parts if part])
    return clean(value)
