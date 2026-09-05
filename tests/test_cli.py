import json

import pytest

from nex.cli import main


def test_demo_and_trace(tmp_path, capsys):
    output = tmp_path / "run"
    assert main(["demo", "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"]
    assert result["metrics"]["rolled_back_sites"] == 5
    assert (output / "trace.jsonl").is_file()
    assert (output / "publications.sqlite").is_file()
    with pytest.raises(SystemExit):
        main(["demo", "--output", str(output)])


def test_paired_benchmark(capsys):
    assert main(["benchmark", "--sizes", "8", "16"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["rows"]) == 4
    assert all(row["model_calls"] == 2 for row in result["rows"])


def test_global_only_demo(capsys):
    assert main(["demo", "--global-only"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["rolled_back_sites"] == 16
