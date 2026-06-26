"""Tests for Phase 1 hardening: fail-on thresholds, max_issues, CRS-aware ops."""

import pytest

from geoqa.result import CheckResult, Issue, LayerReport, Report, Severity, Status


def _report_with(*statuses: Status) -> Report:
    report = Report(suite_name="t")
    lr = LayerReport(layer="l", source="s")
    for i, st in enumerate(statuses):
        sev = Severity.WARN if st == Status.WARN else Severity.ERROR
        lr.results.append(
            CheckResult(check=f"c{i}", layer="l", source="s", status=st, message="m", severity=sev)
        )
    report.layers.append(lr)
    return report


def test_has_failures_thresholds():
    warn_only = _report_with(Status.PASS, Status.WARN)
    assert warn_only.has_failures("error") is False
    assert warn_only.has_failures("warn") is True
    assert warn_only.has_failures("never") is False
    assert warn_only.passed is True

    with_fail = _report_with(Status.PASS, Status.FAIL)
    assert with_fail.has_failures("error") is True
    assert with_fail.passed is False

    with_error = _report_with(Status.ERROR)
    assert with_error.has_failures("error") is True
    assert with_error.has_failures("never") is False


def test_max_issues_truncation():
    report = Report(suite_name="t")
    lr = LayerReport(layer="l", source="s")
    issues = [Issue(message=f"bad {i}", feature_id=i) for i in range(10)]
    lr.results.append(
        CheckResult(
            check="c", layer="l", source="s", status=Status.FAIL, message="m",
            severity=Severity.ERROR, n_total=10, n_failed=10, issues=issues,
        )
    )
    report.layers.append(lr)
    res = report.to_dict(max_issues=3)["layers"][0]["results"][0]
    assert len(res["issues"]) == 3
    assert res["issues_truncated"] == 7


def test_to_metric_reprojects_geographic():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon

    from geoqa.checks.base import to_metric

    gdf = gpd.GeoDataFrame(
        {"geometry": [Polygon([(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)])]},
        crs="EPSG:4326",
    )
    projected, note = to_metric(gdf)
    assert projected.crs is not None and projected.crs.is_projected
    assert note and "reprojected" in note

    # Already-projected data is returned untouched with no note.
    proj_in = gdf.to_crs(3857)
    out, note2 = to_metric(proj_in)
    assert note2 is None
    assert out.crs.to_epsg() == 3857
