# ProteoBench paper mine (pilot)

Laptop pipeline: **cube inventory → PubMed/Europe PMC papers → Claude candidate cards → review app**.

Lives under [`mining/`](../README.md) (dataset building). Does not run agents.
See [docs/repo_layout.md](../../docs/repo_layout.md).

Pilot scope (config): batches **B01** (data/measurement integrity) + **B02** (identification), priority cells only (`final_selection_target_v0 > 0`) → **24 cells**.

## Directory map

```text
paper_mine/
  config.yaml           # scope + paths + model knobs
  export_queue.py       # 0. cube CSV → search packs
  stage1_fetch.py       # 1. Europe PMC papers
  stage2_rank.py        # 2. heuristic top-K
  stage3_extract.py     # 3. Claude SDK extraction
  stage3_extract_cli.py # 3b. Claude CLI extraction (alternate)
  stage4_assemble.py    # 4. dataset + review snapshot (+ yield snapshot)
  compute_yield.py      # funnel / drop-reason observability snapshot
  yield_schemas.py      # StageId, DropReasonCode, YieldSnapshot contracts
  release.py            # freeze mining + data-cube versions (HF / GitHub)
  schemas.py / common.py
  out/                  # working run (gitignored)
  out/observability/    # yield_snapshot.json (funnel UI)
  releases/             # versioned mining freezes (gitignored)
  cubes/                # versioned data-cube freezes (gitignored)
  _scratch/             # accidental dumps; delete anytime
```

Cube taxonomy (live working copy): [`../data/cube_inventory_270_cells.csv`](../data/cube_inventory_270_cells.csv).
Named freezes: `python release.py cube snapshot` → `cubes/vNNNN/` (+ optional HF push).

## Setup

```bash
cd mining/paper_mine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: ANTHROPIC_API_KEY=...  (needed for stage 3)
# optional: NCBI_EMAIL=...
```

Use **this** venv for mining and the review API (repo-root `.venv` is for the harness only).

## Pipeline

### 0. Export search packs from the cube

```bash
python export_queue.py
# → queue/search_packs.jsonl  (24 packs for B01+B02)
```

### 1. Fetch papers (no Claude, free)

```bash
# one cell smoke test
python stage1_fetch.py --cell C01-E01-A --no-fulltext

# full pilot (may take a while; resume-safe)
python stage1_fetch.py --batch B01
python stage1_fetch.py --batch B02
```

Writes `out/papers/{cell_id}/pmid….json`. Re-runs skip existing files.

### 2. Rank top-K per cell

```bash
python stage2_rank.py --batch B01 --batch B02
# → out/ranked/{cell_id}.json  and ranked_all.jsonl
```

### 3. Claude extraction

```bash
# dry-run work list
python stage3_extract.py --dry-run --batch B01

# one paper first
python stage3_extract.py --cell C01-E01-A --limit 1

# batch (checkpointed; skips existing candidates)
python stage3_extract.py --batch B01
python stage3_extract.py --batch B02
```

Writes `out/candidates/{cell_id}/pmid….json` and `out/costs/calls.csv`.

### 4. Assemble dataset + review snapshot

```bash
python stage4_assemble.py --stubs --review-snapshot
# → out/dataset/candidates.jsonl, candidates.csv, coverage_report.md
# → review_app/backend/data/candidates.jsonl
```

### 5. Fetch and validate public data

Candidate accessions are not downloaded by the paper stages. Resolve them through
the repository APIs and select task inputs with:

```bash
python stage5_fetch_data.py --candidate C01-E01-A_pmid39248652_v1 --download
```

The stage currently supports PRIDE/ProteomeXchange `PXD...` accessions. It saves
the manifest and a validation report under `out/data/`, downloads non-raw
processed/supporting files when `--download` is supplied, and skips files larger
than `data_fetch.max_download_bytes`. Raw vendor files are intentionally not
selected by default. A candidate is not ready for a final eval until the report
shows the required run metadata/QC inputs and a compact task bundle has been
staged as a Latch `data_node`.

## Review app

```bash
# terminal 1 — API (cwd must be mining/ so review_app imports)
cd ..   # → mining/
export REVIEW_TOKEN=proteobench-review   # optional override
./paper_mine/.venv/bin/python -m uvicorn review_app.backend.app:app --reload --port 8000

# terminal 2 — UI
cd review_app/frontend
npm install
npm run dev
# open http://127.0.0.1:5173
```

Sign in with your name + token (`proteobench-review` by default). Experts submit **keep / revise / reject**. Admin tab exports CSV.

### Split work across friends

Edit `../review_app/backend/data/assignments.json`:

```json
{
  "default": [],
  "reviewers": {
    "alice": ["C01-E01-A_pmid123_v1", "..."],
    "bob": ["C02-E01-C_pmid456_v1"]
  }
}
```

If a reviewer’s list is empty / missing, they get `default` (all candidates after assemble).

Remote share: run both servers and expose with Tailscale, `cloudflared tunnel`, or ngrok.

## Versioned freezes (local + cloud)

Two version axes live under `release.py`:

| Axis | What | Local path | HF path |
|------|------|------------|---------|
| **Cube** | taxonomy inventory CSV | `cubes/vNNNN/` | `cubes/vNNNN/` |
| **Mining** | candidate-task snapshot | `releases/vNNNN/` | `releases/vNNNN/` |

### Data cube

Freeze the live inventory whenever the taxonomy changes (targets, archetypes, cell set):

```bash
python release.py cube snapshot --notes "initial 270-cell inventory"
python release.py cube list
python release.py cube show v0001

# optional: upload immediately
python release.py cube snapshot --notes "…" --push
# or later:
python release.py cube push
python release.py cube pull v0001
python release.py cube list --remote
```

Each cube freeze contains:

| Path | Contents |
|------|----------|
| `cube_inventory.csv` | full inventory snapshot |
| `manifest.json` | version, notes, git sha, **sha256**, metrics |
| `metrics.json` | cell counts, priority cells, batches, schema versions |

### Mining releases

Each time you improve the miner/infra and re-run, freeze a **release** so you can
diff quality over time (e.g. % of tasks with a public dataset accession attached).
Mining manifests pin the cube via `sha256` and matching `cube_version` when a local cube freeze matches.

```bash
# Prefer freezing the cube first so the pin resolves
python release.py cube snapshot --notes "cube used for this mine"

# After stage4_assemble.py
python release.py snapshot --notes "v1 baseline pilot"

# Later, after prompt/ranker changes + re-mine
python release.py snapshot --notes "prefer papers with PXD/MSV; stronger accession prompt"

python release.py list
python release.py compare v0001 v0002
python release.py show v0002
```

Mining releases land in `paper_mine/releases/v0001/`, `v0002/`, … (gitignored) with:

| Path | Contents |
|------|----------|
| `manifest.json` | version, git sha, notes, metrics, **cube pin** |
| `metrics.json` | counts + `% with public accessions` |
| `dataset/` | candidates.jsonl/csv, coverage, by_cell, stubs |
| `ranked/` | stage2 shortlists |
| `feedback.jsonl` | review votes at snapshot time |
| `config.yaml` | config used for that run |

### Mirror to free cloud (not git)

Datasets stay **out of GitHub git history**. Push versioned bundles to:

1. **Hugging Face Hub** (recommended; cube + mining)
2. **GitHub Releases** (mining only; free via `gh`)

```bash
# --- Hugging Face (free public dataset repo) ---
pip install huggingface_hub
huggingface-cli login          # one-time; create token at hf.co/settings/tokens
export PAPER_MINE_HF_REPO=YOUR_USER/proteobench-mining-releases

python release.py cube push --backend huggingface
python release.py push --backend huggingface
python release.py cube list --remote --backend huggingface
python release.py list --remote --backend huggingface
python release.py cube pull v0001 --backend huggingface
python release.py pull v0001 --backend huggingface

# --- GitHub Releases (mining only; works with existing `gh auth login`) ---
python release.py push --backend github
python release.py list --remote --backend github
python release.py pull v0001 --backend github
```

Or snapshot + upload:

```bash
python release.py cube snapshot --notes "…" --push
python release.py snapshot --notes "…" --push --backend github
```

HF layout:

```text
https://huggingface.co/datasets/YOUR_USER/proteobench-mining-releases
  cubes/v0001/...
  cubes/v0002/...
  cubes/index.json
  releases/v0001/...
  releases/v0002/...
  index.json
  README.md
```

GitHub layout: release tag `mining-v0001` with `proteobench-mining-v0001.tar.gz` (mining only).

## Scale later

In `config.yaml` set:

```yaml
batches: [B01, B02, B03, B04, B05, B06, B07, B08, B09]
```

or:

```bash
python export_queue.py --all-priority
```

## Notes

- Claude is **not** used for search — only paper → structured candidate cards.
- Prefer Europe PMC OA full text; paywalled PDFs are not scraped.
- Candidate cards are **not** final evals. Gold answers still need offline computation + real `data_node`s.
- Draft stubs from `--stubs` have placeholder graders; do not publish as scored benchmarks.
