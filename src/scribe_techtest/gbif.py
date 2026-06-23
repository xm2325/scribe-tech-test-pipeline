from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .constants import GBIF_FIELD_KEYS, TAXONOMY_FIELDS
from .utils import clean, is_blank


@dataclass(frozen=True)
class GbifConfig:
    enabled: bool = True
    base_url: str = "https://api.gbif.org/v1"
    timeout_seconds: float = 25
    retries: int = 3
    rate_limit_seconds: float = 0.2
    min_confidence: float = 80
    cache_path: str = "cache/gbif_species_match.json"


def enrich_taxonomy(
    df: pd.DataFrame,
    output_dir: Path,
    config: GbifConfig,
    *,
    overwrite_taxonomy: bool = False,
) -> pd.DataFrame:
    if not config.enabled:
        return df.copy()

    cache_path = output_dir / config.cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = _load_cache(cache_path)
    session = requests.Session()
    session.headers.update({"User-Agent": "scribe-tech-test-pipeline/0.1"})

    out = df.copy()
    for field in TAXONOMY_FIELDS:
        source_col = f"source_{field}"
        if source_col not in out.columns:
            out[source_col] = ""
    gbif_meta_cols = [
        "gbif_usageKey",
        "gbif_confidence",
        "gbif_matchType",
        "gbif_status",
        "gbif_rank",
        "gbif_error",
    ]
    for col in gbif_meta_cols:
        if col not in out.columns:
            out[col] = ""

    for idx, row in out.iterrows():
        name = clean(row.get("scientificName", ""))
        if not name:
            continue
        if not overwrite_taxonomy and all(not is_blank(row.get(f, "")) for f in TAXONOMY_FIELDS):
            continue
        match = gbif_match(name, config, cache, session)
        apply_match(
            out,
            idx,
            match,
            overwrite_taxonomy=overwrite_taxonomy,
            min_confidence=config.min_confidence,
        )
        if config.rate_limit_seconds:
            time.sleep(config.rate_limit_seconds)

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def gbif_match(
    scientific_name: str,
    config: GbifConfig,
    cache: dict[str, Any],
    session: requests.Session,
) -> dict[str, Any]:
    if scientific_name in cache and not _is_error(cache[scientific_name]):
        value = cache[scientific_name]
        return value if isinstance(value, dict) else {}

    last_error = ""
    url = f"{config.base_url.rstrip('/')}/species/match"
    for attempt in range(1, config.retries + 2):
        try:
            response = session.get(
                url,
                params={"name": scientific_name, "kingdom": "Plantae"},
                timeout=config.timeout_seconds,
            )
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt <= config.retries:
                    time.sleep(_retry_after(response, attempt))
                    continue
            response.raise_for_status()
            value = response.json()
            cache[scientific_name] = value if isinstance(value, dict) else {}
            return cache[scientific_name]
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt <= config.retries:
                time.sleep(min(60.0, 2.0 * (2 ** (attempt - 1))))
                continue
            break
    cache[scientific_name] = {"_error": last_error}
    return cache[scientific_name]


def apply_match(
    df: pd.DataFrame,
    idx: int,
    match: dict[str, Any],
    *,
    overwrite_taxonomy: bool = False,
    min_confidence: float = 0.0,
) -> None:
    error = clean(match.get("_error", ""))
    df.at[idx, "gbif_error"] = error
    if error:
        return
    df.at[idx, "gbif_usageKey"] = clean(match.get("usageKey", ""))
    df.at[idx, "gbif_confidence"] = clean(match.get("confidence", ""))
    df.at[idx, "gbif_matchType"] = clean(match.get("matchType", ""))
    df.at[idx, "gbif_status"] = clean(match.get("status", ""))
    df.at[idx, "gbif_rank"] = clean(match.get("rank", ""))

    try:
        confidence = float(match.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < float(min_confidence):
        return

    for field, match_key in GBIF_FIELD_KEYS.items():
        value = clean(match.get(match_key, ""))
        if not value:
            continue
        current = clean(df.at[idx, field])
        if current and not overwrite_taxonomy:
            continue
        df.at[idx, field] = value
        df.at[idx, f"source_{field}"] = "gbif"


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("_error"))


def _retry_after(response: requests.Response, attempt: int) -> float:
    raw = clean(response.headers.get("Retry-After", ""))
    try:
        return max(0.0, float(raw))
    except ValueError:
        return min(60.0, 2.0 * (2 ** (attempt - 1)))
