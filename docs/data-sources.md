# Data sources

Point geoqa at files, folders, or a PostGIS database.

```yaml
sources:
  - path: "data/"            # folder (expands to every matching file)
    pattern: "*.gpkg"
  - path: "data/roads.shp"   # single file
  - connection: "postgresql://user:pass@host:5432/gis"   # PostGIS
    table: "public.parcels"  # ...or use `query: "SELECT * FROM ..."`
    geom_column: "geom"
    name: "parcels"
```

## Files & folders

A folder expands into every matching file. Without a `pattern`, common formats
are picked up automatically (`.shp`, `.geojson`, `.gpkg`, `.parquet`, ...).
Multi-layer containers (GeoPackage) expand into one layer per sub-layer.

## PostGIS

PostGIS support needs the optional extra:

```bash
pip install "geoqa[postgis]"
```

Provide a SQLAlchemy `connection` URL plus either a `table` or a raw `query`.
Credentials embedded in the URL are **redacted** in every report.
