"""Optional PostGIS write-back for repaired geometries."""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd

from geoqa.config import PostgisWriteConfig
from geoqa.datasource import _quote_table, _redact

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
    """
    result: dict[str, Any] = {
        "dry_run": cfg.dry_run or not allow_write,
        "table": cfg.table,
        "connection": _redact(cfg.connection or ""),
        "n_rows": len(gdf),
        "updated": 0,
        "message": "",
    }
    if not cfg.connection or not cfg.table:
        result["message"] = "postgis write requires connection and table"
        return result

    if cfg.id_column not in gdf.columns:
        result["message"] = f"id_column {cfg.id_column!r} not in layer"
        return result

    if result["dry_run"]:
        result["message"] = (
            f"dry_run: would UPDATE {len(gdf)} row(s) in {_quote_table(cfg.table)} "
            f"via {cfg.id_column!r} / {cfg.geom_column!r}"
        )
        logger.info("%s", result["message"])
        return result

    try:
        from sqlalchemy import create_engine, text
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"SQLAlchemy required for PostGIS write-back: {exc}"
        return result

    engine = None
    try:
        engine = create_engine(str(cfg.connection))
        table_sql = _quote_table(cfg.table)
        id_col = cfg.id_column
        geom_col = cfg.geom_column
        updated = 0
        with engine.begin() as conn:
            for _, row in gdf.iterrows():
                geom = row.geometry
                if geom is None:
                    continue
                pk = row[id_col]
                srid = 0
                if gdf.crs is not None:
                    try:
                        srid = int(gdf.crs.to_epsg() or 0)
                    except Exception:  # noqa: BLE001
                        srid = 0
                conn.execute(
                    text(
                        f'UPDATE {table_sql} '
                        f'SET "{geom_col}" = ST_SetSRID(ST_GeomFromWKB(:wkb), :srid) '
                        f'WHERE "{id_col}" = :pk'
                    ),
                    {"wkb": bytes(geom.wkb), "srid": srid, "pk": pk},
                )
                updated += 1
        result["updated"] = updated
        result["message"] = f"updated {updated} row(s) in {table_sql}"
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"postgis write failed: {exc}"
        logger.exception("postgis write-back failed")
    finally:
        if engine is not None:
            engine.dispose()
    return result
