#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path

TOP = Path("/mnt/iusers01/sk01/f70829xm/code/nhm")
IN_MANIFEST = TOP / "data_rds/01_manifest/nhm_all750_manifest.tsv"
OUT_TSV = Path("/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/03_audit/main500_image_presence_audit_full500.tsv")


def get(row, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return ""


def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(s)).strip("_") or "unknown"


def resolve_candidates(local_jpeg: str, specimen_id: str, task_id: int):
    p = Path(local_jpeg)
    safe = safe_name(specimen_id)

    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend([
            TOP / p,
            TOP / "nhm_scribe_repo" / p,
            TOP / "data_rds/02_raw_images/main500" / p.name,
            TOP / "data_rds/02_raw_images/main_data" / p.name,
        ])

    # deterministic fallback target if file was never downloaded
    candidates.append(TOP / "data_rds/02_raw_images/main500" / f"main500_row{task_id+1:04d}_{safe}.jpg")

    # de-duplicate
    seen = set()
    out = []
    for c in candidates:
        if str(c) not in seen:
            out.append(c)
            seen.add(str(c))
    return out


def main():
    out = []

    with IN_MANIFEST.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for idx0, r in enumerate(reader):
            if idx0 >= 500:
                break

            r = dict(r)
            task_id = idx0
            specimen_id = get(r, "specimen_id", "catalogNumber", "catalog_number", "Index", "index", "occurrenceID")
            local_jpeg = get(r, "local_jpeg", "image_path", "jpeg_path", "local_image")
            candidates = resolve_candidates(local_jpeg, specimen_id, task_id)
            existing = [p for p in candidates if p.is_file()]
            chosen = existing[0] if existing else candidates[-1]

            out.append({
                "main500_task_id": task_id,
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
    with OUT_TSV.open("w", newline="", encoding="utf-8") as f:
        fields = list(out[0].keys())
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(out)

    ok = sum(x["image_exists"] == "True" for x in out)
    miss = len(out) - ok

    print(f"main500_rows={len(out)}")
    print(f"existing={ok}")
    print(f"missing={miss}")
    print(f"out={OUT_TSV}")

    print("\nFirst missing:")
    shown = 0
    for x in out:
        if x["image_exists"] != "True":
            print(x["main500_task_id"], x["specimen_id"], x["chosen_image_path"], x["jpegURL"])
            shown += 1
            if shown >= 30:
                break


if __name__ == "__main__":
    main()
