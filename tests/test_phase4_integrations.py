"""Tests for Phase 4 integrations: failures collection, GeoJSON, JUnit, schema."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

gpd = pytest.importorskip("geopandas")

from geoqa.cli import app  # noqa: E402
from geoqa.config import config_json_schema, load_suite  # noqa: E402
from geoqa.engine import run_suite  # noqa: E402
from geoqa.reporting import write_geojson_failures, write_junit  # noqa: E402
from geoqa.reporting.junit import build_junit  # noqa: E402
from geoqa.result import (  # noqa: E402
    CheckResult,
    LayerReport,
    Report,
    Severity,
    Status,
)

runner = CliRunner()


# ---- failures collection ----

def test_failures_collected_when_requested(suite_file: Path):
    report = run_suite(load_suite(suite_file), collect_failures=True)
    parcels = next(layer for layer in report.layers if layer.layer == "parcels")
    assert parcels.failures is not None
    assert parcels.failures["type"] == "FeatureCollection"
    assert parcels.failures["features"]
    props = parcels.failures["features"][0]["properties"]
    assert "geoqa_failed_checks" in props


def test_failures_not_collected_by_default(suite_file: Path):
    report = run_suite(load_suite(suite_file))
    assert all(layer.failures is None for layer in report.layers)


def test_failures_reprojected_to_wgs84(suite_file: Path):
    # Source is EPSG:3857; collected failures must be lon/lat (|coord| <= 180).
    report = run_suite(load_suite(suite_file), collect_failures=True)
    parcels = next(layer for layer in report.layers if layer.layer == "parcels")
    coords = parcels.failures["features"][0]["geometry"]["coordinates"]
    # Drill down to the first coordinate pair.
    pt = coords
    while isinstance(pt[0], list):
        pt = pt[0]
    assert abs(pt[0]) <= 180 and abs(pt[1]) <= 90


# ---- GeoJSON reporter ----

def test_write_geojson_failures(suite_file: Path, tmp_path: Path):
    report = run_suite(load_suite(suite_file), collect_failures=True)
    written = write_geojson_failures(report, tmp_path / "fails")
    assert written
    for p in written:
        assert p.exists() and p.suffix == ".geojson"


def test_write_geojson_failures_empty(tmp_path: Path):
    report = Report(suite_name="empty")
    report.layers.append(LayerReport(layer="l", source="s"))
    assert write_geojson_failures(report, tmp_path / "out") == []


# ---- JUnit reporter ----

def _report_for_junit() -> Report:
    report = Report(suite_name="suite")
    lr = LayerReport(layer="parcels", source="parcels.gpkg")
    lr.results = [
        CheckResult("crs", "parcels", "s", Status.PASS, "ok"),
        CheckResult("geometry.valid", "parcels", "s", Status.FAIL, "bad geom",
                    severity=Severity.ERROR),
        CheckResult("topology.no_overlaps", "parcels", "s", Status.WARN, "overlaps",
                    severity=Severity.WARN),
        CheckResult("crs2", "parcels", "s", Status.ERROR, "crashed"),
        CheckResult("topology.no_gaps", "parcels", "s", Status.SKIP, "n/a"),
    ]
    report.layers.append(lr)
    return report


def test_build_junit_counts_and_elements():
    root = build_junit(_report_for_junit())
    assert root.tag == "testsuites"
    assert root.get("tests") == "5"
    assert root.get("failures") == "1"
    assert root.get("errors") == "1"
    assert root.get("skipped") == "1"
    suite = root.find("testsuite")
    assert suite.find(".//failure") is not None
    assert suite.find(".//error") is not None
    assert suite.find(".//skipped") is not None
    assert suite.find(".//system-out") is not None  # WARN


def test_write_junit_is_valid_xml(tmp_path: Path):
    out = write_junit(_report_for_junit(), tmp_path / "junit.xml")
    tree = ET.parse(out)
    assert tree.getroot().tag == "testsuites"


# ---- JSON Schema ----

def test_config_json_schema_describes_checks():
    schema = config_json_schema()
    assert schema["title"] == "geoqa configuration"
    assert "properties" in schema
    assert {"version", "sources", "defaults", "layers"} <= set(schema["properties"])
    # Built-in check models are referenced in the definitions.
    defs = schema.get("$defs", {})
    assert "TopologyCheck" in defs
    assert "AttributesCheck" in defs


# ---- CLI ----

def test_cli_schema_stdout():
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    assert '"geoqa configuration"' in result.stdout


def test_cli_schema_output_file(tmp_path: Path):
    out = tmp_path / "schema.json"
    result = runner.invoke(app, ["schema", "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists() and "geoqa configuration" in out.read_text(encoding="utf-8")


def test_cli_run_junit_and_geojson(suite_file: Path, tmp_path: Path):
    junit = tmp_path / "junit.xml"
    gj = tmp_path / "gj"
    result = runner.invoke(
        app,
        ["run", "-c", str(suite_file), "--no-fail", "--junit", str(junit), "--geojson-out", str(gj)],
    )
    assert result.exit_code == 0
    assert junit.exists()
    assert list(gj.glob("*.geojson"))
