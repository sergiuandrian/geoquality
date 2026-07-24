"""Schema conformance: columns, geometry types, CRS, coordinate precision."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import yaml

from geoqa.checks.base import result, status_for, to_metric
from geoqa.config import SchemaCheck
from geoqa.result import CheckResult, Issue, Status

CHECK = "schema"

_TYPE_ALIASES = {
    "string": {"object", "string", "str"},
    "number": {"float64", "float32", "float", "number", "int64", "int32", "int"},
    "integer": {"int64", "int32", "int", "Int64", "integer"},
    "boolean": {"bool", "boolean"},
}


def run(gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: SchemaCheck) -> list[CheckResult]:
    if not cfg.enabled:
        return []

    if cfg.path:
        path = _schema_file_path(cfg, source)
        if path is None:
            return [
                result(
                    CHECK,
                    layer,
                    source,
                    Status.ERROR,
                    f"schema path not found: {cfg.path}",
                    severity=cfg.severity,
                )
            ]
        cfg = _load_schema_file(cfg, path)

    results: list[CheckResult] = []
    results.extend(_check_columns(gdf, layer, source, cfg))
    results.extend(_check_geometry(gdf, layer, source, cfg))
    results.extend(_check_precision(gdf, layer, source, cfg))
    return results


def _schema_file_path(cfg: SchemaCheck, source: str) -> Path | None:
    """Return an existing schema file path, or ``None`` if missing."""
    if not cfg.path:
        return None
    path = Path(cfg.path)
    if path.is_file():
        return path
    # Try beside the layer source (relative leftover / non-suite callers).
    candidate = Path(source).resolve().parent / cfg.path
    if candidate.is_file():
        return candidate
    return None


def _load_schema_file(cfg: SchemaCheck, path: Path) -> SchemaCheck:
    """Load external schema YAML and merge under inline fields."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return cfg
    merged = {**raw, **cfg.model_dump(exclude_unset=True)}
    merged["enabled"] = True
    merged["path"] = str(path)
    return SchemaCheck.model_validate(merged)


def _check_columns(
    gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: SchemaCheck
) -> list[CheckResult]:
    if not cfg.columns:
        return []
    issues: list[Issue] = []
    for name, rule in cfg.columns.items():
        if name not in gdf.columns:
            if rule.required:
                issues.append(Issue(message=f"missing required column {name!r}"))
            continue
        series = gdf[name]
        if rule.type:
            if not _dtype_matches(series.dtype, rule.type):
                issues.append(
                    Issue(
                        message=f"column {name!r} type {series.dtype} ≠ {rule.type}",
                        detail={"column": name, "dtype": str(series.dtype)},
                    )
                )
        if rule.min is not None or rule.max is not None:
            numeric = _as_numeric(series)
            if numeric is None:
                issues.append(
                    Issue(message=f"column {name!r} not numeric for min/max",
                          detail={"column": name})
                )
            else:
                if rule.min is not None and bool((numeric < rule.min).any()):
                    issues.append(
                        Issue(message=f"column {name!r} has values < {rule.min}",
                              detail={"column": name, "min": rule.min})
                    )
                if rule.max is not None and bool((numeric > rule.max).any()):
                    issues.append(
                        Issue(message=f"column {name!r} has values > {rule.max}",
                              detail={"column": name, "max": rule.max})
                    )

    n = len(issues)
    return [
        result(
            CHECK + ".columns", layer, source, status_for(n, cfg.severity),
            "Schema columns OK." if n == 0 else f"{n} schema column issue(s).",
            severity=cfg.severity, n_total=len(cfg.columns), n_failed=n, issues=issues[:200],
        )
    ]


def _check_geometry(
    gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: SchemaCheck
) -> list[CheckResult]:
    geom_cfg = cfg.geometry
    if not geom_cfg.types and geom_cfg.srid is None:
        return []
    issues: list[Issue] = []
    n_total = len(gdf)

    if geom_cfg.types and gdf.geometry.name in gdf.columns:
        allowed = {t.lower() for t in geom_cfg.types}
        types = gdf.geometry.geom_type.fillna("").str.lower()
        bad = types[~types.isin(allowed) & (types != "")]
        for idx in list(bad.index)[:200]:
            issues.append(
                Issue(
                    message=f"geometry type {gdf.geometry.geom_type.at[idx]!r} not in {geom_cfg.types}",
                    feature_id=idx, row_index=idx,
                    detail={"geom_type": str(gdf.geometry.geom_type.at[idx])},
                )
            )

    if geom_cfg.srid is not None:
        epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
        if epsg != geom_cfg.srid:
            issues.append(
                Issue(
                    message=f"CRS EPSG:{epsg} ≠ expected EPSG:{geom_cfg.srid}",
                    detail={"epsg": epsg, "expected": geom_cfg.srid},
                )
            )

    n = len(issues)
    return [
        result(
            CHECK + ".geometry", layer, source, status_for(n, cfg.severity),
            "Schema geometry OK." if n == 0 else f"{n} schema geometry issue(s).",
            severity=cfg.severity, n_total=n_total, n_failed=n, issues=issues[:200],
        )
    ]


def _check_precision(
    gdf: gpd.GeoDataFrame, layer: str, source: str, cfg: SchemaCheck
) -> list[CheckResult]:
    prec = cfg.precision
    if prec.max_decimal_places is None and prec.max_xy_resolution is None:
        return []
    if gdf.geometry.name not in gdf.columns or gdf.empty:
        return [
            result(CHECK + ".precision", layer, source, Status.SKIP, "No geometries.")
        ]

    work = gdf
    note = None
    if prec.max_xy_resolution is not None:
        work, note = to_metric(gdf)

    issues: list[Issue] = []
    max_dp = prec.max_decimal_places
    max_res = prec.max_xy_resolution

    for idx, geom in work.geometry.items():
        if geom is None or geom.is_empty:
            continue
        try:
            coords = _coords(geom)
        except Exception:  # noqa: BLE001
            continue
        if not coords:
            continue
        arr = np.asarray(coords, dtype=float)
        if max_dp is not None:
            # Excess precision: value differs from rounded value.
            rounded = np.round(arr, max_dp)
            if not np.allclose(arr, rounded, rtol=0, atol=10 ** (-(max_dp + 2))):
                issues.append(
                    Issue(
                        message=f"coordinate exceeds {max_dp} decimal places",
                        feature_id=idx, row_index=idx,
                        detail={"max_decimal_places": max_dp},
                    )
                )
                if len(issues) >= 200:
                    break
        if max_res is not None and max_res > 0:
            # Quantize to resolution grid; flag if snap changes coords beyond noise.
            snapped = np.round(arr / max_res) * max_res
            if not np.allclose(arr, snapped, rtol=0, atol=max_res * 1e-6):
                issues.append(
                    Issue(
                        message=f"coordinate finer than resolution {max_res}",
                        feature_id=idx, row_index=idx,
                        detail={"max_xy_resolution": max_res},
                    )
                )
                if len(issues) >= 200:
                    break

    n = len(issues)
    msg = "Coordinate precision OK." if n == 0 else f"{n} precision issue(s)."
    if note:
        msg = f"{msg} [{note}]"
    return [
        result(
            CHECK + ".precision", layer, source, status_for(n, cfg.severity),
            msg, severity=cfg.severity, n_total=len(gdf), n_failed=n, issues=issues[:200],
        )
    ]


def _dtype_matches(dtype: Any, expected: str) -> bool:
    kind = str(dtype)
    aliases = _TYPE_ALIASES.get(expected.lower(), {expected.lower()})
    return any(a.lower() in kind.lower() for a in aliases)


def _as_numeric(series):
    try:
        return series.astype(float)
    except (TypeError, ValueError):
        return None


def _coords(geom) -> list[tuple[float, float]]:
    if geom.geom_type == "Point":
        return [(float(geom.x), float(geom.y))]
    if hasattr(geom, "exterior"):
        return [(float(x), float(y)) for x, y, *_ in geom.exterior.coords]
    if geom.geom_type in ("LineString", "LinearRing"):
        return [(float(x), float(y)) for x, y, *_ in geom.coords]
    if hasattr(geom, "geoms"):
        out: list[tuple[float, float]] = []
        for g in geom.geoms:
            out.extend(_coords(g))
        return out
    return []
