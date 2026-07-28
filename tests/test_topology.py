"""Tests for topology checks (overlaps, gaps, coverage, dangles, coincident)."""

from __future__ import annotations

import time

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


def test_coverage_gaps_with_aoi_bbox():
    # Two tiles leave a 10×10 hole inside a 30×30 AOI.
    left = box(0, 0, 10, 30)
    right = box(20, 0, 30, 30)
    gdf = gpd.GeoDataFrame({"geometry": [left, right]}, crs="EPSG:3857")
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(
            enabled=True, no_coverage_gaps=True, aoi_bbox=[0, 0, 30, 30], min_area=1.0
        ),
    ))
    assert res["topology.no_coverage_gaps"].status == Status.WARN
    assert res["topology.no_coverage_gaps"].n_failed >= 1


def test_coverage_gaps_complete_aoi_passes():
    gdf = gpd.GeoDataFrame({"geometry": [box(0, 0, 30, 30)]}, crs="EPSG:3857")
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(enabled=True, no_coverage_gaps=True, aoi_bbox=[0, 0, 30, 30]),
    ))
    assert res["topology.no_coverage_gaps"].status == Status.PASS


def test_coverage_gaps_without_aoi_is_warn_even_when_empty():
    """total_bounds fallback must not report a confident PASS."""
    gdf = gpd.GeoDataFrame(
        {"geometry": [box(0, 0, 10, 10), box(10, 0, 20, 10)]}, crs="EPSG:3857"
    )
    res = _by_check(topology.run(
        gdf, "l", "s", TopologyCheck(enabled=True, no_coverage_gaps=True),
    ))
    assert res["topology.no_coverage_gaps"].status == Status.WARN
    assert "total_bounds" in res["topology.no_coverage_gaps"].message
    assert "inconclusive" in res["topology.no_coverage_gaps"].message.lower()


def test_t_junction_network_no_internal_dangles():
    # Connected T: three lines meet; only outer ends are degree-1.
    gdf = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(0, 0), (10, 0)]),
                LineString([(10, 0), (20, 0)]),
                LineString([(10, 0), (10, 10)]),
            ]
        },
        crs="EPSG:3857",
    )
    res = _by_check(topology.run(gdf, "l", "s", TopologyCheck(enabled=True, no_dangles=True)))
    # Outer ends: (0,0), (20,0), (10,10) — three dangles; junction is degree 3.
    assert res["topology.no_dangles"].n_failed == 3


def test_ignore_boundary_allows_extent_ends():
    gdf = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(0, 0), (10, 0)]),
                LineString([(10, 0), (20, 0)]),
            ]
        },
        crs="EPSG:3857",
    )
    # Without ignore_boundary: ends at 0 and 20 are dangles.
    plain = _by_check(topology.run(gdf, "l", "s", TopologyCheck(enabled=True, no_dangles=True)))
    assert plain["topology.no_dangles"].n_failed == 2

    # With ignore_boundary + AOI matching the line extent, edge ends are OK.
    ignored = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(
            enabled=True,
            no_dangles=True,
            ignore_boundary=True,
            snap_tolerance=0.1,
            aoi_bbox=[0, -1, 20, 1],
        ),
    ))
    assert ignored["topology.no_dangles"].status == Status.PASS


def test_ignore_boundary_still_flags_internal_spur():
    gdf = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(0, 0), (20, 0)]),
                LineString([(10, 0), (10, 5)]),  # spur ending inside AOI
            ]
        },
        crs="EPSG:3857",
    )
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(
            enabled=True,
            no_dangles=True,
            ignore_boundary=True,
            snap_tolerance=0.1,
            aoi_bbox=[0, -1, 20, 10],
        ),
    ))
    assert res["topology.no_dangles"].n_failed >= 1
    detail = res["topology.no_dangles"].issues[0].detail
    assert detail.get("degree", 0) < 2


def test_dangle_issues_include_lon_lat_for_geographic():
    gdf = gpd.GeoDataFrame(
        {"geometry": [LineString([(0, 0), (0.01, 0)])]},
        crs="EPSG:4326",
    )
    res = _by_check(topology.run(gdf, "l", "s", TopologyCheck(enabled=True, no_dangles=True)))
    detail = res["topology.no_dangles"].issues[0].detail
    assert "lon" in detail and "lat" in detail
    assert "degree" in detail


def test_coincident_almost_adjacent_gap():
    # 1 m gap between shared sides.
    a = box(0, 0, 10, 10)
    b = box(11, 0, 21, 10)
    gdf = gpd.GeoDataFrame({"geometry": [a, b]}, crs="EPSG:3857")
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(enabled=True, coincident_edges=True, boundary_tolerance=1.5),
    ))
    assert res["topology.coincident_edges"].status == Status.WARN
    assert res["topology.coincident_edges"].issues[0].detail["kind"] == "almost_adjacent"


def test_coincident_shared_edge_passes():
    a = box(0, 0, 10, 10)
    b = box(10, 0, 20, 10)
    gdf = gpd.GeoDataFrame({"geometry": [a, b]}, crs="EPSG:3857")
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(enabled=True, coincident_edges=True, boundary_tolerance=0.5),
    ))
    assert res["topology.coincident_edges"].status == Status.PASS


def test_coverage_area_ratio_detects_overlap():
    gdf = gpd.GeoDataFrame(
        {"geometry": [square(0, 0), square(5, 0)]}, crs="EPSG:3857"
    )
    res = _by_check(topology.run(
        gdf, "l", "s", TopologyCheck(enabled=True, coverage_area_ratio=True)
    ))
    assert res["topology.coverage_area_ratio"].status == Status.WARN


def test_spillover_flags_feature_outside_aoi():
    # AOI [0,0,10,10]; second poly sticks out to the east.
    gdf = gpd.GeoDataFrame(
        {"geometry": [box(0, 0, 5, 5), box(8, 0, 15, 5)]}, crs="EPSG:3857"
    )
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(
            enabled=True, no_spillover=True, aoi_bbox=[0, 0, 10, 10], min_area=0.1,
        ),
    ))
    assert res["topology.no_spillover"].status == Status.WARN
    assert res["topology.no_spillover"].n_failed == 1


def test_spillover_without_aoi_skips():
    gdf = gpd.GeoDataFrame({"geometry": [box(0, 0, 5, 5)]}, crs="EPSG:3857")
    res = _by_check(topology.run(
        gdf, "l", "s", TopologyCheck(enabled=True, no_spillover=True),
    ))
    assert res["topology.no_spillover"].status == Status.SKIP


def test_undershoot_near_another_line():
    # Horizontal trunk; vertical stub stops 0.5 m short of touching (undershoot).
    gdf = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(0, 0), (20, 0)]),
                LineString([(10, 5), (10, 0.5)]),
            ]
        },
        crs="EPSG:3857",
    )
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(enabled=True, no_undershoots=True, snap_tolerance=1.0),
    ))
    assert res["topology.no_undershoots"].status == Status.WARN
    assert res["topology.no_undershoots"].n_failed >= 1


def test_undershoot_connected_t_junction_passes():
    gdf = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(0, 0), (20, 0)]),
                LineString([(10, 5), (10, 0)]),
            ]
        },
        crs="EPSG:3857",
    )
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(enabled=True, no_undershoots=True, snap_tolerance=1.0),
    ))
    assert res["topology.no_undershoots"].status == Status.PASS


def test_overshoot_stub_past_crossing():
    # Horizontal crosses vertical at x=10 and continues 0.5 m past (overshoot).
    gdf = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(0, 0), (10.5, 0)]),
                LineString([(10, -5), (10, 5)]),
            ]
        },
        crs="EPSG:3857",
    )
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(enabled=True, no_overshoots=True, snap_tolerance=1.0),
    ))
    assert res["topology.no_overshoots"].status == Status.WARN
    assert res["topology.no_overshoots"].n_failed >= 1
    assert res["topology.no_overshoots"].issues[0].detail["stub_length"] > 0


def test_overshoot_exact_t_junction_passes():
    gdf = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(0, 0), (10, 0)]),
                LineString([(10, -5), (10, 5)]),
            ]
        },
        crs="EPSG:3857",
    )
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(enabled=True, no_overshoots=True, snap_tolerance=1.0),
    ))
    assert res["topology.no_overshoots"].status == Status.PASS


def test_overlaps_algorithm_coverage():
    gdf = gpd.GeoDataFrame(
        {"geometry": [square(0, 0), square(5, 0)]}, crs="EPSG:3857"
    )
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(enabled=True, no_overlaps=True, algorithm="coverage"),
    ))
    assert res["topology.no_overlaps"].status == Status.WARN
    assert res["topology.no_overlaps"].issues[0].detail["algorithm"] == "coverage"


def test_pairwise_50k_synthetic_budget():
    # Acceptance: ~50k non-overlapping parcels finish without ERROR in a CI-ish budget.
    n = 50_000
    # Grid of 1×1 squares with 1 m gaps — no overlaps; stress the spatial index path.
    cols = 250
    geoms = [box(i % cols * 2, i // cols * 2, i % cols * 2 + 1, i // cols * 2 + 1) for i in range(n)]
    gdf = gpd.GeoDataFrame({"geometry": geoms}, crs="EPSG:3857")
    t0 = time.perf_counter()
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(
            enabled=True,
            no_overlaps=True,
            algorithm="pairwise",
            max_pairs=200_000,
        ),
    ))
    elapsed = time.perf_counter() - t0
    assert res["topology.no_overlaps"].status == Status.PASS
    # Soft budget: keep generous for CI runners; fail only on pathological slowdown.
    assert elapsed < 120.0, f"50k pairwise overlaps took {elapsed:.1f}s"


def test_max_pairs_truncation_with_zero_hits_is_warn():
    # Many candidate pairs from sindex, but none are true overlaps after area filter;
    # with a tiny max_pairs cap the scan is incomplete → WARN, not PASS.
    n = 40
    # Slightly overlapping neighbors so sindex returns many pairs; min_area huge → 0 hits.
    geoms = [box(i * 0.5, 0, i * 0.5 + 1, 1) for i in range(n)]
    gdf = gpd.GeoDataFrame({"geometry": geoms}, crs="EPSG:3857")
    res = _by_check(topology.run(
        gdf, "l", "s",
        TopologyCheck(
            enabled=True,
            no_overlaps=True,
            algorithm="pairwise",
            max_pairs=5,
            min_area=1e9,
        ),
    ))
    assert res["topology.no_overlaps"].status == Status.WARN
    assert "max_pairs" in res["topology.no_overlaps"].message
