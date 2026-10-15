"""The teaching example must solve data, preserve evidence, and work offline."""

import csv
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from latch_eval_tools.graders import get_grader

from proteobench.cli import main
from proteobench.demo import compute_coverage

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "proteobench" / "data"


def test_demo_end_to_end(tmp_path, monkeypatch):
    # Any accidental download/API call is a failure, even on a connected machine.
    import socket

    def deny_network(*args, **kwargs):
        raise AssertionError("The demo must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    output = tmp_path / "demo"
    result = CliRunner().invoke(main, ["demo", "--output-dir", str(output)])
    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output
    assert "not a scientific benchmark" in result.output
    summary = json.loads((output / "results" / "summary.json").read_text())
    assert summary["answer"] == {"coverage": {"run_A": 3, "run_B": 2, "run_C": 0}}
    trial = next((output / "trajectories").rglob("result.json"))
    payload = json.loads(trial.read_text())
    assert payload["result"]["passed"] is True
    assert payload["result"]["grader_result"]["passed"] is True
    assert payload["result"]["metadata"]["synthetic"] is True
    assert (
        json.loads(trial.with_name("eval_answer.json").read_text()) == summary["answer"]
    )
    assert (output / "results" / "coverage.csv").read_text().splitlines() == [
        "run,confident_unique_precursors",
        "run_A,3",
        "run_B,2",
        "run_C,0",
    ]
    validation = CliRunner().invoke(main, ["validate", str(output / "evals")])
    assert validation.exit_code == 0, validation.output


def test_reference_answer_changes_when_input_changes(tmp_path):
    """A hardcoded gold answer would miss a newly confident precursor."""
    path = tmp_path / "precursors.tsv"
    path.write_text(
        (DATA / "demo" / "precursors.tsv").read_text() + "run_C\tNEW/2\t0.01\t1\n"
    )
    answer = compute_coverage(path)
    assert answer["coverage"]["run_C"] == 1
    evaluation = json.loads((DATA / "demo" / "toy_precursor_coverage.json").read_text())
    grader = evaluation["grader"]
    assert (
        not get_grader(grader["type"]).evaluate_answer(answer, grader["config"]).passed
    )


def test_demo_preserves_existing_output(tmp_path):
    saved = tmp_path / "my-results.txt"
    saved.write_text("keep this")
    result = CliRunner().invoke(main, ["demo", "--output-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "not empty" in result.output
    assert saved.read_text() == "keep this"
    assert list(tmp_path.iterdir()) == [saved]


@pytest.mark.parametrize(
    "q_value,quantity",
    [("nan", "1"), ("-1", "1"), ("2", "1"), ("0.01", "inf"), ("bad", "1")],
)
def test_reference_rejects_invalid_numbers(tmp_path, q_value, quantity):
    path = tmp_path / "bad.tsv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["Run", "Precursor.Id", "Global.Q.Value", "Precursor.Quantity"])
        writer.writerow(["A", "P/2", q_value, quantity])
    with pytest.raises(ValueError, match="Row 2"):
        compute_coverage(path)


def test_packaged_smoke_examples_match_source():
    """Keep the wheel's portable examples in sync with the editable checkout."""
    source_manifest = json.loads((REPO / "example_evals" / "manifest.json").read_text())
    bundled_manifest = json.loads((DATA / "manifest.json").read_text())
    source_entries = {entry["id"]: entry for entry in source_manifest["evals"]}
    for bundled in bundled_manifest["evals"]:
        source = source_entries[bundled["id"]]
        assert json.loads((REPO / source["path"]).read_text()) == json.loads(
            (DATA / bundled["path"]).read_text()
        )
