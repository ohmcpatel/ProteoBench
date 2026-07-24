"""Unit tests for public accession discovery and file selection."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mining" / "paper_mine"))

from stage5_fetch_data import (  # noqa: E402
    extract_accessions,
    is_processed_file,
    is_raw_file,
    select_files,
    validate_candidate,
)


def _file(name: str, category: str, size: int = 10) -> dict:
    return {
        "fileName": name,
        "fileSizeBytes": size,
        "fileCategory": {"value": category},
        "publicFileLocations": [{"value": f"https://example.test/{name}"}],
    }


def test_extract_accessions_deduplicates_and_normalizes() -> None:
    assert extract_accessions(["PXD054360", "pXD054360; PXD054417"]) == ["PXD054360", "PXD054417"]


def test_processed_selection_excludes_raw_and_prefers_metadata() -> None:
    raw = _file("sample.raw", "RAW", 100)
    sdrf = _file("experiment_SDRF.tsv", "OTHER", 20)
    result = _file("protein_Report.tsv", "SEARCH", 30)
    assert is_raw_file(raw)
    assert not is_processed_file(raw)
    selected = select_files([raw, result, sdrf], "run manifest and QC metrics", 2)
    assert [x["file_name"] for x in selected] == ["experiment_SDRF.tsv", "protein_Report.tsv"]


def test_validation_flags_missing_run_manifest() -> None:
    manifest = [_file("protein_Report.tsv", "SEARCH")]
    selected = select_files(manifest, "per-run QC metrics and run manifest", 2)
    result = validate_candidate("per-run QC metrics and run manifest", manifest, selected)
    assert not result["ready_for_task_bundle"]
    assert any("manifest" in warning.lower() for warning in result["warnings"])
