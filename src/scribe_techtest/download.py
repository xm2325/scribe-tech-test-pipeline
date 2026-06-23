from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import pandas as pd
import requests


@dataclass
class DownloadConfig:
    timeout_seconds: int = 90
    retries: int = 3
    rate_limit_seconds: float = 1.0
    backoff_seconds: float = 3.0
    max_backoff_seconds: float = 180.0
    user_agent: str = "Mozilla/5.0 nhm-scribe-short-project/1.0"


def encode_url(url: str) -> str:
    """Quote unsafe URL path characters but keep the URL structure."""
    parts = urlsplit(str(url).strip())
    path = quote(parts.path, safe="/:%")
    query = quote(parts.query, safe="=&?/:,%")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def candidate_urls(url: str) -> list[str]:
    """Zenodo can redirect between /record/ and /records/; download=1 is safer for files."""
    url = str(url).strip()
    urls = [url]

    if "/record/" in url:
        urls.append(url.replace("/record/", "/records/"))
    if "/records/" in url:
        urls.append(url.replace("/records/", "/record/"))

    base_urls = list(urls)
    for u in base_urls:
        if "?" not in u:
            urls.append(u + "?download=1")
        elif "download=1" not in u:
            urls.append(u + "&download=1")

    out = []
    seen = set()
    for u in urls:
        eu = encode_url(u)
        if eu not in seen:
            out.append(eu)
            seen.add(eu)
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sleep(attempt: int, config: DownloadConfig) -> None:
    seconds = min(config.max_backoff_seconds, config.backoff_seconds * attempt)
    if seconds > 0:
        time.sleep(seconds)


def _base_result(
    *,
    record_key: str,
    source_sheet: str,
    index: str,
    url_type: str,
    url: str,
    encoded_url: str,
    local_path: Path,
) -> dict[str, Any]:
    return {
        "record_key": record_key,
        "source_sheet": source_sheet,
        "index": index,
        "url_type": url_type,
        "url": url,
        "encoded_url": encoded_url,
        "local_path": str(local_path),
    }


def download_one(
    session: requests.Session,
    url: str,
    local_path: Path,
    config: DownloadConfig,
    *,
    record_key: str,
    source_sheet: str,
    index: str,
    url_type: str,
) -> dict[str, Any]:
    """Download one JSON or image asset with stable audit fields."""
    raw_url = str(url).strip()
    first_encoded = encode_url(raw_url)

    base = _base_result(
        record_key=record_key,
        source_sheet=source_sheet,
        index=str(index),
        url_type=url_type,
        url=raw_url,
        encoded_url=first_encoded,
        local_path=local_path,
    )

    if not raw_url.startswith(("http://", "https://")):
        print(f"[SKIP_BAD_URL] {record_key} {url_type} url={raw_url}", flush=True)
        return {
            **base,
            "status": "error",
            "status_code": "",
            "bytes": 0,
            "sha256": "",
            "attempts": 0,
            "error": "SKIP_BAD_URL",
        }

    local_path.parent.mkdir(parents=True, exist_ok=True)

    if local_path.exists() and local_path.stat().st_size > 0:
        nbytes = local_path.stat().st_size
        sha = _sha256_file(local_path)
        print(f"[CACHED] {record_key} {url_type} bytes={nbytes} path={local_path}", flush=True)
        return {
            **base,
            "status": "cached",
            "status_code": "",
            "bytes": nbytes,
            "sha256": sha,
            "attempts": 0,
            "error": "",
        }

    headers = {
        "User-Agent": config.user_agent,
        "Accept": "*/*",
    }

    tmp_path = local_path.with_suffix(local_path.suffix + ".part")
    last_code: int | str = ""
    last_error = ""
    total_attempts = 0
    used_url = first_encoded

    for candidate_url in candidate_urls(raw_url):
        used_url = candidate_url

        for attempt in range(1, config.retries + 2):
            total_attempts += 1

            try:
                print(
                    f"[START] {record_key} {url_type} "
                    f"candidate={candidate_url} attempt={attempt}",
                    flush=True,
                )

                response = session.get(
                    candidate_url,
                    headers=headers,
                    timeout=config.timeout_seconds,
                    allow_redirects=True,
                )
                last_code = response.status_code
                content = response.content

                print(
                    f"[HTTP] {record_key} {url_type} "
                    f"status={response.status_code} bytes={len(content)} "
                    f"final_url={response.url}",
                    flush=True,
                )

                if response.status_code == 200 and content:
                    tmp_path.write_bytes(content)
                    tmp_path.replace(local_path)

                    nbytes = local_path.stat().st_size
                    sha = _sha256_file(local_path)

                    print(
                        f"[OK] {record_key} {url_type} "
                        f"bytes={nbytes} sha256={sha[:12]} path={local_path}",
                        flush=True,
                    )

                    if config.rate_limit_seconds:
                        time.sleep(config.rate_limit_seconds)

                    return {
                        **base,
                        "encoded_url": candidate_url,
                        "status": "ok",
                        "status_code": response.status_code,
                        "bytes": nbytes,
                        "sha256": sha,
                        "attempts": total_attempts,
                        "error": "",
                    }

                last_error = f"HTTP_{response.status_code}"
                print(
                    f"[WARN] {record_key} {url_type} "
                    f"{last_error} bytes={len(content)}",
                    flush=True,
                )

            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                print(
                    f"[ERROR] {record_key} {url_type} "
                    f"candidate={candidate_url} attempt={attempt} {last_error}",
                    flush=True,
                )

            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

            if attempt <= config.retries:
                _sleep(attempt, config)

    print(
        f"[FAIL] {record_key} {url_type} "
        f"status_code={last_code} attempts={total_attempts} error={last_error}",
        flush=True,
    )

    return {
        **base,
        "encoded_url": used_url,
        "status": "error",
        "status_code": last_code,
        "bytes": 0,
        "sha256": "",
        "attempts": total_attempts,
        "error": last_error,
    }


def _safe_suffix_from_url(url: str, default: str) -> str:
    suffix = Path(urlsplit(str(url)).path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    return default


def _safe_stem(x: str) -> str:
    stem = Path(urlsplit(str(x)).path).stem
    stem = "".join(c if c.isalnum() or c in "._-" else "_" for c in stem)
    stem = "_".join([p for p in stem.split("_") if p])
    return stem or "asset"


def _local_path(download_dir: Path, row: pd.Series, url_type: str) -> Path:
    record_key = str(row.get("record_key", "")).strip()
    url = str(row.get("jsonURL" if url_type == "json" else "jpegURL", "")).strip()
    stem = _safe_stem(url)
    if url_type == "json":
        return download_dir / "json" / f"{record_key}_{stem}.json"
    suffix = _safe_suffix_from_url(url, ".jpg")
    return download_dir / "images" / f"{record_key}_{stem}{suffix}"


def download_all(
    records: pd.DataFrame,
    download_dir: Path,
    config: DownloadConfig | None = None,
    *,
    skip_images: bool = False,
    allow_partial: bool = False,
) -> pd.DataFrame:
    """Download JSON and optional image assets.

    This function is intentionally sequential for politeness to Zenodo.
    Existing non-empty files are cached, so rerunning the same output directory
    safely resumes incomplete downloads.
    """
    if config is None:
        config = DownloadConfig()

    output_dir = Path(download_dir)
    download_dir = output_dir / "downloads"
    (download_dir / "json").mkdir(parents=True, exist_ok=True)
    (download_dir / "images").mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    rows: list[dict[str, Any]] = []

    total = len(records)
    for i, (_, row) in enumerate(records.iterrows(), start=1):
        record_key = str(row.get("record_key", "")).strip()
        source_sheet = str(row.get("source_sheet", "")).strip()
        index = str(row.get("index", "")).strip()

        print(f"[RECORD] {i}/{total} {record_key}", flush=True)

        json_url = str(row.get("jsonURL", "")).strip()
        if json_url:
            rows.append(
                download_one(
                    session,
                    json_url,
                    _local_path(download_dir, row, "json"),
                    config,
                    record_key=record_key,
                    source_sheet=source_sheet,
                    index=index,
                    url_type="json",
                )
            )

        if not skip_images:
            image_url = str(row.get("jpegURL", "")).strip()
            if image_url:
                rows.append(
                    download_one(
                        session,
                        image_url,
                        _local_path(download_dir, row, "image"),
                        config,
                        record_key=record_key,
                        source_sheet=source_sheet,
                        index=index,
                        url_type="image",
                    )
                )

    manifest = pd.DataFrame(rows)

    # Keep the original pipeline contract: write a manifest under output/downloads/.
    manifest_path = download_dir / "manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    print(f"[WRITE] manifest {manifest_path}", flush=True)

    if len(manifest):
        print("\n[DOWNLOAD_STATUS_COUNTS]", flush=True)
        print(manifest.groupby(["url_type", "status"], dropna=False).size().to_string(), flush=True)

    return manifest
