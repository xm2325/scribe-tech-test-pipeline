#!/usr/bin/env python3
import csv
import json
from pathlib import Path
import os

EXTRACT = Path(os.environ.get("SCRIBE_QWEN_EXTRACT", "/net/scratch/f70829xm/qwen3_14b_ocr_to_37column_jsonfilled750_v1"))
OUT = EXTRACT / "03_outputs/merged_current_0_16"
OUT.mkdir(parents=True, exist_ok=True)

chunks = list(range(0, 17))

# record-level best status from jsonl
best_status = {}
status_source = {}

for c in chunks:
    p = EXTRACT / f"03_outputs/chunk_{c:04d}/qwen3_14b_ocr_to_37column_predictions.jsonl"
    if not p.exists():
        continue

    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue

        rk = obj.get("record_key", "")
        if not rk:
            continue

        if rk not in best_status:
            best_status[rk] = obj.get("status", "")
            status_source[rk] = f"chunk_{c:04d}"
        elif c >= 15 and obj.get("status") == "PASS":
            best_status[rk] = "PASS"
            status_source[rk] = f"chunk_{c:04d}_retry"

# field-level predictions from TSV
# Later retry chunks override earlier failed/weak rows for same record_key + field.
field_rows = {}
all_columns = set()

for c in chunks:
    p = EXTRACT / f"03_outputs/chunk_{c:04d}/qwen3_14b_ocr_to_37column_predictions.tsv"
    if not p.exists():
        continue

    rows = list(csv.DictReader(p.open(newline="", encoding="utf-8"), delimiter="\t"))
    for r in rows:
        rk = (r.get("record_key", "") or "").strip()
        field = (r.get("field", "") or "").strip()
        value = (r.get("value", "") or "").strip()
        status = (r.get("status", "") or "").strip()

        if not rk or not field:
            continue
        if status and status != "PASS":
            continue
        if not value:
            continue

        rr = dict(r)
        rr["status"] = "PASS"
        rr["merged_source"] = f"chunk_{c:04d}" + ("_retry" if c >= 15 else "")
        field_rows[(rk, field)] = rr
        all_columns.update(rr.keys())

# stable output columns
base_cols = ["record_key", "status", "field", "value", "confidence", "evidence", "elapsed_sec", "merged_source"]
extra_cols = [c for c in sorted(all_columns) if c not in base_cols]
fieldnames = base_cols + extra_cols

out_tsv = OUT / "qwen3_14b_predictions_current_0_16.tsv"
out_jsonl = OUT / "qwen3_14b_predictions_current_0_16.record_status.jsonl"
out_summary = OUT / "qwen3_14b_predictions_current_0_16.summary.json"

out_rows = [field_rows[k] for k in sorted(field_rows)]

with out_tsv.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
    w.writeheader()
    w.writerows(out_rows)

with out_jsonl.open("w", encoding="utf-8") as f:
    for rk in sorted(best_status):
        f.write(json.dumps({
            "record_key": rk,
            "status": best_status[rk],
            "merged_source": status_source.get(rk, "")
        }, ensure_ascii=False) + "\n")

summary = {
    "chunks_used": chunks,
    "records": len(best_status),
    "pass_records": sum(1 for s in best_status.values() if s == "PASS"),
    "fail_records": sum(1 for s in best_status.values() if s != "PASS"),
    "prediction_rows": len(out_rows),
    "out_tsv": str(out_tsv),
    "out_record_status_jsonl": str(out_jsonl),
}
out_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(summary, indent=2, ensure_ascii=False))
