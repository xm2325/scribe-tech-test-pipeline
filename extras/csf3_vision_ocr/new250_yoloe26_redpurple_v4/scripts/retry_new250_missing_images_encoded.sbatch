#!/usr/bin/env bash
#SBATCH --job-name=retry_new250_dl
#SBATCH --partition=multicore
#SBATCH --time=03:00:00
#SBATCH --mem=8G
#SBATCH --cpus-per-task=2
#SBATCH --output=/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/logs/retry_new250_missing_images_encoded.%j.out
#SBATCH --error=/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/logs/retry_new250_missing_images_encoded.%j.err

set -Eeuo pipefail

MISS="/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/03_audit/new250_missing_images_to_download.tsv"
OUT="/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/03_audit/new250_missing_download_status_retry_encoded.tsv"

echo "DATE=$(date)"
echo "MISS=${MISS}"
wc -l "${MISS}"

python - <<'PY'
from __future__ import annotations

import csv
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote, unquote, parse_qsl, urlencode

MISS = Path("/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/03_audit/new250_missing_images_to_download.tsv")
OUT = Path("/net/scratch/f70829xm/nhm_scribe_new250_yoloe1000_redpurple_v4_auxbarrier/03_audit/new250_missing_download_status_retry_encoded.tsv")


def quote_url_path(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/")
    query = parts.query
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def add_download_query(url: str) -> str:
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["download"] = "1"
    query = urlencode(q)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def zenodo_record_id(url: str) -> str:
    m = re.search(r"/records?/(\d+)/", url)
    return m.group(1) if m else ""


def zenodo_filename(url: str) -> str:
    m = re.search(r"/files/(.+)$", urlsplit(url).path)
    return unquote(m.group(1)) if m else Path(urlsplit(url).path).name


def api_file_links(url: str) -> list[str]:
    rid = zenodo_record_id(url)
    fname = zenodo_filename(url)
    if not rid:
        return []

    api = f"https://zenodo.org/api/records/{rid}"
    req = urllib.request.Request(
        api,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    links = []
    files = data.get("files", []) or []

    def norm(x: str) -> str:
        return re.sub(r"\s+", " ", x.strip()).lower()

    for item in files:
        key = item.get("key", "")
        if norm(key) == norm(fname) or key.replace(" ", "_") == fname.replace(" ", "_"):
            lnk = item.get("links", {}) or {}
            for k in ["self", "download", "content"]:
                if lnk.get(k):
                    links.append(lnk[k])
            # Zenodo API self links often need /content
            if lnk.get("self") and not lnk["self"].endswith("/content"):
                links.append(lnk["self"].rstrip("/") + "/content")

    return links


def candidate_urls(url: str) -> list[str]:
    out = []

    q1 = quote_url_path(url)
    out.append(q1)

    # old /record/ and newer /records/ routes
    if "/record/" in q1:
        out.append(q1.replace("/record/", "/records/"))
    if "/records/" in q1:
        out.append(q1.replace("/records/", "/record/"))

    # add ?download=1 variants
    base = list(out)
    for u in base:
        out.append(add_download_query(u))

    # API fallback
    out.extend(api_file_links(q1))

    # de-duplicate
    seen = set()
    final = []
    for u in out:
        if u and u not in seen:
            final.append(u)
            seen.add(u)
    return final


def download_with_curl(url: str, target: Path) -> tuple[bool, str, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")

    if tmp.exists():
        tmp.unlink()

    cmd = [
        "curl",
        "-L",
        "--fail",
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
        err = f"download too small: {size} bytes"
        if tmp.exists():
            tmp.unlink()
        return False, err, size

    # Avoid saving HTML error pages as jpg
    head = tmp.read_bytes()[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        err = "downloaded HTML page instead of image"
        tmp.unlink()
        return False, err, size

    tmp.replace(target)
    return True, "", target.stat().st_size


rows = list(csv.DictReader(MISS.open(), delimiter="\t"))
out_rows = []

for i, r in enumerate(rows, start=1):
    sid = r["specimen_id"]
    target = Path(r["target_path"])
    raw_url = r["jpegURL"]

    if target.is_file() and target.stat().st_size > 1000:
        out_rows.append({
            "specimen_id": sid,
            "status": "SKIP_EXISTS",
            "size_bytes": target.stat().st_size,
            "target_path": str(target),
            "raw_jpegURL": raw_url,
            "used_url": "",
            "error": "",
        })
        continue

    print(f"\n[{i}/{len(rows)}] {sid}", flush=True)
    print(f"target={target}", flush=True)
    print(f"raw_url={raw_url}", flush=True)

    success = False
    last_error = ""
    used_url = ""
    final_size = 0

    for url in candidate_urls(raw_url):
        print(f"try: {url}", flush=True)
        ok, err, size = download_with_curl(url, target)
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
    fields = ["specimen_id", "status", "size_bytes", "target_path", "raw_jpegURL", "used_url", "error"]
    w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
    w.writeheader()
    w.writerows(out_rows)

from collections import Counter
print("\nwrote", OUT)
print(Counter(x["status"] for x in out_rows))
PY
