"""Shared fixtures and geometry helpers for the geoqa test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")
from shapely.geometry import LineString, Polygon  # noqa: E402


def square(x: float = 0, y: float = 0, size: float = 10) -> Polygon:
    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size)])


def bowtie(x: float = 0, y: float = 0) -> Polygon:
    """A self-intersecting (invalid) polygon."""
    return Polygon([(x, y), (x + 10, y + 10), (x + 10, y), (x, y + 10)])


def make_parcels() -> gpd.GeoDataFrame:
    p1 = square(0, 0)
    p2 = square(8, 0)  # overlaps p1
    invalid = bowtie(0, 20)
    dup = square(0, 0)  # exact duplicate of p1
    return gpd.GeoDataFrame(
        {
            "parcel_id": ["P1", "P2", "P3", "P1"],  # duplicate id
            "zone": ["residential", "commercial", "industrial", None],
            "geometry": [p1, p2, invalid, dup],
        },
        crs="EPSG:3857",
    )


def make_roads() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "name": ["Main St", "2nd Ave", None],
            "surface": ["asphalt", "gravel", "concrete"],
            "lanes": [2, 4, 12],
            "geometry": [
                LineString([(0, 0), (10, 0)]),
                LineString([(10, 0), (10, 10)]),
                LineString([(10, 0), (10, -5)]),  # dangling endpoint
            ],
        },
        crs="EPSG:3857",
    )


@pytest.fixture
def parcels_gdf() -> gpd.GeoDataFrame:
    return make_parcels()


@pytest.fixture
def roads_gdf() -> gpd.GeoDataFrame:
    return make_roads()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A directory containing a GeoPackage and a GeoJSON layer."""
    d = tmp_path / "data"
    d.mkdir()
    make_parcels().to_file(d / "parcels.gpkg", driver="GPKG")
    make_roads().to_file(d / "roads.geojson", driver="GeoJSON")
    return d


@pytest.fixture
def suite_file(data_dir: Path) -> Path:
    """A geoqa.yml wired to ``data_dir`` exercising several checks."""
    cfg = f"""
version: 1
name: "Test suite"
sources:
  - path: "{data_dir.as_posix()}"
defaults:
  crs:
    required: true
  geometry:
    valid: true
  duplicates:
    exact: true
    severity: warn
layers:
  parcels:
    attributes:
      required: [parcel_id, zone]
      unique: [parcel_id]
      domains:
        zone:
          allowed: [residential, commercial, industrial]
    topology:
      enabled: true
      severity: warn
      no_overlaps: true
  roads:
    attributes:
      not_null: [name]
      domains:
        lanes:
          min: 1
          max: 8
    topology:
      enabled: true
      severity: warn
      no_dangles: true
"""
    path = data_dir.parent / "geoqa.yml"
    path.write_text(cfg, encoding="utf-8")
    return path
