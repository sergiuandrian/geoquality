"""Helpers shared across checks."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np

from geoqa.result import CheckResult, Issue, Severity, Status

# Global, metric, equal-area CRS used when a single UTM zone cannot represent the
# data (wide/near-global extents). EPSG:6933 (WGS 84 / NSIDC EASE-Grid 2.0
# Global) is valid worldwide and keeps areas comparable for overlap/gap math.
_GLOBAL_EQUAL_AREA = 6933


def to_metric(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, str | None]:
    """Return ``gdf`` in a metric CRS for area/distance/IoU computations.

    Areas and distances are only meaningful in a projected CRS. When the layer
    is in a geographic CRS (degrees) we reproject to an estimated local UTM zone
    so thresholds expressed in metres behave sensibly. For wide/near-global
    extents a single UTM zone produces distorted or **non-finite** coordinates
    far from the zone (which crash GEOS overlay/sjoin), so we fall back to a
    global equal-area CRS. Returns the (possibly reprojected) frame and a
    human-readable note (or ``None`` when the data was already projected).
    """
    crs = gdf.crs
    if crs is None:
        return gdf, "CRS undefined; areas/distances are in raw coordinate units"
    try:
        if crs.is_geographic:
            metric = gdf.estimate_utm_crs()
            projected = gdf.to_crs(metric)
            if not _all_finite(projected):
                # A single UTM zone cannot represent this extent; use a global
                # equal-area projection that is valid worldwide.
                projected = gdf.to_crs(_GLOBAL_EQUAL_AREA)
                return projected, (
                    f"reprojected to EPSG:{_GLOBAL_EQUAL_AREA} "
                    "(global equal-area) for metric computations"
                )
            label = f"EPSG:{metric.to_epsg()}" if metric.to_epsg() else metric.name
            return projected, f"reprojected to {label} for metric computations"
    except Exception:  # noqa: BLE001 - fall back to raw units if estimation fails
        return gdf, "could not reproject to a metric CRS; using raw coordinate units"
    return gdf, None


def _all_finite(gdf: gpd.GeoDataFrame) -> bool:
    """True when the layer's total bounds are finite (no NaN/Inf coordinates)."""
    try:
        bounds = gdf.total_bounds
    except Exception:  # noqa: BLE001
        return False
    return bool(np.all(np.isfinite(bounds)))


def result(
    check: str,
    layer: str,
    source: str,
    status: Status,
    message: str,
    severity: Severity = Severity.ERROR,
    n_total: int = 0,
    n_failed: int = 0,
    issues: list[Issue] | None = None,
    fixed: int = 0,
) -> CheckResult:
    return CheckResult(
        check=check,
        layer=layer,
        source=source,
        status=status,
        message=message,
        severity=severity,
        n_total=n_total,
        n_failed=n_failed,
        issues=issues or [],
        fixed=fixed,
    )


def status_for(n_failed: int, severity: Severity) -> Status:
    """Map a failure count + configured severity onto a result status."""
    if n_failed == 0:
        return Status.PASS
    return Status.WARN if severity == Severity.WARN else Status.FAIL


def build_issues(
    failed_index: Any,
    gdf: Any,
    message: str,
    id_col: str | None = None,
    limit: int = 200,
) -> list[Issue]:
    """Create ``Issue`` objects for a set of failing row indices."""
    issues: list[Issue] = []
    for idx in list(failed_index)[:limit]:
        feature_id = idx
        if id_col and id_col in gdf.columns:
            try:
                feature_id = gdf.at[idx, id_col]
            except Exception:  # noqa: BLE001
                feature_id = idx
        issues.append(Issue(message=message, feature_id=feature_id))
    return issues
