"""Registry check dispatch with timing and crash isolation."""

from __future__ import annotations

import logging
import time

import geopandas as gpd

from geoqa.config import check_config_for
from geoqa.progress import ProgressCb, ProgressEvent, emit
from geoqa.registry import get_registry
from geoqa.result import CheckResult, Severity, Status

logger = logging.getLogger("geoqa")


def run_checks(
    gdf: gpd.GeoDataFrame,
    layer_name: str,
    source: str,
    cfg,
    *,
    skip: set[str] | None = None,
    progress: ProgressCb = None,
) -> list[CheckResult]:
    """Run every registered check against ``gdf`` (except names in ``skip``)."""
    skip = skip or set()
    out: list[CheckResult] = []
    for spec in get_registry().specs():
        if spec.name in skip:
            continue
        sub_cfg = check_config_for(cfg, spec.name)
        if sub_cfg is None:
            continue
        emit(progress, ProgressEvent(
            layer=layer_name, phase="check", check=spec.name,
        ))
        out.extend(timed(spec.runner, spec.name, gdf, layer_name, source, sub_cfg))
    return out


def timed(fn, check_name, gdf, layer_name, source, sub_cfg) -> list[CheckResult]:
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
