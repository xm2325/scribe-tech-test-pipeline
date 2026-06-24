#!/usr/bin/env python3
"""
Infer / audit candidate institutionCode from collectionID.

This script is intentionally conservative:
- It does NOT hard-code HR.85 / HR.189 / HR.129.
- It loops over all collectionID values where institutionCode is blank.
- It uses exact collection-code lookup, not noisy q= search, for automatic evidence.
- It writes candidate columns and evidence classes; it does not overwrite institutionCode by default.

Evidence levels:
1. direct_grscicoll_and_finbif_agree
2. direct_finbif_collection_metadata
3. direct_grscicoll_collection_code
4. candidate_finbif_setID_plus_collection_metadata
5. candidate_finbif_setID_only_review
6. candidate_finbif_collection_metadata_no_institutionCode
7. review_no_external_institution_evidence
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests


def clean(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


def nonblank(x: Any) -> bool:
    return clean(x) != ""


def extract_collection_code(collection_id: str) -> str:
    """
    General tail-code extraction.

    Examples:
      http://tun.fi/HR.168 -> HR.168
      https://example.org/collections/ABC.1 -> ABC.1
      HR.85 -> HR.85
    """
    x = clean(collection_id)
    if not x:
        return ""
    x = x.rstrip("/")
    return x.split("/")[-1]


def load_json(path: str | Path) -> Any:
    raw = Path(path).read_bytes()
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return json.loads(raw.decode("latin-1"))


def flatten_values(obj: Any, prefix: str = "", out: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                flatten_values(v, key, out)
            else:
                val = clean(v)
                if val:
                    out.append((key, val))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            flatten_values(v, f"{prefix}[{i}]", out)
    return out


def key_tail(key: str) -> str:
    k = clean(key).split(".")[-1]
    if "]" in k:
        k = k.split("]")[-1].lstrip(".")
    return k


def extract_json_signals(json_path: str, cwd: Path) -> dict[str, str]:
    """
    Pull institution-related signals from local occurrence JSON.

    We use setID only as provider-derived candidate evidence unless it is backed by
    direct metadata elsewhere.
    """
    p = Path(clean(json_path))
    if not p:
        return {
            "json_setID_values": "",
            "json_datasetID_values": "",
            "json_direct_institutionCode_values": "",
        }

    if not p.is_absolute():
        p = cwd / p

    if not p.exists():
        return {
            "json_setID_values": "",
            "json_datasetID_values": "",
            "json_direct_institutionCode_values": "",
        }

    try:
        data = load_json(p)
    except Exception:
        return {
            "json_setID_values": "",
            "json_datasetID_values": "",
            "json_direct_institutionCode_values": "",
        }

    flat = flatten_values(data)
    set_ids = []
    dataset_ids = []
    inst_codes = []

    for k, v in flat:
        tail = key_tail(k)
        if tail in {"setID", "setId", "setid"}:
            set_ids.append(v)
        elif tail in {"dwc:datasetID", "datasetID"}:
            dataset_ids.append(v)
        elif tail in {"dwc:institutionCode", "institutionCode"}:
            inst_codes.append(v)

    return {
        "json_setID_values": ";".join(dict.fromkeys(set_ids)),
        "json_datasetID_values": ";".join(dict.fromkeys(dataset_ids)),
        "json_direct_institutionCode_values": ";".join(dict.fromkeys(inst_codes)),
    }


class CachedRequester:
    def __init__(self, cache_path: Path, sleep_seconds: float = 0.2):
        self.cache_path = cache_path
        self.sleep_seconds = sleep_seconds
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        if self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        else:
            self.cache = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "scribe-tech-test-pipeline/institution-from-collectionID"})

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        token_param_name: str | None = None,
        token_value: str | None = None,
    ) -> dict[str, Any]:
        params = dict(params or {})
        if token_param_name and token_value:
            params[token_param_name] = token_value

        # Cache key excludes the actual token value.
        safe_params = dict(params)
        if token_param_name and token_param_name in safe_params:
            safe_params[token_param_name] = "***"

        key = json.dumps({"url": url, "params": safe_params}, sort_keys=True, ensure_ascii=False)
        if key in self.cache:
            return self.cache[key]

        try:
            r = self.session.get(url, params=params, timeout=40)
            try:
                data = r.json()
            except Exception:
                data = {"_non_json_text_preview": r.text[:500]}
            payload = {
                "status_code": r.status_code,
                "safe_url": r.url.replace(token_value, "***") if token_value else r.url,
                "json": data,
            }
        except Exception as e:
            payload = {
                "status_code": "ERROR",
                "safe_url": url,
                "json": {"error": f"{type(e).__name__}: {e}"},
            }

        self.cache[key] = payload
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(self.sleep_seconds)
        return payload


def pick_exact_grscicoll_collection(results: list[dict[str, Any]], code: str) -> dict[str, Any]:
    wanted = clean(code).lower()
    for item in results:
        if clean(item.get("code")).lower() == wanted:
            return item
    return {}


def lookup_grscicoll(code: str, requester: CachedRequester) -> dict[str, str]:
    """
    GBIF GRSciColl exact code lookup.
    """
    out = {
        "grscicoll_collection_status_code": "",
        "grscicoll_collection_results_count": "",
        "grscicoll_collection_safe_url": "",
        "grscicoll_selected_collection_key": "",
        "grscicoll_selected_collection_code": "",
        "grscicoll_selected_collection_name": "",
        "grscicoll_institutionKey": "",
        "grscicoll_institution_status_code": "",
        "grscicoll_institutionCode": "",
        "grscicoll_institutionName": "",
    }

    if not code:
        return out

    payload = requester.get_json(
        "https://api.gbif.org/v1/grscicoll/collection",
        params={"code": code, "limit": 20},
    )
    out["grscicoll_collection_status_code"] = clean(payload.get("status_code"))
    out["grscicoll_collection_safe_url"] = clean(payload.get("safe_url"))

    data = payload.get("json") or {}
    results = data.get("results", []) if isinstance(data, dict) else []
    out["grscicoll_collection_results_count"] = clean(len(results))

    selected = pick_exact_grscicoll_collection(results, code)
    if not selected:
        return out

    out["grscicoll_selected_collection_key"] = clean(selected.get("key"))
    out["grscicoll_selected_collection_code"] = clean(selected.get("code"))
    out["grscicoll_selected_collection_name"] = clean(selected.get("name"))
    out["grscicoll_institutionKey"] = clean(selected.get("institutionKey"))

    inst_code = clean(selected.get("institutionCode"))
    inst_name = clean(selected.get("institutionName"))

    if out["grscicoll_institutionKey"]:
        inst_payload = requester.get_json(
            f"https://api.gbif.org/v1/grscicoll/institution/{out['grscicoll_institutionKey']}",
            params={},
        )
        out["grscicoll_institution_status_code"] = clean(inst_payload.get("status_code"))
        inst_data = inst_payload.get("json") or {}
        if isinstance(inst_data, dict):
            inst_code = clean(inst_data.get("code")) or inst_code
            inst_name = clean(inst_data.get("name")) or inst_name

    out["grscicoll_institutionCode"] = inst_code
    out["grscicoll_institutionName"] = inst_name
    return out


def lookup_finbif_collection(code: str, token: str, requester: CachedRequester) -> dict[str, str]:
    """
    FinBIF exact collection path lookup:
      https://api.laji.fi/v0/collections/{code}?access_token=...

    This is preferred over q= search because q= can return unrelated collections.
    """
    out = {
        "finbif_collection_status_code": "",
        "finbif_collection_safe_url": "",
        "finbif_errorCode": "",
        "finbif_collectionName": "",
        "finbif_collectionCode": "",
        "finbif_institutionCode": "",
        "finbif_intellectualOwner": "",
        "finbif_publisherShortname": "",
        "finbif_owner": "",
        "finbif_longName": "",
    }

    if not code or not token:
        return out

    payload = requester.get_json(
        f"https://api.laji.fi/v0/collections/{quote(code, safe='')}",
        params={"lang": "en"},
        token_param_name="access_token",
        token_value=token,
    )
    out["finbif_collection_status_code"] = clean(payload.get("status_code"))
    out["finbif_collection_safe_url"] = clean(payload.get("safe_url"))

    data = payload.get("json") or {}
    if not isinstance(data, dict):
        return out

    out["finbif_errorCode"] = clean(data.get("errorCode")) or clean((data.get("error") or {}).get("errorCode") if isinstance(data.get("error"), dict) else "")
    out["finbif_collectionName"] = clean(data.get("collectionName"))
    out["finbif_collectionCode"] = clean(data.get("collectionCode"))
    out["finbif_institutionCode"] = clean(data.get("institutionCode"))
    out["finbif_intellectualOwner"] = clean(data.get("intellectualOwner"))
    out["finbif_publisherShortname"] = clean(data.get("publisherShortname"))
    out["finbif_owner"] = clean(data.get("owner"))
    out["finbif_longName"] = clean(data.get("longName"))
    return out


def choose_candidate(row: dict[str, str], allow_setid_only_fill: bool = False) -> dict[str, str]:
    """
    Conservative decision logic.
    """
    grs_code = clean(row.get("grscicoll_institutionCode"))
    grs_name = clean(row.get("grscicoll_institutionName"))

    fin_code = clean(row.get("finbif_institutionCode"))
    fin_name = clean(row.get("finbif_publisherShortname")) or clean(row.get("finbif_intellectualOwner"))

    setid = clean(row.get("json_setID_values"))
    fin_coll_name = clean(row.get("finbif_collectionName")) or clean(row.get("finbif_longName"))
    fin_owner = clean(row.get("finbif_intellectualOwner")) or clean(row.get("finbif_publisherShortname"))

    candidate_code = ""
    candidate_name = ""
    source = ""
    evidence_class = ""
    action = ""

    if fin_code and grs_code and fin_code == grs_code:
        candidate_code = fin_code
        candidate_name = fin_name or grs_name
        source = "grscicoll_collection_code+finbif_collection_metadata"
        evidence_class = "direct_grscicoll_and_finbif_agree"
        action = "fill_direct"
    elif fin_code:
        candidate_code = fin_code
        candidate_name = fin_name
        source = "finbif_collection_metadata"
        evidence_class = "direct_finbif_collection_metadata"
        action = "fill_direct"
    elif grs_code:
        candidate_code = grs_code
        candidate_name = grs_name
        source = "grscicoll_collection_code"
        evidence_class = "direct_grscicoll_collection_code"
        action = "fill_direct"
    elif setid and fin_coll_name:
        candidate_code = setid
        candidate_name = fin_owner
        source = "finbif_setID+finbif_collection_metadata"
        evidence_class = "candidate_finbif_setID_plus_collection_metadata"
        action = "fill_candidate_if_policy_allows" if allow_setid_only_fill else "review_only"
    elif setid:
        candidate_code = setid
        candidate_name = ""
        source = "finbif_setID"
        evidence_class = "candidate_finbif_setID_only_review"
        action = "fill_candidate_if_policy_allows" if allow_setid_only_fill else "review_only"
    elif fin_coll_name or fin_owner:
        candidate_code = ""
        candidate_name = fin_owner
        source = "finbif_collection_metadata"
        evidence_class = "candidate_finbif_collection_metadata_no_institutionCode"
        action = "review_only"
    else:
        source = ""
        evidence_class = "review_no_external_institution_evidence"
        action = "review_only"

    return {
        "candidate_institutionCode": candidate_code,
        "candidate_institutionName": candidate_name,
        "candidate_institutionCode_source": source,
        "candidate_evidence_class": evidence_class,
        "candidate_action": action,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-csv", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--collection-id-col", default="collectionID")
    ap.add_argument("--institution-col", default="institutionCode")
    ap.add_argument("--json-path-col", default="local_json_path")
    ap.add_argument("--record-key-col", default="record_key")
    ap.add_argument("--scientific-name-col", default="scientificName")
    ap.add_argument("--finbif-token-env", default="FINBIF_TOKEN")
    ap.add_argument("--no-finbif", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--apply-direct-fill", action="store_true",
                    help="Write recovered institutionCode into a copy of the table for direct evidence only.")
    ap.add_argument("--allow-setid-only-fill", action="store_true",
                    help="Allow setID-derived candidate as fill policy. Default is review-only.")
    args = ap.parse_args()

    input_csv = Path(args.input_csv)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_csv, dtype=str).fillna("")
    cwd = Path.cwd()

    if args.collection_id_col not in df.columns:
        raise SystemExit(f"Missing collectionID column: {args.collection_id_col}")
    if args.institution_col not in df.columns:
        raise SystemExit(f"Missing institutionCode column: {args.institution_col}")

    token = "" if args.no_finbif else os.environ.get(args.finbif_token_env, "").strip()
    use_finbif = bool(token) and not args.no_finbif

    gbif_req = CachedRequester(out_dir / "cache_gbif_grscicoll.json", sleep_seconds=args.sleep)
    finbif_req = CachedRequester(out_dir / "cache_finbif_collections.json", sleep_seconds=args.sleep)

    # Target rows: collectionID present, institutionCode blank.
    target = df[
        df[args.collection_id_col].map(nonblank)
        & ~df[args.institution_col].map(nonblank)
    ].copy()

    target["collection_code_candidate"] = target[args.collection_id_col].map(extract_collection_code)

    # Per-row JSON signals first.
    json_signal_rows = []
    for idx, r in target.iterrows():
        sig = {
            "_row_index": idx,
            args.collection_id_col: clean(r.get(args.collection_id_col)),
            "collection_code_candidate": clean(r.get("collection_code_candidate")),
        }
        if args.json_path_col in target.columns:
            sig.update(extract_json_signals(clean(r.get(args.json_path_col)), cwd))
        else:
            sig.update({
                "json_setID_values": "",
                "json_datasetID_values": "",
                "json_direct_institutionCode_values": "",
            })
        json_signal_rows.append(sig)

    json_signals = pd.DataFrame(json_signal_rows)

    # Collapse JSON setID signals by collectionID/code.
    if len(json_signals):
        json_by_code = (
            json_signals
            .groupby([args.collection_id_col, "collection_code_candidate"], dropna=False)
            .agg({
                "json_setID_values": lambda s: ";".join(dict.fromkeys(x for v in s for x in clean(v).split(";") if x)),
                "json_datasetID_values": lambda s: ";".join(dict.fromkeys(x for v in s for x in clean(v).split(";") if x)),
                "json_direct_institutionCode_values": lambda s: ";".join(dict.fromkeys(x for v in s for x in clean(v).split(";") if x)),
            })
            .reset_index()
        )
    else:
        json_by_code = pd.DataFrame(columns=[
            args.collection_id_col, "collection_code_candidate",
            "json_setID_values", "json_datasetID_values", "json_direct_institutionCode_values"
        ])

    summary = (
        target.groupby([args.collection_id_col, "collection_code_candidate"], dropna=False)
        .size()
        .reset_index(name="records_missing_institutionCode")
        .sort_values("records_missing_institutionCode", ascending=False)
    )

    lookup_rows = []
    for _, r in summary.iterrows():
        collection_id = clean(r[args.collection_id_col])
        code = clean(r["collection_code_candidate"])

        row = {
            args.collection_id_col: collection_id,
            "collection_code_candidate": code,
            "records_missing_institutionCode": clean(r["records_missing_institutionCode"]),
        }

        row.update(lookup_grscicoll(code, gbif_req))

        if use_finbif:
            row.update(lookup_finbif_collection(code, token, finbif_req))
        else:
            row.update({
                "finbif_collection_status_code": "",
                "finbif_collection_safe_url": "",
                "finbif_errorCode": "FINBIF_DISABLED_OR_TOKEN_MISSING",
                "finbif_collectionName": "",
                "finbif_collectionCode": "",
                "finbif_institutionCode": "",
                "finbif_intellectualOwner": "",
                "finbif_publisherShortname": "",
                "finbif_owner": "",
                "finbif_longName": "",
            })

        # add collapsed local JSON signals
        m = json_by_code[
            (json_by_code[args.collection_id_col].eq(collection_id))
            & (json_by_code["collection_code_candidate"].eq(code))
        ]
        if len(m):
            for col in ["json_setID_values", "json_datasetID_values", "json_direct_institutionCode_values"]:
                row[col] = clean(m.iloc[0][col])
        else:
            row["json_setID_values"] = ""
            row["json_datasetID_values"] = ""
            row["json_direct_institutionCode_values"] = ""

        row.update(choose_candidate(row, allow_setid_only_fill=args.allow_setid_only_fill))
        lookup_rows.append(row)

    lookup = pd.DataFrame(lookup_rows)

    # Row-level merge.
    row_level = target.merge(
        json_signals.drop(columns=[args.collection_id_col, "collection_code_candidate"], errors="ignore"),
        left_index=True,
        right_on="_row_index",
        how="left",
    )

    row_level = row_level.merge(
        lookup,
        on=[args.collection_id_col, "collection_code_candidate"],
        how="left",
        suffixes=("", "_lookup"),
    )

    # Candidate-filled copy. Default fills only direct evidence if requested.
    filled = df.copy()
    filled["candidate_institutionCode"] = ""
    filled["candidate_institutionName"] = ""
    filled["candidate_institutionCode_source"] = ""
    filled["candidate_evidence_class"] = ""
    filled["candidate_action"] = ""

    for _, r in row_level.iterrows():
        idx = int(r["_row_index"])
        for col in [
            "candidate_institutionCode",
            "candidate_institutionName",
            "candidate_institutionCode_source",
            "candidate_evidence_class",
            "candidate_action",
        ]:
            filled.loc[idx, col] = clean(r.get(col))

        if args.apply_direct_fill:
            if clean(r.get("candidate_action")) == "fill_direct" and clean(r.get("candidate_institutionCode")):
                filled.loc[idx, args.institution_col] = clean(r.get("candidate_institutionCode"))
                filled.loc[idx, f"source_{args.institution_col}"] = clean(r.get("candidate_institutionCode_source"))
                filled.loc[idx, f"evidence_{args.institution_col}"] = clean(r.get("candidate_evidence_class"))

        if args.apply_direct_fill and args.allow_setid_only_fill:
            if clean(r.get("candidate_action")) == "fill_candidate_if_policy_allows" and clean(r.get("candidate_institutionCode")):
                filled.loc[idx, args.institution_col] = clean(r.get("candidate_institutionCode"))
                filled.loc[idx, f"source_{args.institution_col}"] = clean(r.get("candidate_institutionCode_source"))
                filled.loc[idx, f"evidence_{args.institution_col}"] = clean(r.get("candidate_evidence_class"))

    # Outputs.
    lookup_path = out_dir / "collectionID_institution_lookup_summary.tsv"
    row_path = out_dir / "row_level_institutionCode_candidates.tsv"
    filled_path = out_dir / "institutionCode_candidate_augmented_table.csv"
    class_path = out_dir / "candidate_evidence_class_counts.tsv"

    lookup.to_csv(lookup_path, sep="\t", index=False)
    row_level.to_csv(row_path, sep="\t", index=False)
    filled.to_csv(filled_path, index=False)

    class_counts = (
        row_level["candidate_evidence_class"]
        .value_counts(dropna=False)
        .rename_axis("candidate_evidence_class")
        .reset_index(name="rows")
    )
    class_counts.to_csv(class_path, sep="\t", index=False)

    print("\n=== InstitutionCode recovery evidence classes ===")
    print(class_counts.to_string(index=False))

    print("\n=== Collection-level lookup summary ===")
    display_cols = [
        args.collection_id_col,
        "collection_code_candidate",
        "records_missing_institutionCode",
        "candidate_institutionCode",
        "candidate_institutionName",
        "candidate_institutionCode_source",
        "candidate_evidence_class",
        "candidate_action",
        "grscicoll_institutionCode",
        "finbif_institutionCode",
        "json_setID_values",
        "finbif_collectionName",
        "finbif_publisherShortname",
    ]
    display_cols = [c for c in display_cols if c in lookup.columns]
    print(lookup[display_cols].to_string(index=False))

    print("\nSaved:")
    print(lookup_path)
    print(row_path)
    print(filled_path)
    print(class_path)

    if not use_finbif:
        print(f"\nNOTE: FinBIF lookup was not used. Set {args.finbif_token_env} to enable it.")


if __name__ == "__main__":
    main()
