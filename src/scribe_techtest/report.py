from __future__ import annotations

import html
from pathlib import Path
from typing import Any

import pandas as pd

from .constants import TARGET_FIELDS
from .utils import clean, is_blank


def field_coverage(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total = len(df)
    for field in TARGET_FIELDS:
        filled = count_nonblank(df, field)
        main = df[df["source_sheet"].eq("main_data")]
        new = df[df["source_sheet"].eq("new_data")]
        source_col = f"source_{field}"
        json_count = int(df[source_col].eq("json").sum()) if source_col in df.columns else 0
        gbif_count = int(df[source_col].eq("gbif").sum()) if source_col in df.columns else 0
        rows.append(
            {
                "field": field,
                "filled_all": filled,
                "total_all": total,
                "coverage_all": round(filled / total, 4) if total else 0,
                "filled_main_data": count_nonblank(main, field),
                "total_main_data": len(main),
                "filled_new_data": count_nonblank(new, field),
                "total_new_data": len(new),
                "source_json": json_count,
                "source_gbif": gbif_count,
                "blank_all": total - filled,
            }
        )
    return pd.DataFrame(rows)


def count_nonblank(df: pd.DataFrame, field: str) -> int:
    if df.empty:
        return 0
    return int(df[field].map(lambda v: not is_blank(v)).sum())


def render_gallery_report(
    df: pd.DataFrame,
    coverage: pd.DataFrame,
    output_path: Path,
    *,
    title: str,
    subtitle: str,
    stage_label: str,
    max_records: int = 750,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shown = df.head(max_records).copy()
    total_cells = len(df) * len(TARGET_FIELDS)
    filled_cells = sum(int(df[field].apply(lambda v: not is_blank(v)).sum()) for field in TARGET_FIELDS)
    blank_cells = total_cells - filled_cells
    coverage_pct = 100 * filled_cells / total_cells if total_cells else 0

    summary_rows = "\n".join(
        "<tr>"
        f"<td><code>{esc(row.field)}</code></td>"
        f"<td>{int(row.filled_all)} / {int(row.total_all)} ({row.coverage_all:.1%})</td>"
        f"<td>{int(row.filled_main_data)} / {int(row.total_main_data)}</td>"
        f"<td>{int(row.filled_new_data)} / {int(row.total_new_data)}</td>"
        f"<td>{int(row.source_json)}</td>"
        f"<td>{int(row.source_gbif)}</td>"
        f"<td>{int(row.blank_all)}</td>"
        "</tr>"
        for row in coverage.itertuples(index=False)
    )

    record_sections = "\n".join(render_record(row, output_path) for _, row in shown.iterrows())
    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
  body {{ margin:0; font-family: Arial, Helvetica, sans-serif; background:#f3f4f6; color:#1f2937; }}
  .page {{ max-width: 1600px; margin: 0 auto; padding: 18px 20px 32px; }}
  .topbox {{ background:white; border:1px solid #d1d5db; padding:18px 20px; margin-bottom:18px; }}
  h1 {{ margin:0 0 10px; font-size:32px; }}
  p {{ line-height:1.4; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:10px; }}
  .stat {{ border:1px solid #d1d5db; background:#fafafa; padding:10px 12px; min-width:160px; }}
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
  .noimg {{ padding:24px; color:#6b7280; }}
  .imglinks {{ padding:10px 12px 0; font-size:14px; }}
  .imglinks a {{ color:#1d4ed8; text-decoration:underline; }}
  h2 {{ margin:0 0 12px; font-size:24px; }}
  .tablewrap {{ overflow-x:auto; margin-top:10px; }}
  table {{ border-collapse: collapse; width:100%; font-size:15px; }}
  th, td {{ border:1px solid #d1d5db; padding:7px 8px; vertical-align:top; }}
  th {{ width:210px; text-align:left; background:#f9fafb; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight:400; }}
  .ok {{ color:#065f46; }}
  .gbif {{ color:#1d4ed8; }}
  .blank {{ color:#b91c1c; font-style:italic; }}
  .jsonrow td {{ background:#f8fffb; }}
  .gbifrow td {{ background:#f8fbff; }}
  .blankrow td {{ background:#fff7f7; }}
  .small {{ color:#4b5563; font-size:14px; }}
  .hidden {{ display:none; }}
  @media (max-width: 1100px) {{
    .record-grid {{ grid-template-columns:1fr; }}
    .rightcol {{ padding:14px 16px 16px; }}
    .imagewrap {{ border-right:none; border-bottom:1px solid #d1d5db; }}
  }}
</style>
</head>
<body>
<div class="page">
  <div class="topbox">
    <h1>{esc(title)}</h1>
    <p>{esc(subtitle)}</p>
    <div class="stats">
      <div class="stat"><b>{len(df)}</b>records</div>
      <div class="stat"><b>{len(TARGET_FIELDS)}</b>target feature columns</div>
      <div class="stat"><b>{filled_cells}</b>filled feature cells</div>
      <div class="stat"><b>{coverage_pct:.2f}%</b>{esc(stage_label)} coverage</div>
      <div class="stat"><b>{blank_cells}</b>feature cells still blank</div>
    </div>
    <p class="small">Search works across index, occurrence ID, scientific name, country, locality, collector, family, genus, and institution code. Field rows are labelled by source: JSON, GBIF, or blank.</p>
  </div>
  <div class="topbox">
    <h2>Field Coverage Summary</h2>
    <div class="tablewrap"><table>
      <thead><tr><th>Field</th><th>All</th><th>main_data</th><th>new_data</th><th>JSON source</th><th>GBIF source</th><th>Blank</th></tr></thead>
      <tbody>{summary_rows}</tbody>
    </table></div>
  </div>
  <div class="controls">
    <div class="controls-inner">
      <input id="searchBox" type="search" placeholder="Search by index, specimen ID, scientific name, country, locality, collector, etc.">
      <button class="btn" onclick="clearSearch()">Clear</button>
      <span id="countLabel" class="small"></span>
    </div>
  </div>
  <div id="records">{record_sections}</div>
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


def render_record(row: pd.Series, report_path: Path) -> str:
    title = clean(row.get("scientificName", "")) or "(scientificName blank)"
    occurrence = clean(row.get("occurrenceID", ""))
    index = clean(row.get("index", ""))
    sheet = clean(row.get("source_sheet", ""))
    nonblank = sum(1 for field in TARGET_FIELDS if not is_blank(row.get(field, "")))
    blank_fields = [field for field in TARGET_FIELDS if is_blank(row.get(field, ""))]
    image_src = clean(row.get("local_image_rel", "")) or clean(row.get("jpegURL", ""))

    # User-facing links should point to the original URLs from the workbook.
    json_href = clean(row.get("jsonURL", "")) or clean(row.get("local_json_rel", ""))
    jpeg_href = clean(row.get("jpegURL", "")) or clean(row.get("local_image_rel", ""))
    search = " ".join(
        clean(row.get(field, ""))
        for field in [
            "index",
            "occurrenceID",
            "scientificName",
            "country",
            "locality",
            "recordedBy",
            "family",
            "genus",
            "institutionCode",
        ]
    ).lower()
    field_rows = "".join(render_field_row(row, field) for field in TARGET_FIELDS)
    image_html = (
        f'<img loading="lazy" src="{esc(image_src)}" alt="{esc(title)}">'
        if image_src
        else '<div class="noimg">No image URL available</div>'
    )
    blank_text = ", ".join(blank_fields) if blank_fields else "none"
    return f"""
    <section class="record" id="{esc(sheet)}-{esc(index)}" data-search="{esc(search)}">
      <div class="record-grid">
        <div class="leftcol">
          <div class="imagewrap">{image_html}</div>
          <div class="imglinks"><a href="{esc(jpeg_href)}" target="_blank" rel="noopener">Open JPEG</a> · <a href="{esc(json_href)}" target="_blank" rel="noopener">Open JSON</a></div>
        </div>
        <div class="rightcol">
          <h2>{esc(sheet)} index {esc(index)}: {esc(title)}</h2>
          <p><strong>Occurrence:</strong> {esc(occurrence)}</p>
          <p><strong>Nonblank feature cells:</strong> {nonblank} / {len(TARGET_FIELDS)}</p>
          <p><strong>Feature fields still blank:</strong> {esc(blank_text)}</p>
          {gbif_summary(row)}
          <div class="tablewrap"><table>{field_rows}</table></div>
        </div>
      </div>
    </section>
    """


def local_debug_links(local_jpeg_href: str, local_json_href: str) -> str:
    links = []
    if local_jpeg_href:
        links.append(f'<a href="{esc(local_jpeg_href)}" target="_blank" rel="noopener">local JPEG cache</a>')
    if local_json_href:
        links.append(f'<a href="{esc(local_json_href)}" target="_blank" rel="noopener">local JSON cache</a>')
    if not links:
        return ""
    return '<br><span class="small">Cache: ' + " · ".join(links) + "</span>"


def render_field_row(row: pd.Series, field: str) -> str:
    value = clean(row.get(field, ""))
    source = clean(row.get(f"source_{field}", ""))
    if not value:
        return f'<tr class="blankrow"><th>{esc(field)}</th><td><span class="blank">blank</span></td></tr>'
    css = "gbifrow" if source == "gbif" else "jsonrow"
    text_css = "gbif" if source == "gbif" else "ok"
    label = f"{esc(value)} <span class=\"small\">({esc(source or 'provided')})</span>"
    return f'<tr class="{css}"><th>{esc(field)}</th><td><span class="{text_css}">{label}</span></td></tr>'


def gbif_summary(row: pd.Series) -> str:
    usage_key = clean(row.get("gbif_usageKey", ""))
    if not usage_key:
        return ""
    parts = [
        f"usageKey {usage_key}",
        f"confidence {clean(row.get('gbif_confidence', ''))}",
        f"matchType {clean(row.get('gbif_matchType', ''))}",
    ]
    return f"<p><strong>GBIF match:</strong> {esc(' | '.join(parts))}</p>"


def esc(value: Any) -> str:
    return html.escape(clean(value), quote=True)
