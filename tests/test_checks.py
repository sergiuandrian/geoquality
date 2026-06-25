import pytest

gpd = pytest.importorskip("geopandas")
from shapely.geometry import Polygon  # noqa: E402

from geoqa.checks import attributes, crs, duplicates, geometry  # noqa: E402
from geoqa.config import (  # noqa: E402
    AttributesCheck,
    CrsCheck,
    DomainRule,
    DuplicatesCheck,
    GeometryCheck,
)
from geoqa.result import Status  # noqa: E402

SQUARE = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
BOWTIE = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])  # self-intersecting -> invalid


def _gdf(geoms, crs_str="EPSG:3857", **cols):
    return gpd.GeoDataFrame({**cols, "geometry": geoms}, crs=crs_str)


def test_geometry_detects_and_fixes_invalid():
    gdf = _gdf([SQUARE, BOWTIE])
    res = geometry.run(gdf, "lyr", "src", GeometryCheck(fix=False))
    valid = next(r for r in res if r.check == "geometry.valid")
    assert valid.status == Status.FAIL
    assert valid.n_failed == 1

    gdf2 = _gdf([SQUARE, BOWTIE])
    res2 = geometry.run(gdf2, "lyr", "src", GeometryCheck(fix=True))
    valid2 = next(r for r in res2 if r.check == "geometry.valid")
    assert valid2.fixed == 1
    assert valid2.status == Status.PASS
    assert bool(gdf2.geometry.is_valid.all())


def test_exact_duplicates():
    gdf = _gdf([SQUARE, SQUARE, BOWTIE])
    res = duplicates.run(gdf, "lyr", "src", DuplicatesCheck(exact=True))
    exact = next(r for r in res if r.check == "duplicates.exact")
    assert exact.n_failed == 1  # one redundant copy


def test_crs_allowed_list():
    gdf = _gdf([SQUARE], crs_str="EPSG:4326")
    ok = crs.run(gdf, "lyr", "src", CrsCheck(allowed_epsg=[4326]))[0]
    assert ok.status == Status.PASS
    bad = crs.run(gdf, "lyr", "src", CrsCheck(allowed_epsg=[3857]))[0]
    assert bad.status == Status.FAIL


def test_attribute_domain_and_nulls():
    gdf = _gdf([SQUARE, SQUARE, SQUARE], zone=["residential", "mars", None])
    cfg = AttributesCheck(
        not_null=["zone"],
        domains={"zone": DomainRule(allowed=["residential", "commercial"])},
    )
    res = attributes.run(gdf, "lyr", "src", cfg)
    not_null = next(r for r in res if "not_null" in r.check)
    domain = next(r for r in res if "domain" in r.check)
    assert not_null.n_failed == 1  # one None
    assert domain.n_failed == 1  # "mars" out of domain (None ignored)
