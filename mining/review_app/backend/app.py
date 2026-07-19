#!/usr/bin/env python3
"""Minimal FastAPI backend for ProteoBench expert keep/revise/reject review."""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

DATA_DIR = Path(__file__).resolve().parent / "data"
CANDIDATES_PATH = DATA_DIR / "candidates.jsonl"
FEEDBACK_PATH = DATA_DIR / "feedback.jsonl"
ASSIGNMENTS_PATH = DATA_DIR / "assignments.json"

REVIEW_TOKEN = os.environ.get("REVIEW_TOKEN", "proteobench-review")

app = FastAPI(title="ProteoBench Review", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_candidates() -> list[dict[str, Any]]:
    return _read_jsonl(CANDIDATES_PATH)


def load_feedback() -> list[dict[str, Any]]:
    return _read_jsonl(FEEDBACK_PATH)


def load_assignments() -> dict[str, Any]:
    if not ASSIGNMENTS_PATH.exists():
        return {"default": [], "reviewers": {}}
    with open(ASSIGNMENTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def require_token(x_review_token: str | None = Header(default=None)) -> None:
    if x_review_token != REVIEW_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Review-Token")


class FeedbackIn(BaseModel):
    candidate_id: str
    reviewer: str = Field(min_length=1)
    verdict: str  # keep|revise|reject
    confidence: str = "med"
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "candidates": len(load_candidates()),
        "feedback": len(load_feedback()),
    }


@app.get("/api/candidates")
def list_candidates(
    reviewer: str | None = Query(default=None),
    batch: str | None = Query(default=None),
    only_unreviewed: bool = Query(default=False),
    _: None = Depends(require_token),
) -> dict[str, Any]:
    cands = load_candidates()
    assigns = load_assignments()
    allowed: set[str] | None = None
    if reviewer:
        rev_map = assigns.get("reviewers") or {}
        if reviewer in rev_map and rev_map[reviewer]:
            allowed = set(rev_map[reviewer])
        else:
            allowed = set(assigns.get("default") or [c["candidate_id"] for c in cands])

    feedback = load_feedback()
    reviewed_by: dict[str, set[str]] = {}
    for fb in feedback:
        reviewed_by.setdefault(fb["candidate_id"], set()).add(fb.get("reviewer", ""))

    out = []
    for c in cands:
        cid = c["candidate_id"]
        if allowed is not None and cid not in allowed:
            continue
        if batch and c.get("batch_id") != batch:
            continue
        reviewers_done = reviewed_by.get(cid, set())
        if only_unreviewed and reviewer and reviewer in reviewers_done:
            continue
        out.append(
            {
                **c,
                "review_count": len(reviewers_done),
                "reviewed_by_me": bool(reviewer and reviewer in reviewers_done),
            }
        )
    return {"count": len(out), "candidates": out}


@app.get("/api/candidates/{candidate_id}")
def get_candidate(candidate_id: str, _: None = Depends(require_token)) -> dict[str, Any]:
    for c in load_candidates():
        if c["candidate_id"] == candidate_id:
            return c
    raise HTTPException(status_code=404, detail="Candidate not found")


@app.get("/api/feedback")
def list_feedback(
    candidate_id: str | None = None,
    reviewer: str | None = None,
    _: None = Depends(require_token),
) -> dict[str, Any]:
    rows = load_feedback()
    if candidate_id:
        rows = [r for r in rows if r.get("candidate_id") == candidate_id]
    if reviewer:
        rows = [r for r in rows if r.get("reviewer") == reviewer]
    return {"count": len(rows), "feedback": rows}


@app.post("/api/feedback")
def post_feedback(body: FeedbackIn, _: None = Depends(require_token)) -> dict[str, Any]:
    if body.verdict not in {"keep", "revise", "reject"}:
        raise HTTPException(status_code=400, detail="verdict must be keep|revise|reject")
    if body.verdict in {"revise", "reject"} and not body.notes.strip():
        raise HTTPException(status_code=400, detail="notes required for revise/reject")

    # Ensure candidate exists
    if not any(c["candidate_id"] == body.candidate_id for c in load_candidates()):
        raise HTTPException(status_code=404, detail="Unknown candidate_id")

    row = {
        "feedback_id": str(uuid.uuid4()),
        "candidate_id": body.candidate_id,
        "reviewer": body.reviewer.strip(),
        "verdict": body.verdict,
        "confidence": body.confidence,
        "notes": body.notes.strip(),
        "tags": body.tags,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    _append_jsonl(FEEDBACK_PATH, row)
    return row


@app.get("/api/admin/summary")
def admin_summary(_: None = Depends(require_token)) -> dict[str, Any]:
    cands = load_candidates()
    feedback = load_feedback()
    by_verdict: dict[str, int] = {}
    by_candidate: dict[str, list] = {}
    for fb in feedback:
        by_verdict[fb["verdict"]] = by_verdict.get(fb["verdict"], 0) + 1
        by_candidate.setdefault(fb["candidate_id"], []).append(fb)

    cells: dict[str, dict[str, int]] = {}
    for c in cands:
        cell = c.get("cube_cell_id") or "?"
        cells.setdefault(cell, {"candidates": 0, "reviewed": 0, "keep": 0, "revise": 0, "reject": 0})
        cells[cell]["candidates"] += 1
        fbs = by_candidate.get(c["candidate_id"], [])
        if fbs:
            cells[cell]["reviewed"] += 1
            # latest vote wins for summary
            v = fbs[-1]["verdict"]
            cells[cell][v] = cells[cell].get(v, 0) + 1

    return {
        "n_candidates": len(cands),
        "n_feedback": len(feedback),
        "by_verdict": by_verdict,
        "by_cell": cells,
    }


@app.get("/api/admin/export.csv")
def export_csv(_: None = Depends(require_token)) -> StreamingResponse:
    feedback = load_feedback()
    buf = io.StringIO()
    fields = [
        "feedback_id",
        "candidate_id",
        "reviewer",
        "verdict",
        "confidence",
        "notes",
        "tags",
        "created_at",
    ]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for row in feedback:
        w.writerow(
            {
                **{k: row.get(k, "") for k in fields if k != "tags"},
                "tags": ";".join(row.get("tags") or []),
            }
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=feedback_export.csv"},
    )


@app.get("/")
def root() -> PlainTextResponse:
    return PlainTextResponse(
        "ProteoBench review API. Use the React frontend or /api/health.\n"
        f"Token header: X-Review-Token (default env REVIEW_TOKEN).\n"
        f"Candidates file: {CANDIDATES_PATH}\n"
    )
