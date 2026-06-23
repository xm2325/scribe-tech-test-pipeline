from __future__ import annotations

from pathlib import Path

import pandas as pd

from .constants import METADATA_COLUMNS, TARGET_FIELDS
from .utils import truthy_text


def load_records(workbook_path: str | Path, limit: int | None = None) -> pd.DataFrame:
    workbook_path = Path(workbook_path)
    sheets = pd.read_excel(
        workbook_path,
        sheet_name=None,
        dtype=str,
        keep_default_na=False,
        engine="openpyxl",
    )
    missing = {"main_data", "new_data"} - set(sheets)
    if missing:
        raise ValueError(f"Workbook is missing required sheets: {sorted(missing)}")

    frames = [
        _normalise_sheet(sheets["main_data"], "main_data"),
        _normalise_sheet(sheets["new_data"], "new_data"),
    ]
    combined = pd.concat(frames, ignore_index=True)
    if limit is not None:
        combined = combined.head(int(limit)).copy()
    combined["record_key"] = combined.apply(
        lambda r: f"{r['source_sheet']}_{int(r['index']):04d}", axis=1
    )
    return combined


def _normalise_sheet(df: pd.DataFrame, source_sheet: str) -> pd.DataFrame:
    out = df.copy()
    if "Index" in out.columns and "index" not in out.columns:
        out = out.rename(columns={"Index": "index"})
    if "index" not in out.columns:
        out.insert(0, "index", range(1, len(out) + 1))

    for column in METADATA_COLUMNS + TARGET_FIELDS:
        if column not in out.columns:
            out[column] = ""
    out["source_sheet"] = source_sheet
    out["source_row"] = [i + 2 for i in range(len(out))]

    keep = (
        ["source_sheet", "source_row", "record_key"]
        + METADATA_COLUMNS
        + TARGET_FIELDS
    )
    out["index"] = out["index"].apply(lambda v: truthy_text(v) or "0")
    for column in keep:
        if column not in out.columns:
            out[column] = ""
    return out[keep]


def write_tables(df: pd.DataFrame, output_base: Path, stem: str) -> None:
    output_base.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_base / f"{stem}.csv", index=False)
    df.to_excel(output_base / f"{stem}.xlsx", index=False)
