"""Fingerprint cache hit / miss / store for a layer run."""

from __future__ import annotations

from pathlib import Path

from geoqa import cache as geoqa_cache
from geoqa.datasource import Layer
from geoqa.progress import ProgressCb, ProgressEvent, emit
from geoqa.result import CheckResult, LayerReport, Severity, Status


def cache_key_for(layer: Layer, cfg) -> str | None:
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


def try_cache_hit(
    layer: Layer,
    cfg,
    lr: LayerReport,
    cache_dir: Path | None,
    progress: ProgressCb,
) -> tuple[str | None, bool]:
    """Return ``(cache_key, hit)``. On hit, ``lr`` is filled with a SKIP result."""
    if cache_dir is None:
        return None, False
    key = cache_key_for(layer, cfg)
    if key is None:
        return None, False
    entry = geoqa_cache.read_entry(cache_dir, key)
    if entry is None or not geoqa_cache.layer_passed(entry):
        return key, False
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
    return key, True


def store_if_clean(
    cache_dir: Path | None, cache_key: str | None, lr: LayerReport
) -> None:
    if cache_dir is None or cache_key is None:
        return
    ok = all(r.status in (Status.PASS, Status.SKIP) for r in lr.results) and bool(lr.results)
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
