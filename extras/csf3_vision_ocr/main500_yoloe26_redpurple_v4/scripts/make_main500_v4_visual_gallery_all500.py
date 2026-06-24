#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
from pathlib import Path
from PIL import Image

BLOCK = Path("/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier")
AUDIT = BLOCK / "03_audit/main500_v4_output_audit.tsv"


def data_img_resized(path: Path, max_width: int, quality: int = 72) -> str:
    if not path.is_file():
        return "<div class='missing'>missing image</div>"

    try:
        im = Image.open(path).convert("RGB")
        if im.width > max_width:
            ratio = max_width / im.width
            im = im.resize((max_width, max(1, int(im.height * ratio))), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"<img src='data:image/jpeg;base64,{data}' loading='lazy'/>"
    except Exception as e:
        return f"<div class='missing'>image error: {html.escape(str(e))}</div>"


def read_queue(path: Path):
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def queue_table(qrows):
    keep = [
        "queue_id",
        "queue_stage",
        "region_type",
        "area_fraction",
        "covered_unit_n",
        "coverage_gain_n",
        "crop_reason",
    ]

    if not qrows:
        return "<p class='muted'>No queue rows.</p>"

    out = ["<table><thead><tr>"]
    for k in keep:
        out.append(f"<th>{html.escape(k)}</th>")
    out.append("</tr></thead><tbody>")

    for r in qrows:
        out.append("<tr>")
        for k in keep:
            v = r.get(k, "")
            if k == "area_fraction" and v:
                try:
                    v = f"{float(v):.4f}"
                except Exception:
                    pass
            out.append(f"<td>{html.escape(str(v))}</td>")
        out.append("</tr>")

    out.append("</tbody></table>")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-html", default=str(BLOCK / "03_audit/main500_v4_visual_gallery_ALL500_selfcontained.html"))
    ap.add_argument("--overlay-width", type=int, default=1200)
    ap.add_argument("--crop-width", type=int, default=360)
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--sort", default="manifest", choices=["manifest", "review_first", "final_queue_desc", "rescue_desc", "aux_desc"])
    args = ap.parse_args()

    rows = list(csv.DictReader(AUDIT.open(), delimiter="\t"))

    if args.sort == "review_first":
        rows.sort(key=lambda r: (r["status"] != "REVIEW", int(r["task_id"])))
    elif args.sort == "final_queue_desc":
        rows.sort(key=lambda r: (-int(r["final_queue_count"]), int(r["task_id"])))
    elif args.sort == "rescue_desc":
        rows.sort(key=lambda r: (-int(r["rescue_queue_count"]), int(r["task_id"])))
    elif args.sort == "aux_desc":
        rows.sort(key=lambda r: (-int(r["auxiliary_panel_count"]), int(r["task_id"])))
    else:
        rows.sort(key=lambda r: int(r["task_id"]))

    css = """
    body {
      font-family: Arial, sans-serif;
      margin: 24px;
      background: #f6f6f6;
      color: #222;
    }
    h1 { margin-bottom: 4px; }
    .meta { color: #666; font-size: 14px; }
    .toolbar {
      position: static;
      top: auto;
      z-index: auto;
      background: #ffffffee;
      border: 1px solid #ddd;
      border-radius: 12px;
      padding: 10px 12px;
      margin: 12px 0 20px 0;
      box-shadow: 0 1px 5px rgba(0,0,0,0.12);
    }
    .toolbar a {
      display: inline-block;
      margin-right: 8px;
      margin-bottom: 6px;
      padding: 4px 8px;
      background: #eee;
      border-radius: 999px;
      color: #333;
      text-decoration: none;
      font-size: 12px;
    }
    .card {
      background: white;
      border-radius: 14px;
      padding: 16px;
      margin: 20px 0;
      box-shadow: 0 1px 8px rgba(0,0,0,0.12);
    }
    .status {
      display: inline-block;
      padding: 3px 9px;
      border-radius: 999px;
      font-size: 12px;
      vertical-align: middle;
    }
    .review { background: #ffe1e1; }
    .pass { background: #dcf7dd; }
    .grid {
      display: grid;
      grid-template-columns: minmax(420px, 1.25fr) minmax(360px, 1fr);
      gap: 16px;
      align-items: start;
    }
    img {
      max-width: 100%;
      height: auto;
      border: 1px solid #ddd;
      border-radius: 8px;
      background: #fafafa;
    }
    .crops {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 10px;
    }
    .cropbox {
      font-size: 12px;
      color: #555;
      border: 1px solid #eee;
      border-radius: 8px;
      padding: 6px;
      background: #fafafa;
    }
    table {
      border-collapse: collapse;
      width: 100%;
      font-size: 12px;
      margin-top: 12px;
    }
    th, td {
      border: 1px solid #ddd;
      padding: 4px 6px;
      vertical-align: top;
    }
    th { background: #f0f0f0; }
    code {
      background: #f0f0f0;
      padding: 1px 4px;
      border-radius: 4px;
    }
    .missing {
      padding: 18px;
      background: #ffecec;
      color: #900;
      border-radius: 8px;
      font-size: 12px;
    }
    .muted { color: #777; }
    .smallpath {
      font-size: 11px;
      color: #777;
      word-break: break-all;
    }
    @media (max-width: 1100px) {
      .grid { grid-template-columns: 1fr; }
    }
    """

    status_counts = {}
    for r in rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1

    out = [
        "<!doctype html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>Main500 v4 ALL500 visual gallery</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body>",
        "<h1>Main500 v4 ALL500 visual gallery</h1>",
        f"<p class='meta'>All {len(rows)} specimens. "
        f"Status counts: {html.escape(str(status_counts))}. "
        "Green/cyan thin boxes = raw YOLOE boxes; blue/cyan panels = candidates/auxiliary; red = main OCR queue; purple = rescue OCR queue.</p>",
        "<div class='toolbar'>",
        "<b>Jump:</b> ",
    ]

    for r in rows:
        sid = r["specimen_id"]
        label = f"{r['task_id']}:{sid}"
        out.append(f"<a href='#sid-{html.escape(r['task_id'])}'>{html.escape(label)}</a>")

    out.append("</div>")

    for i, r in enumerate(rows, start=1):
        sid = r["specimen_id"]
        task_id = r["task_id"]
        status_class = "review" if r["status"] == "REVIEW" else "pass"

        overlay = Path(r["overlay_path"])
        qpath = Path(r["queue_path"])
        qrows = read_queue(qpath)

        out.append(f"<div class='card' id='sid-{html.escape(task_id)}'>")
        out.append(
            f"<h2>{html.escape(task_id)}. {html.escape(sid)} "
            f"<span class='status {status_class}'>{html.escape(r['status'])}</span></h2>"
        )

        out.append(
            "<p class='meta'>"
            f"final={html.escape(r['final_queue_count'])} | "
            f"main={html.escape(r['main_queue_count'])} | "
            f"rescue={html.escape(r['rescue_queue_count'])} | "
            f"auxiliary={html.escape(r['auxiliary_panel_count'])} | "
            f"raw={html.escape(r['raw_box_count'])} | "
            f"max_crop_area={html.escape(r['max_final_crop_area_fraction'])} | "
            f"review_reasons={html.escape(r['review_reasons'])}"
            "</p>"
        )

        out.append("<div class='grid'>")

        out.append("<div>")
        out.append("<h3>Overlay</h3>")
        out.append(data_img_resized(overlay, max_width=args.overlay_width, quality=args.quality))
        out.append(f"<div class='smallpath'>{html.escape(str(overlay))}</div>")
        out.append("</div>")

        out.append("<div>")
        out.append("<h3>Final OCR crops</h3>")
        out.append("<div class='crops'>")
        for q in qrows:
            crop = Path(q.get("crop_path", ""))
            out.append("<div class='cropbox'>")
            out.append(
                f"<div><b>{html.escape(q.get('queue_id',''))}</b> "
                f"{html.escape(q.get('queue_stage',''))}<br>"
                f"{html.escape(q.get('region_type',''))}</div>"
            )
            out.append(data_img_resized(crop, max_width=args.crop_width, quality=args.quality))
            out.append("</div>")
        out.append("</div>")
        out.append("</div>")

        out.append("</div>")

        out.append("<h3>Queue table</h3>")
        out.append(queue_table(qrows))

        out.append("<p class='smallpath'>")
        out.append(f"queue: {html.escape(str(qpath))}<br>")
        out.append(f"source image: {html.escape(r.get('image_path',''))}")
        out.append("</p>")

        out.append("</div>")

        if i % 25 == 0:
            print(f"processed {i}/{len(rows)}")

    out.append("</body></html>")

    out_html = Path(args.out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text("\n".join(out), encoding="utf-8")

    print(f"wrote {out_html}")
    print(f"rows={len(rows)}")
    print(f"size_mb={out_html.stat().st_size / 1024 / 1024:.2f}")


if __name__ == "__main__":
    main()
