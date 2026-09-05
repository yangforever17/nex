"""Explicit opt-in live checks. Default tests neither load models nor call providers."""

import json
import os

import pytest

from nex.cli import main


@pytest.mark.skipif(not os.environ.get("NEX_TEST_LOCAL_MODEL"), reason="set NEX_TEST_LOCAL_MODEL to opt in")
def test_live_local_migration(tmp_path, capsys):
    assert main(["demo", "--backend", "local", "--model", os.environ["NEX_TEST_LOCAL_MODEL"],
                 "--device", os.environ.get("NEX_TEST_DEVICE", "cuda:0"),
                 "--output", str(tmp_path / "local-run")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["provider"]["backend"] == "local"
    assert result["metrics"]["publications"] == 1


@pytest.mark.skipif(not os.environ.get("NEX_TEST_API_MODEL"), reason="set NEX_TEST_API_MODEL to opt in")
def test_live_api_migration(tmp_path, capsys):
    assert main(["demo", "--backend", "api", "--model", os.environ["NEX_TEST_API_MODEL"],
                 "--output", str(tmp_path / "api-run")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["provider"]["backend"] == "api"
    assert result["metrics"]["publications"] == 1
