"""Orchestrate configured geometry repair ops and produce an audit trail."""

from __future__ import annotations

import geopandas as gpd

from geoqa.config import GeometryCheck, RepairConfig
from geoqa.repair.ops import (
    RepairAction,
    op_dissolve_duplicates,
    op_drop_slivers,
    op_make_valid,
    op_snap,
)


def effective_repair(cfg: GeometryCheck) -> RepairConfig | None:
    """Return the repair config to run, or ``None`` when nothing should repair.

    - ``geometry.repair.enabled: true`` → use the repair block as-is.
    - Legacy ``geometry.fix: true`` alone → synthesize make_valid + file write.
    """
    if cfg.repair.enabled:
        return cfg.repair
    if cfg.fix:
        return RepairConfig(enabled=True, make_valid=True, write_mode="file")
    return None


def run_repair(
    gdf: gpd.GeoDataFrame, repair: RepairConfig
) -> tuple[gpd.GeoDataFrame, list[RepairAction]]:
    """Apply repair ops in order; return the repaired frame and audit actions.

    Does not mutate ``gdf`` (works on a copy). Order: make_valid → snap →
    drop_slivers → dissolve_duplicates.
    """
    audit: list[RepairAction] = []
    out = gdf.copy()
    if repair.make_valid:
        out = op_make_valid(out, audit)
    if repair.snap_tolerance > 0:
        out = op_snap(out, repair.snap_tolerance, audit)
    if repair.drop_slivers_area > 0:
        out = op_drop_slivers(out, repair.drop_slivers_area, audit)
    if repair.dissolve_duplicates:
        out = op_dissolve_duplicates(out, audit)
    return out, audit
