#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

TOP = Path("/mnt/iusers01/sk01/f70829xm/code/nhm")
IN_MANIFEST = TOP / "data_rds/01_manifest/nhm_all750_manifest.tsv"
OUT_MANIFEST = Path("/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/00_manifest/new250_local_images.tsv")


def get(row, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return ""


def resolve_image_path(pstr: str) -> Path:
    p = Path(pstr)
    if p.is_absolute():
        return p

    candidates = [
        TOP / p,
        TOP / "nhm_scribe_repo" / p,
        Path.cwd() / p,
    ]
    for c in candidates:
        if c.is_file():
            return c

    return TOP / p


def row_is_new250(row, idx0: int) -> bool:
    local_jpeg = get(row, "local_jpeg", "image_path", "jpeg_path", "local_image")
    subset = get(row, "subset", "split", "source", "dataset").lower()

    if subset in {"new250", "new_data", "new"}:
        return True

    if "new250" in local_jpeg.lower() or "/new_data/" in local_jpeg.lower() or "/new250/" in local_jpeg.lower():
        return True

    # fallback: all750 manifest usually has main500 first, new250 after that
    if idx0 >= 500:
        return True

    return False


def main():
    rows_all = []
    with IN_MANIFEST.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for idx0, r in enumerate(reader):
            rows_all.append((idx0, dict(r)))

    selected = []
    for idx0, r in rows_all:
        if not row_is_new250(r, idx0):
            continue

        local_jpeg = get(r, "local_jpeg", "image_path", "jpeg_path", "local_image")
        image_path = resolve_image_path(local_jpeg)

        specimen_id = get(
            r,
            "specimen_id",
            "catalogNumber",
            "catalog_number",
            "Index",
            "index",
            "occurrenceID",
        )

        selected.append({
            "specimen_id": specimen_id,
            "source_row": get(r, "source_row", "Index", "index"),
            "image_path": str(image_path),
            "occurrenceID": get(r, "occurrenceID"),
            "DOI": get(r, "DOI"),
            "jpegURL": get(r, "jpegURL"),
            "jsonURL": get(r, "jsonURL"),
            "image_exists": str(image_path.is_file()),
            "all750_idx0": idx0,
        })

    usable = [r for r in selected if r["image_exists"] == "True"]

    OUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "task_id",
        "specimen_id",
        "source_row",
        "image_path",
        "occurrenceID",
        "DOI",
        "jpegURL",
        "jsonURL",
        "image_exists",
        "all750_idx0",
    ]

    with OUT_MANIFEST.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        for i, r in enumerate(usable):
            w.writerow({"task_id": i, **r})

    print(f"input_manifest={IN_MANIFEST}")
    print(f"all750_rows={len(rows_all)}")
    print(f"new250_rows_found={len(selected)}")
    print(f"usable_image_rows={len(usable)}")
    print(f"out_manifest={OUT_MANIFEST}")

    if len(usable) != 250:
        print(f"[WARN] usable_image_rows={len(usable)}, expected 250")

    print("first ten:")
    for r in usable[:10]:
        print(r["specimen_id"], r["image_exists"], r["image_path"])


if __name__ == "__main__":
    main()
