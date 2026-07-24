"""Suite-level orchestration: workers, audit flush, layer fan-out."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from geoqa.config import Suite
from geoqa.datasource import Layer, iter_layers
from geoqa.engine.layer import run_layer
from geoqa.engine.util import now
from geoqa.progress import ProgressCb, ProgressEvent, emit
from geoqa.result import LayerReport, Report

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
                run_layer(
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

    report.finished_at = now()
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
                run_layer, suite, layer, fix_dir, collect_failures, audits,
                audit_lock, allow_postgis_write, progress, cache_dir,
            ): i
            for i, layer in enumerate(layers)
        }
        for future in futures:
            idx = futures[future]
            emit(progress, ProgressEvent(layer=layers[idx].name, phase="layer"))
            results[idx] = future.result()
    return [results[i] for i in range(len(layers))]
