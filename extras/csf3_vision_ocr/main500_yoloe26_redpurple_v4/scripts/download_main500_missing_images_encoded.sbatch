#!/usr/bin/env bash
#SBATCH --job-name=dl_main500_missing
#SBATCH --partition=multicore
#SBATCH --time=02:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=2
#SBATCH --output=/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/logs/download_main500_missing_encoded.%j.out
#SBATCH --error=/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/logs/download_main500_missing_encoded.%j.err

set -Eeuo pipefail

MISS="/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/03_audit/main500_missing_images_to_download.tsv"
OUT="/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/03_audit/main500_missing_download_status_encoded.tsv"

echo "DATE=$(date)"
echo "MISS=${MISS}"
wc -l "${MISS}"

python - <<'PY'
from __future__ import annotations

import csv
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote, unquote, parse_qsl, urlencode

MISS = Path("/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/03_audit/main500_missing_images_to_download.tsv")
OUT = Path("/net/scratch/f70829xm/nhm_scribe_main500_yoloe1000_redpurple_v4_auxbarrier/03_audit/main500_missing_download_status_encoded.tsv")


def quote_url_path(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def add_download_query(url: str) -> str:
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["download"] = "1"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def candidate_urls(url: str) -> list[str]:
    q1 = quote_url_path(url)
    urls = [q1]

    if "/record/" in q1:
        urls.append(q1.replace("/record/", "/records/"))
    if "/records/" in q1:
        urls.append(q1.replace("/records/", "/record/"))

    base = list(urls)
    for u in base:
        urls.append(add_download_query(u))

    seen = set()
    out = []
    for u in urls:
        if u and u not in seen:
            out.append(u)
            seen.add(u)
    return out


def download(url: str, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    cmd = [
        "curl", "-L", "--fail",
        "--retry", "3",
        "--connect-timeout", "30",
        "--max-time", "180",
        "-A", "Mozilla/5.0",
        "-H", "Accept: image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "-o", str(tmp),
        url,
    ]

    p = subprocess.run(cmd, text=True, capture_output=True)

    if p.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        return False, (p.stderr or p.stdout or f"curl rc={p.returncode}")[-500:], 0

    size = tmp.stat().st_size if tmp.exists() else 0
    if size < 1000:
        if tmp.exists():
            tmp.unlink()
        return False, f"download too small: {size}", size

    head = tmp.read_bytes()[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        tmp.unlink()
        return False, "downloaded HTML instead of image", size

    tmp.replace(target)
    return True, "", target.stat().st_size


rows = list(csv.DictReader(MISS.open(), delimiter="\t"))
out_rows = []

for i, r in enumerate(rows, start=1):
    sid = r["specimen_id"]
    target = Path(r["target_path"])
    raw_url = r["jpegURL"]

    print(f"\n[{i}/{len(rows)}] {sid}", flush=True)
    print(f"target={target}", flush=True)
    print(f"raw_url={raw_url}", flush=True)

    if target.is_file() and target.stat().st_size > 1000:
        out_rows.append({
            "main500_task_id": r["main500_task_id"],
            "specimen_id": sid,
            "status": "SKIP_EXISTS",
            "size_bytes": target.stat().st_size,
            "target_path": str(target),
            "raw_jpegURL": raw_url,
            "used_url": "",
            "error": "",
        })
        continue

    success = False
    used_url = ""
    last_error = ""
    final_size = 0

    for url in candidate_urls(raw_url):
        print(f"try: {url}", flush=True)
        ok, err, size = download(url, target)
        if ok:
            success = True
            used_url = url
            final_size = size
            print(f"PASS size={size}", flush=True)
            break
        else:
            last_error = err
            print(f"FAIL {err}", flush=True)

    out_rows.append({
        "main500_task_id": r["main500_task_id"],
        "specimen_id": sid,
        "status": "PASS_DOWNLOAD" if success else "FAIL",
        "size_bytes": final_size,
        "target_path": str(target),
        "raw_jpegURL": raw_url,
        "used_url": used_url,
        "error": last_error,
    })

    time.sleep(0.2)

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="", encoding="utf-8") as f:
    fields = ["main500_task_id", "specimen_id", "status", "size_bytes", "target_path", "raw_jpegURL", "used_url", "error"]
    w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
    w.writeheader()
    w.writerows(out_rows)

from collections import Counter
print("\nwrote", OUT)
print(Counter(x["status"] for x in out_rows))
PY
