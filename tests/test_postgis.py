"""Tests for the PostGIS/SQLAlchemy data source and connection redaction."""

from __future__ import annotations

import pytest

gpd = pytest.importorskip("geopandas")

from conftest import make_parcels  # noqa: E402

from geoqa import datasource  # noqa: E402
from geoqa.config import SourceSpec  # noqa: E402
from geoqa.datasource import _load_postgis, _redact  # noqa: E402


def test_sourcespec_connection_requires_table_or_query():
    with pytest.raises(Exception):
        SourceSpec(connection="postgresql://u@h/db")


def test_sourcespec_table_and_query_mutually_exclusive():
    with pytest.raises(Exception):
        SourceSpec(connection="postgresql://u@h/db", table="t", query="select 1")


def test_sourcespec_needs_path_or_connection():
    with pytest.raises(Exception):
        SourceSpec()


def test_redact_hides_password():
    assert _redact("postgresql://user:secret@host:5432/db") == (
        "postgresql://user:***@host:5432/db"
    )
    assert _redact("sqlite:///local.db") == "sqlite:///local.db"


def test_load_postgis_happy_path(monkeypatch):
    pytest.importorskip("sqlalchemy")
    captured = {}

    def fake_read_postgis(sql, con, geom_col):
        captured["sql"] = sql
        captured["geom_col"] = geom_col
        return make_parcels()

    monkeypatch.setattr(gpd, "read_postgis", fake_read_postgis)
    spec = SourceSpec(connection="sqlite://", table="parcels", geom_column="geom")
    layer = _load_postgis(spec)

    assert layer.error is None
    assert layer.gdf is not None and len(layer.gdf) == 4
    assert captured["sql"] == "SELECT * FROM parcels"
    assert captured["geom_col"] == "geom"


def test_load_postgis_query_used(monkeypatch):
    pytest.importorskip("sqlalchemy")

    def fake_read_postgis(sql, con, geom_col):
        assert sql == "SELECT * FROM roads WHERE active"
        return make_parcels()

    monkeypatch.setattr(gpd, "read_postgis", fake_read_postgis)
    spec = SourceSpec(connection="sqlite://", query="SELECT * FROM roads WHERE active")
    assert _load_postgis(spec).error is None


def test_load_postgis_error_becomes_error_layer(monkeypatch):
    pytest.importorskip("sqlalchemy")

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(gpd, "read_postgis", boom)
    spec = SourceSpec(connection="sqlite://", table="t")
    layer = _load_postgis(spec)
    assert layer.error is not None and "connection refused" in layer.error


def test_iter_layers_dispatches_to_postgis(monkeypatch):
    from geoqa.config import Suite

    monkeypatch.setattr(gpd, "read_postgis", lambda *a, **k: make_parcels())
    suite = Suite(sources=[SourceSpec(connection="sqlite://", table="parcels")])
    layers = list(datasource.iter_layers(suite))
    assert len(layers) == 1 and layers[0].gdf is not None
