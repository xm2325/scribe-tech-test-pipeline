from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scribe_techtest.download import encode_url, safe_download_filename
from scribe_techtest.gbif import apply_match
from scribe_techtest.json_extract import extract_fields_from_json
from scribe_techtest.report import field_coverage, render_gallery_report


def test_url_encoding_and_filename_safety() -> None:
    url = "https://zenodo.org/record/1479323/files/B 10 0172773.jpg"
    assert "B%2010%200172773.jpg" in encode_url(url)
    name = safe_download_filename("new_data_0241", url, "jpg")
    assert " " not in name
    assert name.endswith(".jpg")


def test_extract_fields_from_jsonld_occurrence_graph() -> None:
    data = {
        "@graph": [
            {
                "@id": "occurrence",
                "dwc:occurrenceID": "abc",
                "dwc:scientificName": "Abelia forrestii (Diels) W.W.Sm.",
                "dwc:family": "Caprifoliaceae",
                "dwc:recordedBy": [{"@value": "Collector A"}],
                "dc:typeStatus": "ISOTYPE",
            },
            {"@id": "image", "dc:format": "image/jpeg"},
        ]
    }
    fields, keys = extract_fields_from_json(data)
    assert fields["scientificName"] == "Abelia forrestii (Diels) W.W.Sm."
    assert fields["family"] == "Caprifoliaceae"
    assert fields["recordedBy"] == "Collector A"
    assert fields["typeStatus"] == "ISOTYPE"
    assert keys["typeStatus"] == "dc:typeStatus"


def test_apply_gbif_match_fills_missing_taxonomy_only() -> None:
    df = pd.DataFrame(
        [
            {
                "canonicalName": "",
                "phylum": "",
                "class": "",
                "order": "",
                "family": "Existing family",
                "genus": "",
                "source_canonicalName": "",
                "source_phylum": "",
                "source_class": "",
                "source_order": "",
                "source_family": "json",
                "source_genus": "",
            }
        ]
    )
    match = {
        "usageKey": 123,
        "confidence": 98,
        "matchType": "EXACT",
        "status": "ACCEPTED",
        "rank": "SPECIES",
        "canonicalName": "Abelia forrestii",
        "phylum": "Tracheophyta",
        "class": "Magnoliopsida",
        "order": "Dipsacales",
        "family": "Caprifoliaceae",
        "genus": "Abelia",
    }
    apply_match(df, 0, match)
    assert df.loc[0, "canonicalName"] == "Abelia forrestii"
    assert df.loc[0, "source_canonicalName"] == "gbif"
    assert df.loc[0, "family"] == "Existing family"
    assert df.loc[0, "source_family"] == "json"


def test_report_omits_why_inspect_text(tmp_path: Path) -> None:
    row = {
        "record_key": "new_data_0001",
        "source_sheet": "new_data",
        "source_row": "2",
        "index": "1",
        "DOI": "",
        "jpegURL": "https://example.test/image.jpg",
        "jsonURL": "https://example.test/specimen.json",
        "occurrenceID": "occ-1",
        "scientificName": "Adiantum macrophyllum",
        "source_scientificName": "json",
        "family": "",
        "source_family": "",
    }
    from scribe_techtest.constants import TARGET_FIELDS

    for field in TARGET_FIELDS:
        row.setdefault(field, "")
        row.setdefault(f"source_{field}", "")
    df = pd.DataFrame([row])
    coverage = field_coverage(df)
    output = tmp_path / "report.html"
    render_gallery_report(
        df,
        coverage,
        output,
        title="Test report",
        subtitle="Review",
        stage_label="JSON",
    )
    html = output.read_text(encoding="utf-8")
    assert "Why inspect this image" not in html
    assert "Field Coverage Summary" in html
    assert "Adiantum macrophyllum" in html
