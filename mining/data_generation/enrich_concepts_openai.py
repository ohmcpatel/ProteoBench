#!/usr/bin/env python3
"""Enrich ProtBench concepts with an LLM feasibility review and eval prompt.

The script reads one candidate per skeleton cell, uses the rubric markdown as
the system prompt, requests strict structured output from the OpenAI Responses
API, checkpoints successes, and writes canonical JSONL in skeleton order.

No key belongs in this file. Set ``OPENAI_API_KEY`` in the environment.

Examples:
    python enrich_concepts_openai.py --dry-run
    OPENAI_API_KEY=... python enrich_concepts_openai.py --resume
    python enrich_concepts_openai.py --cell C01-E01-A -o smoke.jsonl

Install dependencies:
    python -m pip install --upgrade openai pydantic
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


HERE = Path(__file__).resolve().parent
DEFAULT_SKELETON = HERE / "skeleton.csv"
DEFAULT_CONCEPTS = HERE / "candidate_concepts.jsonl"
DEFAULT_SYSTEM_PROMPT = HERE / "protbench_eval_enrichment_context.md"
DEFAULT_OUTPUT = HERE / "candidate_concepts_enriched_llm.jsonl"
DEFAULT_MODEL = os.environ.get("PROTBENCH_OPENAI_MODEL", "gpt-5.6-sol")
DIMENSIONS = (
    "sourceability",
    "data_access",
    "ground_truth_quality",
    "offline_feasibility",
    "gradability",
)


class Practicality(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rubric_version: str = "1.0.0"
    score: int = Field(ge=1, le=5)
    sourceability: int = Field(ge=1, le=5)
    data_access: int = Field(ge=1, le=5)
    ground_truth_quality: int = Field(ge=1, le=5)
    offline_feasibility: int = Field(ge=1, le=5)
    gradability: int = Field(ge=1, le=5)
    queue_status: str
    justification: str
    blocking_risk: str
    rule_trace: list[str] = Field(min_length=1)

    @field_validator("queue_status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in {"generate", "review", "park"}:
            raise ValueError("queue_status must be generate, review, or park")
        return value


class Enrichment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cell_id: str
    practicality: Practicality
    generate_eval_prompt: str = Field(min_length=900)


class EnrichmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrichment: Enrichment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skeleton", type=Path, default=DEFAULT_SKELETON)
    parser.add_argument("--concepts", type=Path, default=DEFAULT_CONCEPTS)
    parser.add_argument("--system-prompt", type=Path, default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default="medium",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--cell", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_skeleton(path: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    by_id = {row["cell_id"]: row for row in rows}
    if not rows or len(rows) != len(by_id):
        raise ValueError("skeleton is empty or contains duplicate cell IDs")
    return rows, by_id


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on {path}:{line_number}") from error
    return records


def validate_practicality(value: Practicality) -> None:
    expected = "generate" if value.score >= 4 else "review" if value.score == 3 else "park"
    if value.queue_status != expected:
        raise ValueError(
            f"score {value.score} requires queue_status={expected}, got {value.queue_status}"
        )


def build_user_prompt(concept: dict[str, Any], row: dict[str, str]) -> str:
    payload = {
        "skeleton_row": row,
        "candidate_concept": concept,
    }
    return (
        "Assess exactly this candidate with rubric v1.0.0 and produce its evaluation-generation prompt.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nPreserve the exact cell_id. Score each dimension independently, calculate the weighted "
        "overall score, apply every hard cap, and make the queue status consistent with the score. "
        "The justification must explain the evidence-specific strengths and limitations rather than "
        "merely restating the numeric scores. The blocking risk must name the single most important "
        "unresolved risk. The generate_eval_prompt must be self-contained, concept-specific, forbid "
        "invented sources, compare candidate papers/datasets, derive a concrete method and grader, "
        "retrieve only permitted data, and assemble the required reproducible workspace."
    )


def is_retryable(error: Exception) -> bool:
    code = getattr(error, "status_code", None)
    return code in {408, 409, 429} or (isinstance(code, int) and code >= 500) or error.__class__.__name__ in {
        "APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError"
    }


def call_model(client: Any, system_prompt: str, concept: dict[str, Any], row: dict[str, str], args: argparse.Namespace) -> Enrichment:
    prompt = build_user_prompt(concept, row)
    for attempt in range(args.max_retries + 1):
        try:
            response = client.responses.parse(
                model=args.model,
                reasoning={"effort": args.reasoning_effort},
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                text_format=EnrichmentResponse,
                max_output_tokens=args.max_output_tokens,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise RuntimeError("response contained no parsed output")
            enrichment = parsed.enrichment
            if enrichment.cell_id != concept["cell_id"]:
                raise ValueError("model changed the cell_id")
            validate_practicality(enrichment.practicality)
            return enrichment
        except Exception as error:
            if attempt >= args.max_retries or not is_retryable(error):
                raise
            delay = min(60.0, (2**attempt) + random.random())
            print(f"[{concept['cell_id']}] retrying in {delay:.1f}s: {error}", file=sys.stderr)
            time.sleep(delay)
    raise AssertionError("unreachable")


def merge_record(concept: dict[str, Any], row: dict[str, str], enrichment: Enrichment) -> dict[str, Any]:
    return {
        "cell_id": concept["cell_id"],
        "task_depth": row["task_depth"],
        "analytical_category": row["analytical_category"],
        "evidence_family": row["evidence_family"],
        **{key: concept[key] for key in ("title", "task", "inputs", "output", "ground_truth_path", "source_search_query")},
        "practicality": enrichment.practicality.model_dump(),
        "generate_eval_prompt": enrichment.generate_eval_prompt,
    }


def append_checkpoint(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_canonical(path: Path, rows: list[dict[str, str]], records: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            if row["cell_id"] in records:
                handle.write(json.dumps(records[row["cell_id"]], ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    if args.workers < 1 or args.max_retries < 0 or args.max_output_tokens < 1:
        raise ValueError("workers/output tokens must be positive and retries non-negative")
    if args.resume and args.overwrite:
        raise ValueError("--resume and --overwrite are mutually exclusive")

    rows, by_id = read_skeleton(args.skeleton.resolve())
    concepts_list = read_jsonl(args.concepts.resolve())
    concepts = {record["cell_id"]: record for record in concepts_list}
    if len(concepts) != len(concepts_list):
        raise ValueError("concept JSONL contains duplicate cell IDs")
    unknown = sorted(set(concepts) - set(by_id))
    missing = sorted(set(by_id) - set(concepts))
    if unknown or missing:
        raise ValueError(f"candidate/skeleton mismatch; missing={missing[:5]} unknown={unknown[:5]}")

    selected = set(args.cell) if args.cell else set(concepts)
    invalid = selected - set(concepts)
    if invalid:
        raise ValueError(f"unknown --cell values: {sorted(invalid)}")
    system_prompt = args.system_prompt.resolve().read_text(encoding="utf-8").strip()
    if not system_prompt:
        raise ValueError("system prompt is empty")

    output = args.output.resolve()
    checkpoint = output.with_name(output.name + ".checkpoint")
    if args.dry_run:
        print(f"Validated {len(concepts)} concepts; plan: {len(selected)} API calls with model {args.model}.")
        print("Dry run complete; no API calls were made and no files were written.")
        return 0
    if (output.exists() or checkpoint.exists()) and not (args.resume or args.overwrite):
        raise ValueError("output state exists; use --resume or --overwrite")
    if args.overwrite:
        for path in (output, checkpoint):
            if path.exists():
                path.unlink()
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set")

    accepted: dict[str, dict[str, Any]] = {}
    if args.resume:
        for record in read_jsonl(output) + read_jsonl(checkpoint):
            accepted[record["cell_id"]] = record
    pending = [row for row in rows if row["cell_id"] in selected and row["cell_id"] not in accepted]

    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("Install dependencies with: python -m pip install --upgrade openai pydantic") from error
    client = OpenAI()

    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(args.workers, max(1, len(pending)))) as executor:
        futures: dict[Future[Enrichment], dict[str, str]] = {
            executor.submit(call_model, client, system_prompt, concepts[row["cell_id"]], row, args): row
            for row in pending
        }
        for future in as_completed(futures):
            row = futures[future]
            cell_id = row["cell_id"]
            try:
                record = merge_record(concepts[cell_id], row, future.result())
            except Exception as error:
                failures[cell_id] = f"{error.__class__.__name__}: {error}"
                print(f"[{cell_id}] FAILED: {failures[cell_id]}", file=sys.stderr)
                continue
            append_checkpoint(checkpoint, record)
            accepted[cell_id] = record
            print(f"[{cell_id}] accepted", flush=True)

    write_canonical(output, rows, accepted)
    if not failures and checkpoint.exists():
        checkpoint.unlink()
    if failures:
        print(f"Completed with {len(failures)} failure(s); rerun with --resume.", file=sys.stderr)
        return 1
    print(f"Wrote {len(accepted)} enriched concepts -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
