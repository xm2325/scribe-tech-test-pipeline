from __future__ import annotations

import argparse
import html
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .constants import TARGET_FIELDS
from .utils import clean, is_blank


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_add_ocr_candidates(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Add OCR/LLM candidate values to a JSON/GBIF/geo baseline table. "
            "Existing baseline values are never overwritten."
        )
    )
    parser.add_argument("--base", required=True, help="Baseline CSV, e.g. gbif_enriched_750.csv")
    parser.add_argument("--ocr-candidates", required=True, help="OCR candidate TSV/CSV")
    parser.add_argument("--output-candidates", required=True, help="Cleaned OCR candidate CSV")
    parser.add_argument("--output-review-matrix", required=True, help="Field-level review matrix CSV")
    parser.add_argument("--output-html", required=True, help="Review HTML report")
    parser.add_argument("--max-records", type=int, default=750)
    return parser


def run_add_ocr_candidates(args: argparse.Namespace) -> dict[str, Path]:
    base_path = Path(args.base)
    ocr_path = Path(args.ocr_candidates)

    base = pd.read_csv(base_path, dtype=str).fillna("")
    ocr = read_table(ocr_path)

    candidates = normalize_candidates(ocr)
    review = build_review_matrix(base, candidates)

    output_candidates = Path(args.output_candidates)
    output_review = Path(args.output_review_matrix)
    output_html = Path(args.output_html)

    output_candidates.parent.mkdir(parents=True, exist_ok=True)
    output_review.parent.mkdir(parents=True, exist_ok=True)
    output_html.parent.mkdir(parents=True, exist_ok=True)

    candidates.to_csv(output_candidates, index=False)
    review.to_csv(output_review, index=False)

    render_ocr_review_html(
        base,
        candidates,
        review,
        output_html,
        max_records=int(args.max_records),
    )

    summary = {
        "base_records": len(base),
        "candidate_rows": len(candidates),
        "review_rows": len(review),
        "ocr_candidate_cells": int(review["has_ocr_candidate"].eq("yes").sum()),
        "base_filled_cells": int(review["base_filled"].eq("yes").sum()),
        "still_blank_cells": int(review["decision"].eq("blank_after_base_and_ocr").sum()),
        "output_candidates": output_candidates,
        "output_review_matrix": output_review,
        "output_html": output_html,
    }
    print_summary(summary)
    return {
        "candidates": output_candidates,
        "review_matrix": output_review,
        "html": output_html,
    }


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".tsv", ".tab"}:
        return pd.read_csv(path, dtype=str, sep="\t").fillna("")
    return pd.read_csv(path, dtype=str).fillna("")


def normalize_candidates(ocr: pd.DataFrame) -> pd.DataFrame:
    required = ["record_key", "field", "value"]
    missing = [c for c in required if c not in ocr.columns]
    if missing:
        raise ValueError(f"OCR candidate file missing columns: {missing}")

    rows: list[dict[str, Any]] = []
    for _, r in ocr.iterrows():
        record_key = clean(r.get("record_key", ""))
        field = clean(r.get("field", ""))
        value = clean(r.get("value", ""))
        status = clean(r.get("status", ""))

        if not record_key or not field or field not in TARGET_FIELDS:
            continue
        if not value:
            continue
        if status and status.upper() != "PASS":
            continue

        rows.append(
            {
                "record_key": record_key,
                "field": field,
                "candidate_value": value,
                "confidence": clean(r.get("confidence", "")),
                "evidence": clean(r.get("evidence", "")),
                "status": status or "PASS",
                "merged_source": clean(r.get("merged_source", "")),
                "chunk_id": clean(r.get("chunk_id", "")),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(
            columns=[
                "record_key",
                "field",
                "candidate_value",
                "confidence",
                "evidence",
                "status",
                "merged_source",
                "chunk_id",
            ]
        )

    out["_confidence_float"] = out["confidence"].map(parse_float)
    out["_source_rank"] = out["merged_source"].map(source_rank)

    # One best candidate per record_key + field.
    out = (
        out.sort_values(
            ["record_key", "field", "_source_rank", "_confidence_float"],
            ascending=[True, True, False, False],
        )
        .drop_duplicates(["record_key", "field"], keep="first")
        .drop(columns=["_confidence_float", "_source_rank"])
        .reset_index(drop=True)
    )
    return out


def parse_float(x: Any) -> float:
    try:
        return float(clean(x))
    except Exception:
        return 0.0


def source_rank(x: Any) -> int:
    text = clean(x)
    if "_retry" in text:
        return 2
    if text:
        return 1
    return 0


def build_review_matrix(base: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    cand_by_key = {
        (r.record_key, r.field): r
        for r in candidates.itertuples(index=False)
    }

    rows: list[dict[str, Any]] = []
    for _, record in base.iterrows():
        record_key = clean(record.get("record_key", ""))
        for field in TARGET_FIELDS:
            base_value = clean(record.get(field, ""))
            base_source = clean(record.get(f"source_{field}", ""))

            cand = cand_by_key.get((record_key, field))
            cand_value = clean(getattr(cand, "candidate_value", "")) if cand is not None else ""

            base_filled = not is_blank(base_value)
            has_candidate = not is_blank(cand_value)

            if base_filled:
                decision = "keep_base_value"
            elif has_candidate:
                decision = "review_ocr_candidate"
            else:
                decision = "blank_after_base_and_ocr"

            rows.append(
                {
                    "record_key": record_key,
                    "source_sheet": clean(record.get("source_sheet", "")),
                    "source_row": clean(record.get("source_row", "")),
                    "index": clean(record.get("index", "")),
                    "occurrenceID": clean(record.get("occurrenceID", "")),
                    "scientificName": clean(record.get("scientificName", "")),
                    "field": field,
                    "base_value": base_value,
                    "base_source": base_source,
                    "base_filled": "yes" if base_filled else "no",
                    "ocr_candidate_value": cand_value,
                    "ocr_confidence": clean(getattr(cand, "confidence", "")) if cand is not None else "",
                    "ocr_evidence": clean(getattr(cand, "evidence", "")) if cand is not None else "",
                    "ocr_source": clean(getattr(cand, "merged_source", "")) if cand is not None else "",
                    "has_ocr_candidate": "yes" if has_candidate else "no",
                    "decision": decision,
                }
            )

    return pd.DataFrame(rows)


def render_ocr_review_html(
    base: pd.DataFrame,
    candidates: pd.DataFrame,
    review: pd.DataFrame,
    output_path: Path,
    *,
    max_records: int = 750,
) -> None:
    cand_by_key = {
        (r.record_key, r.field): r
        for r in candidates.itertuples(index=False)
    }

    shown = base.head(max_records).copy()
    records_html = "\n".join(
        render_record(record, cand_by_key, output_path)
        for _, record in shown.iterrows()
    )

    total_cells = len(base) * len(TARGET_FIELDS)
    base_filled = int(review["base_filled"].eq("yes").sum())
    candidate_cells = int(review["has_ocr_candidate"].eq("yes").sum())
    still_blank = int(review["decision"].eq("blank_after_base_and_ocr").sum())

    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCRIBE OCR candidate review</title>
<style>
body {{ margin:0; font-family: Arial, Helvetica, sans-serif; background:#f3f4f6; color:#1f2937; }}
.page {{ max-width:1600px; margin:0 auto; padding:18px 20px 32px; }}
.topbox {{ background:white; border:1px solid #d1d5db; padding:18px 20px; margin-bottom:18px; }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px; }}
.stat {{ border:1px solid #d1d5db; background:#fafafa; padding:10px 12px; min-width:180px; }}
.stat b {{ font-size:22px; display:block; }}
.controls {{ position:sticky; top:0; z-index:10; background:rgba(243,244,246,.97); padding:10px 0 14px; }}
.controls-inner {{ display:flex; gap:10px; align-items:center; }}
input[type="search"] {{ flex:1; padding:10px 12px; border:1px solid #9ca3af; font-size:16px; }}
.record {{ background:white; border:1px solid #d1d5db; margin-bottom:18px; }}
.record-grid {{ display:grid; grid-template-columns:420px 1fr; gap:18px; align-items:start; }}
.leftcol {{ padding:0 0 12px 0; }}
.rightcol {{ padding:14px 16px 16px 0; }}
.imagewrap {{ background:#fff; min-height:300px; display:flex; align-items:flex-start; justify-content:center; border-right:1px solid #d1d5db; }}
.imagewrap img {{ width:100%; height:auto; display:block; }}
.imglinks {{ padding:10px 12px 0; font-size:14px; }}
.imglinks a {{ color:#1d4ed8; text-decoration:underline; font-weight:600; }}
.noimg {{ padding:24px; color:#6b7280; }}
table {{ border-collapse:collapse; width:100%; font-size:15px; }}
th, td {{ border:1px solid #d1d5db; padding:7px 8px; vertical-align:top; }}
th {{ width:210px; text-align:left; background:#f9fafb; font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight:400; }}
.base {{ color:#065f46; }}
.ocr {{ color:#1d4ed8; font-weight:600; }}
.blank {{ color:#b91c1c; font-style:italic; }}
.baserow td {{ background:#f8fffb; }}
.ocrrow td {{ background:#f8fbff; }}
.blankrow td {{ background:#fff7f7; }}
.small {{ color:#4b5563; font-size:14px; }}
.evidence {{ margin-top:4px; color:#374151; font-size:14px; }}
.hidden {{ display:none; }}
@media (max-width:1100px) {{
  .record-grid {{ grid-template-columns:1fr; }}
  .rightcol {{ padding:14px 16px 16px; }}
  .imagewrap {{ border-right:none; border-bottom:1px solid #d1d5db; }}
}}
</style>
</head>
<body>
<div class="page">
  <div class="topbox">
    <h1>SCRIBE OCR candidate review</h1>
    <p>Existing JSON/GBIF/geographic values are kept. OCR/LLM values are shown only as candidates for fields that remain blank.</p>
    <div class="stats">
      <div class="stat"><b>{len(base)}</b>records</div>
      <div class="stat"><b>{len(TARGET_FIELDS)}</b>target fields</div>
      <div class="stat"><b>{base_filled}</b>base-filled cells</div>
      <div class="stat"><b>{candidate_cells}</b>OCR candidate cells</div>
      <div class="stat"><b>{still_blank}</b>still blank cells</div>
      <div class="stat"><b>{total_cells}</b>total feature cells</div>
    </div>
  </div>

  <div class="controls">
    <div class="controls-inner">
      <input id="searchBox" type="search" placeholder="Search record_key, index, occurrence ID, scientific name, OCR value, locality, etc.">
      <button onclick="clearSearch()">Clear</button>
      <span id="countLabel" class="small"></span>
    </div>
  </div>

  <div id="records">{records_html}</div>
</div>
<script>
const searchBox = document.getElementById('searchBox');
const records = Array.from(document.querySelectorAll('.record'));
const countLabel = document.getElementById('countLabel');
function updateSearch() {{
  const q = searchBox.value.trim().toLowerCase();
  let shown = 0;
  records.forEach(r => {{
    const ok = !q || r.dataset.search.includes(q);
    r.classList.toggle('hidden', !ok);
    if (ok) shown += 1;
  }});
  countLabel.textContent = shown + ' / ' + records.length + ' visible';
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
    output_path.write_text(html_text, encoding="utf-8")


def render_record(record: pd.Series, cand_by_key: dict[tuple[str, str], Any], output_path: Path) -> str:
    record_key = clean(record.get("record_key", ""))
    title = clean(record.get("scientificName", "")) or "(scientificName blank)"
    occurrence = clean(record.get("occurrenceID", ""))
    index = clean(record.get("index", ""))
    sheet = clean(record.get("source_sheet", ""))

    local_image = clean(record.get("local_image_path", ""))
    if local_image and Path(local_image).exists():
        image_src = os.path.relpath(Path(local_image), start=output_path.parent).replace(os.sep, "/")
    else:
        image_src = clean(record.get("jpegURL", ""))

    jpeg_href = clean(record.get("jpegURL", "")) or image_src
    json_href = clean(record.get("jsonURL", "")) or clean(record.get("local_json_rel", ""))

    field_rows = "".join(render_field_row(record, cand_by_key, field) for field in TARGET_FIELDS)

    search_blob = " ".join(
        [
            record_key,
            index,
            occurrence,
            title,
            clean(record.get("country", "")),
            clean(record.get("locality", "")),
            clean(record.get("recordedBy", "")),
            clean(record.get("institutionCode", "")),
            " ".join(
                clean(getattr(cand_by_key.get((record_key, f)), "candidate_value", ""))
                for f in TARGET_FIELDS
            ),
        ]
    ).lower()

    image_html = (
        f'<img loading="lazy" src="{esc(image_src)}" alt="{esc(title)}">'
        if image_src
        else '<div class="noimg">No image URL available</div>'
    )

    return f"""
<section class="record" id="{esc(record_key)}" data-search="{esc(search_blob)}">
  <div class="record-grid">
    <div class="leftcol">
      <div class="imagewrap">{image_html}</div>
      <div class="imglinks"><a href="{esc(jpeg_href)}" target="_blank" rel="noopener">Open JPEG</a> · <a href="{esc(json_href)}" target="_blank" rel="noopener">Open JSON</a></div>
    </div>
    <div class="rightcol">
      <h2>{esc(record_key)}: {esc(title)}</h2>
      <p><strong>Sheet:</strong> {esc(sheet)} · <strong>Index:</strong> {esc(index)} · <strong>Occurrence:</strong> {esc(occurrence)}</p>
      <table>{field_rows}</table>
    </div>
  </div>
</section>
"""


def render_field_row(record: pd.Series, cand_by_key: dict[tuple[str, str], Any], field: str) -> str:
    record_key = clean(record.get("record_key", ""))
    base_value = clean(record.get(field, ""))
    base_source = clean(record.get(f"source_{field}", ""))
    cand = cand_by_key.get((record_key, field))

    if base_value:
        source = f" ({esc(base_source)})" if base_source else ""
        return f'<tr class="baserow"><th>{esc(field)}</th><td><span class="base">{esc(base_value)}</span><span class="small">{source}</span></td></tr>'

    if cand is not None and clean(getattr(cand, "candidate_value", "")):
        value = clean(getattr(cand, "candidate_value", ""))
        confidence = clean(getattr(cand, "confidence", ""))
        evidence = clean(getattr(cand, "evidence", ""))
        source = clean(getattr(cand, "merged_source", ""))
        return (
            f'<tr class="ocrrow"><th>{esc(field)}</th><td>'
            f'<span class="ocr">{esc(value)} <span class="small">(ocr)</span></span>'
            f'<div class="small">confidence={esc(confidence)} source={esc(source)}</div>'
            f'<div class="evidence">evidence: {esc(evidence)}</div>'
            f'</td></tr>'
        )

    return f'<tr class="blankrow"><th>{esc(field)}</th><td><span class="blank">blank</span></td></tr>'


def print_summary(summary: dict[str, Any]) -> None:
    for key, value in summary.items():
        print(f"{key}: {value}")


def esc(x: Any) -> str:
    return html.escape(clean(x), quote=True)


if __name__ == "__main__":
    raise SystemExit(main())
