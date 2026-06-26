"""Tests for the console, JSON and HTML reporters."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from geoqa.reporting import print_report, write_html, write_json
from geoqa.result import CheckResult, Issue, LayerReport, Report, Severity, Status


def _sample_report() -> Report:
    report = Report(suite_name="Sample")
    lr = LayerReport(layer="parcels", source="parcels.gpkg", n_features=3, crs="EPSG:3857")
    lr.results.append(
        CheckResult(
            check="geometry.valid", layer="parcels", source="parcels.gpkg",
            status=Status.FAIL, message="1 invalid geometry found",
            severity=Severity.ERROR, n_total=3, n_failed=1,
            issues=[Issue(message="Self-intersection", feature_id=2)],
        )
    )
    lr.results.append(
        CheckResult(
            check="crs", layer="parcels", source="parcels.gpkg",
            status=Status.PASS, message="CRS OK",
        )
    )
    report.layers.append(lr)
    return report


def test_write_json_structure(tmp_path: Path):
    out = write_json(_sample_report(), tmp_path / "r.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["suite_name"] == "Sample"
    assert data["passed"] is False
    assert data["counts"]["fail"] == 1
    assert data["layers"][0]["results"][0]["issues"][0]["feature_id"] == 2


def test_write_json_respects_max_issues(tmp_path: Path):
    report = Report(suite_name="m")
    lr = LayerReport(layer="l", source="s")
    issues = [Issue(message=f"bad {i}", feature_id=i) for i in range(20)]
    lr.results.append(
        CheckResult(check="c", layer="l", source="s", status=Status.FAIL,
                    message="m", severity=Severity.ERROR, issues=issues)
    )
    report.layers.append(lr)
    out = write_json(report, tmp_path / "r.json", max_issues=5)
    res = json.loads(out.read_text(encoding="utf-8"))["layers"][0]["results"][0]
    assert len(res["issues"]) == 5
    assert res["issues_truncated"] == 15


def test_write_html_contains_findings(tmp_path: Path):
    out = write_html(_sample_report(), tmp_path / "r.html", title="My Report")
    html = out.read_text(encoding="utf-8")
    assert "My Report" in html
    assert "geometry.valid" in html
    assert "FAILED" in html
    assert "Self-intersection" in html
    # No failures FeatureCollection -> no map assets.
    assert "leaflet" not in html.lower()


def _report_with_failures() -> Report:
    report = _sample_report()
    report.layers[0].failures = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "2",
                "properties": {"geoqa_failed_checks": "geometry.valid"},
                "geometry": {"type": "Point", "coordinates": [10.0, 50.0]},
            }
        ],
    }
    return report


def test_write_html_embeds_map_when_failures(tmp_path: Path):
    out = write_html(_report_with_failures(), tmp_path / "r.html")
    html = out.read_text(encoding="utf-8")
    assert "leaflet" in html.lower()
    assert 'id="geoqa-map-0"' in html
    assert "geoqa_failed_checks" in html


def test_write_html_map_can_be_disabled(tmp_path: Path):
    out = write_html(_report_with_failures(), tmp_path / "r.html", include_map=False)
    html = out.read_text(encoding="utf-8")
    assert "leaflet" not in html.lower()


def test_print_report_runs():
    console = Console(record=True, width=120)
    print_report(_sample_report(), console=console, verbose=True)
    text = console.export_text()
    assert "parcels" in text
    assert "FAILED" in text
