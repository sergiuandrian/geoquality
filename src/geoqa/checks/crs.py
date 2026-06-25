"""Coordinate reference system checks."""

from __future__ import annotations

import geopandas as gpd

from geoqa.checks.base import result
from geoqa.config import CrsCheck
from geoqa.result import CheckResult, Status

CHECK = "crs"


def run(gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: CrsCheck) -> list[CheckResult]:
    if not cfg.enabled:
        return []

    crs = gdf.crs
    sev = cfg.severity

    if crs is None:
        if cfg.required:
            return [
                result(
                    CHECK, layer, source, Status.FAIL,
                    "CRS is undefined (missing .prj / SRID). Set a CRS before publishing.",
                    severity=sev, n_total=len(gdf), n_failed=len(gdf),
                )
            ]
        return [result(CHECK, layer, source, Status.PASS, "CRS undefined but not required.")]

    try:
        epsg = crs.to_epsg()
    except Exception:  # noqa: BLE001
        epsg = None
    label = f"EPSG:{epsg}" if epsg else str(crs).splitlines()[0]

    if cfg.expected_epsg is not None and epsg != cfg.expected_epsg:
        return [
            result(
                CHECK, layer, source, Status.FAIL,
                f"CRS is {label}, expected EPSG:{cfg.expected_epsg}. "
                "Reprojection may shift features; verify the datum transformation.",
                severity=sev,
            )
        ]

    if cfg.allowed_epsg and epsg not in cfg.allowed_epsg:
        allowed = ", ".join(f"EPSG:{e}" for e in cfg.allowed_epsg)
        return [
            result(
                CHECK, layer, source, Status.FAIL,
                f"CRS {label} is not in the allowed list ({allowed}).",
                severity=sev,
            )
        ]

    return [result(CHECK, layer, source, Status.PASS, f"CRS OK ({label}).")]
