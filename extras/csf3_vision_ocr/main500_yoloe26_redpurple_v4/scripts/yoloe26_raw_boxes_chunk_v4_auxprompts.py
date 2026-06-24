#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from PIL import Image, ImageDraw


TEXT_PROMPTS = [
    "primary specimen label",
    "printed label",
    "annotation label",
    "handwritten note",
    "database label",
    "barcode label",
    "catalog number",
    "type specimen label",
    "herbarium stamp",
]

AUX_PROMPTS = [
    "color calibration chart",
    "colour chart",
    "color checker",
    "color bar",
    "colour bar",
    "calibration color map",
    "ruler",
    "scale bar",
    "measurement scale",
    "blank paper label",
    "empty label",
    "institution logo",
]

PROMPTS = TEXT_PROMPTS + AUX_PROMPTS
PROMPT_GROUP = {p: "text" for p in TEXT_PROMPTS}
PROMPT_GROUP.update({p: "auxiliary" for p in AUX_PROMPTS})


def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(s)).strip("_") or "unknown"


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def write_tsv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = dict(r)
            for k, v in list(out.items()):
                if isinstance(v, (list, dict)):
                    out[k] = json.dumps(v, ensure_ascii=False)
            w.writerow(out)


def draw_raw_overlay(image_path: Path, rows: list[dict], out_path: Path):
    im0 = Image.open(image_path).convert("RGB")
    scale = min(1.0, 2400 / max(im0.size))
    im = im0.resize((round(im0.width * scale), round(im0.height * scale)), Image.Resampling.LANCZOS) if scale < 1 else im0.copy()
    draw = ImageDraw.Draw(im)

    for r in rows:
        b = [round(x * scale) for x in r["bbox_xyxy"]]
        color = (0, 210, 80) if r["prompt_group"] == "text" else (0, 190, 220)
        draw.rectangle(b, outline=color, width=1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)


def read_manifest(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f, delimiter="\t")]


def run_one(model, hit: dict, args) -> dict:
    specimen_id = hit["specimen_id"]
    safe_sid = safe_name(specimen_id)
    image_path = Path(hit["image_path"])

    if not image_path.is_file():
        raise RuntimeError(f"image missing: {image_path}")

    outdir = Path(args.out_root) / safe_sid
    outdir.mkdir(parents=True, exist_ok=True)

    raw_tsv = outdir / "raw_yoloe_boxes.tsv"
    overlay = outdir / "raw_yoloe_overlay.png"
    summary_json = outdir / "raw_yoloe_summary.json"

    if raw_tsv.is_file() and summary_json.is_file():
        summary = json.loads(summary_json.read_text(encoding="utf-8"))
        summary["status"] = "SKIP_RAW_EXISTS"
        return summary

    with Image.open(image_path) as im:
        width, height = im.size

    print(f"[YOLOE] {specimen_id} image={image_path} size={width}x{height}", flush=True)

    results = model.predict(
        source=str(image_path),
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.iou,
        max_det=args.max_det,
        agnostic_nms=False,
        device=args.device,
        verbose=False,
        save=False,
    )

    rows = []
    result = results[0]

    if result.boxes is not None and len(result.boxes) > 0:
        xyxy = result.boxes.xyxy.detach().cpu().numpy()
        confs = result.boxes.conf.detach().cpu().numpy()
        clss = result.boxes.cls.detach().cpu().numpy().astype(int)

        order = sorted(range(len(confs)), key=lambda i: float(confs[i]), reverse=True)

        for rank, i in enumerate(order, start=1):
            b = [int(round(x)) for x in xyxy[i].tolist()]
            cid = int(clss[i])
            cls_name = PROMPTS[cid] if 0 <= cid < len(PROMPTS) else str(cid)
            group = PROMPT_GROUP.get(cls_name, "unknown")
            a = area(b)

            rows.append({
                "specimen_id": specimen_id,
                "source_row": hit.get("source_row", ""),
                "occurrenceID": hit.get("occurrenceID", ""),
                "image_path": str(image_path),
                "raw_box_id": f"Y{rank:04d}",
                "box_id": f"Y{rank:04d}",
                "rank": rank,
                "prompt_class": cls_name,
                "prompt_group": group,
                "class_id": cid,
                "confidence": round(float(confs[i]), 8),
                "bbox_xyxy": b,
                "source_width": width,
                "source_height": height,
                "area_fraction": a / (width * height),
                "center_x": ((b[0] + b[2]) / 2) / width,
                "center_y": ((b[1] + b[3]) / 2) / height,
                "aspect_ratio": (b[2] - b[0]) / max(1, b[3] - b[1]),
            })

    fields = [
        "specimen_id", "source_row", "occurrenceID", "image_path",
        "raw_box_id", "box_id", "rank", "prompt_class", "prompt_group", "class_id",
        "confidence", "bbox_xyxy", "source_width", "source_height",
        "area_fraction", "center_x", "center_y", "aspect_ratio",
    ]

    write_tsv(raw_tsv, rows, fields)
    draw_raw_overlay(image_path, rows, overlay)

    summary = {
        "status": "PASS_RAW_YOLOE",
        "specimen_id": specimen_id,
        "task_id": hit.get("task_id", ""),
        "image_path": str(image_path),
        "raw_tsv": str(raw_tsv),
        "overlay": str(overlay),
        "box_count": len(rows),
        "prompt_count": len(PROMPTS),
        "text_prompt_count": len(TEXT_PROMPTS),
        "aux_prompt_count": len(AUX_PROMPTS),
        "imgsz": args.imgsz,
        "conf": args.conf,
        "iou": args.iou,
        "max_det": args.max_det,
        "device": args.device,
    }

    summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--chunk-id", type=int, required=True)
    ap.add_argument("--chunk-size", type=int, default=10)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--conf", type=float, default=0.01)
    ap.add_argument("--iou", type=float, default=0.90)
    ap.add_argument("--max-det", type=int, default=1000)
    ap.add_argument("--device", default="0")
    args = ap.parse_args()

    import torch
    from ultralytics import YOLOE

    rows = read_manifest(Path(args.manifest))
    start = args.chunk_id * args.chunk_size
    end = min(start + args.chunk_size, len(rows))
    chunk_rows = rows[start:end]

    if not chunk_rows:
        raise SystemExit(f"No rows for chunk_id={args.chunk_id}, chunk_size={args.chunk_size}, manifest_rows={len(rows)}")

    print(f"[INFO] manifest_rows={len(rows)} chunk_id={args.chunk_id} chunk_size={args.chunk_size} start={start} end={end}", flush=True)
    print(f"[INFO] torch_cuda={torch.cuda.is_available()} device={args.device}", flush=True)
    print(f"[INFO] prompt_count={len(PROMPTS)}", flush=True)

    model = YOLOE(args.weights)
    model.set_classes(PROMPTS, model.get_text_pe(PROMPTS))

    summaries = []
    for hit in chunk_rows:
        try:
            summary = run_one(model, hit, args)
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
        except Exception as exc:
            fail = {
                "status": f"FAIL_{type(exc).__name__}",
                "specimen_id": hit.get("specimen_id", ""),
                "task_id": hit.get("task_id", ""),
                "error": str(exc),
            }
            summaries.append(fail)
            print(json.dumps(fail, ensure_ascii=False), flush=True)

    failed = [s for s in summaries if not str(s.get("status", "")).startswith(("PASS", "SKIP"))]
    if failed:
        raise SystemExit(f"Chunk completed with failures: {len(failed)} / {len(summaries)}")


if __name__ == "__main__":
    main()
