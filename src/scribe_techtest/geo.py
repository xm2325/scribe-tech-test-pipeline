from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .utils import clean, is_blank


DEFAULT_MAPPING_PATH = "configs/country_continent_from_unsd_m49.csv"
INVALID_OR_UNKNOWN_CODES = {"", "NONE", "NAN", "NULL", "ZZ", "XZ"}


def enrich_geography(
    df: pd.DataFrame,
    *,
    mapping_path: str | Path = DEFAULT_MAPPING_PATH,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Fill missing Darwin Core-style continent from official UNSD M49 mapping.

    This only fills blank continent values. It does not overwrite JSON/provided
    continent values. The source is recorded as `unsd_m49_policy`, because the
    continent value is derived from official UNSD M49 fields plus a documented
    Darwin Core-style continent policy.
    """
    out = df.copy()
    mapping = load_unsd_continent_mapping(mapping_path)

    audit_rows: list[dict[str, Any]] = []

    for idx, row in out.iterrows():
        record_key = clean(row.get("record_key", ""))
        country_code = clean(row.get("countryCode", "")).upper()
        country = clean(row.get("country", ""))
        current_continent = clean(row.get("continent", ""))

        if current_continent:
            audit_rows.append(
                {
                    "record_key": record_key,
                    "countryCode": country_code,
                    "country": country,
                    "continent_before": current_continent,
                    "continent_after": current_continent,
                    "decision": "kept_existing_continent",
                    "source_continent": clean(row.get("source_continent", "")) or "existing",
                    "countryName_unsd": "",
                    "unsd_regionName": "",
                    "unsd_subRegionName": "",
                    "unsd_intermediateRegionName": "",
                    "m49Code": "",
                    "isoAlpha3": "",
                    "continent_policy_rule": "",
                    "review_note": "",
                }
            )
            continue

        if country_code in INVALID_OR_UNKNOWN_CODES:
            audit_rows.append(
                {
                    "record_key": record_key,
                    "countryCode": country_code,
                    "country": country,
                    "continent_before": "",
                    "continent_after": "",
                    "decision": "review_invalid_or_unknown_countryCode",
                    "source_continent": "",
                    "countryName_unsd": "",
                    "unsd_regionName": "",
                    "unsd_subRegionName": "",
                    "unsd_intermediateRegionName": "",
                    "m49Code": "",
                    "isoAlpha3": "",
                    "continent_policy_rule": "",
                    "review_note": "Blank/none/ZZ/XZ countryCode is not mapped automatically.",
                }
            )
            continue

        hit = mapping.get(country_code)
        if not hit:
            audit_rows.append(
                {
                    "record_key": record_key,
                    "countryCode": country_code,
                    "country": country,
                    "continent_before": "",
                    "continent_after": "",
                    "decision": "review_no_official_UNSD_M49_match",
                    "source_continent": "",
                    "countryName_unsd": "",
                    "unsd_regionName": "",
                    "unsd_subRegionName": "",
                    "unsd_intermediateRegionName": "",
                    "m49Code": "",
                    "isoAlpha3": "",
                    "continent_policy_rule": "",
                    "review_note": "countryCode was not found in locked UNSD M49 mapping.",
                }
            )
            continue

        continent = clean(hit.get("continent", ""))
        if not continent:
            audit_rows.append(
                {
                    "record_key": record_key,
                    "countryCode": country_code,
                    "country": country,
                    "continent_before": "",
                    "continent_after": "",
                    "decision": "review_no_dwc_continent_policy",
                    "source_continent": "",
                    "countryName_unsd": clean(hit.get("countryName_unsd", "")),
                    "unsd_regionName": clean(hit.get("unsd_regionName", "")),
                    "unsd_subRegionName": clean(hit.get("unsd_subRegionName", "")),
                    "unsd_intermediateRegionName": clean(hit.get("unsd_intermediateRegionName", "")),
                    "m49Code": clean(hit.get("m49Code", "")),
                    "isoAlpha3": clean(hit.get("isoAlpha3", "")),
                    "continent_policy_rule": clean(hit.get("continent_policy_rule", "")),
                    "review_note": "UNSD mapping exists, but no DwC continent policy was applied.",
                }
            )
            continue

        out.at[idx, "continent"] = continent
        out.at[idx, "source_continent"] = "unsd_m49"
        out.at[idx, "json_key_continent"] = f"derived:countryCode={country_code};source=UNSD_M49;policy=DwC_continent"

        out.at[idx, "unsd_countryName"] = clean(hit.get("countryName_unsd", ""))
        out.at[idx, "unsd_regionName"] = clean(hit.get("unsd_regionName", ""))
        out.at[idx, "unsd_subRegionName"] = clean(hit.get("unsd_subRegionName", ""))
        out.at[idx, "unsd_intermediateRegionName"] = clean(hit.get("unsd_intermediateRegionName", ""))
        out.at[idx, "unsd_m49Code"] = clean(hit.get("m49Code", ""))
        out.at[idx, "unsd_isoAlpha3"] = clean(hit.get("isoAlpha3", ""))
        out.at[idx, "continent_policy_rule"] = clean(hit.get("continent_policy_rule", ""))

        audit_rows.append(
            {
                "record_key": record_key,
                "countryCode": country_code,
                "country": country,
                "continent_before": "",
                "continent_after": continent,
                "decision": "filled_from_official_UNSD_M49_policy",
                "source_continent": "unsd_m49",
                "countryName_unsd": clean(hit.get("countryName_unsd", "")),
                "unsd_regionName": clean(hit.get("unsd_regionName", "")),
                "unsd_subRegionName": clean(hit.get("unsd_subRegionName", "")),
                "unsd_intermediateRegionName": clean(hit.get("unsd_intermediateRegionName", "")),
                "m49Code": clean(hit.get("m49Code", "")),
                "isoAlpha3": clean(hit.get("isoAlpha3", "")),
                "continent_policy_rule": clean(hit.get("continent_policy_rule", "")),
                "review_note": "",
            }
        )

    if output_dir is not None:
        processed_dir = Path(output_dir) / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(audit_rows).to_csv(
            processed_dir / "geo_continent_enrichment_audit.csv",
            index=False,
        )

    return out


def load_unsd_continent_mapping(path: str | Path) -> dict[str, dict[str, str]]:
    path = Path(path)
    if not path.exists():
        return {}

    table = pd.read_csv(path, dtype=str).fillna("")
    mapping: dict[str, dict[str, str]] = {}

    for _, row in table.iterrows():
        code = clean(row.get("countryCode", "")).upper()
        if not code:
            continue
        mapping[code] = {str(k): clean(v) for k, v in row.items()}

    return mapping
