"""Per-layer phase pipeline: cache → load → validate → repair → failures."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from geoqa.checks.sql_postgis import only_sql_safe_checks
from geoqa.config import Suite, check_config_for
from geoqa.datasource import Layer, iter_file_chunks
from geoqa.engine.cache_phase import store_if_clean, try_cache_hit
from geoqa.engine.dispatch import run_checks, timed
from geoqa.engine.failures import collect_failures
from geoqa.engine.load_strategy import (
    materialize_postgis,
    maybe_sql_geometry_hybrid,
    run_sql_only,
)
from geoqa.engine.repair_phase import apply_repair
from geoqa.engine.util import dominant_geom_type, merge_chunk_results
from geoqa.progress import ProgressCb, ProgressEvent, emit
from geoqa.registry import get_registry
from geoqa.result import CheckResult, LayerReport, Severity, Status

logger = logging.getLogger("geoqa")


def run_layer(
    suite: Suite,
    layer: Layer,
    fix_dir: Path | None,
    collect_failures_flag: bool = False,
    audits: dict[str, list[dict]] | None = None,
    audit_lock: threading.Lock | None = None,
    allow_postgis_write: bool = False,
    progress: ProgressCb = None,
    cache_dir: Path | None = None,
) -> LayerReport:
    lr = LayerReport(layer=layer.name, source=layer.source)

    try:
        cfg = suite.config_for_layer(layer.name)
    except Exception as exc:  # noqa: BLE001
        lr.results.append(
            CheckResult(
                check="config", layer=layer.name, source=layer.source,
                status=Status.ERROR, severity=Severity.ERROR,
                message=f"Invalid configuration for layer: {exc}",
            )
        )
        return lr

    cache_key, hit = try_cache_hit(layer, cfg, lr, cache_dir, progress)
    if hit:
        return lr

    # SQL-only path: geometry checks without materializing the GeoDataFrame.
    if (
        layer.prefer_sql
        and layer.connection
        and layer.table
        and layer.source_spec is not None
        and only_sql_safe_checks(cfg)
        and layer.gdf is None
        and not layer.error
    ):
        run_sql_only(layer, cfg, lr, progress)
        store_if_clean(cache_dir, cache_key, lr)
        return lr

    # Deferred PostGIS stubs — materialize when Python-side checks need a GDF.
    if (
        layer.gdf is None
        and not layer.error
        and layer.connection
        and layer.source_spec is not None
    ):
        materialize_postgis(layer, progress)

    if layer.chunk_size and layer.gdf is None and not layer.error:
        return _run_layer_chunked(
            suite, layer, cfg, lr, progress, collect_failures_flag, cache_dir, cache_key,
        )

    if layer.error or layer.gdf is None:
        lr.results.append(
            CheckResult(
                check="load", layer=layer.name, source=layer.source,
                status=Status.ERROR, severity=Severity.ERROR,
                message=f"Could not read layer: {layer.error or 'unknown error'}",
            )
        )
        return lr

    gdf = layer.gdf
    lr.n_features = len(gdf)
    lr.crs = str(gdf.crs) if gdf.crs is not None else None
    lr.geometry_type = dominant_geom_type(gdf)

    skip: set[str] = set()
    if maybe_sql_geometry_hybrid(layer, cfg, lr, progress):
        skip.add("geometry")

    lr.results.extend(
        run_checks(gdf, layer.name, layer.source, cfg, skip=skip, progress=progress)
    )

    gdf = apply_repair(
        gdf, layer, cfg, lr,
        fix_dir=fix_dir, audits=audits, audit_lock=audit_lock,
        allow_postgis_write=allow_postgis_write,
    )

    if collect_failures_flag:
        lr.failures = collect_failures(gdf, lr.results)

    store_if_clean(cache_dir, cache_key, lr)
    return lr


def _run_layer_chunked(
    suite: Suite,
    layer: Layer,
    cfg,
    lr: LayerReport,
    progress: ProgressCb,
    collect_failures_flag: bool,
    cache_dir: Path | None,
    cache_key: str | None,
) -> LayerReport:
    """Run chunk-safe checks in batches; skip full-layer checks with WARN."""
    specs = list(get_registry().specs())
    chunk_safe = [s for s in specs if s.chunk_safe and not s.requires_full_layer]
    full_layer = [s for s in specs if s.requires_full_layer]

    merged: dict[str, list[CheckResult]] = {}
    n_features = 0
    crs = None
    geom_type = None
    first_gdf = None

    attrs_cfg = check_config_for(cfg, "attributes")
    unique_cols: list[str] = []
    if (
        attrs_cfg is not None
        and getattr(attrs_cfg, "enabled", True)
        and getattr(attrs_cfg, "unique", None)
    ):
        unique_cols = list(attrs_cfg.unique)

    chunks = list(iter_file_chunks(layer))
    n_chunks = max(len(chunks), 1)
    for i, chunk in enumerate(chunks):
        n_features += len(chunk)
        if crs is None and chunk.crs is not None:
            crs = str(chunk.crs)
        if geom_type is None:
            geom_type = dominant_geom_type(chunk)
        if first_gdf is None:
            first_gdf = chunk
        frac = (i + 1) / n_chunks
        emit(progress, ProgressEvent(
            layer=layer.name, phase="chunk", fraction=frac,
            message=f"chunk {i + 1}/{n_chunks}",
        ))
        for spec in chunk_safe:
            sub_cfg = check_config_for(cfg, spec.name)
            if sub_cfg is None or not getattr(sub_cfg, "enabled", True):
                continue
            if spec.name == "attributes" and getattr(sub_cfg, "unique", None):
                sub_cfg = sub_cfg.model_copy(update={"unique": []})
            emit(progress, ProgressEvent(
                layer=layer.name, phase="check", check=spec.name, fraction=frac,
            ))
            chunk_results = timed(
                spec.runner, spec.name, chunk, layer.name, layer.source, sub_cfg
            )
            merged.setdefault(spec.name, []).extend(chunk_results)

    lr.n_features = n_features
    lr.crs = crs
    lr.geometry_type = geom_type
    lr.results.extend(merge_chunk_results(merged))

    if unique_cols:
        lr.results.append(
            CheckResult(
                check="attributes.unique", layer=layer.name, source=layer.source,
                status=Status.SKIP, severity=Severity.WARN,
                message=(
                    f"attributes.unique skipped under chunk_size={layer.chunk_size}: "
                    f"needs full layer context ({', '.join(unique_cols)})."
                ),
            )
        )

    for spec in full_layer:
        sub_cfg = check_config_for(cfg, spec.name)
        if sub_cfg is None or not getattr(sub_cfg, "enabled", False):
            continue
        if spec.name == "topology" and not any(
            getattr(sub_cfg, f, False)
            for f in (
                "no_overlaps", "no_gaps", "no_coverage_gaps", "no_spillover",
                "no_dangles", "no_undershoots", "coincident_edges", "coverage_area_ratio",
            )
        ):
            continue
        if spec.name == "duplicates" and not (
            getattr(sub_cfg, "exact", False)
            or getattr(getattr(sub_cfg, "fuzzy", None), "enabled", False)
        ):
            continue
        lr.results.append(
            CheckResult(
                check=spec.name, layer=layer.name, source=layer.source,
                status=Status.SKIP, severity=Severity.WARN,
                message=(
                    f"{spec.name} skipped under chunk_size={layer.chunk_size}: needs full "
                    "layer context (disable chunk_size or run a global pass)."
                ),
            )
        )

    if collect_failures_flag and first_gdf is not None:
        lr.failures = collect_failures(first_gdf, lr.results)
        if n_chunks > 1:
            lr.results.append(
                CheckResult(
                    check="failures.geojson", layer=layer.name, source=layer.source,
                    status=Status.WARN, severity=Severity.WARN,
                    message=(
                        "Chunk mode: failure GeoJSON / HTML map uses geometries from "
                        "the first chunk only; offenders in later chunks may be missing."
                    ),
                )
            )

    store_if_clean(cache_dir, cache_key, lr)
    return lr
