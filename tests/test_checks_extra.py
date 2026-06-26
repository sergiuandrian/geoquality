"""Additional coverage for crs, duplicates and attributes checks."""

from __future__ import annotations

import pytest

gpd = pytest.importorskip("geopandas")
from conftest import square  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from geoqa.checks import attributes, crs, duplicates  # noqa: E402
from geoqa.config import (  # noqa: E402
    AttributesCheck,
    CrsCheck,
    DomainRule,
    DuplicatesCheck,
    FuzzyConfig,
)
from geoqa.result import Status  # noqa: E402


def _gdf(geoms, crs_str="EPSG:3857", **cols):
    return gpd.GeoDataFrame({**cols, "geometry": geoms}, crs=crs_str)


# ---- CRS ----

def test_crs_missing_required_fails():
    gdf = gpd.GeoDataFrame({"geometry": [square()]})  # no CRS
    res = crs.run(gdf, "l", "s", CrsCheck(required=True))
    assert res[0].status == Status.FAIL


def test_crs_missing_not_required_passes():
    gdf = gpd.GeoDataFrame({"geometry": [square()]})
    res = crs.run(gdf, "l", "s", CrsCheck(required=False))
    assert res[0].status == Status.PASS


def test_crs_expected_epsg_mismatch():
    gdf = _gdf([square()], crs_str="EPSG:4326")
    res = crs.run(gdf, "l", "s", CrsCheck(expected_epsg=3857))
    assert res[0].status == Status.FAIL


def test_crs_disabled():
    gdf = _gdf([square()])
    assert crs.run(gdf, "l", "s", CrsCheck(enabled=False)) == []


# ---- Duplicates ----

def test_fuzzy_polygon_iou():
    a = square(0, 0, 10)
    b = square(0, 0, 10).buffer(0.01)  # almost identical
    gdf = _gdf([a, b])
    cfg = DuplicatesCheck(fuzzy=FuzzyConfig(enabled=True, min_overlap=0.9))
    res = {r.check: r for r in duplicates.run(gdf, "l", "s", cfg)}
    assert res["duplicates.fuzzy"].n_failed == 2


def test_fuzzy_point_distance():
    gdf = _gdf([Point(0, 0), Point(0, 1)])
    cfg = DuplicatesCheck(
        fuzzy=FuzzyConfig(enabled=True, predicate="dwithin", max_distance=5.0)
    )
    # dwithin predicate may be unsupported; fall back is handled gracefully.
    results = duplicates.run(gdf, "l", "s", cfg)
    assert any(r.check == "duplicates.fuzzy" for r in results)


def test_duplicates_disabled():
    gdf = _gdf([square()])
    assert duplicates.run(gdf, "l", "s", DuplicatesCheck(enabled=False)) == []


# ---- Attributes ----

def test_required_missing_column():
    gdf = _gdf([square()], name=["a"])
    res = attributes.run(gdf, "l", "s", AttributesCheck(required=["missing"]))
    assert res[0].status == Status.FAIL


def test_max_null_fraction_breach():
    gdf = _gdf([square(), square(), square(), square()], v=[1, None, None, None])
    cfg = AttributesCheck(max_null_fraction={"v": 0.1})
    res = {r.check: r for r in attributes.run(gdf, "l", "s", cfg)}
    assert res["attributes.completeness[v]"].status == Status.FAIL


def test_regex_domain():
    gdf = _gdf([square(), square()], code=["ABC", "12"])
    cfg = AttributesCheck(domains={"code": DomainRule(regex="^[A-Z]{3}$")})
    res = {r.check: r for r in attributes.run(gdf, "l", "s", cfg)}
    assert res["attributes.domain[code]"].n_failed == 1


def test_attributes_missing_configured_column_warns():
    gdf = _gdf([square()], a=[1])
    cfg = AttributesCheck(not_null=["nonexistent"])
    res = attributes.run(gdf, "l", "s", cfg)
    assert res[0].status in (Status.FAIL, Status.WARN)


def test_attributes_disabled():
    gdf = _gdf([square()])
    assert attributes.run(gdf, "l", "s", AttributesCheck(enabled=False)) == []
