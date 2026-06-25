"""Generate intentionally-messy sample data to demo geoqa.

Run from the ``examples`` folder:

    python make_sample_data.py

It writes ``data/parcels.gpkg`` and ``data/roads.gpkg`` containing a mix of
clean and broken features (invalid geometry, overlaps, gaps, dangles,
duplicates, out-of-domain attributes) so every check has something to find.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Polygon

DATA = Path(__file__).parent / "data"


def make_parcels() -> gpd.GeoDataFrame:
    # Clean tile
    p1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    # Overlaps p1
    p2 = Polygon([(8, 0), (18, 0), (18, 10), (8, 10)])
    # Leaves a gap between p1 and this one (starts at x=11, not 10)
    p3 = Polygon([(11, 0), (20, 0), (20, -10), (11, -10)])
    # Self-intersecting "bowtie" -> invalid geometry
    invalid = Polygon([(0, 20), (10, 30), (10, 20), (0, 30)])
    # Exact duplicate of p1
    dup = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])

    return gpd.GeoDataFrame(
        {
            "parcel_id": ["P1", "P2", "P3", "P4", "P1"],  # duplicate id "P1"
            "zone": ["residential", "commercial", "farmland", "industrial", None],
            "geometry": [p1, p2, p3, invalid, dup],
        },
        crs="EPSG:3857",
    )


def make_roads() -> gpd.GeoDataFrame:
    r1 = LineString([(0, 0), (10, 0)])
    r2 = LineString([(10, 0), (10, 10)])
    r3 = LineString([(10, 10), (20, 10)])
    dangler = LineString([(10, 0), (10, -5)])  # endpoint at (10,-5) connects to nothing

    return gpd.GeoDataFrame(
        {
            "name": ["Main St", "2nd Ave", None, "Dead End"],
            "surface": ["asphalt", "gravel", "concrete", None],  # "concrete" out of domain
            "lanes": [2, 4, 12, 1],  # 12 exceeds max
            "geometry": [r1, r2, r3, dangler],
        },
        crs="EPSG:3857",
    )


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    make_parcels().to_file(DATA / "parcels.gpkg", driver="GPKG")
    make_roads().to_file(DATA / "roads.gpkg", driver="GPKG")
    print(f"Wrote sample data to {DATA}")


if __name__ == "__main__":
    main()
