"""Topology checks: overlaps, gaps, coverage, dangles and coincident edges.

These are pure-Python/Shapely implementations of common coverage and network
checks. They are useful CI gates, **not** a cadastral-certified topology engine.
See ``docs/checks.md`` for heuristic vs coverage semantics.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid

from geoqa.checks.base import result, status_for, to_metric
from geoqa.config import TopologyCheck
from geoqa.result import CheckResult, Issue, Severity, Status

CHECK = "topology"
_SNAP = 6  # coordinate rounding (decimal places) when no explicit tolerance is set
_DEFAULT_BOUNDARY_EPS = 0.01  # metres when neither boundary nor snap tolerance is set


def run(gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: TopologyCheck) -> list[CheckResult]:
    if not cfg.enabled or gdf.geometry.name not in gdf.columns:
        return []

    # Topology relies on areas/distances, which are only meaningful in a metric
    # CRS. Reproject once up front and tag every result with any reprojection note.
    metric_gdf, note = to_metric(gdf)
    valid = metric_gdf[metric_gdf.geometry.notna() & ~metric_gdf.geometry.is_empty]
    types = valid.geometry.geom_type
    to_wgs84 = _lonlat_transformer(metric_gdf.crs)
    source_crs = gdf.crs

    results: list[CheckResult] = []
    if cfg.no_overlaps:
        results.append(_overlaps(valid, types, layer, source, cfg))
    if cfg.coverage_area_ratio:
        results.append(_coverage_area_ratio(valid, types, layer, source, cfg))
    if cfg.no_gaps:
        results.append(_gaps(valid, types, layer, source, cfg))
    if cfg.no_coverage_gaps:
        results.append(_coverage_gaps(valid, types, layer, source, cfg, metric_gdf.crs, source_crs))
    if cfg.no_spillover:
        results.append(_spillover(valid, types, layer, source, cfg, metric_gdf.crs, source_crs))
    if cfg.no_dangles:
        results.append(
            _dangles(valid, types, layer, source, cfg, to_wgs84, metric_gdf.crs, source_crs)
        )
    if cfg.no_undershoots:
        results.append(
            _undershoots(valid, types, layer, source, cfg, to_wgs84)
        )
    if cfg.no_overshoots:
        results.append(
            _overshoots(valid, types, layer, source, cfg, to_wgs84)
        )
    if cfg.no_multipart_overlap:
        results.append(_multipart_overlap(valid, types, layer, source, cfg))
    if cfg.coincident_edges:
        results.append(_coincident_edges(valid, types, layer, source, cfg))

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


def _polygons(valid: gpd.GeoDataFrame, types) -> gpd.GeoDataFrame:
    polys = valid[types.isin(["Polygon", "MultiPolygon"])].copy()
    if not polys.empty:
        col = polys.geometry.name
        polys[col] = polys.geometry.apply(_safe_valid)
    return polys


def _boundary_eps(cfg: TopologyCheck) -> float:
    if cfg.boundary_tolerance > 0:
        return cfg.boundary_tolerance
    if cfg.snap_tolerance > 0:
        return cfg.snap_tolerance
    return _DEFAULT_BOUNDARY_EPS


def _lonlat_transformer(crs) -> Transformer | None:
    if crs is None:
        return None
    try:
        return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    except Exception:  # noqa: BLE001
        return None


def _to_lonlat(x: float, y: float, transformer: Transformer | None) -> tuple[float | None, float | None]:
    if transformer is None:
        return None, None
    try:
        lon, lat = transformer.transform(x, y)
        return float(lon), float(lat)
    except Exception:  # noqa: BLE001
        return None, None


def _resolve_aoi(cfg: TopologyCheck, fallback_bounds, target_crs, source_crs=None) -> Any | None:
    """Return an AOI polygon in ``target_crs``, or ``None`` if unavailable.

    ``aoi_bbox`` is interpreted in the **source layer CRS** (before metric
    reprojection). File AOIs are reprojected to ``target_crs`` when needed.
    """
    if cfg.aoi:
        path = Path(cfg.aoi)
        aoi_gdf = gpd.read_file(path)
        if aoi_gdf.empty:
            return None
        if aoi_gdf.crs is None:
            # Prefer source CRS (bbox/file coords match the layer), else metric.
            assume = source_crs if source_crs is not None else target_crs
            if assume is not None:
                aoi_gdf = aoi_gdf.set_crs(assume)
        if target_crs is not None and aoi_gdf.crs is not None and aoi_gdf.crs != target_crs:
            aoi_gdf = aoi_gdf.to_crs(target_crs)
        return _safe_valid(unary_union(list(aoi_gdf.geometry)))
    if cfg.aoi_bbox is not None:
        if len(cfg.aoi_bbox) != 4:
            return None
        minx, miny, maxx, maxy = cfg.aoi_bbox
        geom = box(minx, miny, maxx, maxy)
        if source_crs is not None and target_crs is not None and source_crs != target_crs:
            return gpd.GeoSeries([geom], crs=source_crs).to_crs(target_crs).iloc[0]
        return geom
    if fallback_bounds is None:
        return None
    minx, miny, maxx, maxy = fallback_bounds
    if not np.all(np.isfinite([minx, miny, maxx, maxy])):
        return None
    return box(minx, miny, maxx, maxy)


def _choose_overlap_algorithm(cfg: TopologyCheck, n: int) -> str:
    if cfg.algorithm == "auto":
        return "pairwise" if n < cfg.pairwise_threshold else "coverage"
    return cfg.algorithm


def _overlaps(valid, types, layer, source, cfg) -> CheckResult:
    polys = _polygons(valid, types)
    if polys.empty:
        return result(CHECK + ".no_overlaps", layer, source, Status.SKIP, "No polygon features.")

    algo = _choose_overlap_algorithm(cfg, len(polys))
    if algo == "coverage":
        return _overlaps_via_coverage(polys, layer, source, cfg)

    if cfg.tile_size and cfg.tile_size > 0 and len(polys) >= 2:
        return _overlaps_tiled(polys, layer, source, cfg)

    return _overlaps_pairwise(polys, layer, source, cfg)


def _tile_buffer(cfg: TopologyCheck) -> float:
    if cfg.snap_tolerance > 0:
        return cfg.snap_tolerance
    if cfg.boundary_tolerance > 0:
        return cfg.boundary_tolerance
    return 0.0


def _iter_fishnet_tiles(bounds, tile_size: float, buf: float):
    """Yield buffered tile boxes covering ``bounds``."""
    minx, miny, maxx, maxy = bounds
    if not np.all(np.isfinite([minx, miny, maxx, maxy])):
        return
    if maxx <= minx or maxy <= miny:
        yield box(minx - buf, miny - buf, maxx + buf, maxy + buf)
        return
    x = minx
    while x < maxx:
        y = miny
        x2 = min(x + tile_size, maxx)
        while y < maxy:
            y2 = min(y + tile_size, maxy)
            yield box(x - buf, y - buf, x2 + buf, y2 + buf)
            y += tile_size
        x += tile_size


def _overlaps_tiled(polys, layer, source, cfg) -> CheckResult:
    """Pairwise overlaps on fishnet tiles with an overlap buffer.

    Features near tile edges are evaluated in neighbouring buffered tiles so
    cross-boundary pairs are not missed when ``buffer >= gap``. Documented
    caveat: buffer of 0 can miss pairs that straddle tile edges.
    """
    buf = _tile_buffer(cfg)
    flagged: set = set()
    issues: list[Issue] = []
    pairs_budget = cfg.max_pairs
    tiles_run = 0
    try:
        for tile in _iter_fishnet_tiles(polys.total_bounds, cfg.tile_size, buf):
            tiles_run += 1
            try:
                hit = polys[polys.intersects(tile)]
            except Exception:  # noqa: BLE001
                continue
            if len(hit) < 2:
                continue
            # Temporarily lower max_pairs to remaining budget.
            sub_cfg = cfg.model_copy(update={"max_pairs": max(pairs_budget, 0)})
            if pairs_budget <= 0:
                break
            sub = _overlaps_pairwise(hit, layer, source, sub_cfg)
            pairs_budget = max(0, pairs_budget - max(1, sub.n_failed))
            for issue in sub.issues:
                other = issue.detail.get("other")
                key = (issue.row_index, other)
                if key in {(i.row_index, i.detail.get("other")) for i in issues}:
                    continue
                if len(issues) < 200:
                    detail = dict(issue.detail)
                    detail["tiled"] = True
                    issues.append(
                        Issue(
                            message=issue.message,
                            feature_id=issue.feature_id,
                            row_index=issue.row_index,
                            detail=detail,
                        )
                    )
                flagged.add(issue.row_index)
                if other is not None:
                    flagged.add(other)
            if sub.status == Status.ERROR:
                return sub
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".no_overlaps", layer, source, Status.ERROR,
            f"tiled overlaps failed: {exc}", severity=cfg.severity,
        )

    n = len(flagged)
    msg = (
        "No overlapping polygons." if n == 0
        else f"{n} polygon(s) overlap one another."
    )
    msg += f" (tiled: {tiles_run} tile(s), buffer={buf:g})"
    return result(
        CHECK + ".no_overlaps", layer, source, status_for(n, cfg.severity),
        msg, severity=cfg.severity, n_total=len(polys), n_failed=n, issues=issues,
    )


def _overlaps_via_coverage(polys, layer, source, cfg) -> CheckResult:
    """Fast self-overlap proxy: sum(areas) vs area(union)."""
    geoms = list(polys.geometry)
    try:
        sum_area = float(sum(g.area for g in geoms))
        union_area = float(unary_union(geoms).area)
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".no_overlaps", layer, source, Status.ERROR,
            f"coverage overlap metric failed: {exc}", severity=cfg.severity,
        )
    excess = max(0.0, sum_area - union_area)
    tol = max(cfg.min_area, 1e-9 * max(sum_area, 1.0))
    n_failed = 1 if excess > tol else 0
    issues = []
    if n_failed:
        ratio = (sum_area / union_area) if union_area > 0 else float("inf")
        issues.append(
            Issue(
                message=f"coverage excess area={excess:.6g} (sum/union={ratio:.6g})",
                detail={"excess_area": excess, "sum_area": sum_area, "union_area": union_area,
                        "ratio": ratio, "algorithm": "coverage"},
            )
        )
    return result(
        CHECK + ".no_overlaps", layer, source, status_for(n_failed, cfg.severity),
        "No overlapping polygons (coverage metric)." if n_failed == 0
        else f"Layer self-overlap excess area={excess:.6g} (coverage metric).",
        severity=cfg.severity, n_total=len(polys), n_failed=n_failed, issues=issues,
    )


def _overlaps_pairwise(polys, layer, source, cfg) -> CheckResult:
    """Vectorized pairwise overlaps via spatial index (no ``iterrows``)."""
    geoms = polys.geometry
    geom_arr = geoms.to_numpy()
    index_arr = geoms.index.to_numpy()

    try:
        # GeoPandas / Shapely 2: query returns (input_idx, tree_idx) into positional arrays.
        left_pos, right_pos = polys.sindex.query(polys.geometry, predicate="intersects")
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".no_overlaps", layer, source, Status.ERROR, f"sindex query failed: {exc}",
            severity=cfg.severity,
        )

    left_pos = np.asarray(left_pos, dtype=np.intp)
    right_pos = np.asarray(right_pos, dtype=np.intp)
    # Unique undirected pairs (positional).
    mask = left_pos < right_pos
    left_pos = left_pos[mask]
    right_pos = right_pos[mask]

    truncated = False
    if len(left_pos) > cfg.max_pairs:
        left_pos = left_pos[: cfg.max_pairs]
        right_pos = right_pos[: cfg.max_pairs]
        truncated = True

    if len(left_pos) == 0:
        msg = "No overlapping polygons."
        if truncated:
            msg += f" (pair evaluation capped at max_pairs={cfg.max_pairs})"
        return result(
            CHECK + ".no_overlaps", layer, source, Status.PASS, msg,
            severity=cfg.severity, n_total=len(polys), n_failed=0,
        )

    a = geom_arr[left_pos]
    b = geom_arr[right_pos]
    try:
        inter = shapely.intersection(a, b)
        areas = shapely.area(inter)
        a_areas = shapely.area(a)
        b_areas = shapely.area(b)
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".no_overlaps", layer, source, Status.ERROR,
            f"intersection failed: {exc}", severity=cfg.severity,
        )

    tols = np.maximum(cfg.min_area, 1e-9 * np.maximum(np.maximum(a_areas, b_areas), 1.0))
    hit = areas > tols
    hit_left = index_arr[left_pos[hit]]
    hit_right = index_arr[right_pos[hit]]
    hit_areas = areas[hit]

    flagged: set = set(hit_left.tolist()) | set(hit_right.tolist())
    issues: list[Issue] = []
    for i in range(min(len(hit_left), 200)):
        area = float(hit_areas[i])
        left_idx = hit_left[i]
        right_idx = hit_right[i]
        issues.append(
            Issue(
                message=f"overlap area={area:.6g}",
                feature_id=left_idx,
                row_index=left_idx,
                detail={"other": right_idx, "area": area, "algorithm": "pairwise"},
            )
        )

    n = len(flagged)
    msg = "No overlapping polygons." if n == 0 else f"{n} polygon(s) overlap one another."
    if truncated:
        msg += f" (pair evaluation capped at max_pairs={cfg.max_pairs})"
    if truncated and n == 0:
        # Incomplete scan with no hits — do not report a confident PASS.
        return result(
            CHECK + ".no_overlaps", layer, source, Status.WARN, msg,
            severity=Severity.WARN, n_total=len(polys), n_failed=0, issues=issues,
        )
    return result(
        CHECK + ".no_overlaps", layer, source, status_for(n, cfg.severity),
        msg, severity=cfg.severity, n_total=len(polys), n_failed=n, issues=issues,
    )


def _coverage_area_ratio(valid, types, layer, source, cfg) -> CheckResult:
    polys = _polygons(valid, types)
    if polys.empty:
        return result(
            CHECK + ".coverage_area_ratio", layer, source, Status.SKIP, "No polygon features."
        )
    geoms = list(polys.geometry)
    try:
        sum_area = float(sum(g.area for g in geoms))
        union_area = float(unary_union(geoms).area)
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".coverage_area_ratio", layer, source, Status.ERROR,
            f"coverage ratio failed: {exc}", severity=cfg.severity,
        )
    ratio = (sum_area / union_area) if union_area > 0 else float("inf")
    excess = max(0.0, sum_area - union_area)
    tol = max(cfg.min_area, 1e-9 * max(sum_area, 1.0))
    n_failed = 1 if excess > tol else 0
    issues = [
        Issue(
            message=f"sum/union={ratio:.6g}, excess_area={excess:.6g}",
            detail={"ratio": ratio, "sum_area": sum_area, "union_area": union_area,
                    "excess_area": excess},
        )
    ] if n_failed else []
    return result(
        CHECK + ".coverage_area_ratio", layer, source, status_for(n_failed, cfg.severity),
        f"Coverage area ratio={ratio:.6g}." if n_failed == 0
        else f"Coverage self-overlap ratio={ratio:.6g} (excess={excess:.6g}).",
        severity=cfg.severity, n_total=len(polys), n_failed=n_failed, issues=issues,
    )


def _gaps(valid, types, layer, source, cfg) -> CheckResult:
    """Heuristic: interior rings of the dissolved union (not full coverage gaps)."""
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
        "No interior holes in dissolved polygons (heuristic; prefer no_coverage_gaps + AOI)."
        if n == 0
        else (
            f"{n} interior hole(s) in dissolved polygons "
            "(heuristic; prefer no_coverage_gaps + AOI)."
        ),
        severity=cfg.severity, n_total=len(polys), n_failed=n, issues=issues,
    )


def _iter_gap_polygons(diff) -> list[Polygon]:
    """Explode a difference geometry into polygon parts."""
    out: list[Polygon] = []
    if diff is None or diff.is_empty:
        return out
    if diff.geom_type == "Polygon":
        out.append(diff)
    elif diff.geom_type == "MultiPolygon":
        out.extend(list(diff.geoms))
    elif diff.geom_type == "GeometryCollection":
        for g in diff.geoms:
            out.extend(_iter_gap_polygons(g))
    return out


def _coverage_gaps(valid, types, layer, source, cfg, metric_crs, source_crs) -> CheckResult:
    """AOI (or total_bounds) minus unary_union — true coverage gaps vs dissolve holes."""
    polys = _polygons(valid, types)
    if polys.empty:
        return result(
            CHECK + ".no_coverage_gaps", layer, source, Status.SKIP, "No polygon features."
        )

    used_fallback_bounds = cfg.aoi is None and cfg.aoi_bbox is None
    aoi = _resolve_aoi(cfg, polys.total_bounds, metric_crs, source_crs)
    if aoi is None or aoi.is_empty:
        return result(
            CHECK + ".no_coverage_gaps", layer, source, Status.ERROR,
            "Could not resolve AOI for coverage gaps.", severity=cfg.severity,
        )

    try:
        coverage = unary_union(list(polys.geometry))
        diff = aoi.difference(coverage)
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".no_coverage_gaps", layer, source, Status.ERROR,
            f"coverage gap overlay failed: {exc}", severity=cfg.severity,
        )

    parts = [p for p in _iter_gap_polygons(diff) if p.area > cfg.min_area]
    n = len(parts)
    issues = [
        Issue(
            message=f"coverage gap area={p.area:.6g}",
            detail={"area": float(p.area), "aoi_fallback_bounds": used_fallback_bounds},
        )
        for p in parts[:200]
    ]
    if used_fallback_bounds:
        # total_bounds is not a real AOI — never claim a confident PASS.
        caveat = (
            " AOI was layer total_bounds (not an explicit aoi/aoi_bbox): "
            "sparse/coastal layers often false-positive; set topology.aoi or aoi_bbox."
        )
        if n == 0:
            return result(
                CHECK + ".no_coverage_gaps", layer, source, Status.WARN,
                "No coverage gaps vs total_bounds (inconclusive without AOI)." + caveat,
                severity=Severity.WARN, n_total=len(polys), n_failed=0, issues=issues,
            )
        return result(
            CHECK + ".no_coverage_gaps", layer, source, status_for(n, cfg.severity),
            f"{n} coverage gap(s) vs total_bounds." + caveat,
            severity=cfg.severity, n_total=len(polys), n_failed=n, issues=issues,
        )
    return result(
        CHECK + ".no_coverage_gaps", layer, source, status_for(n, cfg.severity),
        "No coverage gaps vs AOI." if n == 0 else f"{n} coverage gap(s) vs AOI.",
        severity=cfg.severity, n_total=len(polys), n_failed=n, issues=issues,
    )


def _spillover(valid, types, layer, source, cfg, metric_crs, source_crs) -> CheckResult:
    """Features extending outside an explicit AOI (complement of coverage gaps)."""
    geoms = valid[types.isin(["Polygon", "MultiPolygon", "LineString", "MultiLineString"])]
    if geoms.empty:
        return result(
            CHECK + ".no_spillover", layer, source, Status.SKIP, "No polygon/line features."
        )
    if cfg.aoi is None and cfg.aoi_bbox is None:
        return result(
            CHECK + ".no_spillover", layer, source, Status.SKIP,
            "no_spillover requires topology.aoi or aoi_bbox (skipped).",
            severity=Severity.WARN,
        )

    aoi = _resolve_aoi(cfg, geoms.total_bounds, metric_crs, source_crs)
    if aoi is None or aoi.is_empty:
        return result(
            CHECK + ".no_spillover", layer, source, Status.ERROR,
            "Could not resolve AOI for spillover check.", severity=cfg.severity,
        )

    issues: list[Issue] = []
    for idx, geom in geoms.geometry.items():
        try:
            g = _safe_valid(geom)
            if g is None or g.is_empty:
                continue
            excess = g.difference(aoi)
        except Exception:  # noqa: BLE001
            continue
        if excess is None or excess.is_empty:
            continue
        # Length for lines, area for polygons — ignore tiny noise via min_area when area-like.
        metric = float(excess.area) if excess.area > 0 else float(excess.length)
        if metric <= cfg.min_area:
            continue
        issues.append(
            Issue(
                message=f"spillover outside AOI metric={metric:.6g}",
                feature_id=idx,
                row_index=idx,
                detail={"metric": metric},
            )
        )
    n = len(issues)
    return result(
        CHECK + ".no_spillover", layer, source, status_for(n, cfg.severity),
        "No features outside AOI." if n == 0 else f"{n} feature(s) spill outside AOI.",
        severity=cfg.severity, n_total=len(geoms), n_failed=n, issues=issues[:200],
    )


def _iter_lines(geom):
    if geom.geom_type == "LineString":
        yield geom
    elif geom.geom_type == "MultiLineString":
        yield from geom.geoms


def _dangles(valid, types, layer, source, cfg, to_wgs84, metric_crs, source_crs) -> CheckResult:
    lines = valid[types.isin(["LineString", "MultiLineString"])]
    if lines.empty:
        return result(CHECK + ".no_dangles", layer, source, Status.SKIP, "No line features.")

    tol = cfg.snap_tolerance
    min_degree = max(1, cfg.min_degree)

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

    candidates = [k for k, c in counts.items() if c < min_degree]

    if cfg.ignore_boundary and candidates:
        aoi = _resolve_aoi(cfg, lines.total_bounds, metric_crs, source_crs)
        if aoi is not None and not aoi.is_empty:
            boundary = aoi.boundary
            edge_tol = tol if tol > 0 else _DEFAULT_BOUNDARY_EPS
            kept = []
            for key in candidates:
                x, y = repr_pt[key]
                try:
                    if boundary.distance(Point(x, y)) <= edge_tol:
                        continue
                except Exception:  # noqa: BLE001
                    pass
                kept.append(key)
            candidates = kept

    issues: list[Issue] = []
    for key in candidates[:200]:
        x, y = repr_pt[key]
        lon, lat = _to_lonlat(x, y, to_wgs84)
        degree = int(counts[key])
        detail: dict[str, Any] = {"x": x, "y": y, "degree": degree}
        if lon is not None and lat is not None:
            detail["lon"] = lon
            detail["lat"] = lat
        issues.append(
            Issue(
                message=(
                    f"dangling endpoint degree={degree} at "
                    f"({lon:.6f}, {lat:.6f})" if lon is not None
                    else f"dangling endpoint degree={degree} at ({x:.3f}, {y:.3f})"
                ),
                feature_id=where[key][0],
                row_index=where[key][0],
                detail=detail,
            )
        )
    n = len(candidates)
    return result(
        CHECK + ".no_dangles", layer, source, status_for(n, cfg.severity),
        "No dangling endpoints." if n == 0 else f"{n} dangling line endpoint(s) detected.",
        severity=cfg.severity, n_total=len(lines), n_failed=n, issues=issues,
    )


def _undershoots(valid, types, layer, source, cfg, to_wgs84) -> CheckResult:
    """Degree-1 endpoints that lie within snap_tolerance of another line (almost connected)."""
    lines = valid[types.isin(["LineString", "MultiLineString"])]
    if lines.empty:
        return result(
            CHECK + ".no_undershoots", layer, source, Status.SKIP, "No line features."
        )
    tol = cfg.snap_tolerance if cfg.snap_tolerance > 0 else _DEFAULT_BOUNDARY_EPS
    min_degree = max(1, cfg.min_degree)

    def _key(pt: tuple[float, float]) -> tuple[float, float]:
        return (round(pt[0] / tol), round(pt[1] / tol))

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

    # Build a MultiLineString index of all lines for nearest-distance queries.
    try:
        line_list = list(lines.geometry)
        tree = shapely.STRtree(line_list)
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".no_undershoots", layer, source, Status.ERROR,
            f"undershoot search failed: {exc}", severity=cfg.severity,
        )

    issues: list[Issue] = []
    flagged = 0
    for key, degree in counts.items():
        if degree >= min_degree:
            continue
        x, y = repr_pt[key]
        pt = Point(x, y)
        owner = where[key][0]
        try:
            # Candidates near the endpoint; exclude the owner feature's own geometry.
            idxs = tree.query(pt.buffer(tol), predicate="intersects")
        except Exception:  # noqa: BLE001
            continue
        near = False
        for j in idxs:
            other_idx = lines.index[j]
            if other_idx == owner:
                continue
            try:
                dist = float(line_list[j].distance(pt))
            except Exception:  # noqa: BLE001
                continue
            # Touching (dist ~ 0) is a legitimate T-junction / connection, not an undershoot.
            if 1e-9 < dist <= tol:
                near = True
                break
        if not near:
            continue
        flagged += 1
        if len(issues) < 200:
            lon, lat = _to_lonlat(x, y, to_wgs84)
            detail: dict[str, Any] = {"x": x, "y": y, "degree": int(degree), "tolerance": tol}
            if lon is not None and lat is not None:
                detail["lon"] = lon
                detail["lat"] = lat
            issues.append(
                Issue(
                    message=(
                        f"undershoot endpoint near another line at "
                        f"({lon:.6f}, {lat:.6f})" if lon is not None
                        else f"undershoot endpoint near another line at ({x:.3f}, {y:.3f})"
                    ),
                    feature_id=owner,
                    row_index=owner,
                    detail=detail,
                )
            )
    return result(
        CHECK + ".no_undershoots", layer, source, status_for(flagged, cfg.severity),
        "No undershoot endpoints." if flagged == 0
        else f"{flagged} undershoot endpoint(s) near another line.",
        severity=cfg.severity, n_total=len(lines), n_failed=flagged, issues=issues,
    )


def _intersection_points(geom) -> list[Point]:
    """Collect Point parts from an intersection geometry."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Point":
        return [geom]
    if geom.geom_type == "MultiPoint":
        return list(geom.geoms)
    if geom.geom_type in ("LineString", "LinearRing"):
        coords = list(geom.coords)
        if not coords:
            return []
        return [Point(coords[0]), Point(coords[-1])]
    if geom.geom_type == "MultiLineString":
        out: list[Point] = []
        for g in geom.geoms:
            out.extend(_intersection_points(g))
        return out
    if geom.geom_type == "GeometryCollection":
        out = []
        for g in geom.geoms:
            out.extend(_intersection_points(g))
        return out
    return []


def _overshoots(valid, types, layer, source, cfg, to_wgs84) -> CheckResult:
    """Short stubs past a crossing/touching line (complement of undershoots).

    A degree-1 endpoint within ``snap_tolerance`` of another line, where the
    owner line also intersects that line and the along-line stub from the
    nearest intersection to the endpoint is in ``(0, tol]``, is an overshoot.
    Exact T-junctions (stub ≈ 0) pass.
    """
    lines = valid[types.isin(["LineString", "MultiLineString"])]
    if lines.empty:
        return result(
            CHECK + ".no_overshoots", layer, source, Status.SKIP, "No line features."
        )
    tol = cfg.snap_tolerance if cfg.snap_tolerance > 0 else _DEFAULT_BOUNDARY_EPS
    min_degree = max(1, cfg.min_degree)

    def _key(pt: tuple[float, float]) -> tuple[float, float]:
        return (round(pt[0] / tol), round(pt[1] / tol))

    # Per-endpoint metadata for degree-1 candidates only.
    counts: Counter = Counter()
    endpoint_meta: list[tuple[Any, tuple[float, float], Any]] = []  # (owner, xy, LineString)
    for idx, geom in lines.geometry.items():
        for ls in _iter_lines(geom):
            coords = list(ls.coords)
            if len(coords) < 2:
                continue
            for pt in (coords[0], coords[-1]):
                key = _key(pt)
                counts[key] += 1
                endpoint_meta.append((idx, (float(pt[0]), float(pt[1])), ls))

    try:
        line_list = list(lines.geometry)
        tree = shapely.STRtree(line_list)
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".no_overshoots", layer, source, Status.ERROR,
            f"overshoot search failed: {exc}", severity=cfg.severity,
        )

    issues: list[Issue] = []
    flagged = 0
    seen: set[tuple[Any, float, float]] = set()
    for owner, (x, y), ls in endpoint_meta:
        key = _key((x, y))
        if counts[key] >= min_degree:
            continue
        pt = Point(x, y)
        try:
            idxs = tree.query(pt.buffer(tol), predicate="intersects")
        except Exception:  # noqa: BLE001
            continue
        stub: float | None = None
        for j in idxs:
            other_idx = lines.index[j]
            if other_idx == owner:
                continue
            other = line_list[j]
            try:
                if float(pt.distance(other)) > tol:
                    continue
                inter = ls.intersection(other)
            except Exception:  # noqa: BLE001
                continue
            pts = _intersection_points(inter)
            if not pts:
                continue
            end_m = float(ls.project(pt))
            best = min(abs(end_m - float(ls.project(p))) for p in pts)
            if 1e-9 < best <= tol:
                stub = best
                break
        if stub is None:
            continue
        sig = (owner, round(x, 6), round(y, 6))
        if sig in seen:
            continue
        seen.add(sig)
        flagged += 1
        if len(issues) < 200:
            lon, lat = _to_lonlat(x, y, to_wgs84)
            detail: dict[str, Any] = {
                "x": x, "y": y, "stub_length": stub, "tolerance": tol,
            }
            if lon is not None and lat is not None:
                detail["lon"] = lon
                detail["lat"] = lat
            issues.append(
                Issue(
                    message=(
                        f"overshoot stub={stub:.4g} past junction at "
                        f"({lon:.6f}, {lat:.6f})" if lon is not None
                        else f"overshoot stub={stub:.4g} past junction at ({x:.3f}, {y:.3f})"
                    ),
                    feature_id=owner,
                    row_index=owner,
                    detail=detail,
                )
            )
    return result(
        CHECK + ".no_overshoots", layer, source, status_for(flagged, cfg.severity),
        "No overshoot stubs." if flagged == 0
        else f"{flagged} overshoot stub(s) past a junction.",
        severity=cfg.severity, n_total=len(lines), n_failed=flagged, issues=issues,
    )


def _multipart_overlap(valid, types, layer, source, cfg) -> CheckResult:
    """Parts of MultiPolygon / MultiLineString must not overlap (touching is OK).

    Uses raw part geometries — do not ``make_valid`` the multipart as a whole
    (GEOS often treats overlapping or edge-adjacent MultiPolygons as invalid and
    ``make_valid`` rewrites parts, which hides real overlaps or invents false ones).
    """
    feats = valid[types.isin(["MultiPolygon", "MultiLineString"])]
    if feats.empty:
        return result(
            CHECK + ".no_multipart_overlap", layer, source, Status.SKIP,
            "No multipart features.",
        )

    issues: list[Issue] = []
    n_failed = 0
    for idx, geom in feats.geometry.items():
        if geom is None or geom.is_empty or not hasattr(geom, "geoms"):
            continue
        parts = list(geom.geoms)
        if len(parts) < 2:
            continue
        overlap_metric = 0.0
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                a, b = parts[i], parts[j]
                try:
                    if a.touches(b) or not a.intersects(b):
                        continue
                    inter = a.intersection(b)
                except Exception:  # noqa: BLE001
                    continue
                if inter is None or inter.is_empty:
                    continue
                area = float(inter.area)
                length = float(inter.length) if hasattr(inter, "length") else 0.0
                # Polygon parts: area overlap only (shared edges have area 0).
                if area > cfg.min_area:
                    overlap_metric = max(overlap_metric, area)
                elif (
                    a.geom_type in ("LineString", "LinearRing")
                    and b.geom_type in ("LineString", "LinearRing")
                    and length > max(cfg.min_area, 1e-6)
                ):
                    overlap_metric = max(overlap_metric, length)
        if overlap_metric <= 0:
            continue
        n_failed += 1
        if len(issues) < 200:
            issues.append(
                Issue(
                    message=f"multipart parts overlap metric={overlap_metric:.6g}",
                    feature_id=idx,
                    row_index=idx,
                    detail={"metric": overlap_metric, "n_parts": len(parts)},
                )
            )
    return result(
        CHECK + ".no_multipart_overlap", layer, source, status_for(n_failed, cfg.severity),
        "No overlapping multipart parts." if n_failed == 0
        else f"{n_failed} multipart feature(s) with overlapping parts.",
        severity=cfg.severity, n_total=len(feats), n_failed=n_failed, issues=issues,
    )


def _coincident_edges(valid, types, layer, source, cfg) -> CheckResult:
    """Flag almost-adjacent polygons and touching pairs with ragged boundaries."""
    polys = _polygons(valid, types)
    if len(polys) < 2:
        return result(
            CHECK + ".coincident_edges", layer, source, Status.SKIP,
            "Need at least two polygon features.",
        )

    eps = _boundary_eps(cfg)
    geom_arr = polys.geometry.to_numpy()
    index_arr = polys.geometry.index.to_numpy()

    try:
        # Candidates within ε (includes touches / near-misses).
        left_pos, right_pos = polys.sindex.query(
            polys.geometry, predicate="dwithin", distance=eps
        )
    except TypeError:
        # Older GeoPandas without dwithin in sindex.query — buffer fallback.
        buffered = polys.copy()
        buffered[buffered.geometry.name] = polys.geometry.buffer(eps)
        try:
            joined = buffered.sjoin(polys, predicate="intersects", how="inner")
        except Exception as exc:  # noqa: BLE001
            return result(
                CHECK + ".coincident_edges", layer, source, Status.ERROR,
                f"coincident search failed: {exc}", severity=cfg.severity,
            )
        right_col = "index_right" if "index_right" in joined.columns else joined.columns[-1]
        pairs = []
        for left_idx, row in joined.iterrows():
            right_idx = row[right_col]
            if left_idx < right_idx:
                pairs.append((left_idx, right_idx))
        return _coincident_from_index_pairs(pairs, polys, eps, layer, source, cfg)
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".coincident_edges", layer, source, Status.ERROR,
            f"coincident search failed: {exc}", severity=cfg.severity,
        )

    left_pos = np.asarray(left_pos, dtype=np.intp)
    right_pos = np.asarray(right_pos, dtype=np.intp)
    mask = left_pos < right_pos
    left_pos = left_pos[mask]
    right_pos = right_pos[mask]
    if len(left_pos) > cfg.max_pairs:
        left_pos = left_pos[: cfg.max_pairs]
        right_pos = right_pos[: cfg.max_pairs]

    flagged_pairs: list[tuple[Any, Any, str, float]] = []
    for lp, rp in zip(left_pos, right_pos, strict=True):
        a, b = geom_arr[lp], geom_arr[rp]
        left_idx, right_idx = index_arr[lp], index_arr[rp]
        kind_metric = _coincident_pair(a, b, eps, cfg.min_area)
        if kind_metric is not None:
            flagged_pairs.append((left_idx, right_idx, kind_metric[0], kind_metric[1]))

    return _coincident_result(flagged_pairs, len(polys), eps, layer, source, cfg)


def _coincident_pair(a, b, eps: float, min_area: float) -> tuple[str, float] | None:
    """Return (kind, metric) when a neighbour pair fails coincident-edge rules."""
    try:
        dist = float(a.distance(b))
        touches = bool(a.touches(b))
        if bool(a.overlaps(b)):
            return None
        inter = a.intersection(b)
        if getattr(inter, "area", 0.0) > max(min_area, 0.0):
            return None
    except Exception:  # noqa: BLE001
        return None
    if not touches and 0 < dist <= eps:
        return ("almost_adjacent", dist)
    if touches:
        try:
            # Compare only boundary portions near the neighbour (full-boundary
            # Hausdorff is always large for distinct parcels).
            near_a = a.boundary.intersection(b.buffer(eps))
            near_b = b.boundary.intersection(a.buffer(eps))
            if near_a.is_empty or near_b.is_empty:
                return None
            hd = float(near_a.hausdorff_distance(near_b))
        except Exception:  # noqa: BLE001
            return None
        if hd > eps:
            return ("ragged_boundary", hd)
    return None


def _coincident_from_index_pairs(pairs, polys, eps, layer, source, cfg) -> CheckResult:
    """Fallback coincident check when sindex dwithin is unavailable."""
    geoms = polys.geometry
    flagged_pairs: list[tuple[Any, Any, str, float]] = []
    for left_idx, right_idx in pairs[: cfg.max_pairs]:
        kind_metric = _coincident_pair(
            geoms.loc[left_idx], geoms.loc[right_idx], eps, cfg.min_area
        )
        if kind_metric is not None:
            flagged_pairs.append((left_idx, right_idx, kind_metric[0], kind_metric[1]))
    return _coincident_result(flagged_pairs, len(polys), eps, layer, source, cfg)


def _coincident_result(flagged_pairs, n_total, eps, layer, source, cfg) -> CheckResult:
    issues = [
        Issue(
            message=f"{kind} ({metric:.6g} m)",
            feature_id=left_idx,
            row_index=left_idx,
            detail={"other": right_idx, "kind": kind, "metric": metric, "eps": eps},
        )
        for left_idx, right_idx, kind, metric in flagged_pairs[:200]
    ]
    seen = {i for pair in flagged_pairs for i in pair[:2]}
    n = len(seen)
    return result(
        CHECK + ".coincident_edges", layer, source, status_for(n, cfg.severity),
        "No coincident-edge problems." if n == 0
        else f"{n} polygon(s) with almost-adjacent or ragged shared boundaries.",
        severity=cfg.severity, n_total=n_total, n_failed=n, issues=issues,
    )
