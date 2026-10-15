"""CLI input failures and artifact behavior, without remote data or agent calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from proteobench import cli


@pytest.fixture
def eval_data() -> dict:
    return {
        "id": "small_eval",
        "task": "Return the fixed protein count as JSON.",
        "grader": {
            "type": "numeric_tolerance",
            "config": {
                "ground_truth": {"count": 42},
                "tolerances": {"count": {"type": "absolute", "value": 0}},
            },
        },
    }


@pytest.fixture
def eval_path(tmp_path: Path, eval_data: dict) -> Path:
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(eval_data))
    return path


@pytest.fixture(autouse=True)
def isolate_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    def unexpected_download(*args, **kwargs):
        pytest.fail("CLI tests must not download remote data")

    monkeypatch.setattr(
        "latch_eval_tools.harness.utils.download_single_dataset", unexpected_download
    )


@pytest.mark.parametrize("raw", ["[]", "null", '"a string"', "7", "{"])
def test_validate_bad_json(raw: str, tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(raw)
    result = CliRunner().invoke(cli.main, ["validate", str(path)])
    assert result.exit_code == 1
    assert "object" in result.output or "Invalid JSON" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "eval_id", ["", " ", ".", "..", "../escape", "/tmp/escape", "a/b", "a\\b", 3]
)
def test_validate_rejects_unsafe_id(eval_id, eval_data: dict, eval_path: Path) -> None:
    eval_data["id"] = eval_id
    eval_path.write_text(json.dumps(eval_data))
    result = CliRunner().invoke(cli.main, ["validate", str(eval_path)])
    assert result.exit_code == 1
    assert "id must" in result.output


@pytest.mark.parametrize("task", ["", "  \n", None, [], "bad\x00task"])
def test_validate_rejects_blank_task(task, eval_data: dict, eval_path: Path) -> None:
    eval_data["task"] = task
    eval_path.write_text(json.dumps(eval_data))
    result = CliRunner().invoke(cli.main, ["validate", str(eval_path)])
    assert result.exit_code == 1
    assert "task must" in result.output


@pytest.mark.parametrize(
    "selection", ["none", "both", "empty_list", "bad_type", "bad_config"]
)
def test_validate_rejects_invalid_grader_selection(
    selection: str, eval_data: dict, eval_path: Path
) -> None:
    if selection == "none":
        del eval_data["grader"]
    elif selection == "both":
        eval_data["graders"] = [eval_data["grader"]]
    elif selection == "empty_list":
        del eval_data["grader"]
        eval_data["graders"] = []
    elif selection == "bad_type":
        eval_data["grader"]["type"] = []
    else:
        eval_data["grader"]["config"] = []
    eval_path.write_text(json.dumps(eval_data))
    result = CliRunner().invoke(cli.main, ["validate", str(eval_path)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_no_agent_does_not_initialize_runner(
    eval_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_runner(*args, **kwargs):
        pytest.fail("No agent selected: EvalRunner must not be initialized")

    monkeypatch.setattr(cli, "EvalRunner", unexpected_runner)
    result = CliRunner().invoke(cli.main, ["run", str(eval_path)])
    assert result.exit_code == 2
    assert "Choose --agent explicitly" in result.output


@pytest.mark.parametrize("agent", ["mock", "minisweagent"])
def test_multi_grader_run_fails_before_staging(
    agent: str, eval_data: dict, eval_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eval_data["graders"] = [eval_data.pop("grader")]
    eval_path.write_text(json.dumps(eval_data))

    def unexpected_runner(*args, **kwargs):
        pytest.fail("Unsupported grading must fail before EvalRunner is initialized")

    monkeypatch.setattr(cli, "EvalRunner", unexpected_runner)
    assert CliRunner().invoke(cli.main, ["validate", str(eval_path)]).exit_code == 0
    result = CliRunner().invoke(cli.main, ["run", str(eval_path), "--agent", agent])
    assert result.exit_code == 1
    assert "single 'grader'" in result.output


def test_run_rejects_invalid_eval_before_staging(
    eval_data: dict, eval_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eval_data["id"] = "../escape"
    eval_path.write_text(json.dumps(eval_data))

    def unexpected_runner(*args, **kwargs):
        pytest.fail("Invalid evals must fail before EvalRunner is initialized")

    monkeypatch.setattr(cli, "EvalRunner", unexpected_runner)
    result = CliRunner().invoke(cli.main, ["run", str(eval_path), "--agent", "mock"])
    assert result.exit_code == 1
    assert "Invalid evaluation" in result.output


@pytest.mark.parametrize("passed", [False, None])
def test_unsuccessful_result_exits_nonzero_and_preserves_artifact(
    passed, eval_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UnsuccessfulRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, agent_function):
            return {"passed": passed, "agent_answer": None, "grader_result": None}

    monkeypatch.setattr(cli, "EvalRunner", UnsuccessfulRunner)
    result = CliRunner().invoke(cli.main, ["run", str(eval_path), "--agent", "mock"])
    assert result.exit_code == 1
    assert (
        "FAILED" in result.output
        if passed is False
        else "no pass/fail" in result.output
    )
    artifacts = list((tmp_path / "trajectories").rglob("result.json"))
    assert len(artifacts) == 1
    assert json.loads(artifacts[0].read_text())["result"]["passed"] is passed


@pytest.mark.parametrize("keep", [True, False])
def test_output_location_and_workspace_cleanup(
    keep: bool, eval_path: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "results"
    result = CliRunner().invoke(
        cli.main,
        [
            "run",
            str(eval_path),
            "--agent",
            "mock",
            "--output-dir",
            str(output_dir),
            "--keep-workspace" if keep else "--cleanup-workspace",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(list(output_dir.rglob("result.json"))) == 1
    assert not (tmp_path / "trajectories").exists()
    assert (tmp_path / ".proteobench_workspace/workspace/small_eval").exists() is keep


def test_no_trajectory_and_unique_trial_directories(
    eval_path: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    args = ["run", str(eval_path), "--agent", "mock", "--cleanup-workspace"]
    assert runner.invoke(cli.main, [*args, "--no-trajectory"]).exit_code == 0
    assert not (tmp_path / "trajectories").exists()
    for trial in ("r1", "r2"):
        result = runner.invoke(cli.main, args)
        assert result.exit_code == 0, result.output
        assert (
            tmp_path / f"trajectories/small_eval/mock/mock/mock/{trial}/result.json"
        ).exists()


@pytest.mark.parametrize("model", ["..", "../..", "../../outside"])
def test_model_name_cannot_escape_output_directory(
    model: str, eval_path: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "results"
    result = CliRunner().invoke(
        cli.main,
        [
            "run",
            str(eval_path),
            "--agent",
            "mock",
            "--model",
            model,
            "--output-dir",
            str(output_dir),
            "--cleanup-workspace",
        ],
    )
    assert result.exit_code == 0, result.output
    artifacts = list(output_dir.rglob("result.json"))
    assert len(artifacts) == 1
    assert artifacts[0].parent.parent.parent.parent.parent == output_dir / "small_eval"


def test_mock_multiple_choice_uses_current_grader_configuration() -> None:
    from latch_eval_tools.graders import get_grader

    config = {"correct_answers": ["A", "C"]}
    data = {"grader": {"type": "multiple_choice", "config": config}}
    answer = cli._mock_answer_for_eval(data)
    assert get_grader("multiple_choice").evaluate_answer(answer, config).passed
