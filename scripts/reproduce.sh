#!/usr/bin/env bash
# Rebuild tables from graded trajectories. Explicit paths are relative to the caller.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
evals="$repo_root/example_evals"
trajectories="$repo_root/trajectories"
output_dir="$repo_root/results"

usage() {
  cat <<'EOF'
Usage: bash scripts/reproduce.sh [--evals DIR] [--trajectories DIR] [--output-dir DIR]

Defaults to example_evals/, trajectories/, and results/ in this checkout.
Use --output-dir to keep your generated reports separate from checked-in results.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --evals|--trajectories|--output-dir)
      if [[ $# -lt 2 || "$2" == --* ]]; then
        echo "Missing directory after $1" >&2
        exit 2
      fi
      case "$1" in
        --evals) evals="$2" ;;
        --trajectories) trajectories="$2" ;;
        --output-dir) output_dir="$2" ;;
      esac
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -d "$evals" ]]; then
  echo "Eval directory not found: $evals" >&2
  exit 2
fi

if [[ -z "${VIRTUAL_ENV:-}" && -z "${CONDA_PREFIX:-}" && -f "$repo_root/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$repo_root/.venv/bin/activate"
fi

python "$repo_root/scripts/generate_results_table.py" \
  --evals "$evals" \
  --trajectories "$trajectories" \
  --results "$output_dir/results_table.csv"

python "$repo_root/scripts/generate_model_results.py" \
  --results "$output_dir/results_table.csv" \
  --output "$output_dir/model_results.csv"

python "$repo_root/scripts/generate_model_steps.py" \
  --results "$output_dir/results_table.csv" \
  --output "$output_dir/model_steps.csv"

python "$repo_root/scripts/generate_category_results.py" \
  --evals "$evals" \
  --results "$output_dir/results_table.csv" \
  --category "$output_dir/category_results.json" \
  --platform "$output_dir/platform_results.json"

echo "Done. See $output_dir/"
