#!/usr/bin/env python3
"""Stage 1: fetch paper metadata/abstracts (and OA full text) via Europe PMC."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from common import (
    ROOT,
    ensure_dirs,
    filter_packs,
    load_config,
    read_json,
    read_jsonl,
    write_json,
)
from schemas import PaperRecord

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPE_PMC_FT = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _sleep(cfg: dict[str, Any]) -> None:
    time.sleep(float(cfg.get("sleep_between_requests_seconds", 0.4)))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=20))
def _get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    r = client.get(url, params=params)
    r.raise_for_status()
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=20))
def _get_text(client: httpx.Client, url: str) -> str:
    r = client.get(url)
    r.raise_for_status()
    return r.text


def _strip_xml_text(xml_text: str, max_chars: int) -> str:
    try:
        root = ET.fromstring(xml_text)
        texts = [t.strip() for t in root.itertext() if t and t.strip()]
        joined = "\n".join(texts)
    except ET.ParseError:
        joined = re.sub(r"<[^>]+>", " ", xml_text)
        joined = re.sub(r"\s+", " ", joined).strip()
    if len(joined) > max_chars:
        return joined[:max_chars] + "\n...[truncated]..."
    return joined


def search_europepmc(
    client: httpx.Client,
    query: str,
    page_size: int,
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "format": "json",
        "pageSize": min(page_size, 100),
        "resultType": "core",
    }
    data = _get_json(client, EUROPE_PMC_SEARCH, params)
    _sleep(cfg)
    return list(data.get("resultList", {}).get("result", []) or [])


def fetch_fulltext(
    client: httpx.Client,
    source: str,
    paper_id: str,
    cfg: dict[str, Any],
) -> tuple[str, str | None]:
    """Try Europe PMC fullTextXML. source is usually MED or PMC."""
    max_chars = int(cfg.get("max_fulltext_chars", 20000))
    url = f"{EUROPE_PMC_FT}/{source}/{paper_id}/fullTextXML"
    try:
        xml_text = _get_text(client, url)
        _sleep(cfg)
        text = _strip_xml_text(xml_text, max_chars)
        if len(text) > 200:
            return text, f"europepmc_fullTextXML:{source}/{paper_id}"
    except Exception:
        pass
    return "", None


def result_to_paper(
    hit: dict[str, Any],
    cell_id: str,
    query: str,
    client: httpx.Client,
    cfg: dict[str, Any],
    fetch_ft: bool,
) -> PaperRecord:
    pmid = hit.get("pmid") or hit.get("id") if hit.get("source") == "MED" else hit.get("pmid")
    pmcid = hit.get("pmcid")
    doi = hit.get("doi")
    title = hit.get("title") or ""
    abstract = hit.get("abstractText") or ""
    year_raw = hit.get("pubYear")
    try:
        year = int(year_raw) if year_raw else None
    except (TypeError, ValueError):
        year = None
    journal = hit.get("journalTitle")
    authors = hit.get("authorString")
    is_oa = str(hit.get("isOpenAccess", "N")).upper() in {"Y", "TRUE", "1"}
    source = hit.get("source") or "MED"
    paper_id = hit.get("id") or pmid or ""

    fulltext = ""
    fulltext_source = None
    text_quality = "abstract_only"
    if fetch_ft and is_oa and paper_id:
        fulltext, fulltext_source = fetch_fulltext(client, source, str(paper_id), cfg)
        if not fulltext and pmcid:
            # try PMC id form
            pmc_num = pmcid.replace("PMC", "")
            fulltext, fulltext_source = fetch_fulltext(client, "PMC", pmc_num, cfg)
        if fulltext:
            text_quality = "fulltext_oa"

    return PaperRecord(
        cube_cell_id=cell_id,
        pmid=str(pmid) if pmid else None,
        pmcid=str(pmcid) if pmcid else None,
        doi=str(doi) if doi else None,
        title=title,
        abstract=abstract,
        year=year,
        journal=journal,
        authors=authors,
        is_oa=is_oa,
        fulltext=fulltext,
        fulltext_source=fulltext_source,
        text_quality=text_quality,
        query_used=query,
        source_api="europepmc",
    )


def paper_path(papers_dir: Path, cell_id: str, paper: PaperRecord) -> Path:
    return papers_dir / cell_id / f"{paper.paper_key}.json"


def fetch_for_cell(
    pack: dict[str, Any],
    client: httpx.Client,
    cfg: dict[str, Any],
    papers_dir: Path,
    fetch_ft: bool,
) -> tuple[int, int]:
    cell_id = pack["cube_cell_id"]
    queries = pack.get("seed_queries") or []
    page_size = int(cfg.get("papers_per_query", 20))
    max_raw = int(cfg.get("max_papers_raw_per_cell", 40))

    seen_keys: set[str] = set()
    # existing cache
    cell_dir = papers_dir / cell_id
    if cell_dir.exists():
        for p in cell_dir.glob("*.json"):
            seen_keys.add(p.stem)

    new_count = 0
    skipped = 0
    for query in queries:
        if len(seen_keys) >= max_raw:
            break
        try:
            hits = search_europepmc(client, query, page_size, cfg)
        except Exception as e:
            print(f"  WARN search failed for {cell_id}: {e}", file=sys.stderr)
            continue
        for hit in hits:
            if len(seen_keys) >= max_raw:
                break
            paper = result_to_paper(hit, cell_id, query, client, cfg, fetch_ft=False)
            key = paper.paper_key
            if key in seen_keys or key == "unknown":
                skipped += 1
                continue
            # Fetch fulltext only when we keep the paper
            if fetch_ft and paper.is_oa:
                paper = result_to_paper(hit, cell_id, query, client, cfg, fetch_ft=True)
            out = paper_path(papers_dir, cell_id, paper)
            if out.exists():
                skipped += 1
                seen_keys.add(key)
                continue
            write_json(out, paper.model_dump())
            seen_keys.add(key)
            new_count += 1
    return new_count, skipped


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Fetch papers for search packs via Europe PMC")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--batch", action="append", dest="batches", default=None)
    parser.add_argument("--cell", type=str, default=None)
    parser.add_argument("--limit-cells", type=int, default=None)
    parser.add_argument("--no-fulltext", action="store_true", help="Skip OA full-text fetch")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    ensure_dirs(cfg)
    packs_path = Path(cfg["paths"]["queue_dir"]) / "search_packs.jsonl"
    if not packs_path.exists():
        print("ERROR: run export_queue.py first", file=sys.stderr)
        return 1

    packs = read_jsonl(packs_path)
    batches = args.batches or cfg.get("batches")
    packs = filter_packs(packs, batches=batches, cell=args.cell)
    if args.limit_cells is not None:
        packs = packs[: args.limit_cells]

    papers_dir = Path(cfg["paths"]["papers_dir"])
    timeout = float(cfg.get("request_timeout_seconds", 60))
    fetch_ft = not args.no_fulltext

    print(f"Fetching for {len(packs)} cells → {papers_dir}")
    total_new = 0
    with httpx.Client(timeout=timeout, headers={"User-Agent": "ProteoBench-paper-mine/0.1"}) as client:
        for i, pack in enumerate(packs, 1):
            cell_id = pack["cube_cell_id"]
            print(f"[{i}/{len(packs)}] {cell_id} {pack['archetype_title'][:60]}")
            new_c, skipped = fetch_for_cell(pack, client, cfg, papers_dir, fetch_ft=fetch_ft)
            n_cached = len(list((papers_dir / cell_id).glob("*.json"))) if (papers_dir / cell_id).exists() else 0
            print(f"  new={new_c} skipped={skipped} total_on_disk={n_cached}")
            total_new += new_c

    print(f"Done. New papers written: {total_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
