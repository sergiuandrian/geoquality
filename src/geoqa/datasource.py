"""Loading geospatial files and folders into in-memory layers.

A *layer* is the unit geoqa checks. A simple file (Shapefile, GeoJSON) maps to a
single layer; a multi-layer container (GeoPackage) expands into several. A
directory source expands into every matching file.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd

from geoqa.config import Suite

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


@dataclass
class Layer:
    """One named geospatial layer to be validated."""

    name: str
    source: str
    gdf: gpd.GeoDataFrame | None = None
    sublayer: str | None = None
    error: str | None = None


def iter_layers(suite: Suite) -> Iterator[Layer]:
    """Expand every configured source into one or more loaded ``Layer`` objects."""
    seen: set[tuple[str, str | None]] = set()
    for spec in suite.sources:
        base = suite.resolve_path(spec.path)
        files = _expand_files(base, spec.pattern)
        if not files:
            yield Layer(
                name=spec.name or base.name,
                source=str(base),
                error=f"no files matched: {base}"
                + (f" (pattern={spec.pattern})" if spec.pattern else ""),
            )
            continue
        for file in files:
            key = (str(file.resolve()), spec.layer)
            if key in seen:
                continue
            seen.add(key)
            yield from _load_file(file, spec.layer, spec.name)


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


def _list_sublayers(file: Path) -> list[str]:
    try:
        import pyogrio

        info = pyogrio.list_layers(str(file))
        return [str(row[0]) for row in info]
    except Exception:  # noqa: BLE001 - fall back to single-layer read
        return []


def _load_file(file: Path, sublayer: str | None, name_override: str | None) -> Iterator[Layer]:
    suffix = file.suffix.lower()

    if suffix == ".parquet":
        try:
            gdf = gpd.read_parquet(file)
            yield Layer(name=name_override or file.stem, source=str(file), gdf=gdf)
        except Exception as exc:  # noqa: BLE001
            yield Layer(name=name_override or file.stem, source=str(file), error=str(exc))
        return

    sublayers: list[str | None]
    if sublayer is not None:
        sublayers = [sublayer]
    elif suffix in MULTILAYER_EXTENSIONS:
        found = _list_sublayers(file)
        sublayers = found if found else [None]
    else:
        sublayers = [None]

    for sub in sublayers:
        layer_name = name_override or (sub if sub else file.stem)
        try:
            gdf = gpd.read_file(file, layer=sub) if sub else gpd.read_file(file)
            yield Layer(name=layer_name, source=str(file), gdf=gdf, sublayer=sub)
        except Exception as exc:  # noqa: BLE001
            yield Layer(name=layer_name, source=str(file), sublayer=sub, error=str(exc))
