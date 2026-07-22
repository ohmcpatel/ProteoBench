# ProteoBench mining observability

Schema-first **yield / funnel** dashboard for the paper-mine pipeline.

Tracks conversion from cube inventory → priority prune → pilot batches →
papers → ranked top-K → candidates → data gate → viability → review → stubs,
with **machine-stable drop reason codes** so you can see *why* volume fell off.

```text
mining/
  paper_mine/
    yield_schemas.py     # Pydantic contracts (stages, drop codes, snapshot)
    compute_yield.py     # Offline aggregator → out/observability/yield_snapshot.json
  observability/
    backend/app.py       # FastAPI (port 8001)
    frontend/            # React + Vite (port 5174)
```

## Schemas

| Type | Role |
|------|------|
| `StageId` | Ordered funnel stages `S00`…`S11` |
| `DropReasonCode` | Stable attrition codes (cube, fetch, data, viability, review, …) |
| `YieldSnapshot` | Versioned run snapshot (`schema_version: 1.0.0`) |
| `CellYield` / `CandidateYieldRow` | Drill-down units |

**Data-gated** (aggressive, intentional):

- `extract_status == ok`
- ≥1 `public_data_accessions`
- `data_feasibility` ∈ {`high`, `medium`}
- required inputs not marked missing in source

## Compute snapshot (CLI)

```bash
cd mining/paper_mine
source .venv/bin/activate   # or use paper_mine venv
python compute_yield.py
# → out/observability/yield_snapshot.json
```

Also runs automatically at the end of `stage4_assemble.py`.

## Run dashboard

```bash
# API — from mining/ so imports resolve
cd mining
export REVIEW_TOKEN=proteobench-review
paper_mine/.venv/bin/python -m uvicorn observability.backend.app:app --reload --port 8001

# UI
cd observability/frontend
npm install
npm run dev
# → http://127.0.0.1:5174
```

Token defaults to `proteobench-review` (same as review app).

### API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/health` | No auth |
| GET | `/api/yield` | Full snapshot (`?recompute=true` optional) |
| POST | `/api/yield/recompute` | Force recompute from disk |
| GET | `/api/yield/dropoffs` | Between-stage edges + reasons |
| GET | `/api/yield/cells` | Optional `batch`, `bottleneck` |
| GET | `/api/yield/candidates` | Filters: cell, batch, viability, drop_code, … |
| GET | `/api/meta` | Stage + reason label dictionary |

All `/api/yield*` routes require header `X-Review-Token`.

## UI tabs

1. **Funnel & drop-off** — bar funnel + reason rollups per edge  
2. **Cells** — per-cell papers / cands / data / high / keep + bottleneck  
3. **Candidates** — survival row + drop codes + issues drill-down  
4. **Issue taxonomy** — free-text `issues[]` bucketed into codes  

Click a drop-reason chip to jump to filtered candidates.

## Engineering notes

- Yield is **recomputable** from artifacts only (cube CSV, packs, papers, ranked,
  candidates, feedback, stubs, costs). No DB.
- Prefer **aggressive prune metrics** (`data_gated`, `viability_high`) as the
  north-star yield; top-of-funnel is intentionally huge.
- Extend gates by adding codes to `DropReasonCode` + logic in
  `compute_yield.is_data_gated` / `candidate_drop_codes`.
