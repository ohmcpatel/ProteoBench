#!/usr/bin/env python3
"""Stage 3: Claude extraction of ProteoBench candidate task cards from papers."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from common import (
    ROOT,
    ensure_dirs,
    filter_packs,
    load_config,
    read_json,
    read_jsonl,
    write_json,
)
from schemas import CandidateCard, InputRole, SourcePaper

SYSTEM_PROMPT = """You are helping build ProteoBench, a benchmark of bounded computational proteomics tasks for AI agents.

Given ONE cube-cell archetype and ONE scientific paper (abstract and optional full text), produce a SINGLE candidate task card as JSON.

Rules:
- The task must fit the cube cell's analytical category, evidence family, and task depth.
- The task must be empirically bounded and deterministically gradable (no open essay).
- Do NOT invent PMIDs, DOIs, dataset accessions, or numeric results not supported by the text.
- If no public accession is stated, use an empty list for public_data_accessions.
- supporting_excerpts must be short quotes copied from the provided text only.
- If the paper is a poor fit, still return JSON but set viability to "low" and explain in issues.
- Return ONLY valid JSON matching the schema. No markdown fences.
"""


def paper_text_blob(paper: dict[str, Any], max_chars: int) -> str:
    parts = [
        f"Title: {paper.get('title') or ''}",
        f"Year: {paper.get('year')}",
        f"Journal: {paper.get('journal')}",
        f"PMID: {paper.get('pmid')}",
        f"DOI: {paper.get('doi')}",
        f"Abstract:\n{paper.get('abstract') or ''}",
    ]
    ft = paper.get("fulltext") or ""
    if ft:
        parts.append(f"Full text excerpt:\n{ft[:max_chars]}")
    blob = "\n\n".join(parts)
    if len(blob) > max_chars + 2000:
        blob = blob[: max_chars + 2000] + "\n...[truncated]..."
    return blob


def build_user_prompt(pack: dict[str, Any], paper: dict[str, Any], max_chars: int) -> str:
    cell = {
        "cube_cell_id": pack["cube_cell_id"],
        "analytical_category": pack["analytical_category"],
        "evidence_family": pack["evidence_family"],
        "task_depth": pack["task_depth"],
        "task_depth_definition": pack.get("task_depth_definition"),
        "archetype_title": pack["archetype_title"],
        "bounded_objective": pack["bounded_objective"],
        "example_structured_output": pack.get("example_structured_output"),
        "likely_grader_types": pack.get("likely_grader_types"),
        "primary_failure_modes": pack.get("primary_failure_modes"),
        "acquisition_method_tags": pack.get("acquisition_method_tags"),
    }
    schema_hint = {
        "candidate_id": f"{pack['cube_cell_id']}_pmid{paper.get('pmid') or 'NA'}_v1",
        "cube_cell_id": pack["cube_cell_id"],
        "source_paper": {
            "pmid": paper.get("pmid"),
            "pmcid": paper.get("pmcid"),
            "doi": paper.get("doi"),
            "title": paper.get("title"),
            "year": paper.get("year"),
        },
        "archetype_title": pack["archetype_title"],
        "task_sketch": "string",
        "bounded_question": "string",
        "required_inputs": [{"role": "string", "likely_format": "string"}],
        "structured_output_schema": {},
        "grader_hint": "multiple_choice|label_set|numeric_tolerance|structured_dictionary|exact_match",
        "ground_truth_sketch": "string",
        "public_data_accessions": [],
        "fits_cell_rationale": "string",
        "scientific_plausibility": "high|medium|low",
        "data_feasibility": "high|medium|low|unknown",
        "viability": "high|medium|low",
        "issues": [],
        "supporting_excerpts": ["short quote"],
    }
    return (
        "CUBE CELL:\n"
        + json.dumps(cell, indent=2)
        + "\n\nPAPER:\n"
        + paper_text_blob(paper, max_chars)
        + "\n\nOUTPUT SCHEMA (fill values):\n"
        + json.dumps(schema_hint, indent=2)
    )


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


def validate_card(raw: dict[str, Any], pack: dict[str, Any], paper: dict[str, Any], model: str) -> CandidateCard:
    # Force identity fields from trusted sources
    pmid = paper.get("pmid")
    cid = f"{pack['cube_cell_id']}_pmid{pmid or 'NA'}_v1"
    sp = raw.get("source_paper") if isinstance(raw.get("source_paper"), dict) else {}
    source = SourcePaper(
        pmid=str(pmid) if pmid else (sp.get("pmid") if sp.get("pmid") else None),
        pmcid=paper.get("pmcid") or sp.get("pmcid"),
        doi=paper.get("doi") or sp.get("doi"),
        title=paper.get("title") or sp.get("title") or "",
        year=paper.get("year") if paper.get("year") is not None else sp.get("year"),
    )
    inputs_raw = raw.get("required_inputs") or []
    inputs: list[InputRole] = []
    for item in inputs_raw:
        if isinstance(item, dict):
            inputs.append(
                InputRole(
                    role=str(item.get("role") or "input"),
                    likely_format=str(item.get("likely_format") or "to_be_determined"),
                )
            )
    schema = raw.get("structured_output_schema") or {}
    if not isinstance(schema, dict):
        schema = {"value": schema}

    return CandidateCard(
        candidate_id=cid,
        cube_cell_id=pack["cube_cell_id"],
        source_paper=source,
        archetype_title=pack["archetype_title"],
        task_sketch=str(raw.get("task_sketch") or ""),
        bounded_question=str(raw.get("bounded_question") or ""),
        required_inputs=inputs,
        structured_output_schema=schema,
        grader_hint=str(raw.get("grader_hint") or "multiple_choice"),
        ground_truth_sketch=str(raw.get("ground_truth_sketch") or ""),
        public_data_accessions=[str(x) for x in (raw.get("public_data_accessions") or [])],
        fits_cell_rationale=str(raw.get("fits_cell_rationale") or ""),
        scientific_plausibility=str(raw.get("scientific_plausibility") or "medium"),
        data_feasibility=str(raw.get("data_feasibility") or "unknown"),
        viability=str(raw.get("viability") or "medium"),
        issues=[str(x) for x in (raw.get("issues") or [])],
        supporting_excerpts=[str(x) for x in (raw.get("supporting_excerpts") or [])][:5],
        extract_status="ok",
        model=model,
        extracted_at=datetime.utcnow().isoformat() + "Z",
    )


def log_cost(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ts",
                "cube_cell_id",
                "pmid",
                "model",
                "input_tokens",
                "output_tokens",
                "status",
                "candidate_id",
            ],
        )
        if write_header:
            w.writeheader()
        w.writerow(row)


def call_claude(client: Any, model: str, user: str, max_tokens: int, temperature: float) -> tuple[str, dict[str, int]]:
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = ""
    for block in msg.content:
        if hasattr(block, "text"):
            text += block.text
    usage = {
        "input_tokens": getattr(msg.usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(msg.usage, "output_tokens", 0) or 0,
    }
    return text, usage


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Extract candidate cards with Claude")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--batch", action="append", dest="batches", default=None)
    parser.add_argument("--cell", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Max papers to process this run")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    ensure_dirs(cfg)
    packs = {
        p["cube_cell_id"]: p
        for p in filter_packs(
            read_jsonl(Path(cfg["paths"]["queue_dir"]) / "search_packs.jsonl"),
            batches=args.batches or cfg.get("batches"),
            cell=args.cell,
        )
    }
    if not packs:
        print("ERROR: no packs; run export_queue.py", file=sys.stderr)
        return 1

    ranked_dir = Path(cfg["paths"]["ranked_dir"])
    cand_dir = Path(cfg["paths"]["candidates_dir"])
    costs_path = Path(cfg["paths"]["costs_dir"]) / "calls.csv"
    model = args.model or cfg.get("claude_model") or "claude-sonnet-4-5"
    max_tokens = int(cfg.get("claude_max_tokens", 4096))
    temperature = float(cfg.get("claude_temperature", 0.1))
    retries = int(cfg.get("claude_repair_retries", 1))
    max_chars = int(cfg.get("max_fulltext_chars", 20000))

    # Build work list from ranked files
    work: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for cell_id, pack in packs.items():
        ranked_path = ranked_dir / f"{cell_id}.json"
        if not ranked_path.exists():
            print(f"WARN: no ranked file for {cell_id}", file=sys.stderr)
            continue
        papers = read_json(ranked_path).get("papers") or []
        for paper in papers:
            work.append((pack, paper))

    if args.limit is not None:
        work = work[: args.limit]

    if args.dry_run:
        print(f"Dry run: would process {len(work)} paper×cell pairs with model={model}")
        for pack, paper in work[:10]:
            print(f"  {pack['cube_cell_id']} pmid={paper.get('pmid')} {str(paper.get('title'))[:60]}")
        if len(work) > 10:
            print(f"  ... +{len(work) - 10} more")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: set ANTHROPIC_API_KEY in paper_mine/.env", file=sys.stderr)
        return 1

    try:
        import anthropic
    except ImportError:
        print("ERROR: pip install anthropic", file=sys.stderr)
        return 1

    client = anthropic.Anthropic(api_key=api_key)
    processed = 0
    skipped = 0
    failed = 0

    for pack, paper in work:
        cell_id = pack["cube_cell_id"]
        pmid = paper.get("pmid") or "NA"
        out_path = cand_dir / cell_id / f"pmid{pmid}.json"
        if out_path.exists():
            skipped += 1
            continue

        user = build_user_prompt(pack, paper, max_chars)
        status = "ok"
        card: CandidateCard | None = None
        usage = {"input_tokens": 0, "output_tokens": 0}

        for attempt in range(retries + 1):
            try:
                text, usage = call_claude(client, model, user, max_tokens, temperature)
                raw = extract_json_object(text)
                card = validate_card(raw, pack, paper, model)
                break
            except Exception as e:
                status = f"error:{type(e).__name__}"
                if attempt >= retries:
                    failed += 1
                    card = CandidateCard(
                        candidate_id=f"{cell_id}_pmid{pmid}_v1",
                        cube_cell_id=cell_id,
                        source_paper=SourcePaper(
                            pmid=str(pmid) if pmid != "NA" else None,
                            title=paper.get("title") or "",
                            year=paper.get("year"),
                            doi=paper.get("doi"),
                            pmcid=paper.get("pmcid"),
                        ),
                        archetype_title=pack["archetype_title"],
                        task_sketch="",
                        bounded_question="",
                        extract_status="failed",
                        issues=[str(e)],
                        model=model,
                        extracted_at=datetime.utcnow().isoformat() + "Z",
                    )
                else:
                    user = user + f"\n\nPrevious output failed validation ({e}). Return corrected JSON only."

        assert card is not None
        write_json(out_path, card.model_dump())
        log_cost(
            costs_path,
            {
                "ts": datetime.utcnow().isoformat() + "Z",
                "cube_cell_id": cell_id,
                "pmid": pmid,
                "model": model,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "status": card.extract_status,
                "candidate_id": card.candidate_id,
            },
        )
        processed += 1
        print(f"[{processed}] {card.candidate_id} viability={card.viability} status={card.extract_status}")

    print(f"Done. processed={processed} skipped_existing={skipped} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
