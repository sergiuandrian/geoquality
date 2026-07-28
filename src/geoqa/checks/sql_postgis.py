"""PostGIS SQL pushdown for cheap geometry / null checks.

When ``prefer_sql: true`` and the source is a table (not an arbitrary query),
geoqa can run ``ST_IsValid`` / empty / null probes in the database and avoid
materializing geometries for those checks. Other checks still need a GeoDataFrame.
"""

from __future__ import annotations

import logging
from typing import Any

from geoqa.checks.base import result, status_for
from geoqa.config import GeometryCheck, SourceSpec
from geoqa.result import CheckResult, Issue, Severity, Status
from geoqa.sql_ident import quote_table, redact

logger = logging.getLogger("geoqa.sql")

CHECK = "geometry"


def sql_geometry_checks(
    spec: SourceSpec,
    layer: str,
    cfg: GeometryCheck,
    *,
    max_issues: int = 50,
) -> tuple[list[CheckResult], int]:
    """Run geometry.valid / no_empty / no_missing via SQL.

    Returns ``(results, n_rows)``. Does not load geometries into Python.
    """
    if not cfg.enabled:
        return [], 0
    if not spec.connection or not spec.table:
        return [], 0

    try:
        from sqlalchemy import create_engine, text
    except Exception as exc:  # noqa: BLE001
        return [
            result(
                CHECK, layer, redact(spec.connection), Status.ERROR,
                f"SQL pushdown requires SQLAlchemy: {exc}", severity=cfg.severity,
            )
        ], 0

    try:
        quoted = quote_table(spec.table)
    except ValueError as exc:
        return [
            result(CHECK, layer, redact(spec.connection), Status.ERROR, str(exc),
                   severity=cfg.severity)
        ], 0

    geom = _safe_ident(spec.geom_column)
    source = redact(spec.connection)
    engine = create_engine(str(spec.connection))
    results: list[CheckResult] = []
    n_total = 0
    try:
        with engine.connect() as conn:
            n_total = int(conn.execute(text(f"SELECT COUNT(*) FROM {quoted}")).scalar() or 0)

            if cfg.no_missing:
                rows = list(conn.execute(text(
                    f"SELECT ctid::text AS id FROM {quoted} WHERE {geom} IS NULL LIMIT :lim"
                ), {"lim": max_issues}).fetchall())
                n = int(conn.execute(text(
                    f"SELECT COUNT(*) FROM {quoted} WHERE {geom} IS NULL"
                )).scalar() or 0)
                results.append(_sql_result(
                    CHECK + ".no_missing", layer, source, cfg.severity, n_total, n,
                    "All features have geometry." if n == 0
                    else f"{n} feature(s) have NULL geometry.",
                    rows, "NULL geometry",
                ))

            if cfg.no_empty:
                rows = list(conn.execute(text(
                    f"SELECT ctid::text AS id FROM {quoted} "
                    f"WHERE {geom} IS NOT NULL AND ST_IsEmpty({geom}) LIMIT :lim"
                ), {"lim": max_issues}).fetchall())
                n = int(conn.execute(text(
                    f"SELECT COUNT(*) FROM {quoted} "
                    f"WHERE {geom} IS NOT NULL AND ST_IsEmpty({geom})"
                )).scalar() or 0)
                results.append(_sql_result(
                    CHECK + ".no_empty", layer, source, cfg.severity, n_total, n,
                    "No empty geometries." if n == 0
                    else f"{n} feature(s) have EMPTY geometry.",
                    rows, "EMPTY geometry",
                ))

            if cfg.valid:
                rows = list(conn.execute(text(
                    f"SELECT ctid::text AS id FROM {quoted} "
                    f"WHERE {geom} IS NOT NULL AND NOT ST_IsEmpty({geom}) "
                    f"AND NOT ST_IsValid({geom}) LIMIT :lim"
                ), {"lim": max_issues}).fetchall())
                n = int(conn.execute(text(
                    f"SELECT COUNT(*) FROM {quoted} "
                    f"WHERE {geom} IS NOT NULL AND NOT ST_IsEmpty({geom}) "
                    f"AND NOT ST_IsValid({geom})"
                )).scalar() or 0)
                results.append(_sql_result(
                    CHECK + ".valid", layer, source, cfg.severity, n_total, n,
                    "All geometries are valid." if n == 0
                    else f"{n} feature(s) have invalid geometry.",
                    rows, "invalid geometry (SQL)",
                ))
    except Exception as exc:  # noqa: BLE001
        logger.exception("SQL geometry pushdown failed")
        return [
            result(
                CHECK, layer, source, Status.ERROR,
                f"SQL pushdown failed: {exc}", severity=cfg.severity,
            )
        ], n_total
    finally:
        engine.dispose()

    return results, n_total


def only_sql_safe_checks(cfg) -> bool:
    """True when the layer config only needs SQL-pushdown geometry checks.

    Returns False when repair / legacy ``fix`` is enabled so the engine
    materializes the GeoDataFrame and can run the repair pipeline.

    Plugin checks with ``sql_pushdown == "none"`` that are enabled also force
    materialization (they cannot run on the SQL-only path).
    """
    geom = getattr(cfg, "geometry", None)
    if geom is not None:
        if getattr(geom, "fix", False):
            return False
        repair = getattr(geom, "repair", None)
        if repair is not None and getattr(repair, "enabled", False):
            return False
    if getattr(cfg.crs, "enabled", False):
        return False
    dup = cfg.duplicates
    if getattr(dup, "enabled", False) and (
        getattr(dup, "exact", False) or getattr(getattr(dup, "fuzzy", None), "enabled", False)
    ):
        return False
    topo = cfg.topology
    if getattr(topo, "enabled", False) and any(
        getattr(topo, flag, False)
        for flag in (
            "no_overlaps", "no_gaps", "no_coverage_gaps", "no_spillover",
            "no_dangles", "no_undershoots", "coincident_edges", "coverage_area_ratio",
        )
    ):
        return False
    attrs = cfg.attributes
    if getattr(attrs, "enabled", False) and (
        attrs.required or attrs.not_null or attrs.unique or attrs.domains or attrs.max_null_fraction
    ):
        return False
    if getattr(getattr(cfg, "layer_schema", None), "enabled", False):
        return False
    if getattr(getattr(cfg, "metadata", None), "enabled", False):
        return False

    # Plugins / unknown checks: enabled + no SQL pushdown ⇒ need a GeoDataFrame.
    from geoqa.config import check_config_for
    from geoqa.registry import get_registry

    known = {
        "crs", "geometry", "duplicates", "attributes", "topology", "schema", "metadata",
    }
    for spec in get_registry().specs():
        if spec.name in known:
            continue
        sub = check_config_for(cfg, spec.name)
        if sub is None or not getattr(sub, "enabled", False):
            continue
        if spec.sql_pushdown == "none":
            return False

    return bool(getattr(cfg.geometry, "enabled", False))



def _sql_result(
    check: str,
    layer: str,
    source: str,
    severity: Severity,
    n_total: int,
    n_failed: int,
    message: str,
    rows: list[Any],
    issue_msg: str,
) -> CheckResult:
    issues = [
        Issue(message=issue_msg, feature_id=row[0], detail={"via": "sql", "ctid": row[0]})
        for row in rows
    ]
    return result(
        check, layer, source, status_for(n_failed, severity), message,
        severity=severity, n_total=n_total, n_failed=n_failed, issues=issues,
    )


def _safe_ident(name: str) -> str:
    if not name or any(c in name for c in ';--"\'\\') or "." in name or "\x00" in name:
        raise ValueError(f"invalid geometry column: {name!r}")
    return f'"{name}"'
