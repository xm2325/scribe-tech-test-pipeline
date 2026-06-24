# SCRIBE Herbarium Data Extraction Pipeline - Running Instructions

This README explains how to run the code pipeline for the SCRIBE Data Scientist technical test.

The repository has two levels:

1. **Default local pipeline**: JSON metadata extraction, geographic enrichment, GBIF taxonomy enrichment, and HTML coverage reports. This is the main reproducible route and does not require GPU access.
2. **Optional CSF3 vision/OCR workflow**: YOLOE-26 red/purple crop-region proposal, Qwen OCR-text-to-field extraction, and OCR candidate review integration. This is included as a documented optional workflow because it requires GPU resources and local model files.

OCR/LLM outputs are treated as **review candidates**, not final ground truth. Existing JSON, GBIF, or geographic values are not overwritten.

---

## 1. Repository layout

```text
scribe-tech-test-pipeline/
├── configs/
├── scripts/
│   ├── run_pipeline.py
│   └── add_ocr_candidates.py
├── src/scribe_techtest/
│   ├── pipeline.py
│   ├── download.py
│   ├── json_extract.py
│   ├── gbif.py
│   ├── geo.py
│   ├── report.py
│   ├── ocr_candidates.py
│   └── workbook.py
├── examples/
│   ├── ocr_candidates/
│   └── region_detection/
├── extras/csf3_vision_ocr/
│   ├── main500_yoloe26_redpurple_v4/
│   ├── new250_yoloe26_redpurple_v4/
│   └── qwen_field_extraction/
└── tests/
```

Large crops, full overlays, model caches, Slurm logs, and self-contained HTML galleries are intentionally excluded from GitHub.

---

## 2. Installation

Use Python 3.10 or newer.

```bash
git clone https://github.com/xm2325/scribe-tech-test-pipeline.git
cd scribe-tech-test-pipeline

python -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

---

## 3. Input workbook

Set the path to the SCRIBE workbook:

```bash
WORKBOOK=/path/to/techtest_herbariumdata.xlsx
```

The workbook should contain the herbarium sheet records, image links, and JSON links.

---

## 4. Run the default local pipeline

This is the main reproducible route.

```bash
python scripts/run_pipeline.py \
  --workbook "${WORKBOOK}" \
  --output outputs/full_run \
  --allow-partial-downloads
```

Quick 50-record test:

```bash
python scripts/run_pipeline.py \
  --workbook "${WORKBOOK}" \
  --output outputs/debug50 \
  --limit 50 \
  --allow-partial-downloads
```

Metadata-only run without downloading images:

```bash
python scripts/run_pipeline.py \
  --workbook "${WORKBOOK}" \
  --output outputs/metadata_only \
  --skip-images \
  --allow-partial-downloads
```

Run without GBIF enrichment:

```bash
python scripts/run_pipeline.py \
  --workbook "${WORKBOOK}" \
  --output outputs/no_gbif \
  --skip-gbif \
  --allow-partial-downloads
```

---

## 5. Default outputs

Typical outputs:

```text
outputs/full_run/
├── downloads/
│   ├── manifest.csv
│   └── download_summary.json
├── processed/
│   ├── json_filled_750.csv
│   ├── gbif_enriched_750.csv
│   ├── field_coverage.csv
│   ├── gbif_enrichment_audit.csv
│   └── geographic_enrichment_audit.csv
└── reports/
    ├── json_filled_summary.html
    └── gbif_enrichment_summary.html
```

Important files:

- `json_filled_750.csv`: records filled from each record's `jsonURL`.
- `gbif_enriched_750.csv`: JSON-filled records with additional GBIF taxonomy and geographic enrichment.
- `field_coverage.csv`: coverage summary for target feature columns.
- `gbif_enrichment_summary.html`: visual review report with image links, source labels, and missing fields.

---

## 6. Target feature columns

The pipeline targets 32 feature columns:

```text
typeStatus
scientificName
canonicalName
phylum
order
family
genus
class
dateIdentified
verbatimEventDate
eventDate
year
month
day
verbatimLocality
locality
continent
countryCode
country
verbatimCoordinates
decimalLatitude
decimalLongitude
habitat
verbatimElevation
elevation
identifiedBy
verbatimRecordedBy
recordedBy
collectionID
institutionCode
fieldNotes
organismRemarks
```

---

## 7. Optional OCR candidate integration

If a Qwen field-candidate TSV is available, integrate it into the baseline output:

```bash
python scripts/add_ocr_candidates.py \
  --base outputs/full_run/processed/gbif_enriched_750.csv \
  --ocr-candidates examples/ocr_candidates/qwen3_14b_predictions_current_0_16.tsv \
  --output-candidates outputs/full_run/processed/ocr_candidates.csv \
  --output-review-matrix outputs/full_run/processed/ocr_review_matrix.csv \
  --output-html outputs/full_run/reports/ocr_candidate_review.html
```

This step does **not** overwrite base values.

Decision rule:

```text
base value exists       -> keep JSON/GBIF/geographic value
base field is blank     -> show OCR/LLM candidate if available
no candidate available  -> remain blank
```

Expected OCR candidate input columns:

```text
record_key
field
value
confidence
evidence
status
merged_source
chunk_id
```

The output HTML shows specimen image or image link, Open JPEG / Open JSON links, existing structured values, OCR candidates for blank fields, confidence, evidence text, and remaining blank fields.

---

## 8. Optional CSF3 vision/OCR workflow

This workflow is provided for transparency and reproducibility of the GPU image/OCR experiment. It is not required for the default local run.

### 8.1 YOLOE-26 red/purple crop-region proposal

Conceptual flow:

```text
sheet image
  -> YOLOE-26 raw candidate boxes
  -> red parent/context regions
  -> purple auxiliary/focused regions
  -> final OCR crop queue
```

Included directories:

```text
extras/csf3_vision_ocr/main500_yoloe26_redpurple_v4/
extras/csf3_vision_ocr/new250_yoloe26_redpurple_v4/
```

Compact outputs:

```text
examples/region_detection/redpurple_final_ocr_queue_summary.tsv
examples/region_detection/redpurple_per_record_file_summary.tsv
examples/region_detection/per_record_examples/
```

Red/purple meaning:

- **red / parent regions**: larger context crops likely to contain complete specimen labels.
- **purple / auxiliary regions**: smaller focused crops intended to rescue details missed by larger crops.

### 8.2 Qwen OCR-text-to-32-feature extraction

Qwen field-extraction scripts:

```text
extras/csf3_vision_ocr/qwen_field_extraction/
```

Key files:

```text
qwen3_croptext_to_32features_client.py
run_qwen3_croptext_to_32features_array.sbatch
merge_qwen_field_predictions.py
make_ocr_candidate_review_html.py
```

Expected CSF3 environment variables:

```bash
export SCRIBE_QWEN_EXTRACT=/path/to/qwen3_14b_ocr_to_37column_jsonfilled750_v1
export SCRIBE_MAIN500_MANIFEST=/path/to/main500_local_images.tsv
export SCRIBE_NEW250_MANIFEST=/path/to/new250_local_images.tsv
export SCRIBE_NHM_TOP=/path/to/nhm
export SCRIBE_MAMBA=/path/to/micromamba
```

The field-extraction chunks contain OCR text and `blank_fields_json`, so Qwen is only asked to propose values for fields still blank after JSON/GBIF/geographic enrichment.

---

## 9. Evaluation and quality checks

The pipeline supports:

1. **Coverage accounting** via `field_coverage.csv`.
2. **Reference comparison** using the 500 pre-extracted rows, while treating them as imperfect reference data.
3. **Source-labelled HTML review** showing JSON, GBIF, geographic rule, OCR candidate, or blank.
4. **OCR uncertainty handling** by preserving confidence and evidence text.
5. **Small-sample visual review** through `examples/region_detection/per_record_examples/`.

---

## 10. Recommended submission outputs

Recommended files to provide:

```text
outputs/full_run/processed/gbif_enriched_750.csv
outputs/full_run/processed/field_coverage.csv
outputs/full_run/reports/gbif_enrichment_summary.html
outputs/full_run/reports/ocr_candidate_review.html   # if OCR candidate step is run
README.md
docs/vision_ocr_workflow.md
```

A compact submission package can include:

```text
structured dataset CSV
one-page overview
code repository link
running-instructions README
small HTML report or screenshots
```

---

## 11. Troubleshooting

Allow partial downloads if some image URLs fail:

```bash
python scripts/run_pipeline.py \
  --workbook "${WORKBOOK}" \
  --output outputs/full_run \
  --allow-partial-downloads
```

Skip GBIF enrichment if needed:

```bash
python scripts/run_pipeline.py \
  --workbook "${WORKBOOK}" \
  --output outputs/no_gbif \
  --skip-gbif \
  --allow-partial-downloads
```

The CSF3 scripts keep default paths for the original run, but can be overridden using the `SCRIBE_*` environment variables.

---

## 12. Reproducibility note

The default JSON/GBIF/geographic pipeline is the primary reproducible workflow. The CSF3 vision/OCR workflow documents the GPU-based experiment and provides compact examples and scripts, while excluding heavy intermediate images and model artifacts from GitHub.
