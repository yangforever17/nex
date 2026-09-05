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


@pytest.mark.parametrize("arguments", [
    ["--model", "model"], ["--backend", "local"], ["--backend", "api", "--model", "model"],
    ["--backend", "local", "--model", "model", "--base-url", "http://localhost/v1"],
    ["--backend", "api", "--model", "model", "--device", "cpu"],
])
def test_backend_options_are_validated_before_workspace_creation(tmp_path, monkeypatch, arguments):
    monkeypatch.delenv("NEX_MODEL", raising=False)
    monkeypatch.delenv("NEX_BASE_URL", raising=False)
    output = tmp_path / "invalid"
    with pytest.raises(SystemExit):
        main(["demo", *arguments, "--output", str(output)])
    assert not output.exists()


def test_local_backend_selection_reaches_shared_runner(tmp_path, monkeypatch, capsys):
    import nex.cli

    class LocalStub:
        def __init__(self, model, **options):
            self.model = model
            self.options = options
        def complete(self, messages):
            return ('{"decision":"milliseconds"}' if "Choose one shared decision" in messages[-1]["content"]
                    else '{"decisions":["seconds"]}')
        def describe(self):
            return {"backend": "local", "model": self.model, "device": self.options["device"]}

    monkeypatch.setattr(nex.cli, "TransformersBackend", LocalStub)
    assert main(["demo", "--backend", "local", "--model", "model", "--device", "cpu",
                 "--output", str(tmp_path / "run")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["provider"] == {"backend": "local", "model": "model", "device": "cpu"}
