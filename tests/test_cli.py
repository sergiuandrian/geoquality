"""CLI tests via Typer's CliRunner."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

gpd = pytest.importorskip("geopandas")

from geoqa.cli import app  # noqa: E402

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "geoqa" in result.stdout


def test_list_checks():
    result = runner.invoke(app, ["list-checks"])
    assert result.exit_code == 0
    assert "topology" in result.stdout
    assert "duplicates" in result.stdout
    assert "built-in" in result.stdout
    assert "geoqa.checks" in result.stdout


def test_run_with_workers(suite_file: Path):
    result = runner.invoke(app, ["run", "-c", str(suite_file), "--no-fail", "-j", "2"])
    assert result.exit_code == 0


def test_init_writes_config(tmp_path: Path):
    target = tmp_path / "geoqa.yml"
    result = runner.invoke(app, ["init", "-p", str(target)])
    assert result.exit_code == 0
    assert target.exists()
    assert "version: 1" in target.read_text(encoding="utf-8")

    # Refuses to overwrite without --force.
    again = runner.invoke(app, ["init", "-p", str(target)])
    assert again.exit_code == 1
    forced = runner.invoke(app, ["init", "-p", str(target), "--force"])
    assert forced.exit_code == 0


def test_validate_ok(suite_file: Path):
    result = runner.invoke(app, ["validate", "-c", str(suite_file)])
    assert result.exit_code == 0
    assert "Config OK" in result.stdout


def test_validate_bad_config(tmp_path: Path):
    bad = tmp_path / "bad.yml"
    bad.write_text("version: 2\n", encoding="utf-8")
    result = runner.invoke(app, ["validate", "-c", str(bad)])
    assert result.exit_code == 2


def test_run_fail_on_error_exits_1(suite_file: Path):
    result = runner.invoke(app, ["run", "-c", str(suite_file), "--fail-on", "error"])
    assert result.exit_code == 1


def test_run_no_fail_exits_0(suite_file: Path, tmp_path: Path):
    html = tmp_path / "report.html"
    json_out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        ["run", "-c", str(suite_file), "--no-fail", "--html", str(html), "--json", str(json_out)],
    )
    assert result.exit_code == 0
    assert html.exists()
    assert json_out.exists()


def test_run_missing_config_exits_2(tmp_path: Path):
    result = runner.invoke(app, ["run", "-c", str(tmp_path / "nope.yml")])
    assert result.exit_code == 2
