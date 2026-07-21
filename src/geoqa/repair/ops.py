"""Geometry repair operations and audit trail."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import geopandas as gpd
from shapely import set_precision
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

from geoqa.checks.base import to_metric


@dataclass
class RepairAction:
    """One recorded change applied by the repair pipeline."""

    op: str
    row_index: Any = None
    feature_id: Any = None
    note: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _geom_hash(geom: BaseGeometry | None) -> str | None:
    if geom is None or geom.is_empty:
        return None
    try:
        return geom.wkb_hex[:32]
    except Exception:  # noqa: BLE001
        return None


def op_make_valid(gdf: gpd.GeoDataFrame, audit: list[RepairAction]) -> gpd.GeoDataFrame:
    """Repair invalid geometries with ``shapely.make_valid``."""
    col = gdf.geometry.name
    out = gdf.copy()
    for idx, geom in out.geometry.items():
        if geom is None or geom.is_empty:
            continue
        try:
            if geom.is_valid:
                continue
            fixed = make_valid(geom)
        except Exception:  # noqa: BLE001
            continue
        if fixed is not None and not fixed.equals(geom):
            out.at[idx, col] = fixed
            audit.append(
                RepairAction(
                    op="make_valid",
                    row_index=idx,
                    feature_id=idx,
                    note="repaired invalid geometry",
                    detail={"before": _geom_hash(geom), "after": _geom_hash(fixed)},
                )
            )
    return out


def op_snap(
    gdf: gpd.GeoDataFrame, tolerance_m: float, audit: list[RepairAction]
) -> gpd.GeoDataFrame:
    """Snap vertices onto a metric grid of size ``tolerance_m`` metres."""
    if tolerance_m <= 0 or gdf.empty:
        return gdf
    metric, _note = to_metric(gdf)
    col = metric.geometry.name
    out = metric.copy()
    changed = 0
    for idx, geom in out.geometry.items():
        if geom is None or geom.is_empty:
            continue
        try:
            snapped = set_precision(geom, grid_size=tolerance_m)
        except Exception:  # noqa: BLE001
            continue
        if snapped is not None and not snapped.equals(geom):
            out.at[idx, col] = snapped
            changed += 1
            audit.append(
                RepairAction(
                    op="snap",
                    row_index=idx,
                    feature_id=idx,
                    note=f"snapped to {tolerance_m} m grid",
                    detail={"grid_size": tolerance_m},
                )
            )
    # Reproject back to the original CRS when we left it.
    if gdf.crs is not None and out.crs != gdf.crs:
        out = out.to_crs(gdf.crs)
    if changed == 0 and not audit:
        pass
    return out


def _drop_small_parts(geom: BaseGeometry, min_area: float) -> BaseGeometry | None:
    """Remove polygon parts smaller than ``min_area`` (metric units)."""
    if geom is None or geom.is_empty:
        return geom
    gtype = geom.geom_type
    if gtype == "Polygon":
        return geom if geom.area >= min_area else None
    if gtype == "MultiPolygon":
        keep = [g for g in geom.geoms if g.area >= min_area]
        if not keep:
            return None
        if len(keep) == 1:
            return keep[0]
        from shapely.geometry import MultiPolygon

        return MultiPolygon(keep)
    if gtype == "GeometryCollection":
        keep = []
        for g in geom.geoms:
            cleaned = _drop_small_parts(g, min_area)
            if cleaned is not None and not cleaned.is_empty:
                keep.append(cleaned)
        if not keep:
            return None
        if len(keep) == 1:
            return keep[0]
        from shapely.geometry import GeometryCollection

        return GeometryCollection(keep)
    return geom


def op_drop_slivers(
    gdf: gpd.GeoDataFrame, min_area_m2: float, audit: list[RepairAction]
) -> gpd.GeoDataFrame:
    """Drop polygon parts with area below ``min_area_m2`` (metric CRS)."""
    if min_area_m2 <= 0 or gdf.empty:
        return gdf
    metric, _note = to_metric(gdf)
    col = metric.geometry.name
    out = metric.copy()
    for idx, geom in out.geometry.items():
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type not in ("Polygon", "MultiPolygon", "GeometryCollection"):
            continue
        cleaned = _drop_small_parts(geom, min_area_m2)
        if cleaned is None:
            # Keep an empty geometry of the same type rather than dropping the row.
            cleaned = geom.__class__()
        if not cleaned.equals(geom):
            out.at[idx, col] = cleaned
            audit.append(
                RepairAction(
                    op="drop_slivers",
                    row_index=idx,
                    feature_id=idx,
                    note=f"removed parts smaller than {min_area_m2} m²",
                    detail={"min_area": min_area_m2},
                )
            )
    if gdf.crs is not None and out.crs != gdf.crs:
        out = out.to_crs(gdf.crs)
    return out


def op_dissolve_duplicates(
    gdf: gpd.GeoDataFrame, audit: list[RepairAction]
) -> gpd.GeoDataFrame:
    """Keep the first feature in each exact-duplicate geometry group (WKB)."""
    if gdf.empty:
        return gdf

    def _key(g: BaseGeometry | None) -> bytes | None:
        if g is None or g.is_empty:
            return None
        try:
            return g.normalize().wkb
        except Exception:  # noqa: BLE001
            return g.wkb

    keys = gdf.geometry.apply(_key)
    dup_mask = keys.duplicated(keep="first") & keys.notna()
    if not dup_mask.any():
        return gdf
    for idx in gdf.index[dup_mask]:
        audit.append(
            RepairAction(
                op="dissolve_duplicates",
                row_index=idx,
                feature_id=idx,
                note="dropped exact duplicate geometry (kept first)",
            )
        )
    return gdf.loc[~dup_mask].copy()
