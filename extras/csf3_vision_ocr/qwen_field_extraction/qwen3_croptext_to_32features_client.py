#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import traceback
from pathlib import Path

import requests


TARGET_FIELDS = [
    "typeStatus",
    "scientificName",
    "canonicalName",
    "phylum",
    "order",
    "family",
    "genus",
    "class",
    "dateIdentified",
    "verbatimEventDate",
    "eventDate",
    "year",
    "month",
    "day",
    "verbatimLocality",
    "locality",
    "continent",
    "countryCode",
    "country",
    "verbatimCoordinates",
    "decimalLatitude",
    "decimalLongitude",
    "habitat",
    "verbatimElevation",
    "elevation",
    "identifiedBy",
    "verbatimRecordedBy",
    "recordedBy",
    "collectionID",
    "institutionCode",
    "fieldNotes",
    "organismRemarks",
]


SYSTEM_PROMPT = """You are a conservative herbarium-label data extraction engine.

Task:
Extract ONLY the requested Darwin Core / herbarium fields from OCR text.

Strict rules:
1. Output valid JSON only. No markdown. No explanation outside JSON.
2. Fill only fields requested in blank_fields.
3. Do not overwrite or repeat fields that are not requested.
4. Do not invent values.
5. Use OCR evidence only. Do not use outside knowledge.
6. If a value is not visible in OCR text, return an empty string for that field.
7. Prefer verbatim label wording for verbatim fields.
8. For normalized date fields, only normalize when the date is clearly visible.
9. For decimalLatitude/decimalLongitude, only fill if coordinates are explicitly visible.
10. For country/continent/countryCode, only fill if the label explicitly contains a country or unambiguous country code; do not infer from locality alone.
11. For scientificName/canonicalName/genus/family/order/class/phylum, only fill if clearly visible in OCR or already visible in current context; do not taxonomically infer missing higher ranks.
12. For each field, include evidence: a short OCR quote supporting the value.
13. Use confidence 0.0 to 1.0. Use <=0.5 for uncertain OCR.
14. If uncertain, leave value blank rather than guessing.

JSON schema:
{
  "record_key": "...",
  "fields": {
    "fieldName": {
      "value": "",
      "confidence": 0.0,
      "evidence": ""
    }
  }
}
"""



def clean_json_text(text: str) -> str:
    """
    Qwen3 may output <think>...</think> before the final JSON.
    Keep thinking enabled, but parse only the JSON object after thinking.
    """
    text = text or ""

    # Prefer content after the final closing thinking tag.
    if "</think>" in text:
        text = text.split("</think>")[-1]

    # Remove markdown wrappers if present.
    text = text.replace("```json", "").replace("```", "").strip()

    # Extract the outermost JSON object from the remaining text.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1].strip()

    return text.strip()

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk-id", type=int, required=True)
    ap.add_argument("--chunk-index", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--api-base", required=True)
    ap.add_argument("--model", default="qwen3_text")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--request-timeout", type=int, default=2000)
    return ap.parse_args()


def load_chunk_path(index_path: Path, chunk_id: int) -> Path:
    rows = list(csv.DictReader(index_path.open(newline="", encoding="utf-8"), delimiter="\t"))
    for r in rows:
        if int(r["chunk_id"]) == chunk_id:
            return Path(r["chunk_path"])
    raise RuntimeError(f"chunk_id not found: {chunk_id}")


def extract_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(clean_json_text(text))
    except Exception:
        pass

    # fallback: find outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(clean_json_text(text[start:end+1]))
    raise ValueError("No parseable JSON object in model response")


def call_model(api_base: str, model: str, messages: list[dict], temperature: float, max_tokens: int, timeout: int) -> str:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def build_user_prompt(row: dict) -> str:
    blank_fields = json.loads(row["blank_fields_json"])

    # Limit extreme OCR text length for safety; most specimen text should be much shorter.
    ocr_text = row.get("ocr_text", "")
    if len(ocr_text) > 24000:
        ocr_text = ocr_text[:24000] + "\n\n[TRUNCATED_AFTER_24000_CHARS]"

    current_context = {
        "record_key": row.get("record_key", ""),
        "source_sheet": row.get("source_sheet", ""),
        "source_row": row.get("source_row", ""),
        "index": row.get("index", ""),
        "occurrenceID": row.get("occurrenceID", ""),
        "scientificName_current": row.get("scientificName_current", ""),
        "country_current": row.get("country_current", ""),
        "locality_current": row.get("locality_current", ""),
        "recordedBy_current": row.get("recordedBy_current", ""),
        "institutionCode_current": row.get("institutionCode_current", ""),
    }

    return f"""Record context:
{json.dumps(current_context, ensure_ascii=False, indent=2)}

Blank fields to extract:
{json.dumps(blank_fields, ensure_ascii=False, indent=2)}

OCR text from specimen crops:
<<<OCR_TEXT_START
{ocr_text}
OCR_TEXT_END>>>

Return JSON only following the required schema.
"""


def main():
    args = parse_args()

    chunk_index = Path(args.chunk_index)
    chunk_path = load_chunk_path(chunk_index, args.chunk_id)
    rows = list(csv.DictReader(chunk_path.open(newline="", encoding="utf-8"), delimiter="\t"))

    outdir = Path(args.out_root) / f"chunk_{args.chunk_id:04d}"
    outdir.mkdir(parents=True, exist_ok=True)

    out_jsonl = outdir / "qwen3_14b_ocr_to_37column_predictions.jsonl"
    out_tsv = outdir / "qwen3_14b_ocr_to_37column_predictions.tsv"
    summary_json = outdir / "qwen3_14b_ocr_to_37column_summary.json"

    out_rows = []
    pass_count = 0
    fail_count = 0

    with out_jsonl.open("w", encoding="utf-8") as jf:
        for row in rows:
            record_key = row.get("record_key", "")
            t0 = time.time()
            status = "PASS"
            raw_response = ""
            parsed = {}
            notes = ""

            try:
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(row)},
                ]
                raw_response = call_model(
                    args.api_base,
                    args.model,
                    messages,
                    args.temperature,
                    args.max_tokens,
                    args.request_timeout,
                )
                parsed = extract_json_object(raw_response)
                if parsed.get("record_key", record_key) != record_key:
                    parsed["record_key"] = record_key
                pass_count += 1

            except Exception as e:
                status = "FAIL"
                fail_count += 1
                notes = repr(e) + "\n" + traceback.format_exc()[-4000:]
                parsed = {
                    "record_key": record_key,
                    "fields": {},
                }

            elapsed = time.time() - t0

            jf.write(json.dumps({
                "record_key": record_key,
                "status": status,
                "elapsed_sec": elapsed,
                "raw_response": raw_response,
                "parsed": parsed,
                "notes": notes,
            }, ensure_ascii=False) + "\n")
            jf.flush()

            fields_obj = parsed.get("fields", {}) if isinstance(parsed, dict) else {}
            blank_fields = json.loads(row.get("blank_fields_json", "[]"))

            for field in blank_fields:
                obj = fields_obj.get(field, {})
                if not isinstance(obj, dict):
                    obj = {"value": str(obj), "confidence": "", "evidence": ""}
                out_rows.append({
                    "record_key": record_key,
                    "chunk_id": args.chunk_id,
                    "field": field,
                    "value": str(obj.get("value", "") or "").strip(),
                    "confidence": str(obj.get("confidence", "") or "").strip(),
                    "evidence": str(obj.get("evidence", "") or "").strip(),
                    "status": status,
                    "elapsed_sec_record": f"{elapsed:.3f}",
                    "notes": notes,
                })

            print(args.chunk_id, record_key, status, f"{elapsed:.2f}s", flush=True)

    with out_tsv.open("w", newline="", encoding="utf-8") as f:
        fields = ["record_key", "chunk_id", "field", "value", "confidence", "evidence", "status", "elapsed_sec_record", "notes"]
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(out_rows)

    summary = {
        "chunk_id": args.chunk_id,
        "chunk_path": str(chunk_path),
        "records": len(rows),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "prediction_rows": len(out_rows),
        "out_jsonl": str(out_jsonl),
        "out_tsv": str(out_tsv),
    }
    summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if fail_count:
        raise SystemExit(f"FAIL_COUNT={fail_count}")


if __name__ == "__main__":
    main()
