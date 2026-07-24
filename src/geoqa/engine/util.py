"""Small helpers shared across engine phases."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from geoqa.datasource import Layer
from geoqa.result import CheckResult, LayerReport, Severity, Status


def now():
    return datetime.now(timezone.utc)


def dominant_geom_type(gdf: gpd.GeoDataFrame) -> str | None:
    if gdf.geometry.name not in gdf.columns or gdf.empty:
        return None
    try:
        types = gdf.geometry.geom_type.dropna()
        if types.empty:
            return None
        return str(types.mode().iloc[0])
    except Exception:  # noqa: BLE001
        return None


def write_fixed(gdf: gpd.GeoDataFrame, layer: Layer, fix_dir: Path, lr: LayerReport) -> None:
    fix_dir.mkdir(parents=True, exist_ok=True)
    out = fix_dir / f"{layer.name}.fixed.gpkg"
    try:
        gdf.to_file(out, driver="GPKG")
        lr.results.append(
            CheckResult(
                check="geometry.fix.output", layer=layer.name, source=layer.source,
                status=Status.PASS, severity=Severity.INFO,
                message=f"Wrote repaired layer to {out}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        lr.results.append(
            CheckResult(
                check="geometry.fix.output", layer=layer.name, source=layer.source,
                status=Status.ERROR, severity=Severity.WARN,
                message=f"Failed to write repaired layer: {exc}",
            )
        )


def merge_chunk_results(merged: dict[str, list[CheckResult]]) -> list[CheckResult]:
    """Combine per-chunk check results into one result per check name."""
    out: list[CheckResult] = []
    for _group, results in merged.items():
        by_check: dict[str, list[CheckResult]] = {}
        for r in results:
            by_check.setdefault(r.check, []).append(r)
        for check, parts in by_check.items():
            n_total = sum(p.n_total for p in parts)
            n_failed = sum(p.n_failed for p in parts)
            issues = []
            for p in parts:
                issues.extend(p.issues)
                if len(issues) >= 200:
                    issues = issues[:200]
                    break
            rank = {
                Status.ERROR: 5, Status.FAIL: 4, Status.WARN: 3,
                Status.PASS: 1, Status.SKIP: 0,
            }
            status = max((p.status for p in parts), key=lambda s: rank.get(s, 0))
            severity = parts[0].severity
            if n_failed and status in (Status.PASS, Status.SKIP):
                status = Status.FAIL if severity == Severity.ERROR else Status.WARN
            msg = parts[-1].message
            if len(parts) > 1:
                msg = f"{msg} (merged from {len(parts)} chunk(s))"
            out.append(
                CheckResult(
                    check=check, layer=parts[0].layer, source=parts[0].source,
                    status=status, severity=severity, message=msg,
                    n_total=n_total, n_failed=n_failed, issues=issues,
                    duration_s=sum(p.duration_s for p in parts),
                )
            )
    return out
