#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import re
import shutil
from pathlib import Path
import os
from collections import Counter, defaultdict

from PIL import Image, ImageFile

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

EXTRACT = Path(os.environ.get("SCRIBE_QWEN_EXTRACT", "/net/scratch/f70829xm/qwen3_14b_ocr_to_37column_jsonfilled750_v1"))

INPUT_CSV = EXTRACT / "00_input/gbif_enriched_750.input.csv"
CHUNK_TSV = EXTRACT / "02_chunks/ocr_to_37column_chunks_0000_0014_all.tsv"
PRED_TSV = EXTRACT / "03_outputs/merged_current_0_16/qwen3_14b_predictions_current_0_16.tsv"
OCR_TEXT_TSV = EXTRACT / "01_ocr_text/specimen_ocr_text_merged.tsv"

MAIN_MANIFEST = Path(os.environ.get("SCRIBE_MAIN500_MANIFEST", "/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/00_manifest/main500_local_images.tsv"))
NEW_MANIFEST = Path(os.environ.get("SCRIBE_NEW250_MANIFEST", "/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/00_manifest/new250_local_images.tsv"))

OUTDIR = EXTRACT / "04_html_reports"
ASSET_DIR = OUTDIR / "assets_current_0_16_ocr_review"
ASSET_DIR.mkdir(parents=True, exist_ok=True)

OUT_HTML = OUTDIR / "current_0_16_gbifbase_ocr_candidate_review.html"
OUT_SUMMARY = OUTDIR / "current_0_16_gbifbase_ocr_candidate_review.summary.json"

THUMB_MAX_SIDE = 900

TARGET_FIELDS = [
    "typeStatus",
    "scientificName",
    "canonicalName",
    "phylum",
    "order",
    "family",
    "genus",
    "class",
    "dateIdentified",
    "verbatimEventDate",
    "eventDate",
    "year",
    "month",
    "day",
    "verbatimLocality",
    "locality",
    "continent",
    "countryCode",
    "country",
    "verbatimCoordinates",
    "decimalLatitude",
    "decimalLongitude",
    "habitat",
    "verbatimElevation",
    "elevation",
    "identifiedBy",
    "verbatimRecordedBy",
    "recordedBy",
    "collectionID",
    "institutionCode",
    "fieldNotes",
    "organismRemarks",
]


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def is_blank(x) -> bool:
    return x is None or str(x).strip() == ""


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_tsv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("_") or "unknown"


def parse_float(x):
    try:
        return float(x)
    except Exception:
        return None


def source_class(source: str) -> str:
    s = (source or "").lower()
    if "ocr" in s:
        return "ocrrow"
    if "gbif" in s:
        return "gbifrow"
    if "unsd" in s or "geo" in s:
        return "gbifrow"
    if "json" in s:
        return "jsonrow"
    if s:
        return "jsonrow"
    return "blankrow"


def source_label(source: str) -> str:
    s = (source or "").strip()
    return s if s else ""


def build_manifest_image_map() -> dict[str, dict]:
    out = {}

    for dataset, manifest_path, prefix in [
        ("main500", MAIN_MANIFEST, "main_data"),
        ("new250", NEW_MANIFEST, "new_data"),
    ]:
        if not manifest_path.exists():
            continue

        rows = read_tsv(manifest_path)
        for i, r in enumerate(rows, start=1):
            record_key = f"{prefix}_{i:04d}"
            out[record_key] = {
                "dataset": dataset,
                "record_key": record_key,
                "specimen_id": r.get("specimen_id", ""),
                "catalogNumber": r.get("catalogNumber", ""),
                "image_path": r.get("image_path", ""),
                "image_url": r.get("image_url", "") or r.get("jpegURL", "") or r.get("jpeg_url", ""),
                "json_url": r.get("json_url", "") or r.get("jsonURL", ""),
            }

    return out


def make_thumb(record_key: str, image_path: str) -> tuple[str, dict]:
    info = {
        "ok": False,
        "source": image_path,
        "thumb": "",
        "orig_w": "",
        "orig_h": "",
        "thumb_w": "",
        "thumb_h": "",
        "error": "",
    }

    if not image_path:
        info["error"] = "missing image_path"
        return "", info

    src = Path(image_path)
    if not src.exists():
        info["error"] = f"image not found: {src}"
        return "", info

    dst_name = f"{safe_name(record_key)}_{safe_name(src.stem)}.jpg"
    dst = ASSET_DIR / dst_name

    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            ow, oh = im.size
            scale = min(1.0, THUMB_MAX_SIDE / max(ow, oh))
            tw = max(1, int(round(ow * scale)))
            th = max(1, int(round(oh * scale)))
            if scale < 1.0:
                im = im.resize((tw, th), Image.Resampling.LANCZOS)
            im.save(dst, "JPEG", quality=82, optimize=True)

        info.update({
            "ok": True,
            "thumb": str(dst),
            "orig_w": ow,
            "orig_h": oh,
            "thumb_w": tw,
            "thumb_h": th,
        })
        return f"assets_current_0_16_ocr_review/{dst_name}", info

    except Exception as e:
        info["error"] = repr(e)
        return "", info


def pred_risk(field: str, value: str, evidence: str, confidence: str) -> list[str]:
    reasons = []

    cf = parse_float(confidence)
    if cf is not None and cf < 0.75:
        reasons.append("low confidence")

    if field in {"organismRemarks", "fieldNotes"} and re.search(r"\b(TYPE|HOLOTYPE|ISOTYPE|LECTOTYPE|SYNTYPE|PARATYPE)\b", value, re.I):
        reasons.append("possible typeStatus in wrong field")

    if field in {"verbatimEventDate", "eventDate", "year", "month", "day"} and re.search(
        r"Austrobaileya|journal|vol\.|Proceedings|published|publication", evidence, re.I
    ):
        reasons.append("possible publication date")

    if field == "identifiedBy" and value.strip().lower() in {"coll.", "coll", "collector", "det.", "det"}:
        reasons.append("too generic")

    if field == "verbatimCoordinates":
        has_lat = bool(re.search(r"\b[NS]\b|°\s*[NS]|[NS]\s*$", value, re.I))
        has_lon = bool(re.search(r"\b[EW]\b|°\s*[EW]|[EW]\s*$", value, re.I))
        if not (has_lat and has_lon):
            reasons.append("incomplete coordinate")

    return reasons



def first_nonblank(row: dict, names: list[str]) -> str:
    for name in names:
        val = (row.get(name, "") or "").strip()
        if val:
            return val
    return ""

def external_links_html(row: dict, imginfo: dict | None = None) -> str:
    imginfo = imginfo or {}

    jpeg = first_nonblank(row, ["jpegURL", "image_url", "imageURL", "jpgURL", "jpeg_url"])
    if not jpeg:
        jpeg = first_nonblank(imginfo, ["image_url", "jpegURL", "jpeg_url"])

    js = first_nonblank(row, ["jsonURL", "json_url", "jsonUrl"])

    links = []
    if jpeg:
        links.append(f'<a href="{esc(jpeg)}" target="_blank" rel="noopener noreferrer">Open JPEG</a>')
    if js:
        links.append(f'<a href="{esc(js)}" target="_blank" rel="noopener noreferrer">Open JSON</a>')

    if not links:
        return '<div class="open-links small muted">No external image/json link</div>'

    return '<div class="open-links">' + ' <span class="sep">·</span> '.join(links) + '</div>'

def field_cell_html(record_key: str, base_row: dict, pred_by_key: dict, field: str) -> tuple[str, str, bool]:
    """
    HTML-only policy:
    - gbif_enriched_750 is the base table.
    - If base already has a value, show only the base value and its source.
    - OCR candidate is shown only when the base value is blank.
    - This prevents OCR from visually overriding JSON/GBIF/UNSD-enriched fields.
    """
    base_val = (base_row.get(field, "") or "").strip()
    base_source = (base_row.get(f"source_{field}", "") or "").strip()
    pred = pred_by_key.get((record_key, field), {})

    pred_val = (pred.get("value", "") or "").strip()
    pred_conf = (pred.get("confidence", "") or "").strip()
    pred_ev = (pred.get("evidence", "") or "").strip()

    # IMPORTANT: existing gbif/json/unsd value wins.
    # Do not display OCR candidate under an existing base value.
    if base_val:
        row_class = source_class(base_source)
        label = source_label(base_source)
        main = f'<span class="ok">{esc(base_val)}'
        if label:
            main += f' <span class="small">({esc(label)})</span>'
        main += "</span>"
        return row_class, main, False

    # Only blank base fields may show OCR candidate.
    if pred_val:
        risks = pred_risk(field, pred_val, pred_ev, pred_conf)
        risk_html = ""
        if risks:
            risk_html = f'<div class="ocr-risk">review: {esc("; ".join(risks))}</div>'

        row_class = "ocrrow"
        cell = f"""
        <span class="ocr">{esc(pred_val)} <span class="small ocrtag">(ocr)</span></span>
        <span class="conf">conf={esc(pred_conf)}</span>
        {risk_html}
        <div class="evidence">evidence: {esc(pred_ev)}</div>
        """
        return row_class, cell, True

    return "blankrow", '<span class="blank">blank</span>', False

def main():
    base_rows = read_csv(INPUT_CSV)
    chunk_rows = read_tsv(CHUNK_TSV)
    pred_rows = read_tsv(PRED_TSV)
    ocr_text_rows = read_tsv(OCR_TEXT_TSV)
    image_map = build_manifest_image_map()

    base_by_key = {r.get("record_key", ""): r for r in base_rows}
    ocr_text_by_key = {r.get("record_key", ""): r for r in ocr_text_rows}

    pred_by_key = {}
    for r in pred_rows:
        record_key = r.get("record_key", "")
        field = r.get("field", "")
        value = (r.get("value", "") or "").strip()
        if not value:
            continue
        key = (record_key, field)

        old = pred_by_key.get(key)
        if old is None:
            pred_by_key[key] = r
        else:
            old_conf = parse_float(old.get("confidence", "")) or 0
            new_conf = parse_float(r.get("confidence", "")) or 0
            if new_conf > old_conf:
                pred_by_key[key] = r

    stats = {
        "records": len(chunk_rows),
        "target_fields": len(TARGET_FIELDS),
        "current_filled_cells": 0,
        "ocr_candidate_cells": 0,
        "ocr_candidate_records": 0,
        "blank_after_current_and_ocr": 0,
        "risk_candidate_cells": 0,
        "field_ocr_counts": Counter(),
    }

    record_sections = []

    for n, cr in enumerate(chunk_rows, start=1):
        record_key = cr["record_key"]
        base = base_by_key.get(record_key, {})
        imginfo = image_map.get(record_key, {})
        ocr_text_info = ocr_text_by_key.get(record_key, {})

        thumb_rel, thumb_info = make_thumb(record_key, imginfo.get("image_path", ""))
        image_links = external_links_html(base, imginfo)

        ocr_candidate_count_this = 0
        field_rows_html = []

        for field in TARGET_FIELDS:
            if (base.get(field, "") or "").strip():
                stats["current_filled_cells"] += 1

            row_class, cell_html, has_ocr = field_cell_html(record_key, base, pred_by_key, field)

            if has_ocr:
                stats["ocr_candidate_cells"] += 1
                stats["field_ocr_counts"][field] += 1
                ocr_candidate_count_this += 1
                pred = pred_by_key.get((record_key, field), {})
                if pred_risk(field, pred.get("value", ""), pred.get("evidence", ""), pred.get("confidence", "")):
                    stats["risk_candidate_cells"] += 1

            if row_class == "blankrow" and not has_ocr and not (base.get(field, "") or "").strip():
                stats["blank_after_current_and_ocr"] += 1

            field_rows_html.append(f'<tr class="{row_class}"><th>{esc(field)}</th><td>{cell_html}</td></tr>')

        if ocr_candidate_count_this:
            stats["ocr_candidate_records"] += 1

        if thumb_rel:
            image_html = f'<img loading="lazy" src="{esc(thumb_rel)}" alt="{esc(record_key)}">'
        else:
            image_html = f'<div class="noimg">No local image thumbnail<br>{esc(thumb_info.get("error", ""))}</div>'

        scientific = base.get("scientificName", "") or cr.get("scientificName_current", "")
        occurrence = base.get("occurrenceID", "") or cr.get("occurrenceID", "")
        index = base.get("index", "") or cr.get("index", "")
        source_sheet = base.get("source_sheet", "") or cr.get("source_sheet", "")

        ocr_text = ocr_text_info.get("ocr_text", "")
        ocr_text_html = esc(ocr_text).replace("\n", "<br>")

        search_blob = " ".join([
            record_key,
            index,
            occurrence,
            scientific,
            base.get("country", ""),
            base.get("locality", ""),
            base.get("recordedBy", ""),
            imginfo.get("specimen_id", ""),
            " ".join([pred_by_key.get((record_key, f), {}).get("value", "") for f in TARGET_FIELDS]),
        ]).lower()

        record_sections.append(f"""
<section class="record" id="{esc(record_key)}" data-search="{esc(search_blob)}">
  <div class="record-grid">
    <div class="leftcol">
      <div class="imagewrap"><div class="imagepanel">{image_html}{image_links}</div></div>
      <div class="imglinks">
        <div><b>record_key:</b> {esc(record_key)}</div>
        <div><b>specimen_id:</b> {esc(imginfo.get("specimen_id", ""))}</div>
        <div><b>image:</b> {esc(imginfo.get("image_path", ""))}</div>
      </div>
    </div>

    <div class="rightcol">
      <h2>{esc(source_sheet)} index {esc(index)}: {esc(scientific)}</h2>
      <p><strong>Occurrence:</strong> {esc(occurrence)}</p>
      <p><strong>OCR candidates:</strong> <span class="ocr">{ocr_candidate_count_this} fields <span class="small ocrtag">(ocr)</span></span></p>
      <p><strong>OCR crop text:</strong> {esc(ocr_text_info.get("ocr_crop_count", ""))} crops, {esc(ocr_text_info.get("ocr_char_count", ""))} chars</p>

      <div class="tablewrap">
        <table>
          {''.join(field_rows_html)}
        </table>
      </div>

      <details class="ocrdetails">
        <summary>Show merged crop OCR text</summary>
        <div class="rawocr">{ocr_text_html}</div>
      </details>
    </div>
  </div>
</section>
""")

    field_ocr_rows = "\n".join(
        f"<tr><td><code>{esc(field)}</code></td><td>{count}</td></tr>"
        for field, count in stats["field_ocr_counts"].most_common()
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCRIBE current 0-16 OCR candidate review</title>
<style>
  body {{ margin:0; font-family: Arial, Helvetica, sans-serif; background:#f3f4f6; color:#1f2937; }}
  .page {{ max-width: 1600px; margin: 0 auto; padding: 18px 20px 32px; }}
  .topbox {{ background:white; border:1px solid #d1d5db; padding:18px 20px; margin-bottom:18px; }}
  h1 {{ margin:0 0 10px; font-size:32px; }}
  p {{ line-height:1.4; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:10px; }}
  .stat {{ border:1px solid #d1d5db; background:#fafafa; padding:10px 12px; min-width:170px; }}
  .stat b {{ font-size:22px; display:block; margin-bottom:3px; }}
  .controls {{ position: sticky; top: 0; z-index:10; background: rgba(243,244,246,.97); padding: 10px 0 14px; }}
  .controls-inner {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
  input[type="search"] {{ flex:1 1 340px; padding:10px 12px; border:1px solid #9ca3af; font-size:16px; }}
  .btn {{ padding:10px 12px; border:1px solid #9ca3af; background:white; cursor:pointer; }}
  .record {{ background:white; border:1px solid #d1d5db; margin-bottom:18px; }}
  .record-grid {{ display:grid; grid-template-columns: 420px 1fr; gap:18px; align-items:start; }}
  .leftcol {{ padding:0 0 12px 0; }}
  .rightcol {{ padding:14px 16px 16px 0; }}
  .imagewrap {{ background:#fff; min-height:300px; display:flex; align-items:flex-start; justify-content:center; border-right:1px solid #d1d5db; }}
  .imagewrap img {{ width:100%; height:auto; display:block; }}

  .imagepanel {{ width:100%; }}
  .imagepanel img {{ width:100%; height:auto; display:block; }}

  .noimg {{ padding:24px; color:#6b7280; }}
  .imglinks {{ padding:10px 12px 0; font-size:13px; color:#4b5563; word-break:break-all; line-height:1.35; }}
  h2 {{ margin:0 0 12px; font-size:24px; }}
  .tablewrap {{ overflow-x:auto; margin-top:10px; }}
  table {{ border-collapse: collapse; width:100%; font-size:15px; }}
  th, td {{ border:1px solid #d1d5db; padding:7px 8px; vertical-align:top; }}
  th {{ width:210px; text-align:left; background:#f9fafb; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight:400; }}
  .ok {{ color:#065f46; }}
  .gbif {{ color:#1d4ed8; }}
  .blank {{ color:#b91c1c; font-style:italic; }}
  .ocr {{ color:#1d4ed8; font-weight:650; }}
  .ocrtag {{ color:#1d4ed8; }}
  .conf {{ color:#2563eb; font-size:13px; margin-left:8px; }}
  .evidence {{ color:#374151; font-size:13px; margin-top:4px; padding-left:8px; border-left:3px solid #bfdbfe; }}
  .ocr-risk {{ color:#b45309; background:#fff7ed; border:1px solid #fed7aa; display:inline-block; padding:2px 6px; margin-top:4px; font-size:13px; }}
  .ocr-candidate-under-existing {{ margin-top:7px; padding-top:7px; border-top:1px dashed #bfdbfe; }}
  .jsonrow td {{ background:#f8fffb; }}
  .gbifrow td {{ background:#f8fbff; }}
  .ocrrow td {{ background:#eff6ff; }}
  .blankrow td {{ background:#fff7f7; }}
  .small {{ color:#4b5563; font-size:14px; }}
  .hidden {{ display:none; }}
  .ocrdetails {{ margin-top:12px; }}
  .rawocr {{ margin-top:8px; padding:10px; background:#f8fafc; border:1px solid #d1d5db; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:13px; max-height:360px; overflow:auto; line-height:1.35; }}
  @media (max-width: 1100px) {{
    .record-grid {{ grid-template-columns:1fr; }}
    .rightcol {{ padding:14px 16px 16px; }}
    .imagewrap {{ border-right:none; border-bottom:1px solid #d1d5db; }}
  }}

.open-links {{
  margin-top: 8px;
  font-size: 14px;
  line-height: 1.4;
}}
.open-links a {{
  font-weight: 600;
  text-decoration: underline;
}}
.open-links .sep {{
  color: #777;
  padding: 0 3px;
}}
.muted {{
  color: #888;
}}

</style>
</head>
<body>
<div class="page">
  <div class="topbox">
    <h1>SCRIBE current 0-16 OCR candidate review</h1>
    <p>This report visualizes Qwen3-14B OCR-to-field extraction candidates from chunks 0000-0016. Existing JSON/GBIF/UNSD values are preserved. OCR-derived candidate values are shown in blue and labelled <b class="ocr">(ocr)</b>.</p>
    <div class="stats">
      <div class="stat"><b>{stats["records"]}</b>records</div>
      <div class="stat"><b>{stats["target_fields"]}</b>target feature columns</div>
      <div class="stat"><b>{stats["current_filled_cells"]}</b>current filled cells</div>
      <div class="stat"><b>{stats["ocr_candidate_cells"]}</b>OCR candidate cells</div>
      <div class="stat"><b>{stats["ocr_candidate_records"]}</b>records with OCR candidates</div>
      <div class="stat"><b>{stats["risk_candidate_cells"]}</b>candidate cells flagged for review</div>
      <div class="stat"><b>{stats["blank_after_current_and_ocr"]}</b>still blank after OCR candidate view</div>
    </div>
  </div>

  <div class="topbox">
    <h2>OCR candidate counts by field</h2>
    <div class="tablewrap">
      <table>
        <thead><tr><th>Field</th><th>OCR candidates</th></tr></thead>
        <tbody>
          {field_ocr_rows}
        </tbody>
      </table>
    </div>
  </div>

  <div class="controls">
    <div class="controls-inner">
      <input id="searchBox" type="search" placeholder="Search by record_key, index, specimen ID, scientific name, OCR value, locality, etc.">
      <button class="btn" onclick="clearSearch()">Clear</button>
      <span id="countLabel" class="small"></span>
    </div>
  </div>

  <div id="records">
    {''.join(record_sections)}
  </div>
</div>

<script>
const searchBox = document.getElementById('searchBox');
const countLabel = document.getElementById('countLabel');
const records = Array.from(document.querySelectorAll('.record'));

function updateSearch() {{
  const q = searchBox.value.trim().toLowerCase();
  let shown = 0;
  for (const rec of records) {{
    const hay = rec.getAttribute('data-search') || '';
    const ok = !q || hay.includes(q);
    rec.classList.toggle('hidden', !ok);
    if (ok) shown++;
  }}
  countLabel.textContent = shown + ' / ' + records.length + ' records shown';
}}

function clearSearch() {{
  searchBox.value = '';
  updateSearch();
}}

searchBox.addEventListener('input', updateSearch);
updateSearch();
</script>
</body>
</html>
"""

    OUT_HTML.write_text(html_doc, encoding="utf-8")

    summary = dict(stats)
    summary["field_ocr_counts"] = dict(stats["field_ocr_counts"])
    summary["html"] = str(OUT_HTML)
    summary["assets"] = str(ASSET_DIR)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("WROTE_HTML", OUT_HTML)
    print("WROTE_SUMMARY", OUT_SUMMARY)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
