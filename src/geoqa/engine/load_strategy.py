"""Load strategy: SQL-only, materialize PostGIS, chunk file, or full GDF."""

from __future__ import annotations

from enum import Enum

from geoqa.checks.sql_postgis import only_sql_safe_checks, sql_geometry_checks
from geoqa.datasource import Layer, _load_postgis
from geoqa.progress import ProgressCb, ProgressEvent, emit
from geoqa.result import LayerReport, Status


class LoadStrategy(str, Enum):
    SQL_ONLY = "sql_only"
    MATERIALIZE_POSTGIS = "materialize_postgis"
    CHUNK_FILE = "chunk_file"
    FULL_GDF = "full_gdf"
    LOAD_ERROR = "load_error"


def choose_strategy(layer: Layer, cfg) -> LoadStrategy:
    """Decide how to load / validate ``layer`` given its config."""
    if (
        layer.prefer_sql
        and layer.connection
        and layer.table
        and layer.source_spec is not None
        and only_sql_safe_checks(cfg)
        and layer.gdf is None
        and not layer.error
    ):
        return LoadStrategy.SQL_ONLY
    if layer.chunk_size and layer.gdf is None and not layer.error and not layer.connection:
        return LoadStrategy.CHUNK_FILE
    if layer.connection and layer.gdf is None and not layer.error and layer.source_spec is not None:
        return LoadStrategy.MATERIALIZE_POSTGIS
    if layer.error or layer.gdf is None:
        # May still need materialize first — callers run materialize before re-choose.
        if layer.connection and layer.source_spec is not None and not layer.error:
            return LoadStrategy.MATERIALIZE_POSTGIS
        return LoadStrategy.LOAD_ERROR
    return LoadStrategy.FULL_GDF


def run_sql_only(
    layer: Layer, cfg, lr: LayerReport, progress: ProgressCb
) -> LayerReport:
    emit(progress, ProgressEvent(
        layer=layer.name, phase="check", check="geometry", message="SQL pushdown",
    ))
    assert layer.source_spec is not None
    sql_results, n_rows = sql_geometry_checks(layer.source_spec, layer.name, cfg.geometry)
    lr.n_features = n_rows
    lr.results.extend(sql_results)
    return lr


def materialize_postgis(layer: Layer, progress: ProgressCb) -> None:
    """Fill ``layer.gdf`` / ``layer.error`` from a deferred PostGIS stub."""
    if layer.source_spec is None:
        return
    emit(progress, ProgressEvent(
        layer=layer.name, phase="load", message="materialize PostGIS layer",
    ))
    loaded = _load_postgis(layer.source_spec)
    layer.gdf = loaded.gdf
    layer.error = loaded.error


def maybe_sql_geometry_hybrid(
    layer: Layer, cfg, lr: LayerReport, progress: ProgressCb
) -> bool:
    """Run SQL geometry pushdown when prefer_sql; return True to skip Python geometry."""
    if not (
        layer.prefer_sql and layer.connection and layer.table and layer.source_spec is not None
    ):
        return False
    emit(progress, ProgressEvent(
        layer=layer.name, phase="check", check="geometry", message="SQL pushdown",
    ))
    sql_results, _n = sql_geometry_checks(layer.source_spec, layer.name, cfg.geometry)
    if sql_results and all(r.status != Status.ERROR for r in sql_results):
        lr.results.extend(sql_results)
        return True
    return False
