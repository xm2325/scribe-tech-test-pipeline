#!/usr/bin/env python3
from __future__ import annotations

import base64
import csv
import html
from pathlib import Path

BLOCK = Path("/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier")
AUDIT = BLOCK / "03_audit/main500_v4_output_audit.tsv"
OUT_HTML = BLOCK / "03_audit/main500_v4_visual_gallery_review_plus_first30.html"


def b64_img(path: Path, max_bytes: int = 8_000_000) -> str:
    if not path.is_file():
        return "<div class='missing'>missing image</div>"
    if path.stat().st_size > max_bytes:
        return f"<div class='missing'>image too large: {html.escape(str(path))}</div>"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"<img src='data:{mime};base64,{data}' loading='lazy'/>"


def read_queue(path: Path):
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def table(rows):
    if not rows:
        return "<p class='muted'>No queue rows.</p>"

    keep = [
        "queue_id", "queue_stage", "region_type", "area_fraction",
        "covered_unit_n", "coverage_gain_n", "crop_reason", "crop_path"
    ]

    s = ["<table><thead><tr>"]
    for k in keep:
        s.append(f"<th>{html.escape(k)}</th>")
    s.append("</tr></thead><tbody>")

    for r in rows:
        s.append("<tr>")
        for k in keep:
            v = r.get(k, "")
            if k == "crop_path" and v:
                v = Path(v).name
            s.append(f"<td>{html.escape(str(v))}</td>")
        s.append("</tr>")

    s.append("</tbody></table>")
    return "".join(s)


def main():
    rows = list(csv.DictReader(AUDIT.open(), delimiter="\t"))

    first30 = rows[:30]
    review = [r for r in rows if r["status"] == "REVIEW"]

    selected = []
    seen = set()

    for r in review + first30:
        sid = r["specimen_id"]
        if sid not in seen:
            selected.append(r)
            seen.add(sid)

    selected = selected[:80]

    css = """
    body { font-family: Arial, sans-serif; margin: 24px; background: #f6f6f6; color: #222; }
    h1 { margin-bottom: 0; }
    .meta { color: #666; margin-top: 4px; }
    .card { background: white; border-radius: 12px; padding: 16px; margin: 18px 0; box-shadow: 0 1px 6px rgba(0,0,0,0.12); }
    .status { display:inline-block; padding: 3px 8px; border-radius: 999px; font-size: 12px; background: #eee; }
    .review { background: #ffe3e3; }
    .pass { background: #ddf7df; }
    .grid { display: grid; grid-template-columns: minmax(320px, 1.3fr) minmax(320px, 1fr); gap: 16px; align-items: start; }
    img { max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 8px; background: #fafafa; }
    .crops { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
    .cropbox { font-size: 12px; color: #555; }
    table { border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 12px; }
    th, td { border: 1px solid #ddd; padding: 4px 6px; vertical-align: top; }
    th { background: #f0f0f0; }
    .missing { padding: 20px; background: #ffecec; color: #900; border-radius: 8px; }
    .muted { color: #777; }
    code { background: #f0f0f0; padding: 1px 4px; border-radius: 4px; }
    """

    out = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Main500 v4 visual gallery</title>",
        f"<style>{css}</style></head><body>",
        "<h1>Main500 v4 visual gallery</h1>",
        f"<p class='meta'>Selected {len(selected)} specimens: REVIEW cases first, then first 30 manifest rows. "
        "Green/cyan thin boxes are raw YOLOE boxes; blue/cyan candidate panels; red main queue; purple rescue queue.</p>",
    ]

    for r in selected:
        sid = r["specimen_id"]
        overlay = Path(r["overlay_path"])
        queue = Path(r["queue_path"])
        qrows = read_queue(queue)

        status_class = "review" if r["status"] == "REVIEW" else "pass"

        out.append("<div class='card'>")
        out.append(
            f"<h2>{html.escape(r['task_id'])}. {html.escape(sid)} "
            f"<span class='status {status_class}'>{html.escape(r['status'])}</span></h2>"
        )
        out.append(
            f"<p class='meta'>final={r['final_queue_count']} "
            f"main={r['main_queue_count']} rescue={r['rescue_queue_count']} "
            f"aux={r['auxiliary_panel_count']} raw={r['raw_box_count']} "
            f"reasons={html.escape(r['review_reasons'])}</p>"
        )
        out.append("<div class='grid'>")
        out.append("<div>")
        out.append("<h3>Overlay</h3>")
        out.append(b64_img(overlay))
        out.append("</div>")

        out.append("<div>")
        out.append("<h3>Final OCR crops</h3>")
        out.append("<div class='crops'>")
        for q in qrows:
            crop = Path(q.get("crop_path", ""))
            out.append("<div class='cropbox'>")
            out.append(f"<div><b>{html.escape(q.get('queue_id',''))}</b> {html.escape(q.get('queue_stage',''))}</div>")
            out.append(b64_img(crop, max_bytes=3_000_000))
            out.append("</div>")
        out.append("</div>")
        out.append("</div>")
        out.append("</div>")

        out.append("<h3>Queue table</h3>")
        out.append(table(qrows))
        out.append("</div>")

    out.append("</body></html>")

    OUT_HTML.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {OUT_HTML}")
    print(f"selected={len(selected)}")
    print(f"size_mb={OUT_HTML.stat().st_size / 1024 / 1024:.2f}")


if __name__ == "__main__":
    main()
