"""Repair pipeline phase and write-back gates."""

from __future__ import annotations

import threading
from pathlib import Path

import geopandas as gpd

from geoqa.datasource import Layer
from geoqa.engine.dispatch import run_checks
from geoqa.engine.util import write_fixed
from geoqa.registry import get_registry
from geoqa.result import CheckResult, LayerReport, Severity, Status


def apply_repair(
    gdf: gpd.GeoDataFrame,
    layer: Layer,
    cfg,
    lr: LayerReport,
    *,
    fix_dir: Path | None,
    audits: dict[str, list[dict]] | None,
    audit_lock: threading.Lock | None,
    allow_postgis_write: bool,
) -> gpd.GeoDataFrame:
    """Run effective repair + optional file/PostGIS write; mutate ``lr``."""
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
                write_fixed(gdf, layer, fix_dir, lr)
            else:
                lr.results.append(
                    CheckResult(
                        check="geometry.repair.output", layer=layer.name,
                        source=layer.source,
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
                    check="geometry.repair.postgis", layer=layer.name,
                    source=layer.source,
                    status=status,
                    severity=Severity.INFO if status == Status.PASS else Severity.ERROR,
                    message=pg["message"],
                )
            )
        if repair_cfg.then_recheck:
            _then_recheck(gdf, layer, cfg, lr)
        return gdf

    # Legacy path: geometry.fix mutated gdf in-place during the check.
    fixed_total = sum(r.fixed for r in lr.results)
    if fixed_total and fix_dir is not None:
        write_fixed(gdf, layer, fix_dir, lr)
    return gdf


def _then_recheck(gdf: gpd.GeoDataFrame, layer: Layer, cfg, lr: LayerReport) -> None:
    """Re-run geometry (+ topology if enabled) on the repaired layer."""
    keep = {"geometry", "topology"}
    skip = {spec.name for spec in get_registry().specs() if spec.name not in keep}
    for r in run_checks(gdf, layer.name, layer.source, cfg, skip=skip):
        r.check = f"{r.check}.after_repair"
        lr.results.append(r)