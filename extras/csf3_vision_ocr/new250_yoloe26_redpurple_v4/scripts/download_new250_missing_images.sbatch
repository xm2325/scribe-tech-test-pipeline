#!/usr/bin/env bash
#SBATCH --job-name=dl_new250_missing
#SBATCH --partition=multicore
#SBATCH --time=02:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=2
#SBATCH --output=/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/logs/download_new250_missing.%j.out
#SBATCH --error=/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/logs/download_new250_missing.%j.err

set -Eeuo pipefail

MISS="/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/03_audit/new250_missing_images_to_download.tsv"

echo "DATE=$(date)"
echo "MISS=${MISS}"
wc -l "${MISS}"

python - <<'PY'
from __future__ import annotations

import csv
import time
import urllib.request
from pathlib import Path

MISS = Path("/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/03_audit/new250_missing_images_to_download.tsv")
LOG = Path("/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/03_audit/new250_missing_download_status.tsv")

rows = list(csv.DictReader(MISS.open(), delimiter="\t"))

out = []
for i, r in enumerate(rows, start=1):
    sid = r["specimen_id"]
    url = r["jpegURL"]
    target = Path(r["target_path"])

    status = "PENDING"
    err = ""

    try:
        if not url:
            raise RuntimeError("empty jpegURL")

        target.parent.mkdir(parents=True, exist_ok=True)

        if target.is_file() and target.stat().st_size > 0:
            status = "SKIP_EXISTS"
        else:
            print(f"[{i}/{len(rows)}] downloading {sid} -> {target}", flush=True)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()

            if len(data) < 1000:
                raise RuntimeError(f"download too small: {len(data)} bytes")

            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            status = "PASS_DOWNLOAD"

        size = target.stat().st_size if target.is_file() else 0

    except Exception as e:
        status = "FAIL"
        err = str(e)
        size = target.stat().st_size if target.is_file() else 0

    out.append({
        "specimen_id": sid,
        "status": status,
        "size_bytes": size,
        "target_path": str(target),
        "jpegURL": url,
        "error": err,
    })

    time.sleep(0.2)

with LOG.open("w", newline="", encoding="utf-8") as f:
    fields = ["specimen_id", "status", "size_bytes", "target_path", "jpegURL", "error"]
    w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
    w.writeheader()
    w.writerows(out)

print(f"wrote {LOG}")
from collections import Counter
print(Counter(x["status"] for x in out))
PY
