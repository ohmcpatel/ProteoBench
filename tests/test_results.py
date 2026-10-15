"""Regression coverage for reports built from fresh and partially graded runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from scripts.generate_results_table import build_results_table
from scripts.utils import get_result_stats

REPO = Path(__file__).resolve().parents[1]


def _eval(tmp_path: Path) -> Path:
    evals = tmp_path / "evals"
    evals.mkdir()
    (evals / "example.json").write_text(
        json.dumps(
            {
                "id": "example",
                "task": "Example task",
                "metadata": {"task": "smoke", "kit": "none"},
            }
        )
    )
    return evals


def _trial(tmp_path: Path, provider: str, model: str, trial: int, result: dict) -> None:
    path = tmp_path / "trajectories" / "example" / provider / model / "agent" / f"r{trial}"
    path.mkdir(parents=True)
    (path / "result.json").write_text(
        json.dumps({"agent_runtime_seconds": 2.5, "result": result})
    )


def _reproduce(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = sys.prefix
    return subprocess.run(
        [
            "bash",
            str(REPO / "scripts/reproduce.sh"),
            "--evals",
            "evals",
            "--trajectories",
            "trajectories",
            "--output-dir",
            "reports with spaces",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def test_empty_trajectories_reproduce_complete_reports(tmp_path: Path) -> None:
    _eval(tmp_path)
    (tmp_path / "trajectories").mkdir()
    _reproduce(tmp_path)

    output = tmp_path / "reports with spaces"
    for filename in ("results_table.csv", "model_results.csv", "model_steps.csv"):
        frame = pd.read_csv(output / filename)
        assert frame.empty
        assert "model_name" in frame.columns
    categories = json.loads((output / "category_results.json").read_text())
    assert categories["tasks"]["smoke"] == {"n_evals": 1}
    platforms = json.loads((output / "platform_results.json").read_text())
    assert platforms["platforms"]["none"] == {"n_evals": 1}


def test_ungraded_trials_do_not_become_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    evals = _eval(tmp_path)
    for index, result in enumerate(
        ({"passed": True}, {"passed": False}, {"passed": None}, {}, {"passed": "false"}),
        start=1,
    ):
        _trial(tmp_path, "provider", "model", index, result)

    frame = build_results_table(evals, tmp_path / "trajectories", tmp_path / "results.csv")
    assert frame["passed"].tolist() == [True, False]
    assert frame["trial"].tolist() == [1, 2]
    assert capsys.readouterr().err.count("Skipping ungraded trial:") == 3


def test_provider_names_do_not_collapse_into_one_model(tmp_path: Path) -> None:
    evals = _eval(tmp_path)
    _trial(tmp_path, "first", "same-model", 1, {"passed": True})
    _trial(tmp_path, "second", "same-model", 1, {"passed": False})
    _trial(tmp_path, "mock", "mock", 1, {"passed": True})
    frame = build_results_table(evals, tmp_path / "trajectories", tmp_path / "results.csv")
    assert set(frame["model_name"]) == {"first/same-model", "second/same-model", "mock"}


def test_single_eval_reports_have_unknown_intervals(tmp_path: Path) -> None:
    _eval(tmp_path)
    _trial(tmp_path, "provider", "model", 1, {"passed": True})
    _reproduce(tmp_path)

    output = tmp_path / "reports with spaces"
    frame = pd.read_csv(output / "model_results.csv")
    assert frame.loc[0, "Accuracy (%)"] == 100.0
    assert pd.isna(frame.loc[0, "ci_lower"])
    assert pd.isna(frame.loc[0, "ci_upper"])
    categories = json.loads((output / "category_results.json").read_text())
    result = categories["tasks"]["smoke"]["model"]["provider/model-agent"]
    assert result == {"passed": 100.0, "ci_low": None, "ci_high": None}


def test_confidence_intervals_require_multiple_evaluations() -> None:
    single = get_result_stats([0.5])
    assert (single.mean, single.ci_low, single.ci_high, single.n) == (50.0, None, None, 1)
    empty = get_result_stats([])
    assert (empty.mean, empty.ci_low, empty.ci_high, empty.n) == (None, None, None, 0)


def test_percentage_intervals_stay_within_zero_to_one_hundred() -> None:
    result = get_result_stats([0.0, 1.0])
    assert (result.mean, result.ci_low, result.ci_high, result.n) == (50.0, 0.0, 100.0, 2)


@pytest.mark.parametrize("arguments", [["--output-dir"], ["--unknown"]])
def test_reproduce_rejects_invalid_options(arguments: list[str], tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(REPO / "scripts/reproduce.sh"), *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stderr
