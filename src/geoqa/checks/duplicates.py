"""Duplicate geometry detection: exact (normalized WKB) and fuzzy (spatial)."""

from __future__ import annotations

import geopandas as gpd

from geoqa.checks.base import build_issues, result, status_for, to_metric
from geoqa.config import DuplicatesCheck
from geoqa.result import CheckResult, Issue, Status

_POLYGONAL = {"Polygon", "MultiPolygon"}

CHECK = "duplicates"


def run(gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: DuplicatesCheck) -> list[CheckResult]:
    if not cfg.enabled or gdf.geometry.name not in gdf.columns:
        return []

    results: list[CheckResult] = []
    n_total = len(gdf)

    if cfg.exact:
        results.append(_exact(gdf, layer, source, cfg, n_total))
    if cfg.fuzzy.enabled:
        results.append(_fuzzy(gdf, layer, source, cfg, n_total))
    return results


def _exact(gdf, layer, source, cfg, n_total) -> CheckResult:
    def _key(g):
        if g is None or g.is_empty:
            return None
        try:
            return g.normalize().wkb
        except Exception:  # noqa: BLE001
            return g.wkb

    keys = gdf.geometry.apply(_key)
    dup_all = keys.duplicated(keep=False) & keys.notna()
    dup_extra = int((keys.duplicated(keep="first") & keys.notna()).sum())
    n = int(dup_all.sum())
    return result(
        CHECK + ".exact", layer, source,
        status_for(dup_extra, cfg.severity),
        "No exact duplicate geometries."
        if dup_extra == 0
        else f"{n} features form exact-duplicate groups ({dup_extra} redundant).",
        severity=cfg.severity, n_total=n_total, n_failed=dup_extra,
        issues=build_issues(gdf.index[dup_all], gdf, "exact duplicate geometry"),
    )


def _fuzzy(gdf, layer, source, cfg, n_total) -> CheckResult:
    fz = cfg.fuzzy
    # IoU and distance only make sense in a metric CRS; reproject up front.
    metric_gdf, note = to_metric(gdf)
    geom_col = metric_gdf.geometry.name
    work = metric_gdf[[geom_col]].copy()
    work = work[work.geometry.notna() & ~work.geometry.is_empty]

    try:
        joined = work.sjoin(work, predicate=fz.predicate, how="inner")
    except Exception as exc:  # noqa: BLE001
        return result(
            CHECK + ".fuzzy", layer, source, Status.ERROR,
            f"Fuzzy duplicate join failed: {exc}", severity=cfg.severity, n_total=n_total,
        )

    right_col = "index_right" if "index_right" in joined.columns else joined.columns[-1]
    geoms = work.geometry

    flagged: set = set()
    issues: list[Issue] = []
    for left_idx, row in joined.iterrows():
        right_idx = row[right_col]
        if left_idx == right_idx or not (left_idx < right_idx):
            continue
        a, b = geoms.loc[left_idx], geoms.loc[right_idx]
        # Decide per pair (not per layer) so mixed-geometry layers behave.
        if a.geom_type in _POLYGONAL and b.geom_type in _POLYGONAL:
            inter = a.intersection(b).area
            union = a.area + b.area - inter
            score = inter / union if union > 0 else 0.0
            if score >= fz.min_overlap:
                flagged.update((left_idx, right_idx))
                if len(issues) < 200:
                    issues.append(
                        Issue(
                            message=f"near-duplicate polygon (IoU={score:.3f})",
                            feature_id=left_idx,
                            detail={"other": right_idx, "iou": round(score, 4)},
                        )
                    )
        elif fz.max_distance > 0:
            dist = a.distance(b)
            if dist <= fz.max_distance:
                flagged.update((left_idx, right_idx))
                if len(issues) < 200:
                    issues.append(
                        Issue(
                            message=f"near-duplicate within {dist:.3g} m",
                            feature_id=left_idx,
                            detail={"other": right_idx, "distance": round(float(dist), 6)},
                        )
                    )

    n = len(flagged)
    msg = (
        "No fuzzy/near duplicates found."
        if n == 0
        else f"{n} features look like near-duplicates (predicate={fz.predicate})."
    )
    if note:
        msg = f"{msg} [{note}]"
    return result(
        CHECK + ".fuzzy", layer, source,
        status_for(n, cfg.severity),
        msg, severity=cfg.severity, n_total=n_total, n_failed=n, issues=issues,
    )
