#!/usr/bin/env python3
"""Create/update auditable quality-gate records for candidate tasks.

Source verification is computed from stage5 fetch reports. The remaining gates
are explicit human or offline-computation attestations and default to pending.

Examples:
  python stage6_quality_gates.py --candidate C01-E01-A_pmid39248652_v1 --init
  python stage6_quality_gates.py --candidate C01-E01-A_pmid39248652_v1 \
    --set bundle_verified=pass --set estimand_identifiable=pass
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from common import load_config, read_json, write_json, read_jsonl

GATES = (
    "source_verified", "bundle_verified", "estimand_identifiable",
    "reference_reproduced", "sensitivity_passed",
    "independently_replicated", "frozen_eval",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record auditable scientific quality gates")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--set", action="append", default=[], metavar="GATE=STATUS")
    parser.add_argument("--evidence", action="append", default=[], metavar="GATE=TEXT")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    candidates = read_jsonl(Path(cfg["paths"]["dataset_dir"]) / "candidates.jsonl")
    candidate = next((c for c in candidates if c.get("candidate_id") == args.candidate), None)
    if candidate is None:
        parser.error(f"candidate not found: {args.candidate}")

    quality_dir = Path(cfg["paths"]["dataset_dir"]) / "quality_gates"
    path = quality_dir / f"{args.candidate}.json"
    record = read_json(path) if path.exists() else {
        "candidate_id": args.candidate,
        "gates": {gate: "pending" for gate in GATES},
        "evidence": {},
    }
    if args.init and not path.exists():
        record["created_at"] = datetime.now(timezone.utc).isoformat()
    for item in args.set:
        try:
            gate, status = item.split("=", 1)
        except ValueError:
            parser.error(f"invalid --set value: {item}; use GATE=pass|fail|pending")
        if gate not in GATES or status not in {"pass", "fail", "pending"}:
            parser.error(f"invalid gate/status: {item}")
        record.setdefault("gates", {})[gate] = status
    for item in args.evidence:
        try:
            gate, evidence = item.split("=", 1)
        except ValueError:
            parser.error(f"invalid --evidence value: {item}; use GATE=TEXT")
        if gate not in GATES:
            parser.error(f"invalid gate: {gate}")
        record.setdefault("evidence", {})[gate] = evidence
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(path, record)
    print(f"Quality gates → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
