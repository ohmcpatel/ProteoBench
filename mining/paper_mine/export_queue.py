#!/usr/bin/env python3
"""Export pilot search packs from the cube inventory CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from common import (
    build_seed_queries,
    ensure_dirs,
    load_config,
    parse_json_maybe,
    write_jsonl,
)
from schemas import SearchPack


def row_to_pack(row: pd.Series, max_queries: int) -> SearchPack:
    acq = parse_json_maybe(row.get("acquisition_method_tags_json")) or []
    exp = parse_json_maybe(row.get("experiment_type_tags_json")) or []
    ctx = parse_json_maybe(row.get("scientific_context_tags_json")) or []
    inputs = parse_json_maybe(row.get("example_agent_inputs_json")) or []
    output = parse_json_maybe(row.get("example_structured_output_json"))
    graders = parse_json_maybe(row.get("likely_grader_types_json")) or []
    failures = parse_json_maybe(row.get("primary_failure_modes_json")) or []
    urls = parse_json_maybe(row.get("source_urls_json")) or []

    title = str(row["archetype_title"])
    evidence = str(row["evidence_family"])
    queries = build_seed_queries(
        title=title,
        evidence_family=evidence,
        acquisition_tags=list(acq),
        experiment_tags=list(exp),
        max_queries=max_queries,
    )

    return SearchPack(
        cube_cell_id=str(row["cube_cell_id"]),
        batch_id=str(row["batch_id"]),
        analytical_category_code=str(row["analytical_category_code"]),
        analytical_category=str(row["analytical_category"]),
        evidence_family_code=str(row["evidence_family_code"]),
        evidence_family=evidence,
        task_depth=str(row["task_depth"]),
        task_depth_definition=str(row["task_depth_definition"]),
        cell_viability=str(row["cell_viability"]),
        candidate_generation_target=int(row["candidate_generation_target"]),
        final_selection_target_v0=int(row["final_selection_target_v0"]),
        archetype_title=title,
        bounded_objective=str(row["bounded_objective"]),
        example_agent_inputs=list(inputs) if isinstance(inputs, list) else [],
        example_structured_output=output if isinstance(output, (dict, list)) else None,
        likely_grader_types=list(graders),
        primary_failure_modes=list(failures),
        acquisition_method_tags=list(acq),
        experiment_type_tags=list(exp),
        scientific_context_tags=list(ctx),
        source_urls=list(urls),
        seed_queries=queries,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export pilot search packs from cube inventory")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--batches",
        nargs="*",
        default=None,
        help="Override config batches (e.g. B01 B02)",
    )
    parser.add_argument(
        "--all-priority",
        action="store_true",
        help="Export all final_selection_target_v0 > 0 cells (ignore pilot batches)",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    ensure_dirs(cfg)

    cube_path = Path(cfg["cube_csv"])
    if not cube_path.exists():
        print(f"ERROR: cube CSV not found: {cube_path}", file=sys.stderr)
        return 1

    df = pd.read_csv(cube_path)
    if cfg.get("only_final_selection", True):
        df = df[df["final_selection_target_v0"] > 0]

    batches = None if args.all_priority else (args.batches or cfg.get("batches") or [])
    if batches:
        df = df[df["batch_id"].isin(batches)]

    df = df.sort_values(["batch_id", "cube_cell_id"]).reset_index(drop=True)
    max_q = int(cfg.get("max_queries_per_cell", 4))

    packs = [row_to_pack(row, max_q).model_dump() for _, row in df.iterrows()]

    queue_dir = Path(cfg["paths"]["queue_dir"])
    packs_path = queue_dir / "search_packs.jsonl"
    cells_path = queue_dir / "priority_cells.csv"

    write_jsonl(packs_path, packs)
    df.to_csv(cells_path, index=False)

    print(f"Wrote {len(packs)} search packs → {packs_path}")
    print(f"Wrote priority cells CSV → {cells_path}")
    if packs:
        by_batch: dict[str, int] = {}
        for p in packs:
            by_batch[p["batch_id"]] = by_batch.get(p["batch_id"], 0) + 1
        for b, n in sorted(by_batch.items()):
            print(f"  {b}: {n} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
