#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

BLOCK = Path("/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier")
MANIFEST = BLOCK / "00_manifest/main500_local_images.tsv"
OUT_TSV = BLOCK / "03_audit/main500_v4_output_audit.tsv"


def read_tsv(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def count_queue_stages(qpath: Path):
    counts = {"main": 0, "rescue": 0}
    max_area = 0.0
    if not qpath.is_file():
        return counts, max_area
    with qpath.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            stage = r.get("queue_stage", "")
            if stage in counts:
                counts[stage] += 1
            try:
                max_area = max(max_area, float(r.get("area_fraction", 0) or 0))
            except Exception:
                pass
    return counts, max_area


def main():
    rows = read_tsv(MANIFEST)
    out = []

    for r in rows:
        sid = r["specimen_id"]
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in sid).strip("_") or "unknown"
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

        stage_counts, max_crop_area = count_queue_stages(queue_path)
        crop_count = len(list(crop_dir.glob("*.jpg"))) if crop_dir.is_dir() else 0

        final_queue_count = int(summary.get("final_queue_count", -1))
        raw_box_count = int(summary.get("raw_box_count", -1))
        deduped_box_count = int(summary.get("deduped_box_count", -1))
        text_panel_count = int(summary.get("text_panel_count", -1))
        aux_panel_count = int(summary.get("auxiliary_panel_count", -1))
        uncovered_unit_count = int(summary.get("uncovered_unit_count", -1))

        status = "PASS"
        review_reasons = []

        if not marker.is_file():
            status = "REVIEW"
            review_reasons.append("missing_marker")
        if not queue_path.is_file():
            status = "REVIEW"
            review_reasons.append("missing_final_queue")
        if not overlay_path.is_file():
            status = "REVIEW"
            review_reasons.append("missing_overlay")
        if final_queue_count <= 0:
            status = "REVIEW"
            review_reasons.append("zero_final_queue")
        if uncovered_unit_count > 0:
            status = "REVIEW"
            review_reasons.append("uncovered_text_units")
        if final_queue_count >= 12:
            status = "REVIEW"
            review_reasons.append("many_final_crops")
        if stage_counts["rescue"] >= 5:
            status = "REVIEW"
            review_reasons.append("many_rescue_crops")
        if aux_panel_count >= 10:
            status = "REVIEW"
            review_reasons.append("many_auxiliary_panels")
        if raw_box_count < 80:
            status = "REVIEW"
            review_reasons.append("low_raw_box_count")
        if max_crop_area > 0.35:
            status = "REVIEW"
            review_reasons.append("oversized_final_crop")

        out.append({
            "task_id": r.get("task_id", ""),
            "specimen_id": sid,
            "status": status,
            "review_reasons": ";".join(review_reasons),
            "raw_box_count": raw_box_count,
            "deduped_box_count": deduped_box_count,
            "text_panel_count": text_panel_count,
            "auxiliary_panel_count": aux_panel_count,
            "main_queue_count": stage_counts["main"],
            "rescue_queue_count": stage_counts["rescue"],
            "final_queue_count": final_queue_count,
            "crop_count": crop_count,
            "uncovered_unit_count": uncovered_unit_count,
            "max_final_crop_area_fraction": max_crop_area,
            "overlay_path": str(overlay_path),
            "queue_path": str(queue_path),
            "auxiliary_path": str(aux_path),
            "image_path": r.get("image_path", ""),
        })

    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    fields = list(out[0].keys())
    with OUT_TSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(out)

    print(f"wrote {OUT_TSV}")
    print(f"rows={len(out)}")
    print("status counts:")
    from collections import Counter
    print(Counter(r["status"] for r in out))
    print("top review reasons:")
    c = Counter()
    for r in out:
        for x in r["review_reasons"].split(";"):
            if x:
                c[x] += 1
    print(c.most_common(20))


if __name__ == "__main__":
    main()
