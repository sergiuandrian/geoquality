"""Orchestration: load layers, resolve config, run every check, collect results."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd

from geoqa.checks import attributes, crs, duplicates, geometry, topology
from geoqa.config import LayerConfig, Suite
from geoqa.datasource import Layer, iter_layers
from geoqa.result import CheckResult, LayerReport, Report, Severity, Status

logger = logging.getLogger("geoqa")

# (config attribute on LayerConfig, check module) pairs, in execution order.
_CHECKS: list[tuple[str, Callable]] = [
    ("crs", crs.run),
    ("geometry", geometry.run),
    ("duplicates", duplicates.run),
    ("attributes", attributes.run),
    ("topology", topology.run),
]

ProgressCb = Callable[[str], None] | None


def run_suite(
    suite: Suite,
    fix_output_dir: str | Path | None = None,
    progress: ProgressCb = None,
) -> Report:
    """Run every configured check against every layer and return a Report."""
    report = Report(suite_name=suite.name)
    fix_dir = Path(fix_output_dir) if fix_output_dir else None

    for layer in iter_layers(suite):
        if progress:
            progress(layer.name)
        logger.debug("running checks for layer %s (%s)", layer.name, layer.source)
        lr = _run_layer(suite, layer, fix_dir)
        report.layers.append(lr)

    report.finished_at = _now()
    return report


def _run_layer(suite: Suite, layer: Layer, fix_dir: Path | None) -> LayerReport:
    lr = LayerReport(layer=layer.name, source=layer.source)

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
    lr.geometry_type = _dominant_geom_type(gdf)

    try:
        cfg: LayerConfig = suite.config_for_layer(layer.name)
    except Exception as exc:  # noqa: BLE001
        lr.results.append(
            CheckResult(
                check="config", layer=layer.name, source=layer.source,
                status=Status.ERROR, severity=Severity.ERROR,
                message=f"Invalid configuration for layer: {exc}",
            )
        )
        return lr

    for attr, fn in _CHECKS:
        sub_cfg = getattr(cfg, attr)
        lr.results.extend(_timed(fn, gdf, layer.name, layer.source, sub_cfg))

    fixed_total = sum(r.fixed for r in lr.results)
    if fixed_total and fix_dir is not None:
        _write_fixed(gdf, layer, fix_dir, lr)

    return lr


def _timed(fn, gdf, layer_name, source, sub_cfg) -> list[CheckResult]:
    start = time.perf_counter()
    try:
        results = fn(gdf, layer_name, source, sub_cfg)
    except Exception as exc:  # noqa: BLE001
        check_name = fn.__module__.split(".")[-1]
        logger.exception("check %r crashed on layer %s", check_name, layer_name)
        results = [
            CheckResult(
                check=check_name, layer=layer_name, source=source,
                status=Status.ERROR, severity=Severity.ERROR,
                message=f"check raised {type(exc).__name__}: {exc}",
            )
        ]
    elapsed = time.perf_counter() - start
    share = elapsed / len(results) if results else 0.0
    for r in results:
        r.duration_s = share
    return results


def _write_fixed(gdf: gpd.GeoDataFrame, layer: Layer, fix_dir: Path, lr: LayerReport) -> None:
    fix_dir.mkdir(parents=True, exist_ok=True)
    out = fix_dir / f"{layer.name}.fixed.gpkg"
    try:
        gdf.to_file(out, driver="GPKG")
        lr.results.append(
            CheckResult(
                check="geometry.fix.output", layer=layer.name, source=layer.source,
                status=Status.PASS, severity=Severity.INFO,
                message=f"Wrote repaired layer to {out}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        lr.results.append(
            CheckResult(
                check="geometry.fix.output", layer=layer.name, source=layer.source,
                status=Status.ERROR, severity=Severity.WARN,
                message=f"Failed to write repaired layer: {exc}",
            )
        )


def _dominant_geom_type(gdf: gpd.GeoDataFrame) -> str | None:
    if gdf.geometry.name not in gdf.columns or gdf.empty:
        return None
    try:
        types = gdf.geometry.geom_type.dropna()
        if types.empty:
            return None
        return str(types.mode().iloc[0])
    except Exception:  # noqa: BLE001
        return None


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
