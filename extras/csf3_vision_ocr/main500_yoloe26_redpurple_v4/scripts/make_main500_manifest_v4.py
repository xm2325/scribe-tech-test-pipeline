#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

TOP = Path("/mnt/iusers01/sk01/f70829xm/code/nhm")
IN_MANIFEST = TOP / "data_rds/01_manifest/nhm_all750_manifest.tsv"
OUT_MANIFEST = Path("/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/00_manifest/main500_local_images.tsv")


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


def main():
    rows = []
    with IN_MANIFEST.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for r in reader:
            local_jpeg = get(r, "local_jpeg", "image_path", "jpeg_path", "local_image")
            subset = get(r, "subset", "split", "source").lower()

            is_main = (
                subset == "main500"
                or "/main500/" in local_jpeg
                or "main500" in local_jpeg
            )

            if not is_main:
                continue

            image_path = resolve_image_path(local_jpeg)
            specimen_id = get(r, "specimen_id", "catalogNumber", "catalog_number", "Index", "index")
            source_row = get(r, "source_row", "Index", "index")

            rows.append({
                "specimen_id": specimen_id,
                "source_row": source_row,
                "image_path": str(image_path),
                "occurrenceID": get(r, "occurrenceID"),
                "DOI": get(r, "DOI"),
                "jpegURL": get(r, "jpegURL"),
                "jsonURL": get(r, "jsonURL"),
                "image_exists": str(image_path.is_file()),
            })

    usable = [r for r in rows if r["image_exists"] == "True"]

    OUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "task_id", "specimen_id", "source_row", "image_path",
        "occurrenceID", "DOI", "jpegURL", "jsonURL", "image_exists"
    ]

    with OUT_MANIFEST.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        for i, r in enumerate(usable):
            w.writerow({"task_id": i, **r})

    print(f"input_manifest={IN_MANIFEST}")
    print(f"main500_rows_found={len(rows)}")
    print(f"usable_image_rows={len(usable)}")
    print(f"out_manifest={OUT_MANIFEST}")

    if len(usable) != 500:
        print(f"[WARN] usable_image_rows={len(usable)}, expected 500")

    print("first five:")
    for r in usable[:5]:
        print(r["specimen_id"], r["image_exists"], r["image_path"])


if __name__ == "__main__":
    main()
