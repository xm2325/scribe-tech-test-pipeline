from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .constants import TARGET_FIELDS
from .download import DownloadConfig, download_all
from .gbif import GbifConfig, enrich_taxonomy
from .geo import enrich_geography
from .json_extract import extract_fields_from_file
from .report import field_coverage, render_gallery_report
from .utils import clean, is_blank
from .workbook import load_records, write_tables


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_pipeline(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SCRIBE tech test baseline pipeline.")
    parser.add_argument("--workbook", required=True, help="Path to techtest_herbariumdata.xlsx")
    parser.add_argument("--output", default="outputs/full_run", help="Output directory")
    parser.add_argument("--config", default="configs/default.yaml", help="Pipeline config YAML")
    parser.add_argument("--limit", type=int, default=None, help="Optional first-N row limit for smoke tests")
    parser.add_argument("--skip-images", action="store_true", help="Download JSON only")
    parser.add_argument(
        "--allow-partial-downloads",
        action="store_true",
        help="Continue even when some downloads fail; failures remain in the manifest.",
    )
    parser.add_argument("--skip-gbif", action="store_true", help="Skip GBIF taxonomy enrichment")
    parser.add_argument(
        "--overwrite-taxonomy",
        action="store_true",
        help="Allow GBIF to overwrite nonblank JSON taxonomy values.",
    )
    return parser


def run_pipeline(args: argparse.Namespace) -> dict[str, Path]:
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)

    records = load_records(args.workbook, limit=args.limit)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records.to_csv(raw_dir / "records_750_input_index.csv", index=False)

    download_config = DownloadConfig(**config.get("download", {}))
    manifest = download_all(
        records,
        output_dir,
        download_config,
        skip_images=args.skip_images,
        allow_partial=args.allow_partial_downloads,
    )
    write_download_summary(records, manifest, output_dir, skip_images=args.skip_images)

    json_filled = build_json_filled(records, manifest, output_dir)
    json_filled = enrich_geography(
        json_filled,
        mapping_path=config.get("geo", {}).get(
            "country_continent_path",
            "configs/country_continent_from_unsd_m49.csv",
        ),
        output_dir=output_dir,
    )
    processed_dir = output_dir / "processed"
    write_tables(json_filled, processed_dir, "json_filled_750")
    write_tables(json_filled[json_filled["source_sheet"].eq("new_data")], processed_dir, "json_filled_new_data")
    write_tables(json_filled[json_filled["source_sheet"].eq("main_data")], processed_dir, "json_filled_main_data")

    json_coverage = field_coverage(json_filled)
    json_coverage.to_csv(processed_dir / "json_field_coverage.csv", index=False)
    reports_dir = output_dir / "reports"
    render_gallery_report(
        json_filled,
        json_coverage,
        reports_dir / "json_coverage_summary.html",
        title="SCRIBE jsonURL coverage summary",
        subtitle=(
            "All 750 records are filled from downloaded jsonURL files only. "
            "Blank fields are highlighted for later OCR/image extraction review."
        ),
        stage_label="jsonURL feature-cell",
        max_records=int(config.get("report", {}).get("max_records", 750)),
    )

    gbif_output = json_filled
    if not args.skip_gbif:
        gbif_config = GbifConfig(**{**config.get("gbif", {}), "enabled": True})
        gbif_output = enrich_taxonomy(
            json_filled,
            output_dir,
            gbif_config,
            overwrite_taxonomy=args.overwrite_taxonomy,
        )
        write_tables(gbif_output, processed_dir, "gbif_enriched_750")
        write_tables(gbif_output[gbif_output["source_sheet"].eq("new_data")], processed_dir, "gbif_enriched_new_data")
        gbif_coverage = field_coverage(gbif_output)
        gbif_coverage.to_csv(processed_dir / "gbif_field_coverage.csv", index=False)
        render_gallery_report(
            gbif_output,
            gbif_coverage,
            reports_dir / "gbif_enrichment_summary.html",
            title="SCRIBE jsonURL plus GBIF taxonomy summary",
            subtitle=(
                "This report starts from jsonURL-filled records and fills missing "
                "canonicalName/phylum/class/order/family/genus from GBIF Species API matches."
            ),
            stage_label="JSON plus GBIF feature-cell",
            max_records=int(config.get("report", {}).get("max_records", 750)),
        )

    return {
        "manifest": output_dir / "downloads" / "manifest.csv",
        "json_report": reports_dir / "json_coverage_summary.html",
        "gbif_report": reports_dir / "gbif_enrichment_summary.html",
        "json_csv": processed_dir / "json_filled_750.csv",
        "gbif_csv": processed_dir / "gbif_enriched_750.csv",
    }


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return value


def build_json_filled(
    records: pd.DataFrame,
    manifest: pd.DataFrame,
    output_dir: Path,
) -> pd.DataFrame:
    json_paths = {
        clean(row.record_key): Path(row.local_path)
        for row in manifest[manifest["url_type"].eq("json")].itertuples(index=False)
        if clean(row.status) in {"ok", "cached"}
    }
    image_paths = {
        clean(row.record_key): Path(row.local_path)
        for row in manifest[manifest["url_type"].eq("image")].itertuples(index=False)
        if clean(row.status) in {"ok", "cached"}
    }
    rows: list[dict[str, Any]] = []
    for _, source in records.iterrows():
        record_key = clean(source["record_key"])
        row = {
            "record_key": record_key,
            "source_sheet": clean(source["source_sheet"]),
            "source_row": clean(source["source_row"]),
            "index": clean(source["index"]),
            "DOI": clean(source["DOI"]),
            "jpegURL": clean(source["jpegURL"]),
            "jsonURL": clean(source["jsonURL"]),
            "occurrenceID": clean(source["occurrenceID"]),
        }
        for field in TARGET_FIELDS:
            row[field] = ""
            row[f"source_{field}"] = ""
            row[f"json_key_{field}"] = ""
        json_path = json_paths.get(record_key)
        if json_path and json_path.exists():
            try:
                fields, keys = extract_fields_from_file(json_path)
            except Exception as exc:
                row["json_parse_error"] = f"{type(exc).__name__}: {exc}"
                fields, keys = {}, {}
            else:
                row["json_parse_error"] = ""
            for field in TARGET_FIELDS:
                value = clean(fields.get(field, ""))
                if value:
                    row[field] = value
                    row[f"source_{field}"] = "json"
                    row[f"json_key_{field}"] = clean(keys.get(field, ""))
            row["local_json_path"] = str(json_path)
            row["local_json_rel"] = _relative(json_path, output_dir / "reports")
        else:
            row["json_parse_error"] = "json_not_downloaded"
            row["local_json_path"] = ""
            row["local_json_rel"] = ""
        image_path = image_paths.get(record_key)
        row["local_image_path"] = str(image_path) if image_path else ""
        row["local_image_rel"] = _relative(image_path, output_dir / "reports") if image_path else ""
        rows.append(row)
    out = pd.DataFrame(rows)
    return out


def _relative(path: Path, start: Path) -> str:
    return os.path.relpath(path.resolve(), start.resolve())


def write_download_summary(
    records: pd.DataFrame,
    manifest: pd.DataFrame,
    output_dir: Path,
    *,
    skip_images: bool,
) -> None:
    ok_statuses = {"ok", "cached"}
    summary: dict[str, Any] = {
        "records": len(records),
        "skip_images": bool(skip_images),
        "json_expected": len(records),
        "json_ok": int(
            manifest[
                manifest["url_type"].eq("json") & manifest["status"].isin(ok_statuses)
            ].shape[0]
        ),
        "json_errors": int(
            manifest[
                manifest["url_type"].eq("json") & manifest["status"].eq("error")
            ].shape[0]
        ),
        "image_expected": 0 if skip_images else len(records),
        "image_ok": int(
            manifest[
                manifest["url_type"].eq("image") & manifest["status"].isin(ok_statuses)
            ].shape[0]
        ),
        "image_errors": int(
            manifest[
                manifest["url_type"].eq("image") & manifest["status"].eq("error")
            ].shape[0]
        ),
    }
    path = output_dir / "downloads" / "download_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
