# Mining data assets

Lives under `mining/data/` (not the agent harness).

| File | Used by | Description |
|------|---------|-------------|
| `cube_inventory_270_cells.csv` | `paper_mine` (`config.yaml` → `cube_csv`) | Live working taxonomy: 270 cells (analytical category × evidence family × task depth). Schema **0.2** adds operation/regime tags (`primary_operation_v2`, `evidence_regime_v2`, strategy/scope tag JSONs, `legacy_cube_cell_id`, …) on top of the original archetype/target/tag columns. |

Do not leave dump files at the repo root — the live path is always `mining/data/cube_inventory_270_cells.csv`.

## Versioned freezes (Hugging Face)

The live CSV above is the working taxonomy (also tracked in git while small). Named freezes go through `paper_mine/release.py` so mining runs can pin a cube version:

```bash
cd ../paper_mine
python release.py cube snapshot --notes "schema 0.2 taxonomy tags"
python release.py cube push          # → HF datasets/.../cubes/v0002/
python release.py cube list --remote
python release.py cube pull v0002
```

Local freezes land in `paper_mine/cubes/vNNNN/` (gitignored). Remote layout shares the same HF dataset repo as mining releases:

```text
cubes/v0001/cube_inventory.csv   # schema 0.1
cubes/v0002/cube_inventory.csv   # schema 0.2 (+ v2 taxonomy columns)
cubes/v0002/manifest.json
cubes/index.json
releases/v0001/...
```

Each freeze records `sha256`, cell counts, priority-cell counts, and schema version. Mining snapshots auto-record a matching `cube_version` when the live CSV hash matches a local cube freeze.

Large MS raw files do **not** live here — those stay on Latch (`data_node` in eval JSON).
