from __future__ import annotations

from pathlib import Path
import pandas as pd


IN = Path("configs/country_region_unsd_m49.csv")
OUT = Path("configs/country_continent_from_unsd_m49.csv")
README = Path("configs/country_continent_from_unsd_m49.README.md")


def clean(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def to_dwc_continent(region: str, subregion: str, intermediate: str) -> tuple[str, str]:
    region = clean(region)
    subregion = clean(subregion)
    intermediate = clean(intermediate)

    if region in {"Africa", "Asia", "Europe", "Oceania", "Antarctica"}:
        return region, f"UNSD Region Name={region}"

    if region == "Americas":
        if intermediate == "South America" or subregion == "South America":
            return "South America", "UNSD Americas + South America -> Darwin Core-style South America"
        if subregion in {"Northern America", "Latin America and the Caribbean"} and intermediate in {"Central America", "Caribbean"}:
            return "North America", f"UNSD Americas + {intermediate} -> Darwin Core-style North America"
        if subregion in {"Northern America", "Central America", "Caribbean"}:
            return "North America", f"UNSD Americas + {subregion} -> Darwin Core-style North America"

    return "", "review_no_dwc_continent_policy"


def main() -> None:
    df = pd.read_csv(IN, dtype=str).fillna("")

    rows = []
    for _, r in df.iterrows():
        continent, rule = to_dwc_continent(
            r.get("unsd_regionName", ""),
            r.get("unsd_subRegionName", ""),
            r.get("unsd_intermediateRegionName", ""),
        )
        status = "auto_from_official_UNSD_M49_policy" if continent else "review"

        rows.append({
            "countryCode": clean(r.get("countryCode", "")),
            "countryName_unsd": clean(r.get("countryName_unsd", "")),
            "continent": continent,
            "continent_policy_rule": rule,
            "mapping_status": status,
            "unsd_regionName": clean(r.get("unsd_regionName", "")),
            "unsd_subRegionName": clean(r.get("unsd_subRegionName", "")),
            "unsd_intermediateRegionName": clean(r.get("unsd_intermediateRegionName", "")),
            "m49Code": clean(r.get("m49Code", "")),
            "isoAlpha3": clean(r.get("isoAlpha3", "")),
            "source": "UNSD_M49_overview_plus_DwC_continent_policy",
            "source_url": clean(r.get("source_url", "")),
            "source_downloaded_utc": clean(r.get("source_downloaded_utc", "")),
        })

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)

    README.write_text(
        """# CountryCode to Darwin Core-style continent mapping

This file is generated from `configs/country_region_unsd_m49.csv`, which is parsed from the official UNSD M49 overview table.

Important distinction:

- `unsd_regionName`, `unsd_subRegionName`, and `unsd_intermediateRegionName` are parsed from the official UNSD M49 overview.
- `continent` is a derived Darwin Core-style continent candidate produced by a documented policy.
- The policy is needed because UNSD uses `Americas`, while Darwin Core examples use `North America` and `South America`.

Policy:

- UNSD Region Name Africa -> Africa
- UNSD Region Name Asia -> Asia
- UNSD Region Name Europe -> Europe
- UNSD Region Name Oceania -> Oceania
- UNSD Region Name Antarctica -> Antarctica
- UNSD Americas + South America -> South America
- UNSD Americas + Northern America / Central America / Caribbean -> North America

Rows with invalid, blank, unknown, or non-standard input countryCode should remain review-only in the enrichment audit.
""",
        encoding="utf-8",
    )

    print("Saved:", OUT)
    print("Saved:", README)
    print("Rows:", len(out))
    print(out.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
