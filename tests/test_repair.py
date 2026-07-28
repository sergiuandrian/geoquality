"""Tests for the geometry repair pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")
from conftest import bowtie, square  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

from geoqa.config import GeometryCheck, PostgisWriteConfig, RepairConfig, load_suite  # noqa: E402
from geoqa.engine import run_suite  # noqa: E402
from geoqa.repair import effective_repair, run_repair, write_postgis  # noqa: E402
from geoqa.repair.ops import op_dissolve_duplicates, op_drop_slivers, op_make_valid  # noqa: E402
from geoqa.result import Status  # noqa: E402


def test_effective_repair_from_legacy_fix():
    cfg = GeometryCheck(fix=True)
    r = effective_repair(cfg)
    assert r is not None and r.make_valid and r.write_mode == "file"


def test_effective_repair_none_when_disabled():
    assert effective_repair(GeometryCheck()) is None


def test_op_make_valid_repairs_bowtie():
    gdf = gpd.GeoDataFrame({"geometry": [bowtie()]}, crs="EPSG:3857")
    assert not gdf.geometry.iloc[0].is_valid
    audit: list = []
    out = op_make_valid(gdf, audit)
    assert out.geometry.iloc[0].is_valid
    assert any(a.op == "make_valid" for a in audit)


def test_op_drop_slivers():
    big = box(0, 0, 10, 10)
    tiny = box(0, 0, 0.01, 0.01)
    from shapely.geometry import MultiPolygon

    gdf = gpd.GeoDataFrame(
        {"geometry": [MultiPolygon([big, tiny])]}, crs="EPSG:3857"
    )
    audit: list = []
    out = op_drop_slivers(gdf, min_area_m2=1.0, audit=audit)
    assert out.geometry.iloc[0].geom_type == "Polygon"
    assert any(a.op == "drop_slivers" for a in audit)


def test_op_dissolve_duplicates_keeps_first():
    g = square()
    gdf = gpd.GeoDataFrame({"id": [1, 2], "geometry": [g, g]}, crs="EPSG:3857")
    audit: list = []
    out = op_dissolve_duplicates(gdf, audit)
    assert len(out) == 1
    assert out.iloc[0]["id"] == 1
    assert len(audit) == 1


def test_run_repair_pipeline_end_to_end():
    g = square()
    gdf = gpd.GeoDataFrame(
        {"geometry": [bowtie(), g, g]},
        crs="EPSG:3857",
    )
    cfg = RepairConfig(
        enabled=True, make_valid=True, dissolve_duplicates=True, write_mode="none"
    )
    out, audit = run_repair(gdf, cfg)
    assert all(out.geometry.is_valid | out.geometry.is_empty)
    assert len(out) == 2  # one duplicate dropped
    assert {a.op for a in audit} >= {"make_valid", "dissolve_duplicates"}


def test_engine_repair_writes_gpkg_and_audit(tmp_path: Path):
    gpkg = tmp_path / "data.gpkg"
    gpd.GeoDataFrame(
        {"parcel_id": ["P1"], "geometry": [bowtie()]}, crs="EPSG:3857"
    ).to_file(gpkg, driver="GPKG")
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f"""
version: 1
name: repair test
sources:
  - path: "{gpkg.as_posix()}"
    name: parcels
defaults:
  geometry:
    valid: true
    repair:
      enabled: true
      make_valid: true
      write_mode: file
""",
        encoding="utf-8",
    )
    out = tmp_path / "fixed"
    audit = tmp_path / "audit.json"
    report = run_suite(
        load_suite(cfg), fix_output_dir=out, repair_audit_path=audit
    )
    assert (out / "parcels.fixed.gpkg").exists()
    assert audit.exists()
    assert any(r.check == "geometry.repair" for r in report.all_results)


def test_then_recheck_appends_after_repair_results(tmp_path: Path):
    gpkg = tmp_path / "data.gpkg"
    gpd.GeoDataFrame(
        {"parcel_id": ["P1"], "geometry": [bowtie()]}, crs="EPSG:3857"
    ).to_file(gpkg, driver="GPKG")
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f"""
version: 1
name: then-recheck
sources:
  - path: "{gpkg.as_posix()}"
    name: parcels
defaults:
  geometry:
    valid: true
    repair:
      enabled: true
      make_valid: true
      write_mode: none
      then_recheck: true
""",
        encoding="utf-8",
    )
    report = run_suite(load_suite(cfg))
    by = {r.check: r for r in report.all_results}
    assert by["geometry.valid"].status == Status.FAIL
    assert by["geometry.valid.after_repair"].status == Status.PASS
    assert by["geometry.repair"].fixed >= 1


def test_postgis_write_dry_run_default():
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326"
    )
    cfg = PostgisWriteConfig(
        connection="postgresql://u:p@localhost/db", table="public.t", dry_run=True
    )
    result = write_postgis(gdf, cfg, allow_write=False)
    assert result["dry_run"] is True
    assert result["ok"] is True
    assert "dry_run" in result["message"]
    assert result["updated"] == 0


def test_postgis_write_refuses_without_allow_flag():
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326"
    )
    cfg = PostgisWriteConfig(
        connection="postgresql://u:p@localhost/db", table="t", dry_run=False
    )
    result = write_postgis(gdf, cfg, allow_write=False)
    assert result["dry_run"] is True
    assert result["ok"] is True
    assert result["updated"] == 0


def test_postgis_write_refuses_missing_epsg():
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [Point(0, 0)]}, crs=None
    )
    cfg = PostgisWriteConfig(
        connection="postgresql://u:p@localhost/db", table="t", dry_run=False
    )
    result = write_postgis(gdf, cfg, allow_write=True)
    assert result["ok"] is False
    assert result["updated"] == 0
    assert "EPSG" in result["message"]


def test_postgis_write_uses_active_geometry_column(monkeypatch):
    """PostGIS layers often use geom as the active geometry column name."""
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geom": [Point(0, 0)]}, geometry="geom", crs="EPSG:4326"
    )
    cfg = PostgisWriteConfig(
        connection="postgresql://u:p@localhost/db",
        table="public.t",
        dry_run=False,
        geom_column="geom",
    )

    class OkResult:
        rowcount = 1

    class OkConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt, params=None):
            assert params is not None and "wkb" in params
            return OkResult()

    class OkEngine:
        def begin(self):
            return OkConn()

        def dispose(self):
            pass

    monkeypatch.setattr(
        "sqlalchemy.create_engine", lambda *a, **k: OkEngine()
    )
    result = write_postgis(gdf, cfg, allow_write=True)
    assert result["ok"] is True
    assert result["updated"] == 1
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "geometry": [Point(0, 0), Point(1, 1)]}, crs="EPSG:4326"
    )
    cfg = PostgisWriteConfig(
        connection="postgresql://u:p@localhost/db",
        table="public.t",
        dry_run=False,
    )

    class FakeResult:
        def __init__(self, n):
            self.rowcount = n

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt, params=None):
            # First id matches, second misses
            return FakeResult(1 if params and params.get("pk") == 1 else 0)

    class FakeEngine:
        def begin(self):
            return FakeConn()

        def dispose(self):
            pass

    import sqlalchemy

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *_a, **_k: FakeEngine())
    result = write_postgis(gdf, cfg, allow_write=True)
    assert result["ok"] is False
    assert result["updated"] == 1
    assert result["missed"] == 1
    assert "failed" in result["message"]


def test_repair_config_rejects_bad_write_mode():
    with pytest.raises(Exception):
        RepairConfig(write_mode="s3")


def test_repair_config_rejects_dissolve_with_postgis():
    with pytest.raises(Exception, match="dissolve_duplicates"):
        RepairConfig(
            enabled=True,
            dissolve_duplicates=True,
            write_mode="postgis",
            postgis=PostgisWriteConfig(connection="postgresql://u@h/db", table="t"),
        )
