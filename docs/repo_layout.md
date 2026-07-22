# Repository layout

Root stays **thin**. Two concerns only:

```text
┌─ HARNESS (run agents on finished evals) ─────────────────────┐
│  proteobench/  example_evals/  trajectories/  results/       │
│  scripts/  tests/  docs/                                     │
└──────────────────────────────────────────────────────────────┘
┌─ MINING (build candidates from literature + human review) ───┐
│  mining/data/  mining/paper_mine/  mining/review_app/        │
└──────────────────────────────────────────────────────────────┘
```

```text
data/cube → paper_mine stages → review_app labels
                                      │
                    (later) real evals in example_evals/
                                      ▼
                         proteobench run → trajectories/ → results/
```

---

## Root directory (what you see at top level)

| Path | Role |
|------|------|
| `proteobench/` | Installable CLI package |
| `example_evals/` | Public eval JSON + manifest (smoke today) |
| `trajectories/` | Per-trial agent outputs |
| `results/` | Leaderboard tables |
| `scripts/` | `reproduce.sh` + table generators |
| `tests/` | Package tests |
| `docs/` | Specs + this map |
| **`mining/`** | All literature → candidate → review tooling |
| `pyproject.toml`, `README.md`, … | Package metadata |

Regenerable junk (`*.egg-info`, `.pytest_cache`, `.proteobench_workspace`) is gitignored and should not live in the tree long-term.

---

## `mining/` — dataset building

| Path | Role |
|------|------|
| `mining/data/cube_inventory_270_cells.csv` | Taxonomy / archetypes to mine |
| `mining/paper_mine/` | Stages 0–4 + versioned `release.py` |
| `mining/paper_mine/out/` | Working run (gitignored) |
| `mining/paper_mine/releases/` | Frozen v0001, v0002, … (gitignored) |
| `mining/review_app/` | FastAPI + React keep/revise/reject |
| `mining/observability/` | Yield funnel dashboard (drop-off + reasons) |
| `mining/paper_mine/compute_yield.py` | Funnel snapshot → `out/observability/` |

**Entry:** [mining/README.md](../mining/README.md) → [paper_mine/README.md](../mining/paper_mine/README.md).

---

## Harness — agent evaluation

| Path | Role |
|------|------|
| `proteobench/` | CLI |
| `example_evals/` | Eval definitions |
| `trajectories/` | Raw runs |
| `results/` | Aggregates |
| `docs/specification.md` | Eval JSON schema |

**Entry:** `proteobench run example_evals/smoke/...`

---

## What belongs where

| Kind of file | Put it here |
|--------------|-------------|
| Finished eval for agents | `example_evals/` |
| Agent trial output | `trajectories/` |
| Leaderboard tables | `results/` |
| Cube taxonomy CSV | `mining/data/` |
| Mining pipeline code | `mining/paper_mine/` |
| One mining run | `mining/paper_mine/out/` |
| Named generation version | Local `mining/paper_mine/releases/vNNNN/` + **cloud** (HF or GitHub Releases) — not git |
| Human labels | `mining/review_app/backend/data/feedback.jsonl` |
