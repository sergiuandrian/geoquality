"""Geometry validity checks (with optional auto-fix via make_valid)."""

from __future__ import annotations

import geopandas as gpd
from shapely.validation import explain_validity, make_valid

from geoqa.checks.base import build_issues, result, status_for
from geoqa.config import GeometryCheck
from geoqa.result import CheckResult, Issue, Status

CHECK = "geometry"


def run(gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: GeometryCheck) -> list[CheckResult]:
    if not cfg.enabled:
        return []
    if gdf.geometry.name not in gdf.columns:
        return [result(CHECK, layer, source, Status.SKIP, "Layer has no geometry column.")]

    geom = gdf.geometry
    results: list[CheckResult] = []
    n_total = len(gdf)

    missing_mask = geom.isna()
    # is_empty raises on missing; restrict to present geometries.
    present = ~missing_mask
    empty_mask = present & geom.is_empty.fillna(False)

    if cfg.no_missing:
        n = int(missing_mask.sum())
        results.append(
            result(
                CHECK + ".no_missing", layer, source,
                status_for(n, cfg.severity),
                "All features have geometry." if n == 0 else f"{n} feature(s) have NULL geometry.",
                severity=cfg.severity, n_total=n_total, n_failed=n,
                issues=build_issues(geom.index[missing_mask], gdf, "NULL geometry"),
            )
        )

    if cfg.no_empty:
        n = int(empty_mask.sum())
        results.append(
            result(
                CHECK + ".no_empty", layer, source,
                status_for(n, cfg.severity),
                "No empty geometries." if n == 0 else f"{n} feature(s) have EMPTY geometry.",
                severity=cfg.severity, n_total=n_total, n_failed=n,
                issues=build_issues(geom.index[empty_mask], gdf, "EMPTY geometry"),
            )
        )

    if cfg.valid:
        checkable = present & ~empty_mask
        valid_mask = geom.is_valid
        invalid_mask = checkable & ~valid_mask
        invalid_idx = list(geom.index[invalid_mask])

        issues: list[Issue] = []
        for idx in invalid_idx[:200]:
            try:
                reason = explain_validity(geom.loc[idx])
            except Exception:  # noqa: BLE001
                reason = "invalid geometry"
            issues.append(Issue(message=reason, feature_id=idx))

        fixed = 0
        if cfg.fix and invalid_idx:
            col = gdf.geometry.name
            for idx in invalid_idx:
                try:
                    gdf.at[idx, col] = make_valid(geom.loc[idx])
                    fixed += 1
                except Exception:  # noqa: BLE001
                    pass

        n = len(invalid_idx)
        if n == 0:
            msg = "All geometries are valid."
        elif fixed:
            msg = f"{n} invalid geometr(ies) found; {fixed} repaired with make_valid()."
        else:
            msg = f"{n} invalid geometr(ies) found (use geometry.fix: true to repair)."
        results.append(
            result(
                CHECK + ".valid", layer, source,
                status_for(n - fixed if cfg.fix else n, cfg.severity),
                msg, severity=cfg.severity, n_total=n_total, n_failed=n,
                issues=issues, fixed=fixed,
            )
        )

    return results
