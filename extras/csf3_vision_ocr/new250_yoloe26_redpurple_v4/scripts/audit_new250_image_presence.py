#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

TOP = Path("/mnt/iusers01/sk01/f70829xm/code/nhm")
IN_MANIFEST = TOP / "data_rds/01_manifest/nhm_all750_manifest.tsv"
OUT_TSV = Path("/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/03_audit/new250_image_presence_audit.tsv")


def get(row, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return ""


def resolve_candidates(pstr: str):
    p = Path(pstr)
    if p.is_absolute():
        return [p]
    return [
        TOP / p,
        TOP / "nhm_scribe_repo" / p,
        TOP / "data_rds/02_raw_images/new250" / p.name,
        TOP / "data_rds/02_raw_images/new_data" / p.name,
        TOP / "data_rds/02_raw_images/main500" / p.name,
    ]


def row_is_new250(row, idx0: int) -> bool:
    local_jpeg = get(row, "local_jpeg", "image_path", "jpeg_path", "local_image")
    subset = get(row, "subset", "split", "source", "dataset").lower()

    if subset in {"new250", "new_data", "new"}:
        return True
    if "new250" in local_jpeg.lower() or "/new_data/" in local_jpeg.lower() or "/new250/" in local_jpeg.lower():
        return True
    if idx0 >= 500:
        return True
    return False


def main():
    out = []

    with IN_MANIFEST.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for idx0, r in enumerate(reader):
            r = dict(r)
            if not row_is_new250(r, idx0):
                continue

            local_jpeg = get(r, "local_jpeg", "image_path", "jpeg_path", "local_image")
            specimen_id = get(r, "specimen_id", "catalogNumber", "catalog_number", "Index", "index", "occurrenceID")
            candidates = resolve_candidates(local_jpeg)

            existing = [p for p in candidates if p.is_file()]
            chosen = existing[0] if existing else candidates[0]

            out.append({
                "new250_task_id": len(out),
                "all750_idx0": idx0,
                "specimen_id": specimen_id,
                "image_exists": str(chosen.is_file()),
                "chosen_image_path": str(chosen),
                "original_local_jpeg": local_jpeg,
                "candidate_paths": "|".join(str(p) for p in candidates),
                "DOI": get(r, "DOI"),
                "jpegURL": get(r, "jpegURL"),
                "jsonURL": get(r, "jsonURL"),
                "occurrenceID": get(r, "occurrenceID"),
            })

    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    fields = list(out[0].keys())
    with OUT_TSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(out)

    n = len(out)
    ok = sum(x["image_exists"] == "True" for x in out)
    miss = n - ok
    print(f"new250_rows={n}")
    print(f"existing={ok}")
    print(f"missing={miss}")
    print(f"out={OUT_TSV}")

    print("\nFirst missing examples:")
    for x in out:
        if x["image_exists"] != "True":
            print(x["new250_task_id"], x["specimen_id"], x["chosen_image_path"], x["jpegURL"])
            if sum(1 for y in out[:out.index(x)+1] if y["image_exists"] != "True") >= 10:
                break


if __name__ == "__main__":
    main()
