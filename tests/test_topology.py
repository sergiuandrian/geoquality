"""Tests for topology checks (overlaps, gaps, dangles) and CRS-awareness."""

from __future__ import annotations

import pytest

gpd = pytest.importorskip("geopandas")
import numpy as np  # noqa: E402
from conftest import square  # noqa: E402
from shapely.geometry import LineString, Polygon, box  # noqa: E402

from geoqa.checks import topology  # noqa: E402
from geoqa.checks.base import to_metric  # noqa: E402
from geoqa.config import TopologyCheck  # noqa: E402
from geoqa.result import Status  # noqa: E402


def _world_spanning():
    # Boxes across every longitude band: a single UTM zone cannot represent this
    # extent and reprojecting to it yields non-finite coordinates.
    boxes = [box(lon, -1, lon + 1, 1) for lon in range(-180, 180, 20)]
    return gpd.GeoDataFrame({"geometry": boxes}, crs="EPSG:4326")


def _by_check(results):
    return {r.check: r for r in results}


def test_overlaps_detected():
    gdf = gpd.GeoDataFrame(
        {"geometry": [square(0, 0), square(5, 0)]}, crs="EPSG:3857"
    )
    res = _by_check(topology.run(gdf, "l", "s", TopologyCheck(enabled=True, no_overlaps=True)))
    assert res["topology.no_overlaps"].status == Status.WARN
    assert res["topology.no_overlaps"].n_failed == 2


def test_non_overlapping_passes():
    gdf = gpd.GeoDataFrame(
        {"geometry": [square(0, 0, 5), square(100, 100, 5)]}, crs="EPSG:3857"
    )
    res = _by_check(topology.run(gdf, "l", "s", TopologyCheck(enabled=True, no_overlaps=True)))
    assert res["topology.no_overlaps"].status == Status.PASS


def test_gaps_detected():
    # A ring of 4 squares around an empty centre hole.
    outer = Polygon([(0, 0), (30, 0), (30, 30), (0, 30)])
    hole = Polygon([(10, 10), (20, 10), (20, 20), (10, 20)])
    donut = outer.difference(hole)
    res = _by_check(topology.run(
        gpd.GeoDataFrame({"geometry": [donut]}, crs="EPSG:3857"),
        "l", "s", TopologyCheck(enabled=True, no_gaps=True),
    ))
    assert res["topology.no_gaps"].status == Status.WARN
    assert res["topology.no_gaps"].n_failed == 1


def test_dangles_detected():
    gdf = gpd.GeoDataFrame(
        {"geometry": [LineString([(0, 0), (10, 0)]), LineString([(10, 0), (10, 10)])]},
        crs="EPSG:3857",
    )
    res = _by_check(topology.run(gdf, "l", "s", TopologyCheck(enabled=True, no_dangles=True)))
    # endpoints (0,0) and (10,10) are dangling; (10,0) is shared.
    assert res["topology.no_dangles"].n_failed == 2


def test_min_area_suppresses_slivers():
    # Two squares overlapping by a 10x0.001 sliver.
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    b = Polygon([(9.999, 0), (20, 0), (20, 10), (9.999, 10)])
    gdf = gpd.GeoDataFrame({"geometry": [a, b]}, crs="EPSG:3857")
    res = _by_check(topology.run(
        gdf, "l", "s", TopologyCheck(enabled=True, no_overlaps=True, min_area=1.0)
    ))
    assert res["topology.no_overlaps"].status == Status.PASS


def test_reprojection_note_for_geographic():
    gdf = gpd.GeoDataFrame(
        {"geometry": [square(0, 0, 0.001), square(0.0005, 0, 0.001)]}, crs="EPSG:4326"
    )
    res = topology.run(gdf, "l", "s", TopologyCheck(enabled=True, no_overlaps=True))
    assert "reprojected" in res[0].message


def test_disabled_returns_nothing():
    gdf = gpd.GeoDataFrame({"geometry": [square()]}, crs="EPSG:3857")
    assert topology.run(gdf, "l", "s", TopologyCheck(enabled=False)) == []


def test_to_metric_global_extent_uses_equal_area_fallback():
    # Regression: a single UTM zone produces non-finite coordinates for a
    # near-global extent; to_metric must fall back to a global equal-area CRS.
    gdf = _world_spanning()
    projected, note = to_metric(gdf)
    assert bool(np.all(np.isfinite(projected.total_bounds)))
    assert "equal-area" in (note or "")
    assert projected.crs.to_epsg() == 6933


def test_topology_global_extent_does_not_error():
    # Regression: topology overlap/gap checks previously crashed (GEOS
    # orientationIndex NaN/Inf) on near-global geographic data.
    gdf = _world_spanning()
    res = _by_check(topology.run(
        gdf, "l", "s", TopologyCheck(enabled=True, no_overlaps=True, no_gaps=True)
    ))
    assert res["topology.no_overlaps"].status != Status.ERROR
    assert res["topology.no_gaps"].status != Status.ERROR
    assert "equal-area" in res["topology.no_overlaps"].message
