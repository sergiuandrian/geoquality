"""Topology checks: overlaps, gaps and dangling line endpoints.

These are pure-Python/Shapely implementations of the things people usually reach
for PostGIS or the QGIS Topology Checker to do. They are intentionally simple
and best treated as warnings rather than hard gates.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from geoqa.checks.base import result, status_for, to_metric
from geoqa.config import TopologyCheck
from geoqa.result import CheckResult, Issue, Status

CHECK = "topology"
_SNAP = 6  # coordinate rounding (decimal places) when no explicit tolerance is set


def run(gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: TopologyCheck) -> list[CheckResult]:
    if not cfg.enabled or gdf.geometry.name not in gdf.columns:
        return []

    # Topology relies on areas/distances, which are only meaningful in a metric
    # CRS. Reproject once up front and tag every result with any reprojection note.
    metric_gdf, note = to_metric(gdf)
    valid = metric_gdf[metric_gdf.geometry.notna() & ~metric_gdf.geometry.is_empty]
    types = valid.geometry.geom_type

    results: list[CheckResult] = []
    if cfg.no_overlaps:
        results.append(_overlaps(valid, types, layer, source, cfg))
    if cfg.no_gaps:
        results.append(_gaps(valid, types, layer, source, cfg))
    if cfg.no_dangles:
        results.append(_dangles(valid, types, layer, source, cfg))

    if note:
        for r in results:
            r.message = f"{r.message} [{note}]"
    return results


def _safe_valid(geom):
    """Repair geometry so GEOS overlay ops don't raise on invalid input."""
    try:
        return geom if geom.is_valid else make_valid(geom)
    except Exception:  # noqa: BLE001
        return geom


def _polygons(valid, types):
    polys = valid[types.isin(["Polygon", "MultiPolygon"])].copy()
    if not polys.empty:
        col = polys.geometry.name
        polys[col] = polys.geometry.apply(_safe_valid)
    return polys


def _overlaps(valid, types, layer, source, cfg) -> CheckResult:
    polys = _polygons(valid, types)
    if polys.empty:
        return result(CHECK + ".no_overlaps", layer, source, Status.SKIP, "No polygon features.")

    geom_col = polys.geometry.name
    work = polys[[geom_col]]
    try:
        joined = work.sjoin(work, predicate="intersects", how="inner")
    except Exception as exc:  # noqa: BLE001
        return result(CHECK + ".no_overlaps", layer, source, Status.ERROR, f"sjoin failed: {exc}",
                      severity=cfg.severity)

    right_col = "index_right" if "index_right" in joined.columns else joined.columns[-1]
    geoms = work.geometry
    flagged: set = set()
    issues: list[Issue] = []
    for left_idx, row in joined.iterrows():
        right_idx = row[right_col]
        if not (left_idx < right_idx):
            continue
        a, b = geoms.loc[left_idx], geoms.loc[right_idx]
        try:
            inter = a.intersection(b)
        except Exception:  # noqa: BLE001
            continue
        area = getattr(inter, "area", 0.0)
        tol = max(cfg.min_area, 1e-9 * max(a.area, b.area, 1.0))
        if area > tol:
            flagged.update((left_idx, right_idx))
            if len(issues) < 200:
                issues.append(
                    Issue(message=f"overlap area={area:.6g}", feature_id=left_idx,
                          detail={"other": right_idx, "area": float(area)})
                )

    n = len(flagged)
    return result(
        CHECK + ".no_overlaps", layer, source, status_for(n, cfg.severity),
        "No overlapping polygons." if n == 0 else f"{n} polygon(s) overlap one another.",
        severity=cfg.severity, n_total=len(polys), n_failed=n, issues=issues,
    )


def _gaps(valid, types, layer, source, cfg) -> CheckResult:
    polys = _polygons(valid, types)
    if polys.empty:
        return result(CHECK + ".no_gaps", layer, source, Status.SKIP, "No polygon features.")

    merged = unary_union(list(polys.geometry))
    holes: list[Polygon] = []

    def _collect(geom):
        if geom.geom_type == "Polygon":
            holes.extend(Polygon(r) for r in geom.interiors)
        elif geom.geom_type in ("MultiPolygon", "GeometryCollection"):
            for g in geom.geoms:
                _collect(g)

    _collect(merged)
    holes = [h for h in holes if h.area > cfg.min_area]
    n = len(holes)
    issues = [
        Issue(message=f"gap (interior hole) area={h.area:.6g}", detail={"area": float(h.area)})
        for h in holes[:200]
    ]
    return result(
        CHECK + ".no_gaps", layer, source, status_for(n, cfg.severity),
        "No gaps between polygons." if n == 0 else f"{n} gap(s) found between dissolved polygons.",
        severity=cfg.severity, n_total=len(polys), n_failed=n, issues=issues,
    )


def _iter_lines(geom):
    if geom.geom_type == "LineString":
        yield geom
    elif geom.geom_type == "MultiLineString":
        yield from geom.geoms


def _dangles(valid, types, layer, source, cfg) -> CheckResult:
    lines = valid[types.isin(["LineString", "MultiLineString"])]
    if lines.empty:
        return result(CHECK + ".no_dangles", layer, source, Status.SKIP, "No line features.")

    tol = cfg.snap_tolerance

    def _key(pt: tuple[float, float]) -> tuple[float, float]:
        # With a tolerance, snap endpoints onto a grid of that size so coincident
        # points within ``tol`` collapse together; otherwise round to _SNAP places.
        if tol > 0:
            return (round(pt[0] / tol), round(pt[1] / tol))
        return (round(pt[0], _SNAP), round(pt[1], _SNAP))

    counts: Counter = Counter()
    where: dict = defaultdict(list)
    repr_pt: dict = {}
    for idx, geom in lines.geometry.items():
        for ls in _iter_lines(geom):
            coords = list(ls.coords)
            if len(coords) < 2:
                continue
            for pt in (coords[0], coords[-1]):
                key = _key(pt)
                counts[key] += 1
                where[key].append(idx)
                repr_pt.setdefault(key, (float(pt[0]), float(pt[1])))

    dangles = [k for k, c in counts.items() if c == 1]
    issues: list[Issue] = []
    for key in dangles[:200]:
        x, y = repr_pt[key]
        issues.append(
            Issue(message=f"dangling endpoint at ({x:.3f}, {y:.3f})", feature_id=where[key][0],
                  detail={"x": x, "y": y})
        )
    n = len(dangles)
    return result(
        CHECK + ".no_dangles", layer, source, status_for(n, cfg.severity),
        "No dangling endpoints." if n == 0 else f"{n} dangling line endpoint(s) detected.",
        severity=cfg.severity, n_total=len(lines), n_failed=n, issues=issues,
    )
