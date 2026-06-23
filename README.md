# SCRIBE Tech Test Pipeline

This repository is a reproducible pipeline for the SCRIBE herbarium tech test.
It starts from the supplied workbook, downloads the linked JSON and image files
with retry and rate-limit handling, fills the 32 target feature columns from
`jsonURL`, summarizes JSON coverage in an HTML review page, then optionally uses
the GBIF Species API to fill missing taxonomy fields.

The 500 `main_data` rows are treated as reference/exploration data. The pipeline
does not need them to fill the 250 `new_data` rows, and it does not make external
LLM calls.

## What This Pipeline Produces

For a full run, outputs are written under `outputs/<run_name>/`:

- `downloads/manifest.csv`: every JSON/image download attempt, including local
  filename, status code, byte count, retry count, and error text.
- `processed/json_filled_750.csv`: all 750 rows with the 32 feature fields filled
  only from downloaded JSON.
- `processed/json_filled_new_data.csv`: the 250 target rows filled from JSON.
- `processed/json_field_coverage.csv`: JSON field coverage by all/main/new data.
- `reports/json_coverage_summary.html`: image-left, metadata-right review page.
- `processed/gbif_enriched_750.csv`: JSON-filled rows after GBIF taxonomy fill.
- `processed/gbif_field_coverage.csv`: post-GBIF coverage and source counts.
- `reports/gbif_enrichment_summary.html`: second review page after taxonomy fill.

## Quick Start

```bash
python -m pip install -e ".[dev]"
python scripts/run_pipeline.py \
  --workbook /Users/user/Downloads/techtest_herbariumdata.xlsx \
  --output outputs/full_run
```

The default run downloads both JSON and JPEG files. For a fast smoke test:

```bash
python scripts/run_pipeline.py \
  --workbook /Users/user/Downloads/techtest_herbariumdata.xlsx \
  --output outputs/smoke \
  --limit 10 \
  --skip-images \
  --allow-partial-downloads
```

## Reproducibility Notes

- Downloads are cached and resumable. Re-running the pipeline reuses existing
  non-empty files.
- URLs with spaces are requested with an encoded path, while local filenames are
  sanitized and recorded in the manifest.
- HTTP 429 responses respect `Retry-After` when present and otherwise use
  exponential backoff.
- GBIF matches are cached in `cache/gbif_species_match.json`.
- JSON and GBIF are provenance-labelled with `source_<field>` columns. Existing
  JSON values are not overwritten by GBIF unless `--overwrite-taxonomy` is used.

## Field Strategy

The 32 target columns are the fields from `typeStatus` through
`organismRemarks`, matching the columns missing from `new_data`. The first stage
uses Darwin Core / Dublin Core keys from each `jsonURL`. The second stage uses
`scientificName` to query GBIF and fill missing `canonicalName`, `phylum`,
`class`, `order`, `family`, and `genus`.

This is deliberately separated from OCR. JSON/GBIF outputs are useful baselines
and audit references; OCR/image extraction should be evaluated as a later stage
against held-out reference data.

## Tests

```bash
pytest
```
