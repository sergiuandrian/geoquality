"""Optional PostGIS write-back for repaired geometries."""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd

from geoqa.config import PostgisWriteConfig
from geoqa.sql_ident import quote_table, redact

logger = logging.getLogger("geoqa")


def write_postgis(
    gdf: gpd.GeoDataFrame,
    cfg: PostgisWriteConfig,
    *,
    allow_write: bool = False,
) -> dict[str, Any]:
    """UPDATE repaired geometries into PostGIS (or dry-run).

    Requires ``cfg.id_column`` to identify rows. Default ``dry_run=True`` never
    mutates the database. Live writes additionally require ``allow_write=True``
    (CLI confirmation flag).

    Live writes refuse SRID 0 (missing / non-EPSG CRS) and verify each UPDATE
    via ``cursor.rowcount`` so a wrong ``id_column`` cannot look like success.
    """
    result: dict[str, Any] = {
        "dry_run": cfg.dry_run or not allow_write,
        "table": cfg.table,
        "connection": redact(cfg.connection or ""),
        "n_rows": len(gdf),
        "updated": 0,
        "missed": 0,
        "ok": False,
        "message": "",
    }
    if not cfg.connection or not cfg.table:
        result["message"] = "postgis write requires connection and table"
        return result

    if cfg.id_column not in gdf.columns:
        result["message"] = f"id_column {cfg.id_column!r} not in layer"
        return result

    if result["dry_run"]:
        result["ok"] = True
        result["message"] = (
            f"dry_run: would UPDATE {len(gdf)} row(s) in {quote_table(cfg.table)} "
            f"via {cfg.id_column!r} / {cfg.geom_column!r}"
        )
        logger.info("%s", result["message"])
        return result

    srid = _epsg_srid(gdf)
    if srid is None:
        result["message"] = (
            "postgis write failed: layer CRS has no EPSG code; "
            "refusing live UPDATE with SRID 0"
        )
        return result

    try:
        from sqlalchemy import create_engine, text
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"SQLAlchemy required for PostGIS write-back: {exc}"
        return result

    engine = None
    try:
        engine = create_engine(str(cfg.connection))
        table_sql = quote_table(cfg.table)
        id_col = cfg.id_column
        geom_col = cfg.geom_column
        updated = 0
        missed = 0
        with engine.begin() as conn:
            for _, row in gdf.iterrows():
                geom = row.geometry
                if geom is None:
                    continue
                pk = row[id_col]
                res = conn.execute(
                    text(
                        f'UPDATE {table_sql} '
                        f'SET "{geom_col}" = ST_SetSRID(ST_GeomFromWKB(:wkb), :srid) '
                        f'WHERE "{id_col}" = :pk'
                    ),
                    {"wkb": bytes(geom.wkb), "srid": srid, "pk": pk},
                )
                n = res.rowcount
                if n is None or n < 0:
                    # Driver did not report rowcount — treat as unknown failure.
                    missed += 1
                elif n == 0:
                    missed += 1
                else:
                    updated += int(n)
        result["updated"] = updated
        result["missed"] = missed
        if missed:
            result["message"] = (
                f"postgis write failed: updated {updated} row(s), "
                f"{missed} id(s) matched 0 rows (check id_column / types)"
            )
        else:
            result["ok"] = True
            result["message"] = f"updated {updated} row(s) in {table_sql}"
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"postgis write failed: {exc}"
        logger.exception("postgis write-back failed")
    finally:
        if engine is not None:
            engine.dispose()
    return result


def _epsg_srid(gdf: gpd.GeoDataFrame) -> int | None:
    """Return a positive EPSG code, or ``None`` if CRS is missing/non-EPSG."""
    if gdf.crs is None:
        return None
    try:
        epsg = gdf.crs.to_epsg()
    except Exception:  # noqa: BLE001
        return None
    if epsg is None:
        return None
    code = int(epsg)
    return code if code > 0 else None
