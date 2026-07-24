"""Tests for Phase F scale & ops (chunking, cache, tiles, progress, SQL helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")
from shapely.geometry import box  # noqa: E402

from geoqa.cache import fingerprint, read_entry  # noqa: E402
from geoqa.checks import topology  # noqa: E402
from geoqa.checks.sql_postgis import only_sql_safe_checks  # noqa: E402
from geoqa.config import (  # noqa: E402
    AttributesCheck,
    CrsCheck,
    DuplicatesCheck,
    GeometryCheck,
    LayerConfig,
    TopologyCheck,
    load_suite,
)
from geoqa.engine import run_suite  # noqa: E402
from geoqa.progress import ProgressEvent  # noqa: E402
from geoqa.result import Status  # noqa: E402


def _write_points(path: Path, n: int = 25) -> None:
    gdf = gpd.GeoDataFrame(
        {"id": list(range(n)), "geometry": [box(i, 0, i + 0.5, 0.5) for i in range(n)]},
        crs="EPSG:3857",
    )
    gdf.to_file(path, driver="GPKG")


def test_chunked_run_skips_topology(tmp_path: Path):
    gpkg = tmp_path / "pts.gpkg"
    _write_points(gpkg, 20)
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f"""
version: 1
sources:
  - path: "{gpkg.as_posix()}"
    chunk_size: 5
defaults:
  crs: {{ enabled: false }}
  duplicates: {{ enabled: false }}
  attributes: {{ enabled: false }}
  geometry: {{ valid: true, no_empty: true, no_missing: true }}
layers:
  pts:
    topology:
      enabled: true
      no_overlaps: true
""",
        encoding="utf-8",
    )
    report = run_suite(load_suite(cfg))
    assert len(report.layers) == 1
    by = {r.check: r for r in report.layers[0].results}
    assert "geometry.valid" in by
    assert by["topology"].status == Status.SKIP
    assert "chunk_size" in by["topology"].message


def test_chunked_run_skips_attributes_unique(tmp_path: Path):
    gpkg = tmp_path / "pts.gpkg"
    gpd.GeoDataFrame(
        {"id": list(range(12)), "geometry": [box(i, 0, i + 0.5, 0.5) for i in range(12)]},
        crs="EPSG:3857",
    ).to_file(gpkg, driver="GPKG")
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f"""
version: 1
sources:
  - path: "{gpkg.as_posix()}"
    chunk_size: 4
defaults:
  crs: {{ enabled: false }}
  duplicates: {{ enabled: false }}
  topology: {{ enabled: false }}
  geometry: {{ valid: true }}
  attributes:
    unique: [id]
""",
        encoding="utf-8",
    )
    report = run_suite(load_suite(cfg))
    by = {r.check: r for r in report.layers[0].results}
    assert by["attributes.unique"].status == Status.SKIP
    assert "chunk_size" in by["attributes.unique"].message


def test_fingerprint_cache_skips_second_run(tmp_path: Path):
    gpkg = tmp_path / "clean.gpkg"
    gpd.GeoDataFrame(
        {"geometry": [box(0, 0, 1, 1), box(2, 2, 3, 3)]}, crs="EPSG:3857"
    ).to_file(gpkg, driver="GPKG")
    cache_dir = tmp_path / ".geoqa" / "cache"
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f"""
version: 1
cache:
  enabled: true
  dir: "{cache_dir.as_posix()}"
sources:
  - path: "{gpkg.as_posix()}"
defaults:
  crs: {{ enabled: false }}
  duplicates: {{ enabled: false }}
  attributes: {{ enabled: false }}
  geometry: {{ valid: true }}
  topology: {{ enabled: false }}
""",
        encoding="utf-8",
    )
    suite = load_suite(cfg)
    first = run_suite(suite, use_cache=True)
    assert all(r.status in (Status.PASS, Status.SKIP) for r in first.layers[0].results)
    events: list[str] = []

    def progress(ev):
        events.append(ev.phase if isinstance(ev, ProgressEvent) else "legacy")

    second = run_suite(suite, use_cache=True, progress=progress)
    assert second.layers[0].results[0].check == "cache"
    assert second.layers[0].results[0].status == Status.SKIP
    assert "cache" in events


def test_no_cache_flag_forces_rerun(tmp_path: Path):
    gpkg = tmp_path / "clean.gpkg"
    gpd.GeoDataFrame({"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:3857").to_file(
        gpkg, driver="GPKG"
    )
    cache_dir = tmp_path / "cache"
    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f"""
version: 1
cache:
  enabled: true
  dir: "{cache_dir.as_posix()}"
sources:
  - path: "{gpkg.as_posix()}"
defaults:
  crs: {{ enabled: false }}
  duplicates: {{ enabled: false }}
  attributes: {{ enabled: false }}
  geometry: {{ valid: true }}
""",
        encoding="utf-8",
    )
    suite = load_suite(cfg)
    run_suite(suite, use_cache=True)
    again = run_suite(suite, use_cache=False)
    assert again.layers[0].results[0].check != "cache"


def test_tiled_overlaps_detects_cross_boundary_pair():
    # Two overlapping squares that sit across a tile boundary at x=10.
    a = box(8, 0, 11, 3)
    b = box(9.5, 0, 12.5, 3)
    gdf = gpd.GeoDataFrame({"geometry": [a, b]}, crs="EPSG:3857")
    res = {
        r.check: r
        for r in topology.run(
            gdf, "l", "s",
            TopologyCheck(
                enabled=True, no_overlaps=True, algorithm="pairwise",
                tile_size=10.0, snap_tolerance=1.0,
            ),
        )
    }
    assert res["topology.no_overlaps"].status == Status.WARN
    assert res["topology.no_overlaps"].n_failed == 2
    assert "tiled" in res["topology.no_overlaps"].message


def test_progress_events_include_checks(suite_file: Path):
    seen: list[ProgressEvent] = []
    run_suite(load_suite(suite_file), progress=seen.append)
    assert any(isinstance(e, ProgressEvent) and e.phase == "layer" for e in seen)
    assert any(isinstance(e, ProgressEvent) and e.phase == "check" for e in seen)


def test_legacy_progress_callback_still_works(suite_file: Path):
    seen: list[str] = []

    def legacy(name: str) -> None:
        # TypeError path: reject ProgressEvent by requiring str operations that fail.
        if not isinstance(name, str):
            raise TypeError("legacy callback expects str")
        seen.append(name)

    run_suite(load_suite(suite_file), progress=legacy)
    assert {"parcels", "roads"} <= set(seen)


def test_only_sql_safe_checks_helper():
    safe = LayerConfig(
        crs=CrsCheck(enabled=False),
        duplicates=DuplicatesCheck(enabled=False),
        attributes=AttributesCheck(enabled=False),
        topology=TopologyCheck(enabled=False),
        geometry=GeometryCheck(enabled=True, valid=True),
    )
    assert only_sql_safe_checks(safe) is True
    unsafe = LayerConfig(
        geometry=GeometryCheck(enabled=True),
        topology=TopologyCheck(enabled=True, no_overlaps=True),
    )
    assert only_sql_safe_checks(unsafe) is False
    with_repair = LayerConfig(
        crs=CrsCheck(enabled=False),
        duplicates=DuplicatesCheck(enabled=False),
        attributes=AttributesCheck(enabled=False),
        topology=TopologyCheck(enabled=False),
        geometry=GeometryCheck(
            enabled=True,
            repair={"enabled": True, "make_valid": True, "write_mode": "none"},
        ),
    )
    assert only_sql_safe_checks(with_repair) is False
    with_fix = LayerConfig(
        crs=CrsCheck(enabled=False),
        duplicates=DuplicatesCheck(enabled=False),
        attributes=AttributesCheck(enabled=False),
        topology=TopologyCheck(enabled=False),
        geometry=GeometryCheck(enabled=True, fix=True),
    )
    assert only_sql_safe_checks(with_fix) is False


def test_fingerprint_includes_layer_identity():
    base = dict(source="/data.gpkg", file_stat=(1.0, 10), config_fragment={})
    a = fingerprint(**base, layer_name="parcels", sublayer="parcels")
    b = fingerprint(**base, layer_name="roads", sublayer="roads")
    assert a != b
    t1 = fingerprint(**base, layer_name="x", table="public.a")
    t2 = fingerprint(**base, layer_name="x", table="public.b")
    assert t1 != t2


def test_postgis_layers_are_not_fingerprint_cached(monkeypatch, tmp_path: Path):
    """Connection sources must never skip via fingerprint cache."""
    from geoqa.config import SourceSpec
    from geoqa.datasource import Layer
    from geoqa.result import CheckResult, Severity

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    def fake_sql(spec, layer, cfg, max_issues=50):
        return [
            CheckResult(
                check="geometry.valid", layer=layer, source="s",
                status=Status.PASS, severity=Severity.ERROR,
                message="ok", n_total=3,
            )
        ], 3

    monkeypatch.setattr("geoqa.engine.sql_geometry_checks", fake_sql)

    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        f"""
version: 1
sources:
  - connection: "postgresql://u:p@localhost/db"
    table: public.places
    prefer_sql: true
    name: places
cache:
  enabled: true
  dir: "{cache_dir.as_posix()}"
defaults:
  crs: {{ enabled: false }}
  duplicates: {{ enabled: false }}
  attributes: {{ enabled: false }}
  topology: {{ enabled: false }}
  geometry: {{ valid: true }}
""",
        encoding="utf-8",
    )
    stub = Layer(
        name="places",
        source="postgresql://u:***@localhost/db",
        prefer_sql=True,
        connection="postgresql://u:p@localhost/db",
        table="public.places",
        source_spec=SourceSpec(
            connection="postgresql://u:p@localhost/db",
            table="public.places",
            prefer_sql=True,
            name="places",
        ),
    )
    monkeypatch.setattr("geoqa.engine.iter_layers", lambda suite, defer_load=False: [stub])
    first = run_suite(load_suite(cfg), use_cache=True)
    second = run_suite(load_suite(cfg), use_cache=True)
    assert first.layers[0].results[0].check != "cache"
    assert second.layers[0].results[0].check != "cache"
    assert list(cache_dir.glob("*.json")) == []


def test_engine_sql_only_skips_materialize(monkeypatch, tmp_path: Path):
    from geoqa.result import CheckResult, Severity

    def fake_sql(spec, layer, cfg, max_issues=50):
        return [
            CheckResult(
                check="geometry.valid", layer=layer, source="s",
                status=Status.PASS, severity=Severity.ERROR,
                message="ok via sql", n_total=42,
            )
        ], 42

    monkeypatch.setattr("geoqa.engine.sql_geometry_checks", fake_sql)
    monkeypatch.setattr("geoqa.engine.only_sql_safe_checks", lambda cfg: True)

    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        """
version: 1
sources:
  - connection: "postgresql://u:p@localhost/db"
    table: public.parcels
    prefer_sql: true
    name: parcels
defaults:
  crs: { enabled: false }
  duplicates: { enabled: false }
  attributes: { enabled: false }
  topology: { enabled: false }
  geometry: { valid: true }
""",
        encoding="utf-8",
    )
    # Avoid real PostGIS load: stub iter_layers to yield a prefer_sql Layer without gdf.
    from geoqa.config import SourceSpec
    from geoqa.datasource import Layer

    stub = Layer(
        name="parcels",
        source="postgresql://u:***@localhost/db",
        prefer_sql=True,
        connection="postgresql://u:p@localhost/db",
        table="public.parcels",
        source_spec=SourceSpec(
            connection="postgresql://u:p@localhost/db",
            table="public.parcels",
            prefer_sql=True,
            name="parcels",
        ),
    )
    monkeypatch.setattr("geoqa.engine.iter_layers", lambda suite, defer_load=False: [stub])
    report = run_suite(load_suite(cfg))
    assert report.layers[0].n_features == 42
    assert report.layers[0].results[0].message == "ok via sql"
    assert stub.gdf is None


def test_prefer_sql_materializes_when_other_checks_need_gdf(monkeypatch, tmp_path: Path):
    """prefer_sql defers load; if CRS/etc. are on, engine must materialize PostGIS."""
    from conftest import make_parcels

    from geoqa.config import SourceSpec, load_suite
    from geoqa.datasource import Layer
    from geoqa.engine import run_suite
    from geoqa.result import CheckResult, Severity, Status

    loaded = {"n": 0}

    def fake_load(spec):
        loaded["n"] += 1
        return Layer(
            name=spec.name or "parcels",
            source="postgresql://u:***@localhost/db",
            gdf=make_parcels(),
            prefer_sql=True,
            connection=spec.connection,
            table=spec.table,
            source_spec=spec,
        )

    def fake_sql(spec, layer, cfg, *, max_issues=50):
        return [
            CheckResult(
                check="geometry.valid",
                layer=layer,
                source="postgresql://u:***@localhost/db",
                status=Status.PASS,
                severity=Severity.ERROR,
                message="ok via sql after materialize",
            )
        ], 5

    monkeypatch.setattr("geoqa.engine._load_postgis", fake_load)
    monkeypatch.setattr("geoqa.engine.sql_geometry_checks", fake_sql)

    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        """
version: 1
sources:
  - connection: "postgresql://u:p@localhost/db"
    table: public.parcels
    prefer_sql: true
    name: parcels
defaults:
  crs: { required: true, allowed_epsg: [3857] }
  geometry: { valid: true }
""",
        encoding="utf-8",
    )
    stub = Layer(
        name="parcels",
        source="postgresql://u:***@localhost/db",
        prefer_sql=True,
        connection="postgresql://u:p@localhost/db",
        table="public.parcels",
        source_spec=SourceSpec(
            connection="postgresql://u:p@localhost/db",
            table="public.parcels",
            prefer_sql=True,
            name="parcels",
        ),
    )
    monkeypatch.setattr("geoqa.engine.iter_layers", lambda suite, defer_load=False: [stub])
    report = run_suite(load_suite(cfg))
    assert loaded["n"] == 1
    assert stub.gdf is not None
    assert report.layers[0].n_features == len(stub.gdf)
    assert not any(r.check == "load" and r.status.value == "error" for r in report.layers[0].results)


def test_corrupt_cache_entry_ignored(tmp_path: Path):
    key = fingerprint(source="z", file_stat=(1.0, 1), config_fragment={})
    bad = tmp_path / f"{key}.json"
    bad.write_text("{not-json", encoding="utf-8")
    assert read_entry(tmp_path, key) is None


def test_prefer_sql_with_repair_materializes(monkeypatch, tmp_path: Path):
    """Repair enabled must not take the SQL-only early return."""
    from conftest import make_parcels

    from geoqa.config import SourceSpec
    from geoqa.datasource import Layer
    from geoqa.result import CheckResult, Severity, Status

    loaded = {"n": 0}

    def fake_load(spec):
        loaded["n"] += 1
        return Layer(
            name="parcels",
            source="postgresql://u:***@localhost/db",
            gdf=make_parcels(),
            prefer_sql=True,
            connection=spec.connection,
            table=spec.table,
            source_spec=spec,
        )

    def fake_sql(spec, layer, cfg, *, max_issues=50):
        return [
            CheckResult(
                check="geometry.valid",
                layer=layer,
                source="s",
                status=Status.PASS,
                severity=Severity.ERROR,
                message="sql",
            )
        ], 5

    monkeypatch.setattr("geoqa.engine._load_postgis", fake_load)
    monkeypatch.setattr("geoqa.engine.sql_geometry_checks", fake_sql)

    cfg = tmp_path / "geoqa.yml"
    cfg.write_text(
        """
version: 1
sources:
  - connection: "postgresql://u:p@localhost/db"
    table: public.parcels
    prefer_sql: true
    name: parcels
defaults:
  crs: { enabled: false }
  duplicates: { enabled: false }
  attributes: { enabled: false }
  topology: { enabled: false }
  geometry:
    valid: true
    repair:
      enabled: true
      make_valid: true
      write_mode: none
""",
        encoding="utf-8",
    )
    stub = Layer(
        name="parcels",
        source="postgresql://u:***@localhost/db",
        prefer_sql=True,
        connection="postgresql://u:p@localhost/db",
        table="public.parcels",
        source_spec=SourceSpec(
            connection="postgresql://u:p@localhost/db",
            table="public.parcels",
            prefer_sql=True,
            name="parcels",
        ),
    )
    monkeypatch.setattr("geoqa.engine.iter_layers", lambda suite, defer_load=False: [stub])
    report = run_suite(load_suite(cfg))
    assert loaded["n"] == 1
    assert any(r.check == "geometry.repair" for r in report.layers[0].results)


def test_sql_pushdown_mocked(monkeypatch):
    import sqlalchemy

    from geoqa.checks import sql_postgis
    from geoqa.config import SourceSpec

    class FakeResult:
        def __init__(self, value):
            self._value = value

        def scalar(self):
            return self._value

        def fetchall(self):
            return []

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "COUNT(*)" in sql and "IS NULL" not in sql and "ST_" not in sql:
                return FakeResult(1000)
            return FakeResult(0)

    class FakeEngine:
        def connect(self):
            return FakeConn()

        def dispose(self):
            pass

    eng = FakeEngine()
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *_a, **_k: eng)

    spec = SourceSpec(
        connection="postgresql://u:p@localhost/db",
        table="public.parcels",
        prefer_sql=True,
    )
    results, n = sql_postgis.sql_geometry_checks(
        spec, "parcels", GeometryCheck(enabled=True, valid=True, no_empty=True, no_missing=True)
    )
    assert n == 1000
    assert {r.check for r in results} >= {
        "geometry.valid", "geometry.no_empty", "geometry.no_missing",
    }
    assert all(r.status == Status.PASS for r in results)