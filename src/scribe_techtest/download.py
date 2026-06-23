from __future__ import annotations

import hashlib
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import pandas as pd
import requests

from .utils import clean


@dataclass(frozen=True)
class DownloadConfig:
    timeout_seconds: float = 30
    retries: int = 4
    rate_limit_seconds: float = 0.25
    backoff_seconds: float = 2.0
    max_backoff_seconds: float = 60.0
    user_agent: str = "scribe-tech-test-pipeline/0.1"


def encode_url(url: str) -> str:
    url = clean(url)
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/%:@")
    query = quote(unquote(parts.query), safe="=&%/:+?,")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def safe_download_filename(record_key: str, url: str, fallback_ext: str) -> str:
    parts = urlsplit(clean(url))
    raw_name = Path(unquote(parts.path)).name
    if not raw_name:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        raw_name = f"{digest}.{fallback_ext.lstrip('.')}"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._")
    if "." not in stem and fallback_ext:
        stem = f"{stem}.{fallback_ext.lstrip('.')}"
    stem = stem[:140]
    return f"{record_key}_{stem}"


def download_all(
    records: pd.DataFrame,
    output_dir: Path,
    config: DownloadConfig,
    *,
    skip_images: bool = False,
    allow_partial: bool = False,
) -> pd.DataFrame:
    downloads_dir = output_dir / "downloads"
    json_dir = downloads_dir / "json"
    image_dir = downloads_dir / "images"
    json_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})
    rows: list[dict[str, Any]] = []

    for _, row in records.iterrows():
        record_key = clean(row["record_key"])
        rows.append(
            download_one(
                session,
                clean(row["jsonURL"]),
                json_dir / safe_download_filename(record_key, clean(row["jsonURL"]), "json"),
                config,
                record_key=record_key,
                source_sheet=clean(row["source_sheet"]),
                index=clean(row["index"]),
                url_type="json",
            )
        )
        if not skip_images:
            rows.append(
                download_one(
                    session,
                    clean(row["jpegURL"]),
                    image_dir
                    / safe_download_filename(record_key, clean(row["jpegURL"]), "jpg"),
                    config,
                    record_key=record_key,
                    source_sheet=clean(row["source_sheet"]),
                    index=clean(row["index"]),
                    url_type="image",
                )
            )

    manifest = pd.DataFrame(rows)
    manifest_path = output_dir / "downloads" / "manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(manifest_path, index=False)
    assert_download_counts(records, manifest, skip_images=skip_images, allow_partial=allow_partial)
    return manifest


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
    encoded_url = encode_url(url)
    base = {
        "record_key": record_key,
        "source_sheet": source_sheet,
        "index": index,
        "url_type": url_type,
        "url": url,
        "encoded_url": encoded_url,
        "local_path": str(local_path),
    }
    if local_path.exists() and local_path.stat().st_size > 0:
        return {
            **base,
            "status": "cached",
            "status_code": "",
            "bytes": local_path.stat().st_size,
            "attempts": 0,
            "error": "",
        }

    last_error = ""
    status_code: int | str = ""
    for attempt in range(1, config.retries + 2):
        try:
            response = session.get(encoded_url, timeout=config.timeout_seconds)
            status_code = response.status_code
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt <= config.retries:
                    _sleep_for_retry(response, attempt, config)
                    continue
            response.raise_for_status()
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(response.content)
            if config.rate_limit_seconds:
                time.sleep(config.rate_limit_seconds)
            return {
                **base,
                "status": "ok",
                "status_code": status_code,
                "bytes": len(response.content),
                "attempts": attempt,
                "error": "",
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt <= config.retries:
                _sleep_for_retry(None, attempt, config)
                continue
            break

    return {
        **base,
        "status": "error",
        "status_code": status_code,
        "bytes": 0,
        "attempts": config.retries + 1,
        "error": last_error,
    }


def _sleep_for_retry(
    response: requests.Response | None,
    attempt: int,
    config: DownloadConfig,
) -> None:
    retry_after = response.headers.get("Retry-After", "") if response is not None else ""
    wait = _parse_retry_after(retry_after)
    if wait is None:
        wait = min(
            config.max_backoff_seconds,
            config.backoff_seconds * (2 ** max(0, attempt - 1)) + random.uniform(0, 0.5),
        )
    time.sleep(wait)


def _parse_retry_after(value: str) -> float | None:
    value = clean(value)
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def assert_download_counts(
    records: pd.DataFrame,
    manifest: pd.DataFrame,
    *,
    skip_images: bool,
    allow_partial: bool,
) -> None:
    expected = len(records)
    ok_statuses = {"ok", "cached"}
    json_ok = int(
        manifest[
            manifest["url_type"].eq("json") & manifest["status"].isin(ok_statuses)
        ].shape[0]
    )
    image_ok = int(
        manifest[
            manifest["url_type"].eq("image") & manifest["status"].isin(ok_statuses)
        ].shape[0]
    )
    if json_ok != expected and not allow_partial:
        raise RuntimeError(f"JSON download count mismatch: expected {expected}, got {json_ok}")
    if not skip_images and image_ok != expected and not allow_partial:
        raise RuntimeError(f"Image download count mismatch: expected {expected}, got {image_ok}")
