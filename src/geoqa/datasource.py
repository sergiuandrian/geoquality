"""Loading geospatial files and folders into in-memory layers.

A *layer* is the unit geoqa checks. A simple file (Shapefile, GeoJSON) maps to a
single layer; a multi-layer container (GeoPackage) expands into several. A
directory source expands into every matching file.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd

from geoqa.config import SourceSpec, Suite
from geoqa.sql_ident import quote_table, redact

# Extensions we will pick up automatically when a directory is given without a
# pattern. (pyogrio/GDAL can read more, but these are the common interchange
# formats; users can always supply an explicit ``pattern``.)
DEFAULT_EXTENSIONS = (
    "*.shp",
    "*.geojson",
    "*.json",
    "*.gpkg",
    "*.gml",
    "*.kml",
    "*.fgb",
    "*.gpx",
    "*.parquet",
)

MULTILAYER_EXTENSIONS = {".gpkg", ".gml", ".kml"}

# Deprecated aliases — prefer CheckSpec.chunk_safe / requires_full_layer.
# Kept for any external importers; derived from the registry when available.
CHUNK_SAFE_CHECKS = frozenset({"crs", "geometry", "attributes", "schema", "metadata"})
CHUNK_GLOBAL_CHECKS = frozenset({"duplicates", "topology"})


@dataclass
class Layer:
    """One named geospatial layer to be validated."""

    name: str
    source: str
    gdf: gpd.GeoDataFrame | None = None
    sublayer: str | None = None
    error: str | None = None
    chunk_size: int | None = None
    prefer_sql: bool = False
    # PostGIS extras for SQL pushdown (connection is never written to reports).
    connection: str | None = None
    table: str | None = None
    query: str | None = None
    geom_column: str = "geom"
    source_spec: SourceSpec | None = field(default=None, repr=False)


def iter_layers(suite: Suite, *, defer_load: bool = False) -> Iterator[Layer]:
    """Expand every configured source into one or more ``Layer`` objects.

    When ``defer_load`` is True, file layers are returned without reading the
    GeoDataFrame (used when chunked iteration will load batches later).
    """
    seen: set[tuple[str, str | None]] = set()
    for spec in suite.sources:
        if spec.connection:
            if defer_load and spec.prefer_sql:
                yield _postgis_stub(spec)
            else:
                yield _load_postgis(spec)
            continue
        if spec.path is None:  # guarded by SourceSpec validation, narrows for typing
            continue
        base = suite.resolve_path(spec.path)
        files = _expand_files(base, spec.pattern)
        if not files:
            yield Layer(
                name=spec.name or base.name,
                source=str(base),
                error=f"no files matched: {base}"
                + (f" (pattern={spec.pattern})" if spec.pattern else ""),
                chunk_size=spec.chunk_size,
                source_spec=spec,
            )
            continue
        for file in files:
            key = (str(file.resolve()), spec.layer)
            if key in seen:
                continue
            seen.add(key)
            if defer_load and spec.chunk_size:
                yield from _file_stubs(file, spec)
            else:
                yield from _load_file(file, spec)


def iter_file_chunks(layer: Layer) -> Iterator[gpd.GeoDataFrame]:
    """Yield GeoDataFrame batches for a file layer with ``chunk_size`` set."""
    if not layer.chunk_size:
        if layer.gdf is not None:
            yield layer.gdf
        return
    path = Path(layer.source)
    sub = layer.sublayer
    chunk = layer.chunk_size
    try:
        import pyogrio

        info: dict[str, Any] = pyogrio.read_info(str(path), layer=sub) if sub else pyogrio.read_info(str(path))
        n = int(info.get("features") or 0)
        for start in range(0, max(n, 1), chunk):
            kwargs: dict[str, Any] = {
                "skip_features": start,
                "max_features": chunk,
            }
            if sub is not None:
                kwargs["layer"] = sub
            gdf = pyogrio.read_dataframe(str(path), **kwargs)
            if gdf is None or len(gdf) == 0:
                if start == 0:
                    yield gpd.GeoDataFrame(geometry=[], crs=None)
                break
            yield gdf
    except Exception:
        # Fallback: load once and slice in memory (still validates chunk merge logic).
        gdf = gpd.read_file(path, layer=sub) if sub else gpd.read_file(path)
        if len(gdf) == 0:
            yield gdf
            return
        for start in range(0, len(gdf), chunk):
            yield gdf.iloc[start : start + chunk].copy()


def _file_stubs(file: Path, spec: SourceSpec) -> Iterator[Layer]:
    suffix = file.suffix.lower()
    if suffix == ".parquet":
        yield Layer(
            name=spec.name or file.stem,
            source=str(file),
            chunk_size=spec.chunk_size,
            source_spec=spec,
        )
        return
    sublayers: list[str | None]
    if spec.layer is not None:
        sublayers = [spec.layer]
    elif suffix in MULTILAYER_EXTENSIONS:
        found = _list_sublayers(file)
        sublayers = found if found else [None]
    else:
        sublayers = [None]
    for sub in sublayers:
        yield Layer(
            name=spec.name or (sub if sub else file.stem),
            source=str(file),
            sublayer=sub,
            chunk_size=spec.chunk_size,
            source_spec=spec,
        )


def _postgis_stub(spec: SourceSpec) -> Layer:
    name = spec.name or spec.table or "query"
    return Layer(
        name=name,
        source=redact(spec.connection or ""),
        prefer_sql=True,
        connection=spec.connection,
        table=spec.table,
        query=spec.query,
        geom_column=spec.geom_column,
        source_spec=spec,
    )


def _quote_table(table: str) -> str:
    """Backward-compatible alias for :func:`geoqa.sql_ident.quote_table`."""
    return quote_table(table)


def _load_postgis(spec: SourceSpec) -> Layer:
    """Read a single layer from a PostGIS/SQLAlchemy connection."""
    name = spec.name or spec.table or "query"
    redacted = redact(spec.connection or "")
    try:
        from sqlalchemy import create_engine
    except Exception:  # noqa: BLE001 - optional dependency
        return Layer(
            name=name, source=redacted,
            error="PostGIS sources require SQLAlchemy. Install geoqa[postgis].",
            prefer_sql=spec.prefer_sql,
            connection=spec.connection,
            table=spec.table,
            query=spec.query,
            geom_column=spec.geom_column,
            source_spec=spec,
        )

    if spec.query:
        sql = spec.query
    else:
        try:
            sql = f"SELECT * FROM {quote_table(str(spec.table))}"
        except ValueError as exc:
            return Layer(name=name, source=redacted, error=str(exc), source_spec=spec)

    engine = None
    try:
        engine = create_engine(str(spec.connection))
        with engine.connect() as conn:
            gdf = gpd.read_postgis(sql, conn, geom_col=spec.geom_column)
        return Layer(
            name=name, source=redacted, gdf=gdf,
            prefer_sql=spec.prefer_sql,
            connection=spec.connection,
            table=spec.table,
            query=spec.query,
            geom_column=spec.geom_column,
            source_spec=spec,
        )
    except Exception as exc:  # noqa: BLE001
        return Layer(name=name, source=redacted, error=str(exc), source_spec=spec)
    finally:
        if engine is not None:
            engine.dispose()


def _redact(url: str) -> str:
    """Backward-compatible alias for :func:`geoqa.sql_ident.redact`."""
    return redact(url)


def _expand_files(base: Path, pattern: str | None) -> list[Path]:
    if base.is_dir():
        patterns = [pattern] if pattern else list(DEFAULT_EXTENSIONS)
        out: list[Path] = []
        for pat in patterns:
            out.extend(sorted(base.glob(pat)))
        # Shapefile sidecars are not separate layers; only *.shp matters.
        return [p for p in out if p.is_file()]
    if base.exists():
        return [base]
    return []


def _list_sublayers(file: Path) -> list[str | None]:
    try:
        import pyogrio

        info = pyogrio.list_layers(str(file))
        return [str(row[0]) for row in info]
    except Exception:  # noqa: BLE001 - fall back to single-layer read
        return []


def _load_file(file: Path, spec: SourceSpec) -> Iterator[Layer]:
    suffix = file.suffix.lower()
    name_override = spec.name

    if suffix == ".parquet":
        try:
            gdf = gpd.read_parquet(file)
            yield Layer(
                name=name_override or file.stem, source=str(file), gdf=gdf,
                chunk_size=spec.chunk_size, source_spec=spec,
            )
        except Exception as exc:  # noqa: BLE001
            yield Layer(
                name=name_override or file.stem, source=str(file), error=str(exc),
                chunk_size=spec.chunk_size, source_spec=spec,
            )
        return

    sublayers: list[str | None]
    if spec.layer is not None:
        sublayers = [spec.layer]
    elif suffix in MULTILAYER_EXTENSIONS:
        found = _list_sublayers(file)
        sublayers = found if found else [None]
    else:
        sublayers = [None]

    for sub in sublayers:
        layer_name = name_override or (sub if sub else file.stem)
        try:
            gdf = gpd.read_file(file, layer=sub) if sub else gpd.read_file(file)
            yield Layer(
                name=layer_name, source=str(file), gdf=gdf, sublayer=sub,
                chunk_size=spec.chunk_size, source_spec=spec,
            )
        except Exception as exc:  # noqa: BLE001
            yield Layer(
                name=layer_name, source=str(file), sublayer=sub, error=str(exc),
                chunk_size=spec.chunk_size, source_spec=spec,
            )
