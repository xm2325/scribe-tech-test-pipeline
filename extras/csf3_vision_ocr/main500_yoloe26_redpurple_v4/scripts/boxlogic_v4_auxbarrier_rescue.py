#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


TEXT_CLASSES = {
    "primary specimen label",
    "printed label",
    "annotation label",
    "handwritten note",
    "database label",
    "barcode label",
    "catalog number",
    "type specimen label",
    "herbarium stamp",
}

AUX_CLASSES = {
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
}

CLASS_WEIGHT = {
    "primary specimen label": 1.8,
    "printed label": 1.5,
    "annotation label": 1.5,
    "handwritten note": 1.9,
    "database label": 1.0,
    "barcode label": 1.5,
    "catalog number": 1.8,
    "type specimen label": 1.7,
    "herbarium stamp": 0.45,
}


def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(s)).strip("_") or "unknown"


def parse_box(v):
    if isinstance(v, list):
        return [float(x) for x in v]
    return [float(x) for x in json.loads(v)]


def clamp_box(box, w, h):
    x0, y0, x1, y1 = [int(round(float(x))) for x in box]
    x0 = max(0, min(x0, w - 1))
    y0 = max(0, min(y0, h - 1))
    x1 = max(x0 + 1, min(x1, w))
    y1 = max(y0 + 1, min(y1, h))
    return [x0, y0, x1, y1]


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def intersection(a, b):
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    return max(0, x1 - x0) * max(0, y1 - y0)


def iou(a, b):
    inter = intersection(a, b)
    den = area(a) + area(b) - inter
    return inter / den if den > 0 else 0.0


def ioa(a, b):
    aa = area(a)
    return intersection(a, b) / aa if aa > 0 else 0.0


def center(b):
    return 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])


def union_box(boxes):
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def expand_box(b, w, h, pad_frac=0.08, min_pad=16):
    bw = b[2] - b[0]
    bh = b[3] - b[1]
    px = max(min_pad, int(round(bw * pad_frac)))
    py = max(min_pad, int(round(bh * pad_frac)))
    return clamp_box([b[0] - px, b[1] - py, b[2] + px, b[3] + py], w, h)


def intersects(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.p[rb] = ra


def read_raw(path: Path):
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for i, r in enumerate(reader, start=1):
            r = dict(r)
            box_id = r.get("raw_box_id") or r.get("box_id") or f"Y{i:04d}"
            cls = r.get("prompt_class", "")
            group = r.get("prompt_group", "")

            if not group:
                if cls in AUX_CLASSES:
                    group = "auxiliary"
                elif cls in TEXT_CLASSES:
                    group = "text"
                else:
                    group = "unknown"

            rows.append({
                "specimen_id": r.get("specimen_id", ""),
                "image_path": r.get("image_path", ""),
                "box_id": box_id,
                "rank": int(float(r.get("rank", i) or i)),
                "prompt_class": cls,
                "prompt_group": group,
                "confidence": float(r.get("confidence", 0) or 0),
                "bbox_xyxy": [int(round(x)) for x in parse_box(r["bbox_xyxy"])],
                "source_width": int(float(r.get("source_width", 0) or 0)),
                "source_height": int(float(r.get("source_height", 0) or 0)),
            })

    if not rows:
        raise SystemExit(f"No rows in {path}")

    image_path = Path(rows[0]["image_path"])
    if not image_path.is_file():
        raise SystemExit(f"Missing image: {image_path}")

    with Image.open(image_path) as im:
        w, h = im.size

    for r in rows:
        r["source_width"] = w
        r["source_height"] = h
        r["bbox_xyxy"] = clamp_box(r["bbox_xyxy"], w, h)
        b = r["bbox_xyxy"]
        a = area(b)
        cx, cy = center(b)
        r["area_fraction"] = a / (w * h)
        r["center_x"] = cx / w
        r["center_y"] = cy / h
        r["aspect_ratio"] = (b[2] - b[0]) / max(1, b[3] - b[1])
        r["is_text"] = r["prompt_group"] == "text" or r["prompt_class"] in TEXT_CLASSES
        r["is_auxiliary"] = r["prompt_group"] == "auxiliary" or r["prompt_class"] in AUX_CLASSES
        r["class_weight"] = CLASS_WEIGHT.get(r["prompt_class"], 0.8)

    return rows


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


def dedupe_rows(rows):
    # Soft dedupe: keep text and auxiliary duplicate tracks separately.
    n = len(rows)
    dsu = DSU(n)

    for i in range(n):
        for j in range(i + 1, n):
            a = rows[i]
            b = rows[j]
            same_class = a["prompt_class"] == b["prompt_class"]
            same_group = a["prompt_group"] == b["prompt_group"]

            iou_ab = iou(a["bbox_xyxy"], b["bbox_xyxy"])

            if same_class and iou_ab >= 0.85:
                dsu.union(i, j)
            elif same_group and iou_ab >= 0.92:
                dsu.union(i, j)

    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[dsu.find(i)].append(r)

    keep_ids = set()
    dup_of = {}

    def score(r):
        return (
            1 if r["is_text"] else 0,
            1 if r["prompt_class"] != "herbarium stamp" else 0,
            r["class_weight"],
            r["confidence"],
            -r["rank"],
        )

    for group in groups.values():
        rep = max(group, key=score)
        keep_ids.add(rep["box_id"])
        for r in group:
            if r["box_id"] != rep["box_id"]:
                dup_of[r["box_id"]] = rep["box_id"]

    audit = []
    deduped = []
    for r in rows:
        rr = dict(r)
        rr["duplicate_of"] = dup_of.get(r["box_id"], "")
        rr["is_duplicate_suppressed"] = bool(rr["duplicate_of"])

        # Text seed: only text prompts. Auxiliary is never allowed to bridge text panels.
        rr["text_seed"] = (
            not rr["is_duplicate_suppressed"]
            and rr["is_text"]
            and rr["confidence"] >= 0.005
            and rr["area_fraction"] <= 0.45
        )

        # Aux seed: retained as barrier/audit, not deleted.
        rr["auxiliary_seed"] = (
            not rr["is_duplicate_suppressed"]
            and rr["is_auxiliary"]
            and rr["confidence"] >= 0.005
            and rr["area_fraction"] <= 0.70
        )

        audit.append(rr)
        if not rr["is_duplicate_suppressed"]:
            deduped.append(rr)

    return deduped, audit


def connected_components(seed_rows, w, h, pad_frac=0.08, min_pad=16):
    if not seed_rows:
        return []

    exp = [expand_box(r["bbox_xyxy"], w, h, pad_frac=pad_frac, min_pad=min_pad) for r in seed_rows]
    dsu = DSU(len(seed_rows))

    for i in range(len(seed_rows)):
        for j in range(i + 1, len(seed_rows)):
            if intersects(exp[i], exp[j]):
                dsu.union(i, j)

    groups = defaultdict(list)
    for i, r in enumerate(seed_rows):
        groups[dsu.find(i)].append(r)

    return list(groups.values())


def split_oversized_text_group(group, w, h):
    ub = union_box([r["bbox_xyxy"] for r in group])
    area_f = area(ub) / (w * h)

    if area_f <= 0.35:
        return [group]

    # Recluster with tight pad to split distant text islands.
    tight = connected_components(group, w, h, pad_frac=0.02, min_pad=6)

    if len(tight) > 1:
        return tight

    return [group]


def build_panels(deduped, w, h):
    text_seeds = [r for r in deduped if r["text_seed"]]
    aux_seeds = [r for r in deduped if r["auxiliary_seed"]]

    panels = []

    text_groups = []
    for group in connected_components(text_seeds, w, h, pad_frac=0.08, min_pad=16):
        text_groups.extend(split_oversized_text_group(group, w, h))

    for group in text_groups:
        ub = clamp_box(union_box([r["bbox_xyxy"] for r in group]), w, h)
        counts = Counter(r["prompt_class"] for r in group)
        panel = {
            "candidate_panel_id": f"P{len(panels)+1:04d}",
            "panel_kind": "text",
            "bbox_xyxy": ub,
            "area_fraction": area(ub) / (w * h),
            "n_child_boxes": len(group),
            "best_confidence": max(r["confidence"] for r in group),
            "mean_confidence": sum(r["confidence"] for r in group) / len(group),
            "child_box_ids": [r["box_id"] for r in group],
            "class_counts": dict(counts),
            "child_rows": group,
        }

        if panel["area_fraction"] > 0.35:
            role = "oversized_text_panel_shrink_required"
        elif panel["n_child_boxes"] >= 6:
            role = "main_text_label_panel"
        elif panel["n_child_boxes"] >= 2:
            role = "small_text_label_panel"
        else:
            role = "isolated_text_label_panel"

        panel["panel_role"] = role
        panel["panel_score"] = score_panel(panel)
        panels.append(panel)

    for group in connected_components(aux_seeds, w, h, pad_frac=0.05, min_pad=10):
        ub = clamp_box(union_box([r["bbox_xyxy"] for r in group]), w, h)
        counts = Counter(r["prompt_class"] for r in group)
        panel = {
            "candidate_panel_id": f"P{len(panels)+1:04d}",
            "panel_kind": "auxiliary",
            "bbox_xyxy": ub,
            "area_fraction": area(ub) / (w * h),
            "n_child_boxes": len(group),
            "best_confidence": max(r["confidence"] for r in group),
            "mean_confidence": sum(r["confidence"] for r in group) / len(group),
            "child_box_ids": [r["box_id"] for r in group],
            "class_counts": dict(counts),
            "child_rows": group,
            "panel_role": "auxiliary_barrier_panel",
        }
        panel["panel_score"] = score_panel(panel)
        panels.append(panel)

    panels.sort(key=lambda p: (p["panel_kind"] != "text", -p["panel_score"], -p["n_child_boxes"], p["area_fraction"]))
    for i, p in enumerate(panels, start=1):
        p["candidate_panel_id"] = f"P{i:04d}"

    return panels


def score_panel(panel):
    score = math.log1p(panel["n_child_boxes"]) + 3.0 * panel["best_confidence"]

    for cls, n in panel["class_counts"].items():
        score += CLASS_WEIGHT.get(cls, 0.8) * n

    if panel["panel_kind"] == "auxiliary":
        score *= 0.25

    if panel["area_fraction"] > 0.35:
        score *= 0.25

    return score


def final_crop_for_text_panel(panel, w, h):
    text_rows = [r for r in panel["child_rows"] if r["is_text"]]
    if not text_rows:
        return panel["bbox_xyxy"], "panel_bbox_no_text_children"

    ub = union_box([r["bbox_xyxy"] for r in text_rows])
    crop = expand_box(ub, w, h, pad_frac=0.10, min_pad=24)
    return crop, "tight_union_of_text_children"


def build_units(deduped):
    units = []
    for r in deduped:
        if not r["is_text"]:
            continue
        if r["confidence"] < 0.005:
            continue

        weight = r["class_weight"] * max(0.2, min(2.0, r["confidence"] / 0.05))
        units.append({
            "unit_id": r["box_id"],
            "bbox_xyxy": r["bbox_xyxy"],
            "weight": weight,
            "prompt_class": r["prompt_class"],
        })
    return units


def units_covered_by_box(box, units):
    return [u["unit_id"] for u in units if ioa(u["bbox_xyxy"], box) >= 0.45]


def panel_covered_by_selected(panel, selected):
    b = panel["final_crop_bbox_xyxy"]
    return any(ioa(b, q["final_crop_bbox_xyxy"]) >= 0.85 for q in selected)


def select_queue(panels, deduped, w, h, coverage_target):
    units = build_units(deduped)
    unit_by_id = {u["unit_id"]: u for u in units}
    total_weight = sum(u["weight"] for u in units) or 1.0

    text_panels = [p for p in panels if p["panel_kind"] == "text"]
    aux_panels = [p for p in panels if p["panel_kind"] == "auxiliary"]

    for p in text_panels:
        crop, reason = final_crop_for_text_panel(p, w, h)
        p["final_crop_bbox_xyxy"] = crop
        p["crop_reason"] = reason
        p["covered_unit_ids"] = units_covered_by_box(crop, units)
        p["covered_unit_weight"] = sum(unit_by_id[x]["weight"] for x in p["covered_unit_ids"])

    selected = []
    covered = set()

    # Main: greedy over text panels only.
    for _ in range(200):
        covered_weight = sum(unit_by_id[x]["weight"] for x in covered)
        if covered_weight / total_weight >= coverage_target:
            break

        best = None
        best_score = -1e18

        for p in text_panels:
            if p.get("_selected"):
                continue

            gain_ids = [x for x in p["covered_unit_ids"] if x not in covered]
            if not gain_ids:
                continue

            gain = sum(unit_by_id[x]["weight"] for x in gain_ids)
            crop_area_f = area(p["final_crop_bbox_xyxy"]) / (w * h)
            area_penalty = 1.0 + 8.0 * max(0.0, crop_area_f - 0.12)

            score = gain / area_penalty + 0.05 * len(gain_ids)

            if crop_area_f > 0.35:
                score *= 0.20

            if score > best_score:
                best = p
                best_score = score

        if best is None:
            break

        best["_selected"] = True
        best["_stage"] = "main"
        best["_gain_ids"] = [x for x in best["covered_unit_ids"] if x not in covered]
        covered.update(best["covered_unit_ids"])
        selected.append(best)

    # Rescue: all uncovered text-like candidate panels not covered by main.
    for p in text_panels:
        if p.get("_selected"):
            continue
        if panel_covered_by_selected(p, selected):
            continue

        p["_selected"] = True
        p["_stage"] = "rescue"
        p["_gain_ids"] = [x for x in p["covered_unit_ids"] if x not in covered]
        covered.update(p["covered_unit_ids"])
        selected.append(p)

    queue = []
    for rank, p in enumerate(selected, start=1):
        q = {
            "queue_id": f"Q{rank:04d}",
            "queue_rank": rank,
            "queue_stage": p["_stage"],
            "candidate_panel_id": p["candidate_panel_id"],
            "region_type": p["panel_role"],
            "panel_kind": p["panel_kind"],
            "panel_bbox_xyxy": p["bbox_xyxy"],
            "final_crop_bbox_xyxy": p["final_crop_bbox_xyxy"],
            "area_fraction": area(p["final_crop_bbox_xyxy"]) / (w * h),
            "panel_area_fraction": p["area_fraction"],
            "panel_score": p["panel_score"],
            "covered_unit_n": len(p["covered_unit_ids"]),
            "coverage_gain_n": len(p.get("_gain_ids", [])),
            "covered_unit_ids_json": json.dumps(p["covered_unit_ids"], ensure_ascii=False),
            "coverage_gain_ids_json": json.dumps(p.get("_gain_ids", []), ensure_ascii=False),
            "class_counts_json": json.dumps(p["class_counts"], ensure_ascii=False, sort_keys=True),
            "crop_reason": p["crop_reason"],
            "reason": f"{p['_stage']}; {p['panel_role']}; {p['crop_reason']}; gain={len(p.get('_gain_ids', []))}",
        }
        queue.append(q)

    aux_rows = []
    for p in aux_panels:
        aux_rows.append({
            "candidate_panel_id": p["candidate_panel_id"],
            "panel_kind": p["panel_kind"],
            "panel_role": p["panel_role"],
            "bbox_xyxy": p["bbox_xyxy"],
            "area_fraction": p["area_fraction"],
            "n_child_boxes": p["n_child_boxes"],
            "best_confidence": p["best_confidence"],
            "class_counts_json": json.dumps(p["class_counts"], ensure_ascii=False, sort_keys=True),
            "child_box_ids_json": json.dumps(p["child_box_ids"], ensure_ascii=False),
            "audit_use": "merge_barrier_not_deleted",
        })

    summary = {
        "text_unit_count": len(units),
        "text_panel_count": len(text_panels),
        "auxiliary_panel_count": len(aux_panels),
        "main_queue_count": sum(1 for q in queue if q["queue_stage"] == "main"),
        "rescue_queue_count": sum(1 for q in queue if q["queue_stage"] == "rescue"),
        "final_queue_count": len(queue),
        "coverage_target": coverage_target,
        "covered_unit_count": len(covered),
        "uncovered_unit_count": len(set(unit_by_id) - covered),
        "uncovered_unit_ids": sorted(set(unit_by_id) - covered),
    }

    return queue, aux_rows, summary


def save_crops(image_path: Path, queue, outdir: Path, w, h):
    crop_dir = outdir / "05_final_queue" / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    im = Image.open(image_path).convert("RGB")

    for q in queue:
        b = clamp_box(q["final_crop_bbox_xyxy"], w, h)
        crop = im.crop(tuple(b))
        crop_path = crop_dir / f'{q["queue_id"]}_{q["queue_stage"]}_{safe_name(q["region_type"])}.jpg'
        crop.save(crop_path, format="JPEG", quality=94)
        q["crop_path"] = str(crop_path)


def draw_overlay(image_path: Path, raw_rows, panels, queue, out_path: Path):
    im0 = Image.open(image_path).convert("RGB")
    scale = min(1.0, 2400 / max(im0.size))
    im = im0.resize((round(im0.width * scale), round(im0.height * scale)), Image.Resampling.LANCZOS) if scale < 1 else im0.copy()

    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except Exception:
        font = ImageFont.load_default()

    # raw boxes: green=text, cyan=auxiliary
    for r in raw_rows:
        b = [round(x * scale) for x in r["bbox_xyxy"]]
        color = (0, 210, 80) if r["is_text"] else (0, 190, 220)
        draw.rectangle(b, outline=color, width=1)

    # candidate panels: blue=text candidate, cyan=auxiliary candidate
    for p in panels:
        b = [round(x * scale) for x in p["bbox_xyxy"]]
        color = (0, 80, 255) if p["panel_kind"] == "text" else (0, 190, 220)
        draw.rectangle(b, outline=color, width=2)

    # final queue: red=main, purple=rescue
    for q in queue:
        b = [round(x * scale) for x in q["final_crop_bbox_xyxy"]]
        if q["queue_stage"] == "main":
            color = (255, 35, 35)
            fill = (135, 0, 0)
        else:
            color = (180, 40, 255)
            fill = (90, 0, 130)

        label = f'{q["queue_id"]} {q["queue_stage"]} gain={q["coverage_gain_n"]}'
        draw.rectangle(b, outline=color, width=7)
        tb = draw.textbbox((b[0], b[1]), label, font=font)
        draw.rectangle(tb, fill=fill)
        draw.text((b[0], b[1]), label, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)


def process_one(input_tsv: Path, out_root: Path, coverage_target: float):
    raw_rows = read_raw(input_tsv)
    specimen_id = raw_rows[0]["specimen_id"]
    image_path = Path(raw_rows[0]["image_path"])
    w = raw_rows[0]["source_width"]
    h = raw_rows[0]["source_height"]

    outdir = out_root / safe_name(specimen_id)
    outdir.mkdir(parents=True, exist_ok=True)

    deduped, audit = dedupe_rows(raw_rows)
    panels = build_panels(deduped, w, h)
    queue, aux_rows, qsum = select_queue(panels, deduped, w, h, coverage_target)
    save_crops(image_path, queue, outdir, w, h)

    for p in panels:
        p["specimen_id"] = specimen_id
        p["child_box_ids_json"] = json.dumps(p["child_box_ids"], ensure_ascii=False)
        p["class_counts_json"] = json.dumps(p["class_counts"], ensure_ascii=False, sort_keys=True)
        p.pop("child_rows", None)

    for q in queue:
        q["specimen_id"] = specimen_id

    for a in aux_rows:
        a["specimen_id"] = specimen_id

    write_tsv(
        outdir / "03_dedup" / "deduped_box_audit.tsv",
        audit,
        [
            "specimen_id", "image_path", "box_id", "rank", "prompt_class", "prompt_group",
            "confidence", "bbox_xyxy", "area_fraction", "center_x", "center_y",
            "aspect_ratio", "is_text", "is_auxiliary", "duplicate_of",
            "is_duplicate_suppressed", "text_seed", "auxiliary_seed",
        ],
    )

    write_tsv(
        outdir / "04_candidate_panels" / "candidate_panels.tsv",
        panels,
        [
            "specimen_id", "candidate_panel_id", "panel_kind", "panel_role",
            "bbox_xyxy", "area_fraction", "n_child_boxes", "best_confidence",
            "mean_confidence", "panel_score", "child_box_ids_json", "class_counts_json",
        ],
    )

    write_tsv(
        outdir / "04_candidate_panels" / "auxiliary_barrier_panels.tsv",
        aux_rows,
        [
            "specimen_id", "candidate_panel_id", "panel_kind", "panel_role",
            "bbox_xyxy", "area_fraction", "n_child_boxes", "best_confidence",
            "class_counts_json", "child_box_ids_json", "audit_use",
        ],
    )

    write_tsv(
        outdir / "05_final_queue" / "final_ocr_queue.tsv",
        queue,
        [
            "specimen_id", "queue_id", "queue_rank", "queue_stage", "candidate_panel_id",
            "region_type", "panel_kind", "panel_bbox_xyxy", "final_crop_bbox_xyxy",
            "area_fraction", "panel_area_fraction", "panel_score", "covered_unit_n",
            "coverage_gain_n", "covered_unit_ids_json", "coverage_gain_ids_json",
            "class_counts_json", "crop_reason", "reason", "crop_path",
        ],
    )

    overlay = outdir / "05_final_queue" / "final_queue_overlay.png"
    draw_overlay(image_path, raw_rows, panels, queue, overlay)

    summary = {
        "specimen_id": specimen_id,
        "input_tsv": str(input_tsv),
        "image_path": str(image_path),
        "raw_box_count": len(raw_rows),
        "deduped_box_count": len(deduped),
        "candidate_panel_count": len(panels),
        **qsum,
        "final_queue_tsv": str(outdir / "05_final_queue" / "final_ocr_queue.tsv"),
        "auxiliary_barrier_tsv": str(outdir / "04_candidate_panels" / "auxiliary_barrier_panels.tsv"),
        "overlay": str(overlay),
    }

    audit_dir = outdir / "06_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "boxlogic_v4_auxbarrier_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-tsv", action="append", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--coverage-target", type=float, default=0.98)
    args = ap.parse_args()

    out_root = Path(args.outdir)
    out_root.mkdir(parents=True, exist_ok=True)

    for p in args.input_tsv:
        process_one(Path(p), out_root, args.coverage_target)


if __name__ == "__main__":
    main()
