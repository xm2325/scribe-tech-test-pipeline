from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re

import pandas as pd
import requests


UNSD_URL = "https://unstats.un.org/unsd/methodology/m49/overview/"

RAW_HTML = Path("configs/raw/unsd_m49_overview.html")
OFFICIAL_TABLE = Path("configs/unsd_m49_overview_official.csv")
COUNTRY_REGION = Path("configs/country_region_unsd_m49.csv")
README = Path("configs/country_region_unsd_m49.README.md")


def clean(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).replace("\xa0", " ").strip()


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [clean(c) for c in out.columns]
    for c in out.columns:
        out[c] = out[c].map(clean)
    return out


def find_m49_table(html: str) -> pd.DataFrame:
    tables = pd.read_html(StringIO(html))
    candidates = []
    for t in tables:
        t = normalize_columns(t)
        cols = set(t.columns)
        needed = {
            "Region Name",
            "Sub-region Name",
            "Country or Area",
            "ISO-alpha2 Code",
            "ISO-alpha3 Code",
        }
        if needed.issubset(cols):
            candidates.append(t)

    if not candidates:
        raise SystemExit("Could not find UNSD M49 overview table with ISO-alpha2 Code.")

    # The English overview table is normally the first matching table.
    return candidates[0]


def main() -> None:
    RAW_HTML.parent.mkdir(parents=True, exist_ok=True)

    r = requests.get(
        UNSD_URL,
        timeout=60,
        headers={"User-Agent": "scribe-tech-test-pipeline/1.0"},
    )
    r.raise_for_status()
    RAW_HTML.write_text(r.text, encoding="utf-8")

    downloaded_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    table = find_m49_table(r.text)

    keep = [
        "Global Code",
        "Global Name",
        "Region Code",
        "Region Name",
        "Sub-region Code",
        "Sub-region Name",
        "Intermediate Region Code",
        "Intermediate Region Name",
        "Country or Area",
        "M49 Code",
        "ISO-alpha2 Code",
        "ISO-alpha3 Code",
        "Least Developed Countries (LDC)",
        "Land Locked Developing Countries (LLDC)",
        "Small Island Developing States (SIDS)",
    ]

    # Keep only columns that exist, to be robust to minor HTML changes.
    keep = [c for c in keep if c in table.columns]
    table = table[keep].copy()

    for c in table.columns:
        table[c] = table[c].map(clean)

    # Only actual country/area rows with ISO alpha-2 codes.
    table = table[table["ISO-alpha2 Code"].str.strip().ne("")].copy()
    table["source_url"] = UNSD_URL
    table["source_downloaded_utc"] = downloaded_utc

    table.to_csv(OFFICIAL_TABLE, index=False)

    out = pd.DataFrame()
    out["countryCode"] = table["ISO-alpha2 Code"]
    out["countryName_unsd"] = table["Country or Area"]
    out["unsd_regionCode"] = table["Region Code"]
    out["unsd_regionName"] = table["Region Name"]
    out["unsd_subRegionCode"] = table["Sub-region Code"]
    out["unsd_subRegionName"] = table["Sub-region Name"]
    out["unsd_intermediateRegionCode"] = table.get("Intermediate Region Code", "")
    out["unsd_intermediateRegionName"] = table.get("Intermediate Region Name", "")
    out["m49Code"] = table["M49 Code"]
    out["isoAlpha3"] = table["ISO-alpha3 Code"]
    out["source"] = "UNSD_M49_overview"
    out["source_url"] = UNSD_URL
    out["source_downloaded_utc"] = downloaded_utc

    out.to_csv(COUNTRY_REGION, index=False)

    README.write_text(
        f"""# Official UNSD M49 country/region mapping

Generated from the United Nations Statistics Division M49 overview page.

Source URL:
{UNSD_URL}

Downloaded UTC:
{downloaded_utc}

Files:

- `configs/raw/unsd_m49_overview.html`: raw downloaded official HTML snapshot.
- `configs/unsd_m49_overview_official.csv`: parsed official M49 overview table.
- `configs/country_region_unsd_m49.csv`: simplified countryCode -> UNSD region/subregion mapping.

Important interpretation:

- `unsd_regionName` is the official UNSD macro-geographical region.
- This is not always exactly the same as Darwin Core's `continent` examples.
- UNSD uses `Americas`; Darwin Core examples use `North America` and `South America`.
- Therefore this file should be treated as official geography evidence. Any conversion into Darwin Core-style `continent` should be documented as a separate policy step.
""",
        encoding="utf-8",
    )

    print("Downloaded:", UNSD_URL)
    print("Rows:", len(out))
    print("Saved:", OFFICIAL_TABLE)
    print("Saved:", COUNTRY_REGION)
    print("Saved:", README)


if __name__ == "__main__":
    main()
