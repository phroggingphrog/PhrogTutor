import json
import subprocess
import sys

import learning_tracking.__main__ as cli


def test_cli_uses_configurable_inputs_and_prints_one_brainstorm_block(tmp_path):
    command = [
        sys.executable,
        "-m",
        "learning_tracking",
        "start-brainstorm",
        "--canvas-summary",
        "tests/fixtures/canvas.json",
        "--progress",
        str(tmp_path / "progress.jsonl"),
        "--example-root",
        "tests/fixtures/hermes",
        "--max-files",
        "20",
        "--max-bytes",
        "2000",
    ]

    result = subprocess.run(command, check=True, capture_output=True, text=True)
    output = json.loads(result.stdout)

    assert output["assignment"]["id"] == "canvas-1"
    assert output["execution_allowed"] is False
    assert "options" not in output
    assert output["block"]["real_example"]["path"].endswith("project_example.py")
    assert output["mastery"] == {}


def test_cli_searches_analytics_before_generic_hermes_roots(monkeypatch, tmp_path, capsys):
    seen_roots = []

    def capture_roots(query, roots, **limits):
        seen_roots.extend(roots)
        return None

    monkeypatch.setattr(cli, "find_example", capture_roots)

    cli.main(
        [
            "start-learning",
            "--canvas-summary",
            "tests/fixtures/canvas.json",
            "--progress",
            str(tmp_path / "progress.jsonl"),
        ]
    )
    capsys.readouterr()

    assert seen_roots == [
        cli.Path("/home/phroggingphrog/projects/hermes-data-system/worktrees/analytics-ops-v1/src"),
        cli.Path("/home/phroggingphrog/projects/hermes-tools"),
        cli.Path("/home/phroggingphrog/.hermes/hermes-agent"),
    ]
