#!/usr/bin/env python3
"""Stage 2: rank fetched papers per cube cell and keep top-K for Claude."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from common import (
    ensure_dirs,
    filter_packs,
    load_config,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from schemas import PaperRecord

ACCESSION_RE = re.compile(
    r"\b(PXD\d+|MSV\d+|PASS\d+|S-BSST\d+|PDC\d+|CPTAC[- ]?\w+)\b",
    re.IGNORECASE,
)

EVIDENCE_KEYWORDS: dict[str, list[str]] = {
    "E01": ["dda", "data-dependent", "label-free", "shotgun", "maxquant", "msfragger"],
    "E02": ["dia", "data-independent", "diann", "spectronaut", "swath"],
    "E03": ["tmt", "itraq", "isobaric", "plex"],
    "E04": ["prm", "srm", "mrm", "targeted", "skyline", "parallel reaction"],
    "E05": ["ptm", "phosphorylation", "ubiquitin", "glycosylation", "top-down", "proteoform", "modifi"],
    "E06": ["olink", "soma", "aptamer", "proximity extension", "affinity proteomics", "ngs"],
    "E07": ["ap-ms", "affinity purification", "cross-link", "xl-ms", "interact", "complex"],
    "E08": ["spatial proteomics", "maldi", "imaging mass", "deep visual proteomics", "lmd"],
    "E09": ["single-cell proteomics", "scp", "single cell proteomics", "nanoproteomics"],
    "E10": ["multi-omics", "proteogenomic", "integrated", "orthogonal"],
}

TASK_KEYWORDS: dict[str, list[str]] = {
    "C01": ["quality control", "qc", "batch effect", "carryover", "failed run", "mzqc", "contamination"],
    "C02": ["identification", "peptide-spectrum", "psm", "protein group", "fdr", "inference", "decoy"],
    "C03": ["quantif", "normalization", "lfq", "intensity", "abundance"],
    "C04": ["differential", "statistical", "limma", "msstats", "fold change", "p-value"],
    "C05": ["localization", "site-specific", "peptidoform", "modification"],
    "C06": ["interaction", "bait", "prey", "complex", "co-elut"],
    "C07": ["spatial", "single-cell", "cell type", "region"],
    "C08": ["validation", "lod", "loq", "precision", "reproducibility", "cv"],
    "C09": ["biomarker", "clinical", "translational", "pharmacodynamic", "patient"],
}


def score_paper(paper: PaperRecord, pack: dict[str, Any]) -> tuple[float, list[str]]:
    text = f"{paper.title}\n{paper.abstract}\n{paper.fulltext[:5000]}".lower()
    score = 0.0
    reasons: list[str] = []

    if not paper.abstract and not paper.fulltext:
        return -10.0, ["no_abstract"]

    # Abstract presence
    if paper.abstract and len(paper.abstract) > 100:
        score += 1.0
        reasons.append("has_abstract")

    if paper.text_quality == "fulltext_oa":
        score += 2.0
        reasons.append("oa_fulltext")
    elif paper.is_oa:
        score += 0.5
        reasons.append("oa_flag")

    ecode = pack.get("evidence_family_code", "")
    for kw in EVIDENCE_KEYWORDS.get(ecode, []):
        if kw in text:
            score += 1.5
            reasons.append(f"evidence:{kw}")
            break

    # acquisition tags
    for tag in pack.get("acquisition_method_tags") or []:
        if tag.lower() in text:
            score += 0.8
            reasons.append(f"acq:{tag}")
            break

    ccode = pack.get("analytical_category_code", "")
    for kw in TASK_KEYWORDS.get(ccode, []):
        if kw in text:
            score += 1.2
            reasons.append(f"task:{kw}")
            break

    # title keywords from archetype
    title_bits = re.findall(r"[a-z]{4,}", pack.get("archetype_title", "").lower())
    hits = sum(1 for w in title_bits[:8] if w in text)
    if hits:
        score += min(hits * 0.4, 2.0)
        reasons.append(f"title_kw_hits:{hits}")

    acc = ACCESSION_RE.findall(f"{paper.abstract} {paper.fulltext[:8000]}")
    if acc:
        score += 2.5
        reasons.append(f"accession:{acc[0]}")

    # Downrank reviews / editorials lightly
    if re.search(r"\b(review|editorial|perspective|commentary)\b", paper.title.lower()):
        score -= 1.5
        reasons.append("reviewish_title")

    # Prefer newer slightly
    if paper.year and paper.year >= 2018:
        score += 0.3
        reasons.append("recent")
    elif paper.year and paper.year < 2010:
        score -= 0.5
        reasons.append("old")

    return score, reasons


def rank_cell(
    pack: dict[str, Any],
    papers_dir: Path,
    top_k: int,
) -> list[dict[str, Any]]:
    cell_id = pack["cube_cell_id"]
    cell_dir = papers_dir / cell_id
    if not cell_dir.exists():
        return []

    scored: list[PaperRecord] = []
    for path in cell_dir.glob("*.json"):
        data = read_json(path)
        paper = PaperRecord.model_validate(data)
        s, reasons = score_paper(paper, pack)
        paper.rank_score = s
        paper.rank_reasons = reasons
        scored.append(paper)

    scored.sort(key=lambda p: (p.rank_score or -999), reverse=True)
    top = scored[:top_k]
    return [p.model_dump() for p in top]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank papers per cube cell")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--batch", action="append", dest="batches", default=None)
    parser.add_argument("--cell", type=str, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    ensure_dirs(cfg)
    packs_path = Path(cfg["paths"]["queue_dir"]) / "search_packs.jsonl"
    if not packs_path.exists():
        print("ERROR: run export_queue.py first", file=sys.stderr)
        return 1

    packs = filter_packs(
        read_jsonl(packs_path),
        batches=args.batches or cfg.get("batches"),
        cell=args.cell,
    )
    papers_dir = Path(cfg["paths"]["papers_dir"])
    ranked_dir = Path(cfg["paths"]["ranked_dir"])
    top_k = args.top_k or int(cfg.get("top_k_for_claude", 5))

    all_ranked: list[dict[str, Any]] = []
    empty = 0
    for pack in packs:
        cell_id = pack["cube_cell_id"]
        top = rank_cell(pack, papers_dir, top_k)
        write_json(ranked_dir / f"{cell_id}.json", {"cube_cell_id": cell_id, "papers": top})
        for p in top:
            all_ranked.append(p)
        n = len(top)
        if n == 0:
            empty += 1
            print(f"{cell_id}: 0 papers (fetch first?)")
        else:
            best = top[0].get("rank_score")
            print(f"{cell_id}: top {n} (best_score={best:.2f}) — {top[0].get('title', '')[:70]}")

    write_jsonl(ranked_dir / "ranked_all.jsonl", all_ranked)
    print(f"\nRanked {len(packs)} cells; empty={empty}; total paper slots={len(all_ranked)}")
    print(f"Wrote {ranked_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
