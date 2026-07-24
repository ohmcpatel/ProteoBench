#!/usr/bin/env python3
"""Stage 5: resolve public proteomics accessions and fetch useful files.

This stage deliberately starts with PRIDE-hosted ProteomeXchange accessions.
It fetches manifests for all supported accessions and, when requested, downloads
small processed/supporting files. Raw vendor files are never selected by the
default policy. The output is a per-candidate JSON report that can be reviewed
before staging a compact bundle as a ProteoBench ``data_node``.

Examples (from mining/paper_mine):

  python stage5_fetch_data.py --candidate C01-E01-A_pmid39248652_v1 --download
  python stage5_fetch_data.py --accession PXD054360 --download --max-files 2
  python stage5_fetch_data.py --all
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from common import ensure_dirs, load_config, read_json, read_jsonl, write_json

ACCESSION_RE = re.compile(r"\bPXD\d{6}\b", re.I)
DEFAULT_PAGE_SIZE = 100
PROCESSED_EXTENSIONS = {
    ".csv", ".tsv", ".txt", ".xlsx", ".json", ".mzidentml", ".mzid",
    ".mztab", ".pepxml", ".pep.xml", ".params", ".sdrf", ".xml",
}
RAW_EXTENSIONS = {".raw", ".wiff", ".d", ".baf", ".mzml", ".mzxml"}
METADATA_WORDS = ("sdrf", "manifest", "metadata", "sample", "design", "experimental")
QC_WORDS = ("qc", "quality", "metric", "timeseries", "system", "suitability")


def extract_accessions(values: Any) -> list[str]:
    """Return unique supported accessions from strings/lists."""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    found: list[str] = []
    for value in values:
        for match in ACCESSION_RE.findall(str(value)):
            accession = match.upper()
            if accession not in found:
                found.append(accession)
    return found


def _file_name(item: dict[str, Any]) -> str:
    return str(item.get("fileName") or item.get("name") or "")


def _category(item: dict[str, Any]) -> str:
    category = item.get("fileCategory") or {}
    if isinstance(category, dict):
        return str(category.get("value") or category.get("name") or "").upper()
    return str(category).upper()


def _suffix(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pep.xml"):
        return ".pep.xml"
    return Path(lower).suffix


def is_raw_file(item: dict[str, Any]) -> bool:
    return _suffix(_file_name(item)) in RAW_EXTENSIONS or _category(item) == "RAW"


def is_processed_file(item: dict[str, Any]) -> bool:
    name = _file_name(item)
    return not is_raw_file(item) and (_suffix(name) in PROCESSED_EXTENSIONS or _category(item) in {"SEARCH", "RESULT", "QUANTIFICATION"})


def _locations(item: dict[str, Any]) -> list[str]:
    locations = item.get("publicFileLocations") or []
    out: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        value = str(location.get("value") or "")
        if value.startswith("ftp://ftp.pride.ebi.ac.uk/"):
            value = "https://ftp.pride.ebi.ac.uk/" + value.removeprefix("ftp://ftp.pride.ebi.ac.uk/")
        if value.startswith("http"):
            out.append(value)
    return out


def file_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_name": _file_name(item),
        "size_bytes": item.get("fileSizeBytes"),
        "category": _category(item),
        "is_raw": is_raw_file(item),
        "is_processed": is_processed_file(item),
        "urls": _locations(item),
        "accession": item.get("accession"),
    }


def fetch_manifest(client: httpx.Client, accession: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    base = str(cfg.get("data_fetch", {}).get("pride_api_base", "https://www.ebi.ac.uk/pride/ws/archive/v3")).rstrip("/")
    page_size = int(cfg.get("data_fetch", {}).get("manifest_page_size", DEFAULT_PAGE_SIZE))
    max_pages = int(cfg.get("data_fetch", {}).get("max_manifest_pages", 100))
    rows: list[dict[str, Any]] = []
    for page in range(max_pages):
        response = client.get(
            f"{base}/projects/{quote(accession)}/files",
            params={"page": page, "pageSize": page_size},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            page_rows = payload.get("content") or payload.get("_embedded", {}).get("files") or []
        else:
            page_rows = payload
        if not isinstance(page_rows, list):
            raise ValueError(f"Unexpected PRIDE manifest response for {accession}")
        rows.extend(x for x in page_rows if isinstance(x, dict))
        if len(page_rows) < page_size:
            break
    return rows


def select_files(manifest: list[dict[str, Any]], required_text: str, max_files: int) -> list[dict[str, Any]]:
    """Select metadata/QC/processed files, ranked for a candidate's needs."""
    required = required_text.lower()

    def score(item: dict[str, Any]) -> tuple[int, int, str]:
        name = _file_name(item).lower()
        category = _category(item)
        score_value = 0
        if any(word in name for word in METADATA_WORDS):
            score_value += 50
        if any(word in name for word in QC_WORDS):
            score_value += 45
        if "run" in required and ("run" in name or "timeseries" in name):
            score_value += 20
        if "qc" in required and any(word in name for word in QC_WORDS):
            score_value += 20
        if category in {"SEARCH", "RESULT", "QUANTIFICATION"}:
            score_value += 20
        if _suffix(name) in {".csv", ".tsv", ".sdrf", ".mztab", ".mzidentml", ".mzid", ".pepxml", ".pep.xml"}:
            score_value += 10
        size = int(item.get("fileSizeBytes") or 0)
        return (-score_value, size, name)

    candidates = [x for x in manifest if is_processed_file(x) and _locations(x)]
    return [file_summary(x) for x in sorted(candidates, key=score)[:max_files]]


def validate_candidate(required_text: str, manifest: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    names = [_file_name(x).lower() for x in manifest]
    selected_names = [x["file_name"].lower() for x in selected]
    has_metadata = any(any(word in name for word in METADATA_WORDS) for name in names)
    has_qc = any(any(word in name for word in QC_WORDS) for name in names)
    has_processed = any(is_processed_file(x) for x in manifest)
    required = required_text.lower()
    checks = {
        "manifest_nonempty": bool(manifest),
        "processed_files_present": has_processed,
        "metadata_or_run_manifest_present": has_metadata or "run" not in required,
        "qc_or_metric_file_present": has_qc or not any(word in required for word in ("qc", "metric")),
        "selected_files_downloadable": all(bool(x.get("urls")) for x in selected),
    }
    warnings: list[str] = []
    if not manifest:
        warnings.append("Accession returned no files from the repository API.")
    if not checks["metadata_or_run_manifest_present"]:
        warnings.append("No filename clearly identifies a sample/run metadata manifest.")
    if not checks["qc_or_metric_file_present"]:
        warnings.append("No filename clearly identifies a QC/metrics file; processed search results may still be usable.")
    if not selected_names:
        warnings.append("No non-raw downloadable processed files were selected.")
    return {
        "checks": checks,
        "ready_for_task_bundle": bool(manifest) and has_processed and not warnings,
        "warnings": warnings,
        "manifest_counts": {
            "total": len(manifest),
            "raw": sum(is_raw_file(x) for x in manifest),
            "processed": sum(is_processed_file(x) for x in manifest),
        },
    }


def download_selected(client: httpx.Client, selected: list[dict[str, Any]], destination: Path, max_bytes: int) -> list[dict[str, Any]]:
    destination.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for item in selected:
        size = int(item.get("size_bytes") or 0)
        result = {"file_name": item["file_name"], "status": "skipped", "path": None, "error": None}
        if size > max_bytes:
            result["error"] = f"file exceeds max_download_bytes ({size} > {max_bytes})"
            results.append(result)
            continue
        url = (item.get("urls") or [None])[0]
        if not url:
            result["error"] = "no public HTTPS URL"
            results.append(result)
            continue
        path = destination / Path(item["file_name"]).name
        if path.exists() and path.stat().st_size == size:
            result.update(status="exists", path=str(path))
            results.append(result)
            continue
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(path, "wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        result.update(status="downloaded", path=str(path), size_bytes=path.stat().st_size)
        results.append(result)
    return results


def build_report(candidate_id: str, required_text: str, accession: str, manifest: list[dict[str, Any]], selected: list[dict[str, Any]], downloads: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "accession": accession,
        "required_inputs": required_text,
        "selected_files": selected,
        "downloads": downloads,
        "validation": validate_candidate(required_text, manifest, selected),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and validate public proteomics accession files")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--candidate", action="append", help="Candidate ID from out/dataset/candidates.jsonl")
    parser.add_argument("--accession", action="append", help="PXD accession; can be repeated")
    parser.add_argument("--all", action="store_true", help="Process all candidates with supported accessions")
    parser.add_argument("--download", action="store_true", help="Download selected non-raw files")
    parser.add_argument("--max-files", type=int, default=5)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    ensure_dirs(cfg)
    data_dir = Path(cfg["paths"]["data_dir"])
    candidates_path = Path(cfg["paths"]["dataset_dir"]) / "candidates.jsonl"
    candidates = read_jsonl(candidates_path)
    wanted_ids = set(args.candidate or [])
    selected_candidates = [c for c in candidates if args.all or not wanted_ids or c.get("candidate_id") in wanted_ids]
    jobs: list[tuple[str, str, str]] = []
    if args.accession:
        jobs.extend(("manual", "", x.upper()) for x in extract_accessions(args.accession))
    for candidate in selected_candidates:
        required = " ".join(str(x) for x in (candidate.get("required_inputs") or []))
        for accession in extract_accessions(candidate.get("public_data_accessions")):
            jobs.append((str(candidate.get("candidate_id") or "unknown"), required, accession))
    if not jobs:
        parser.error("provide --candidate, --accession, or --all (and ensure candidates.jsonl has accessions)")

    timeout = float(cfg.get("data_fetch", {}).get("request_timeout_seconds", 120))
    max_bytes = int(cfg.get("data_fetch", {}).get("max_download_bytes", 524288000))
    reports: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for candidate_id, required, accession in jobs:
            try:
                manifest = fetch_manifest(client, accession, cfg)
                selected = select_files(manifest, required, args.max_files)
                destination = data_dir / accession / candidate_id
                downloads = download_selected(client, selected, destination, max_bytes) if args.download else []
                report = build_report(candidate_id, required, accession, manifest, selected, downloads)
                report["manifest"] = [file_summary(x) for x in manifest]
            except Exception as exc:  # keep batch runs resumable across bad accessions
                report = {"candidate_id": candidate_id, "accession": accession, "error": f"{type(exc).__name__}: {exc}"}
            report_path = data_dir / accession / f"{candidate_id}.json"
            write_json(report_path, report)
            reports.append(report)
            status = report.get("validation", {}).get("ready_for_task_bundle", False)
            print(f"{candidate_id}\t{accession}\tfiles={len(report.get('manifest', []))}\tready={status}")
    write_json(data_dir / "fetch_summary.json", {"reports": reports})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
