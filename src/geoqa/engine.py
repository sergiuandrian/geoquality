"""Orchestration: load layers, resolve config, run every check, collect results."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import geopandas as gpd

from geoqa import cache as geoqa_cache
from geoqa.checks.sql_postgis import only_sql_safe_checks, sql_geometry_checks
from geoqa.config import Suite, check_config_for
from geoqa.datasource import (
    CHUNK_GLOBAL_CHECKS,
    CHUNK_SAFE_CHECKS,
    Layer,
    _load_postgis,
    iter_file_chunks,
    iter_layers,
)
from geoqa.progress import ProgressCb, ProgressEvent, emit
from geoqa.registry import get_registry
from geoqa.result import CheckResult, LayerReport, Report, Severity, Status

logger = logging.getLogger("geoqa")


def run_suite(
    suite: Suite,
    fix_output_dir: str | Path | None = None,
    progress: ProgressCb = None,
    workers: int = 1,
    collect_failures: bool = False,
    repair_audit_path: str | Path | None = None,
    allow_postgis_write: bool = False,
    use_cache: bool | None = None,
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

    ``use_cache`` overrides ``suite.cache.enabled`` when not ``None``.
    """
    report = Report(suite_name=suite.name)
    fix_dir = Path(fix_output_dir) if fix_output_dir else None
    audit_path = Path(repair_audit_path) if repair_audit_path else None
    audits: dict[str, list[dict]] = {}
    audit_lock = threading.Lock()

    cache_enabled = suite.cache.enabled if use_cache is None else use_cache
    cache_dir = suite.resolve_path(suite.cache.dir) if cache_enabled else None

    needs_defer = any(
        (s.chunk_size and s.path) or (s.prefer_sql and s.connection and s.table)
        for s in suite.sources
    )
    layers = list(iter_layers(suite, defer_load=needs_defer))

    if workers and workers > 1 and len(layers) > 1:
        report.layers = _run_layers_parallel(
            suite, layers, fix_dir, progress, workers, collect_failures,
            audits, audit_lock, allow_postgis_write, cache_dir,
        )
    else:
        for layer in layers:
            emit(progress, ProgressEvent(layer=layer.name, phase="layer",
                                         message=f"checking {layer.name}"))
            logger.debug("running checks for layer %s (%s)", layer.name, layer.source)
            report.layers.append(
                _run_layer(
                    suite, layer, fix_dir, collect_failures, audits, audit_lock,
                    allow_postgis_write, progress, cache_dir,
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
    cache_dir: Path | None,
) -> list[LayerReport]:
    from concurrent.futures import ThreadPoolExecutor

    results: dict[int, LayerReport] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_layer, suite, layer, fix_dir, collect_failures, audits,
                audit_lock, allow_postgis_write, progress, cache_dir,
            ): i
            for i, layer in enumerate(layers)
        }
        for future in futures:
            idx = futures[future]
            emit(progress, ProgressEvent(layer=layers[idx].name, phase="layer"))
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

    # Fingerprint cache: skip work when source + config unchanged and prior PASS.
    # PostGIS layers never use the fingerprint cache (no reliable content mtime).
    cache_key = None
    if cache_dir is not None:
        cache_key = _cache_key_for(layer, cfg)
        if cache_key is not None:
            entry = geoqa_cache.read_entry(cache_dir, cache_key)
            if entry is not None and geoqa_cache.layer_passed(entry):
                emit(progress, ProgressEvent(
                    layer=layer.name, phase="cache", message="cache hit (skip)",
                ))
                lr.n_features = int(entry.get("n_features") or 0)
                lr.crs = entry.get("crs")
                lr.geometry_type = entry.get("geometry_type")
                lr.results.append(
                    CheckResult(
                        check="cache", layer=layer.name, source=layer.source,
                        status=Status.SKIP, severity=Severity.INFO,
                        message="Skipped: fingerprint cache hit (prior PASS).",
                    )
                )
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
        emit(progress, ProgressEvent(
            layer=layer.name, phase="check", check="geometry", message="SQL pushdown",
        ))
        sql_results, n_rows = sql_geometry_checks(layer.source_spec, layer.name, cfg.geometry)
        lr.n_features = n_rows
        lr.results.extend(sql_results)
        _maybe_write_cache(cache_dir, cache_key, lr)
        return lr

    # Deferred PostGIS stubs (prefer_sql) still need a GeoDataFrame when other
    # checks are enabled — materialize now instead of failing with "unknown error".
    if (
        layer.gdf is None
        and not layer.error
        and layer.connection
        and layer.source_spec is not None
    ):
        emit(progress, ProgressEvent(
            layer=layer.name, phase="load", message="materialize PostGIS layer",
        ))
        loaded = _load_postgis(layer.source_spec)
        layer.gdf = loaded.gdf
        layer.error = loaded.error

    # Chunked path for large files.
    if layer.chunk_size and layer.gdf is None and not layer.error:
        return _run_layer_chunked(
            suite, layer, cfg, lr, progress, collect_failures, cache_dir, cache_key,
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
    lr.geometry_type = _dominant_geom_type(gdf)

    # Prefer SQL geometry when configured, then skip in-Python geometry check.
    skip_geometry = False
    if layer.prefer_sql and layer.connection and layer.table and layer.source_spec is not None:
        emit(progress, ProgressEvent(
            layer=layer.name, phase="check", check="geometry", message="SQL pushdown",
        ))
        sql_results, _n = sql_geometry_checks(layer.source_spec, layer.name, cfg.geometry)
        if sql_results and all(r.status != Status.ERROR for r in sql_results):
            lr.results.extend(sql_results)
            skip_geometry = True

    for spec in get_registry().specs():
        if skip_geometry and spec.name == "geometry":
            continue
        sub_cfg = check_config_for(cfg, spec.name)
        if sub_cfg is None:
            continue
        emit(progress, ProgressEvent(
            layer=layer.name, phase="check", check=spec.name,
        ))
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
            status = Status.PASS if pg.get("ok", False) else Status.ERROR
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

    _maybe_write_cache(cache_dir, cache_key, lr)
    return lr


def _run_layer_chunked(
    suite: Suite,
    layer: Layer,
    cfg,
    lr: LayerReport,
    progress: ProgressCb,
    collect_failures: bool,
    cache_dir: Path | None,
    cache_key: str | None,
) -> LayerReport:
    """Run chunk-safe checks in batches; skip global-context checks with WARN."""
    registry = {s.name: s for s in get_registry().specs()}
    merged: dict[str, list[CheckResult]] = {}
    n_features = 0
    crs = None
    geom_type = None
    first_gdf = None

    chunks = list(iter_file_chunks(layer))
    n_chunks = max(len(chunks), 1)
    for i, chunk in enumerate(chunks):
        n_features += len(chunk)
        if crs is None and chunk.crs is not None:
            crs = str(chunk.crs)
        if geom_type is None:
            geom_type = _dominant_geom_type(chunk)
        if first_gdf is None:
            first_gdf = chunk
        frac = (i + 1) / n_chunks
        emit(progress, ProgressEvent(
            layer=layer.name, phase="chunk", fraction=frac,
            message=f"chunk {i + 1}/{n_chunks}",
        ))
        for name in CHUNK_SAFE_CHECKS:
            spec = registry.get(name)
            if spec is None:
                continue
            sub_cfg = check_config_for(cfg, name)
            if sub_cfg is None or not getattr(sub_cfg, "enabled", True):
                continue
            # Attributes with unique need global context — skip unique in chunk mode.
            if name == "attributes" and getattr(sub_cfg, "unique", None):
                sub_cfg = sub_cfg.model_copy(update={"unique": []})
            emit(progress, ProgressEvent(
                layer=layer.name, phase="check", check=name, fraction=frac,
            ))
            chunk_results = _timed(
                spec.runner, name, chunk, layer.name, layer.source, sub_cfg
            )
            merged.setdefault(name, []).extend(chunk_results)

    lr.n_features = n_features
    lr.crs = crs
    lr.geometry_type = geom_type
    lr.results.extend(_merge_chunk_results(merged))

    for name in CHUNK_GLOBAL_CHECKS:
        spec = registry.get(name)
        if spec is None:
            continue
        sub_cfg = check_config_for(cfg, name)
        if sub_cfg is None or not getattr(sub_cfg, "enabled", False):
            continue
        # Topology enabled with no flags → skip silently.
        if name == "topology" and not any(
            getattr(sub_cfg, f, False)
            for f in (
                "no_overlaps", "no_gaps", "no_coverage_gaps", "no_dangles",
                "coincident_edges", "coverage_area_ratio",
            )
        ):
            continue
        if name == "duplicates" and not (
            getattr(sub_cfg, "exact", False)
            or getattr(getattr(sub_cfg, "fuzzy", None), "enabled", False)
        ):
            continue
        lr.results.append(
            CheckResult(
                check=name, layer=layer.name, source=layer.source,
                status=Status.SKIP, severity=Severity.WARN,
                message=(
                    f"{name} skipped under chunk_size={layer.chunk_size}: needs full "
                    "layer context (disable chunk_size or run a global pass)."
                ),
            )
        )

    if collect_failures and first_gdf is not None:
        # Best-effort: only first chunk geometries available without full load.
        lr.failures = _collect_failures(first_gdf, lr.results)

    _maybe_write_cache(cache_dir, cache_key, lr)
    return lr


def _merge_chunk_results(merged: dict[str, list[CheckResult]]) -> list[CheckResult]:
    """Combine per-chunk check results into one result per check name."""
    out: list[CheckResult] = []
    for _group, results in merged.items():
        by_check: dict[str, list[CheckResult]] = {}
        for r in results:
            by_check.setdefault(r.check, []).append(r)
        for check, parts in by_check.items():
            n_total = sum(p.n_total for p in parts)
            n_failed = sum(p.n_failed for p in parts)
            issues = []
            for p in parts:
                issues.extend(p.issues)
                if len(issues) >= 200:
                    issues = issues[:200]
                    break
            # Worst status wins (ERROR > FAIL > WARN > PASS > SKIP).
            rank = {
                Status.ERROR: 5, Status.FAIL: 4, Status.WARN: 3,
                Status.PASS: 1, Status.SKIP: 0,
            }
            status = max((p.status for p in parts), key=lambda s: rank.get(s, 0))
            severity = parts[0].severity
            if n_failed and status in (Status.PASS, Status.SKIP):
                status = Status.FAIL if severity == Severity.ERROR else Status.WARN
            msg = parts[-1].message
            if len(parts) > 1:
                msg = f"{msg} (merged from {len(parts)} chunk(s))"
            out.append(
                CheckResult(
                    check=check, layer=parts[0].layer, source=parts[0].source,
                    status=status, severity=severity, message=msg,
                    n_total=n_total, n_failed=n_failed, issues=issues,
                    duration_s=sum(p.duration_s for p in parts),
                )
            )
    return out


def _cache_key_for(layer: Layer, cfg) -> str | None:
    """Build a cache key, or ``None`` when this layer must not be cached.

    PostGIS / SQLAlchemy sources are excluded: ``file_stat`` is always ``None``
    for connection URLs, so a PASS would never invalidate after data changes.
    """
    if layer.connection:
        return None
    fragment = cfg.model_dump(mode="json") if hasattr(cfg, "model_dump") else {}
    return geoqa_cache.fingerprint(
        source=layer.source,
        file_stat=geoqa_cache.file_stat(layer.source),
        config_fragment=fragment,
        layer_name=layer.name,
        sublayer=layer.sublayer,
        table=layer.table,
        query=layer.query,
    )


def _maybe_write_cache(cache_dir: Path | None, cache_key: str | None, lr: LayerReport) -> None:
    if cache_dir is None or cache_key is None:
        return
    ok = all(r.status in (Status.PASS, Status.SKIP) for r in lr.results) and bool(lr.results)
    # Do not cache pure cache-skip markers as a new PASS.
    if len(lr.results) == 1 and lr.results[0].check == "cache":
        return
    geoqa_cache.write_entry(
        cache_dir,
        cache_key,
        {
            "ok": ok,
            "n_features": lr.n_features,
            "crs": lr.crs,
            "geometry_type": lr.geometry_type,
            "layer": lr.layer,
            "source": lr.source,
        },
    )


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
