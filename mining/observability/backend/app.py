#!/usr/bin/env python3
"""FastAPI backend for ProteoBench mining yield / funnel observability."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

MINING_ROOT = Path(__file__).resolve().parents[2]
PAPER_MINE = MINING_ROOT / "paper_mine"
if str(PAPER_MINE) not in sys.path:
    sys.path.insert(0, str(PAPER_MINE))

from common import load_config, read_json  # noqa: E402
from compute_yield import compute_snapshot  # noqa: E402
from yield_schemas import (  # noqa: E402
    DROP_REASON_LABELS,
    STAGE_META,
    STAGE_ORDER,
    DropReasonCode,
    YieldSnapshot,
)

REVIEW_TOKEN = os.environ.get("REVIEW_TOKEN", "proteobench-review")

app = FastAPI(title="ProteoBench Observability", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_token(x_review_token: str | None = Header(default=None)) -> None:
    if x_review_token != REVIEW_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Review-Token")


def snapshot_path(cfg: dict[str, Any]) -> Path:
    obs = Path(cfg["paths"]["observability_dir"])
    return obs / "yield_snapshot.json"


def load_or_compute(force: bool = False) -> YieldSnapshot:
    cfg = load_config()
    path = snapshot_path(cfg)
    if not force and path.exists():
        try:
            return YieldSnapshot.model_validate(read_json(path))
        except Exception:
            pass
    snap = compute_snapshot(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    from common import write_json

    write_json(path, snap.model_dump(mode="json"))
    write_json(
        path.parent / "latest.json",
        {"path": str(path.resolve()), "generated_at": snap.generated_at},
    )
    return snap


@app.get("/api/health")
def health() -> dict[str, Any]:
    cfg = load_config()
    path = snapshot_path(cfg)
    return {
        "ok": True,
        "service": "observability",
        "snapshot_exists": path.exists(),
        "snapshot_path": str(path),
    }


@app.get("/api/meta")
def meta(x_review_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_token(x_review_token)
    return {
        "stage_order": [s.value for s in STAGE_ORDER],
        "stage_meta": {s.value: STAGE_META[s] for s in STAGE_ORDER},
        "drop_reason_labels": {c.value: DROP_REASON_LABELS[c] for c in DropReasonCode},
    }


@app.get("/api/yield")
def get_yield(
    recompute: bool = Query(default=False),
    x_review_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_token(x_review_token)
    try:
        snap = load_or_compute(force=recompute)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return snap.model_dump(mode="json")


@app.post("/api/yield/recompute")
def recompute_yield(x_review_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_token(x_review_token)
    try:
        snap = load_or_compute(force=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "ok": True,
        "generated_at": snap.generated_at,
        "kpis": snap.kpis,
    }


@app.get("/api/yield/stages")
def yield_stages(x_review_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_token(x_review_token)
    snap = load_or_compute(False)
    return {"generated_at": snap.generated_at, "stages": [s.model_dump(mode="json") for s in snap.stages]}


@app.get("/api/yield/cells")
def yield_cells(
    batch: str | None = Query(default=None),
    bottleneck: str | None = Query(default=None),
    x_review_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_token(x_review_token)
    snap = load_or_compute(False)
    rows = snap.cells
    if batch:
        rows = [c for c in rows if c.batch_id == batch]
    if bottleneck:
        rows = [c for c in rows if c.bottleneck_stage and c.bottleneck_stage.value == bottleneck]
    return {
        "generated_at": snap.generated_at,
        "n": len(rows),
        "cells": [c.model_dump(mode="json") for c in rows],
    }


@app.get("/api/yield/candidates")
def yield_candidates(
    cell: str | None = Query(default=None),
    batch: str | None = Query(default=None),
    viability: str | None = Query(default=None),
    data_feasibility: str | None = Query(default=None),
    drop_code: str | None = Query(default=None),
    survived_to: str | None = Query(default=None),
    has_accession: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    x_review_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_token(x_review_token)
    snap = load_or_compute(False)
    rows = snap.candidates
    if cell:
        rows = [c for c in rows if c.cube_cell_id == cell]
    if batch:
        rows = [c for c in rows if c.batch_id == batch]
    if viability:
        rows = [c for c in rows if (c.viability or "").lower() == viability.lower()]
    if data_feasibility:
        rows = [
            c
            for c in rows
            if (c.data_feasibility or "").lower() == data_feasibility.lower()
        ]
    if drop_code:
        rows = [c for c in rows if drop_code in [d.value for d in c.drop_codes]]
    if survived_to:
        rows = [c for c in rows if c.survived_to.value == survived_to]
    if has_accession is True:
        rows = [c for c in rows if c.n_accessions > 0]
    if has_accession is False:
        rows = [c for c in rows if c.n_accessions == 0]
    total = len(rows)
    return {
        "generated_at": snap.generated_at,
        "n": total,
        "candidates": [c.model_dump(mode="json") for c in rows[:limit]],
    }


@app.get("/api/yield/dropoffs")
def yield_dropoffs(x_review_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Between-stage drop-offs with reason rollups for the UI funnel."""
    require_token(x_review_token)
    snap = load_or_compute(False)
    edges = []
    stages = snap.stages
    for i in range(1, len(stages)):
        prev = stages[i - 1]
        cur = stages[i]
        edges.append(
            {
                "from_stage": prev.stage_id.value,
                "to_stage": cur.stage_id.value,
                "from_label": prev.label,
                "to_label": cur.label,
                "from_passed": prev.passed,
                "to_passed": cur.passed,
                "dropped": max(0, cur.dropped),
                "conversion": cur.conversion_from_prev,
                "unit_from": prev.unit,
                "unit_to": cur.unit,
                "drop_reasons": [r.model_dump(mode="json") for r in cur.drop_reasons],
            }
        )
    return {"generated_at": snap.generated_at, "edges": edges, "kpis": snap.kpis}
