#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

BLOCK = Path("/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier")
MANIFEST = BLOCK / "00_manifest/main500_local_images.tsv"
OUT_TSV = BLOCK / "03_audit/main500_v4_output_audit.tsv"


def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(s)).strip("_") or "unknown"


def count_queue(path: Path):
    main = rescue = 0
    max_area = 0.0
    rows = 0
    if not path.is_file():
        return rows, main, rescue, max_area

    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows += 1
            if r.get("queue_stage") == "main":
                main += 1
            elif r.get("queue_stage") == "rescue":
                rescue += 1
            try:
                max_area = max(max_area, float(r.get("area_fraction", 0) or 0))
            except Exception:
                pass
    return rows, main, rescue, max_area


def main():
    with MANIFEST.open(newline="", encoding="utf-8") as f:
        manifest_rows = list(csv.DictReader(f, delimiter="\t"))

    out = []

    for r in manifest_rows:
        task_id = r.get("task_id", "")
        sid = r.get("specimen_id", "")
        safe = safe_name(sid)

        odir = BLOCK / "02_boxlogic" / safe
        summary_path = odir / "06_audit/boxlogic_v4_auxbarrier_summary.json"
        queue_path = odir / "05_final_queue/final_ocr_queue.tsv"
        aux_path = odir / "04_candidate_panels/auxiliary_barrier_panels.tsv"
        overlay_path = odir / "05_final_queue/final_queue_overlay.png"
        crop_dir = odir / "05_final_queue/crops"
        marker = BLOCK / "markers" / f"{safe}.V4_AUXBARRIER.PASS"

        summary = {}
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text())
            except Exception:
                summary = {}

        queue_rows, main_n, rescue_n, max_area = count_queue(queue_path)
        crop_count = len(list(crop_dir.glob("*.jpg"))) if crop_dir.is_dir() else 0

        raw_box_count = int(summary.get("raw_box_count", -1))
        deduped_box_count = int(summary.get("deduped_box_count", -1))
        text_panel_count = int(summary.get("text_panel_count", -1))
        auxiliary_panel_count = int(summary.get("auxiliary_panel_count", -1))
        final_queue_count = int(summary.get("final_queue_count", queue_rows if queue_path.is_file() else -1))
        uncovered_unit_count = int(summary.get("uncovered_unit_count", -1))

        status = "PASS"
        reasons = []

        if not marker.is_file():
            status = "REVIEW"; reasons.append("missing_marker")
        if not queue_path.is_file():
            status = "REVIEW"; reasons.append("missing_final_queue")
        if not overlay_path.is_file():
            status = "REVIEW"; reasons.append("missing_overlay")
        if final_queue_count <= 0:
            status = "REVIEW"; reasons.append("zero_final_queue")
        if uncovered_unit_count > 0:
            status = "REVIEW"; reasons.append("uncovered_text_units")
        if final_queue_count >= 12:
            status = "REVIEW"; reasons.append("many_final_crops")
        if rescue_n >= 5:
            status = "REVIEW"; reasons.append("many_rescue_crops")
        if auxiliary_panel_count >= 10:
            status = "REVIEW"; reasons.append("many_auxiliary_panels")
        if raw_box_count != -1 and raw_box_count < 80:
            status = "REVIEW"; reasons.append("low_raw_box_count")
        if max_area > 0.35:
            status = "REVIEW"; reasons.append("oversized_final_crop")

        out.append({
            "task_id": task_id,
            "specimen_id": sid,
            "status": status,
            "review_reasons": ";".join(reasons),
            "raw_box_count": raw_box_count,
            "deduped_box_count": deduped_box_count,
            "text_panel_count": text_panel_count,
            "auxiliary_panel_count": auxiliary_panel_count,
            "main_queue_count": main_n,
            "rescue_queue_count": rescue_n,
            "final_queue_count": final_queue_count,
            "crop_count": crop_count,
            "uncovered_unit_count": uncovered_unit_count,
            "max_final_crop_area_fraction": max_area,
            "overlay_path": str(overlay_path),
            "queue_path": str(queue_path),
            "auxiliary_path": str(aux_path),
            "image_path": r.get("image_path", ""),
        })

    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_TSV.open("w", newline="", encoding="utf-8") as f:
        fields = list(out[0].keys())
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(out)

    print(f"manifest_rows={len(manifest_rows)}")
    print(f"audit_rows={len(out)}")
    print(f"wrote={OUT_TSV}")
    print("status_counts:", Counter(x["status"] for x in out))

    missing = [x for x in out if "missing_final_queue" in x["review_reasons"] or "missing_marker" in x["review_reasons"]]
    print(f"missing_or_incomplete={len(missing)}")
    for x in missing[:20]:
        print(x["task_id"], x["specimen_id"], x["review_reasons"])


if __name__ == "__main__":
    main()
