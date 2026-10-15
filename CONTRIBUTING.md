# Contributing to ProteoBench

Useful contributions make evaluations easier to run, inspect, and reproduce.
The public project is at an early stage: it has a runnable harness, two smoke
evaluations, a synthetic demo, and research tooling for developing future
tasks.

## Development setup

Use Python 3.11 and [uv](https://docs.astral.sh/uv/). From a clone of the
repository:

```bash
uv sync --extra dev --locked
uv run proteobench demo --output-dir demo-output
uv run pytest
```

The demo preserves existing results by requiring a new or empty output
directory. Choose a fresh `--output-dir` when rerunning it.

The demo and local test suite do not need model API keys. Installing the
dependencies requires network access; real agent adapters and mining services
have separate setup requirements. The [README](README.md) covers the primary
quickstart, and [mining/README.md](mining/README.md) describes the research tools.

Use the committed lockfile for repeatable development. If you change
dependencies, update `pyproject.toml` and regenerate `uv.lock` together, then
verify the locked install still works.

## Choose the right area

| Contribution | Start here |
|---|---|
| CLI, demo, or saved run behavior | `proteobench/` and `tests/` |
| Aggregation or result interpretation | `scripts/`, `results/README.md`, and `METHODS.md` |
| New evaluation | [docs/adding_evals.md](docs/adding_evals.md) |
| JSON and grader contract | [docs/specification.md](docs/specification.md) |
| Candidate generation or literature mining | `mining/` and its component READMEs |

Keep changes focused enough that a reviewer can reproduce the result. Add a
regression test for changed behavior or a bug fix; prefer small local fixtures
over tests that require credentials or live services.

## Preserve the meaning of results

- Label harness checks as **infrastructure** and constructed examples as
  **synthetic**. Mock runs read expected answers and cannot support claims about
  an agent's ability.
- For a **scientific evaluation**, provide accessible data, its provenance and
  license, a ground-truth derivation, justified tolerances, and correct/incorrect
  answer checks. A generated candidate is not yet a validated evaluation.
- Keep ungraded, failed, and passing runs distinguishable. Do not replace
  missing measurements with measured zeros, or report excluded runs as
  completed trials. Existing cost/step parsers use zero placeholders; preserve
  that limitation when describing their outputs.
- Preserve the commands, versions, settings, and limitations needed to trace a
  result back to its inputs. Do not rewrite historical evidence to make a new
  result look like an old one.

## Before opening a pull request

Run the checks relevant to your change. For harness or evaluation changes:

```bash
uv run pytest
uv run proteobench validate example_evals/
uv run proteobench demo --output-dir demo-output
```

For changes to aggregation, also exercise it on the demo output:

```bash
uv run bash scripts/reproduce.sh \
  --evals demo-output/evals \
  --trajectories demo-output/trajectories \
  --output-dir demo-output/results
```

Describe the problem, resulting behavior, and checks performed in the pull
request. Update user-facing instructions when commands or outputs change. If
a check requires a service you did not run, state that limitation explicitly.

Review the diff for generated files and private material. Do not commit API
keys, `.env` files, virtual environments, dependency directories, caches,
private feedback, or large raw datasets. Small reviewed fixtures and deliberate
evidence artifacts are welcome when their provenance and distribution rights
are clear.
