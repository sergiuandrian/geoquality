"""Orchestration: load layers, resolve config, run every check, collect results."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import geopandas as gpd

from geoqa.config import Suite
from geoqa.datasource import Layer, iter_layers
from geoqa.registry import get_registry
from geoqa.result import CheckResult, LayerReport, Report, Severity, Status

logger = logging.getLogger("geoqa")

ProgressCb = Callable[[str], None] | None


def run_suite(
    suite: Suite,
    fix_output_dir: str | Path | None = None,
    progress: ProgressCb = None,
    workers: int = 1,
    collect_failures: bool = False,
    repair_audit_path: str | Path | None = None,
    allow_postgis_write: bool = False,
) -> Report:
    """Run every configured check against every layer and return a Report.

    ``workers`` > 1 validates layers concurrently with a thread pool. Geometry
    checks spend most of their time in GEOS/pandas, which release the GIL, so
    threads give real speedups while avoiding the pickling/spawn cost (and
    Windows ``__main__`` pitfalls) of process pools.

    ``collect_failures`` additionally gathers the offending features of each
    layer into a WGS84 GeoJSON FeatureCollection on ``LayerReport.failures`` (for
    GeoJSON export and the HTML map).

    ``repair_audit_path`` writes a JSON audit of repair actions when the repair
    pipeline runs. ``allow_postgis_write`` is required (with ``dry_run: false``)
    before any live PostGIS UPDATE.
    """
    report = Report(suite_name=suite.name)
    fix_dir = Path(fix_output_dir) if fix_output_dir else None
    audit_path = Path(repair_audit_path) if repair_audit_path else None
    audits: dict[str, list[dict]] = {}
    audit_lock = threading.Lock()

    layers = list(iter_layers(suite))
    if workers and workers > 1 and len(layers) > 1:
        report.layers = _run_layers_parallel(
            suite, layers, fix_dir, progress, workers, collect_failures,
            audits, audit_lock, allow_postgis_write,
        )
    else:
        for layer in layers:
            if progress:
                progress(layer.name)
            logger.debug("running checks for layer %s (%s)", layer.name, layer.source)
            report.layers.append(
                _run_layer(
                    suite, layer, fix_dir, collect_failures, audits, audit_lock,
                    allow_postgis_write,
                )
            )

    if audit_path is not None and audits:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps({"suite": suite.name, "layers": audits}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    report.finished_at = _now()
    return report


def _run_layers_parallel(
    suite: Suite,
    layers: list[Layer],
    fix_dir: Path | None,
    progress: ProgressCb,
    workers: int,
    collect_failures: bool,
    audits: dict[str, list[dict]],
    audit_lock: threading.Lock,
    allow_postgis_write: bool,
) -> list[LayerReport]:
    from concurrent.futures import ThreadPoolExecutor

    results: dict[int, LayerReport] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_layer, suite, layer, fix_dir, collect_failures, audits,
                audit_lock, allow_postgis_write,
            ): i
            for i, layer in enumerate(layers)
        }
        for future in futures:
            idx = futures[future]
            if progress:
                progress(layers[idx].name)
            results[idx] = future.result()
    return [results[i] for i in range(len(layers))]


def _run_layer(
    suite: Suite,
    layer: Layer,
    fix_dir: Path | None,
    collect_failures: bool = False,
    audits: dict[str, list[dict]] | None = None,
    audit_lock: threading.Lock | None = None,
    allow_postgis_write: bool = False,
) -> LayerReport:
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

    for spec in get_registry().specs():
        sub_cfg = getattr(cfg, spec.name, None)
        if sub_cfg is None:
            continue
        lr.results.extend(
            _timed(spec.runner, spec.name, gdf, layer.name, layer.source, sub_cfg)
        )

    from geoqa.repair import effective_repair, run_repair, write_postgis

    repair_cfg = effective_repair(cfg.geometry)
    if repair_cfg is not None and layer.gdf is not None:
        repaired, actions = run_repair(gdf, repair_cfg)
        if audits is not None and actions:
            payload = [a.to_dict() for a in actions]
            if audit_lock is not None:
                with audit_lock:
                    audits[layer.name] = payload
            else:
                audits[layer.name] = payload
        gdf = repaired
        layer.gdf = repaired
        n_actions = len(actions)
        lr.results.append(
            CheckResult(
                check="geometry.repair", layer=layer.name, source=layer.source,
                status=Status.PASS,
                severity=Severity.INFO,
                message=(
                    f"Repair pipeline applied {n_actions} action(s)."
                    if n_actions
                    else "Repair pipeline ran; no geometry changes."
                ),
                fixed=n_actions,
            )
        )
        if repair_cfg.write_mode == "file":
            if fix_dir is not None:
                _write_fixed(gdf, layer, fix_dir, lr)
            else:
                lr.results.append(
                    CheckResult(
                        check="geometry.repair.output", layer=layer.name, source=layer.source,
                        status=Status.WARN, severity=Severity.WARN,
                        message="Repair ran but --fix-output was not set; nothing written.",
                    )
                )
        elif repair_cfg.write_mode == "postgis":
            pg = write_postgis(
                gdf, repair_cfg.postgis, allow_write=allow_postgis_write,
            )
            status = (
                Status.ERROR if "failed" in pg["message"].lower() else Status.PASS
            )
            lr.results.append(
                CheckResult(
                    check="geometry.repair.postgis", layer=layer.name, source=layer.source,
                    status=status,
                    severity=Severity.INFO if status == Status.PASS else Severity.ERROR,
                    message=pg["message"],
                )
            )
    else:
        # Legacy path: geometry.fix mutated gdf in-place during the check.
        fixed_total = sum(r.fixed for r in lr.results)
        if fixed_total and fix_dir is not None:
            _write_fixed(gdf, layer, fix_dir, lr)

    if collect_failures:
        lr.failures = _collect_failures(gdf, lr.results)

    return lr


def _collect_failures(gdf: gpd.GeoDataFrame, results: list[CheckResult]) -> dict | None:
    """Build a WGS84 GeoJSON FeatureCollection of offending features.

    Prefer ``Issue.row_index`` (GeoDataFrame index). Fall back to
    ``Issue.feature_id`` only when it is a valid index label (legacy spatial
    checks that set feature_id to the row index). Attribute display ids that
    are not index labels are ignored for geometry collection.
    """
    failed: dict[Any, set[str]] = {}
    for r in results:
        if r.status not in (Status.FAIL, Status.ERROR):
            continue
        for issue in r.issues:
            key = issue.row_index if issue.row_index is not None else issue.feature_id
            if key is not None:
                failed.setdefault(key, set()).add(r.check)

    if not failed:
        return None

    try:
        index = gdf.index
        ids = [fid for fid in failed if fid in index]
        if not ids:
            return None
        subset = gdf.loc[ids, [gdf.geometry.name]].copy()
        subset["geoqa_failed_checks"] = [", ".join(sorted(failed[i])) for i in ids]
        if subset.crs is not None and subset.crs.to_epsg() != 4326:
            subset = subset.to_crs(4326)
        return json.loads(subset.to_json())
    except Exception:  # noqa: BLE001 - export is best-effort, never fail a run
        logger.exception("failed to collect offending features")
        return None


def _timed(fn, check_name, gdf, layer_name, source, sub_cfg) -> list[CheckResult]:
    start = time.perf_counter()
    try:
        results = fn(gdf, layer_name, source, sub_cfg)
    except Exception as exc:  # noqa: BLE001
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
